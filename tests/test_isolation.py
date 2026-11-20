"""Real agent processes (no mocks): JSONL protocol, isolation, eval-mode secrecy, timeouts, attacks."""

import json
import os
import tempfile

import pytest

from auditenv.env import ACTIONS
from auditenv.generator import Episode, generate_episode
from auditenv.policies import POLICIES
from auditenv.runner import run_episode
from auditenv.sandbox.runner import run_isolated, run_official

from conftest import CLEAN_CONFIG, episode

MESSAGE_KEYS = {"type", "observation", "reward", "terminated", "truncated"}
FORBIDDEN = ("issue_id", "expected_amount_minor", "evidence_sets", "provenance", "\"tp\"", "\"fp\"", "\"fn\"",
             "precision", "recall", "mutations", "id_map", "\"seed\"")


@pytest.fixture(scope="module")
def ep():
    return episode(3, {"interactions": {"fx_partial_at_cutoff": {"count": 1}}})


def attempts(run):
    return [json.loads(line[7:]) for line in run.agent["stderr_tail"].splitlines() if line.startswith("ATTACK ")]


def assert_clean_shutdown(run):
    assert run.agent["process_exited"] and run.agent["process_group_empty"]
    assert run.agent["workdir_removed"] and run.agent["workdir_files_left"] == []


def assert_no_leak(run, ep):
    text = "".join(run.sent_messages)
    for issue in ep.truth["issues"]:
        assert issue["issue_id"] not in text
    for key in FORBIDDEN:
        assert key not in text, key
    for message in map(json.loads, run.sent_messages):
        assert set(message) == MESSAGE_KEYS


# ---------------------------------------------------------------------------- protocol and baselines
@pytest.mark.parametrize("name", ["random_explorer", "simple_reconciler", "flag_everything", "reference_solver"])
@pytest.mark.parametrize("seed", [2, 5])
def test_isolated_policy_matches_in_process_policy(name, seed):
    e = episode(seed)
    isolated = run_isolated(name, seed, episode=e, mode="eval", policy_seed=4)
    local = run_episode(POLICIES[name](), seed, episode=e, mode="eval", policy_seed=4)
    assert isolated.isolated and isolated.outcome in ("submitted", "step_limit")
    assert isolated.result["metrics"] == local.result["metrics"]
    assert [t["action"] for t in isolated.trajectory] == [t["action"] for t in local.trajectory]
    assert [t["observation"] for t in isolated.trajectory] == [t["observation"] for t in local.trajectory]
    assert_clean_shutdown(isolated)


def test_oracle_runs_only_on_the_evaluator_side(ep):
    official = run_official("oracle", 3, episode=ep, mode="eval")
    assert official.isolated is False and official.result["reward"] == 1.0
    refused = run_isolated("oracle", 3, episode=ep, mode="eval", time_limit_s=10)
    assert refused.outcome == "agent_exited" and refused.result["outcome"] == "truncated"
    assert "require the truth" in refused.agent["stderr_tail"]


def test_messages_are_plain_observations_and_eval_mode_hides_rewards(ep):
    run = run_isolated("reference_solver", 3, episode=ep, mode="eval")
    assert run.result["metrics"]["f1"] == 1.0
    assert all(json.loads(m)["reward"] == 0.0 for m in run.sent_messages)
    assert json.loads(run.sent_messages[-1])["observation"]["result"] == {"status": "received"}
    assert_no_leak(run, ep)
    assert_clean_shutdown(run)


def test_train_mode_returns_only_the_terminal_reward(ep):
    run = run_isolated("reference_solver", 3, episode=ep, mode="train")
    rewards = [json.loads(m)["reward"] for m in run.sent_messages]
    assert rewards[:-1] == [0.0] * (len(rewards) - 1) and rewards[-1] == run.result["reward"] > 0.9
    assert_no_leak(run, ep)


def test_only_the_five_tools_exist():
    assert set(ACTIONS) == {"list_records", "get_record", "get_document", "calculator", "submit"}


# ---------------------------------------------------------------------------- attacks
def test_agent_cannot_read_truth_provenance_or_the_filesystem(ep):
    run = run_isolated("attack_read_files", 3, episode=ep, mode="eval", time_limit_s=20)
    local = [a for a in attempts(run) if "status" in a and a["attempt"] != "environ"]
    assert len(local) >= 20 and all(a["status"] == "blocked" for a in local), [a for a in local if a["status"] != "blocked"]
    tools = [a for a in attempts(run) if "status" not in a and "error" in a]
    assert tools and {a["error"] for a in tools} <= {"unknown_table", "not_found", "unknown_action"}
    env = next(a for a in attempts(run) if a["attempt"] == "environ")
    assert "AUDITENV_ROOT" not in env.get("detail", "")
    argv = next(a for a in attempts(run) if a["attempt"] == "argv")["value"]
    assert argv == ["--policy", "attack_read_files", "--policy-seed", "0"]  # the episode seed is never passed
    assert_no_leak(run, ep)
    assert_clean_shutdown(run)


def test_calculator_cannot_touch_the_filesystem(ep):
    run = run_isolated("attack_calculator", 3, episode=ep, mode="eval")
    errors = [a["error"] for a in attempts(run) if a["attempt"].startswith("{")]
    assert len(errors) == 9 and set(errors) == {"calculator_error"}


