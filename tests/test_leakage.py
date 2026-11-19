"""No closed data may reach the policy: observations, info, errors, rewards in eval mode."""

import re

import pytest

from auditenv import canon
from auditenv.env import AuditEnv
from auditenv.policies import POLICIES
from auditenv.runner import run_episode

from conftest import episode

# tolerance_minor is deliberately absent: tolerances are published in policy.issue_conventions.
FORBIDDEN_KEYS = {"truth", "provenance", "issues", "issue_id", "expected_amount_minor", "evidence_sets",
                  "seed", "id_map", "mutations", "variant"}
INTERNAL = re.compile(r"(inv#|pay#|cust#|num#|bg#|E:|issue:|pid:)")
STEP_OBS_KEYS = {"result", "error", "steps_remaining"}
INFO_KEYS = {"step", "final", "episode_id", "mode", "max_steps", "env_version"}


def _keys(obj, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            _keys(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _keys(v, out)
    return out


@pytest.mark.parametrize("seed", [2, 3, 19])
@pytest.mark.parametrize("name", sorted(POLICIES))
@pytest.mark.parametrize("mode", ["train", "eval"])
def test_trajectories_contain_no_closed_data(seed, name, mode):
    ep = episode(seed)
    run = run_episode(POLICIES[name](), seed, mode=mode, episode=ep)
    text = canon.dumps(run.trajectory)
    for issue in ep.truth["issues"]:
        assert issue["issue_id"] not in text
    assert not INTERNAL.search(text)
    observations = [r["observation"] for r in run.trajectory]
    for obs in observations[1:]:
        assert set(obs) == STEP_OBS_KEYS
    assert not (_keys(observations, set()) & FORBIDDEN_KEYS)
    if mode == "eval":
        assert all(r["reward"] == 0.0 for r in run.trajectory)


def test_info_never_carries_closed_data():
    ep = episode(2)
    env = AuditEnv(episode_factory=lambda s, c: ep)
    _, info = env.reset(2)
    infos = [info]
    for action in ({"action": "list_records", "args": {"table": "ledger"}}, {"action": "nope"},
                   {"action": "submit", "args": {"episode_id": ep.episode_id, "findings": []}}):
        infos.append(env.step(action)[4])
    for i in infos:
        assert set(i) <= INFO_KEYS
    assert "seed" not in canon.dumps(infos)


def test_environment_state_outside_the_grader_holds_no_truth():
    ep = episode(2)
    env = AuditEnv(episode_factory=lambda s, c: ep)
    env.reset(2)
    state = {k: v for k, v in vars(env).items() if k not in ("_grader", "_factory")}
    text = canon.dumps({k: v for k, v in state.items() if isinstance(v, (dict, list, str, int, float, bool, type(None)))})
    for issue in ep.truth["issues"]:
        assert issue["issue_id"] not in text
    assert "expected_amount_minor" not in text and "evidence_sets" not in text


def test_error_messages_are_fixed_templates():
    ep = episode(2)
    env = AuditEnv(episode_factory=lambda s, c: ep)
    env.reset(2)
    secret_ids = {i["issue_id"] for i in ep.truth["issues"]}
    for action in ({"action": "get_record", "args": {"table": "ledger", "id": next(iter(secret_ids))}},
                   {"action": "get_document", "args": {"doc_id": "../truth/truth.json"}},
                   {"action": "list_records", "args": {"table": "../truth"}}):
        error = env.step(action)[0]["error"]
        assert error and not any(s in error["message"] for s in secret_ids)
        assert "truth" not in error["message"] and "/" not in error["message"]