def test_nonexistent_documents_and_forged_cursors(ep):
    run = run_isolated("attack_nonexistent_docs", 3, episode=ep, mode="eval")
    errors = [a["error"] for a in attempts(run) if a["attempt"].startswith("{")]
    assert errors == ["not_found", "not_found", "not_found", "not_found", "invalid_cursor"]


@pytest.mark.parametrize("name", ["attack_guess_ids", "attack_fake_evidence"])
def test_guessed_ids_and_fake_evidence_score_nothing(ep, name):
    run = run_isolated(name, 3, episode=ep, mode="eval")
    assert run.result["metrics"]["tp"] == 0 and run.result["metrics"]["fp"] == 30 and run.result["reward"] == -1.0


def test_resubmission_is_never_graded(ep):
    run = run_isolated("attack_resubmit", 3, episode=ep, mode="eval")
    assert run.outcome == "submitted" and run.steps == 1
    assert run.result["metrics"]["fp"] == 0 and len(run.trajectory) == 2


def test_reward_probe_sees_nothing_during_evaluation(ep):
    run = run_isolated("attack_reward_probe", 3, episode=ep, mode="eval")
    final = next(a for a in attempts(run) if a["attempt"] == "final_message")
    assert final["reward"] == 0.0 and final["result"] == {"status": "received"}
    assert {tuple(a["keys"]) for a in attempts(run) if a["attempt"] == "observation_seen"} <= {
        ("actions", "manifest", "policy", "steps_remaining", "tables", "task"), ("error", "result", "steps_remaining")}
    assert run.result["reward"] == -1.0 and run.result["metrics"]["fp"] == 1


def test_step_limit_cannot_be_exceeded(ep):
    run = run_isolated("attack_step_flood", 3, episode=ep, mode="eval")
    assert run.outcome == "step_limit" and run.steps == 100 and len(run.trajectory) == 101


def test_malformed_json_costs_steps_and_malformed_submission_scores_minus_one(ep):
    run = run_isolated("attack_malformed_json", 3, episode=ep, mode="eval")
    errors = [a.get("error") for a in attempts(run) if "error" in a]
    assert errors[:7] == ["invalid_json", "invalid_json", "invalid_action", "invalid_action", "invalid_json",
                          "invalid_json", "invalid_json"]
    assert run.result["outcome"] == "malformed" and run.result["reward"] == -1.0
    assert json.loads(run.sent_messages[-1])["observation"] == {"result": {"status": "received"}, "error": None,
                                                              "steps_remaining": 92}


@pytest.mark.parametrize("name, outcome", [("attack_oversized_line", "protocol_violation"), ("attack_crash", "agent_exited")])
def test_protocol_violations_and_crashes_truncate_with_an_empty_answer(ep, name, outcome):
    run = run_isolated(name, 3, episode=ep, mode="eval", time_limit_s=10)
    assert run.outcome == outcome and run.trajectory[-1]["truncated"] and run.result["outcome"] == "truncated"
    assert_clean_shutdown(run)


@pytest.mark.parametrize("name", ["attack_slow", "attack_busy_loop"])
def test_time_limit_kills_the_agent_and_cleans_up(name):
    e = episode(5, CLEAN_CONFIG)
    run = run_isolated(name, 5, episode=e, mode="eval", time_limit_s=2.0)
    assert run.outcome == "timeout" and 2.0 <= run.wall_time_s < 4.0
    assert run.agent["killed_by_evaluator"] and run.agent["exit_code"] == -9
    assert run.trajectory[-1]["truncated"] and run.result["outcome"] == "truncated"
    assert run.result["reward"] == pytest.approx(1.0) and run.result["experimental"]["strict_timeout_reward"] == -1.0
    assert_clean_shutdown(run)


def test_evaluator_faults_are_not_revealed_to_the_agent():
    e = episode(5, CLEAN_CONFIG)
    broken = Episode(public=e.public, truth={"episode_id": e.episode_id, "issues": None, "note": "ISS-BROKEN"},
                     provenance=e.provenance)
    run = run_isolated("reference_solver", 5, episode=broken, mode="train")
    assert run.result["outcome"] == "evaluator_error" and "TypeError" in run.result["errors"][0]
    last = json.loads(run.sent_messages[-1])
    assert last["reward"] == 0.0 and last["observation"]["error"] is None
    assert "ISS-BROKEN" not in "".join(run.sent_messages) and "TypeError" not in "".join(run.sent_messages)


def test_no_agent_directories_or_truth_files_are_left_in_temp(ep):
    temp = tempfile.gettempdir()
    before = set(os.listdir(temp))
    for name in ("reference_solver", "attack_read_files", "attack_slow"):
        run_isolated(name, 3, episode=ep, mode="eval", time_limit_s=2.0)
    created = set(os.listdir(temp)) - before
    assert not [d for d in created if d.startswith("auditenv-agent-")]
    for entry in created:
        path = os.path.join(temp, entry)
        if os.path.isfile(path):
            with open(path, "rb") as fh:
                data = fh.read(1 << 20)
            assert not any(i["issue_id"].encode() in data for i in ep.truth["issues"])
