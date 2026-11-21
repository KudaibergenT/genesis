"""episode_id in findings.json is required and must equal the current episode (case §05).

A missing or foreign episode_id is an invalid submit: terminal reward -1, never substituted and never
graded as empty findings (which on clean books would earn a positive reward).
"""

import json
import os

import pytest

import judge_adapter as ja

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
METRICS = ("tp", "fp", "fn", "precision", "recall", "f1")


@pytest.fixture(scope="module")
def episodes(tmp_path_factory):
    base = tmp_path_factory.mktemp("eid")
    out = {}
    for name, seed in (("clean", 111), ("duplicate_invoice", 112)):
        d = str(base / name)
        with open(os.path.join(ROOT, "configs", "scenarios", f"{name}.json")) as fh:
            ja.generate_episode(seed, json.load(fh), d)
        with open(os.path.join(d, "truth.json")) as fh:
            truth = json.load(fh)
        findings = [{"type": i["type"], "affected_ids": i["affected_ids"], "claimed_amount_minor": i["expected_amount_minor"],
                     "currency": i["currency"], "evidence_ids": i["evidence_sets"][0], "explanation": ""}
                    for i in truth["issues"]]
        out[name] = (d, truth["episode_id"], findings)
    return out


def wrong_ids(eid):
    return {"missing": None, "foreign": "ep-someotherepisode", "null": "NULL", "integer": 7, "empty": "",
            "upper_case": eid.upper(), "trailing_space": eid + " ", "list": [eid]}


def submission(eid, findings, variant):
    if variant == "missing":
        return {"findings": findings}
    value = wrong_ids(eid)[variant]
    return {"episode_id": None if value == "NULL" else value, "findings": findings}


VARIANTS = ["missing", "foreign", "null", "integer", "empty", "upper_case", "trailing_space", "list"]


@pytest.mark.parametrize("name", ["clean", "duplicate_invoice"])
@pytest.mark.parametrize("mode", ["closed", "train"])
@pytest.mark.parametrize("variant", VARIANTS)
def test_missing_or_foreign_episode_id_is_an_invalid_submit(episodes, name, mode, variant):
    d, eid, findings = episodes[name]
    sub = submission(eid, findings, variant)
    env = ja.make_env()
    env.reset(0, {"mode": mode, "episode_dir": d, "team_config": {}})
    env.step({"name": "list_records", "args": {"table": "ledger", "cursor": None, "limit": 50}})
    obs, reward, terminated, truncated, info = env.step({"name": "submit", "args": {"findings": sub}})
    assert terminated and not truncated
    assert obs["result"] is None and obs["error"]["code"] == "invalid_submission"
    assert reward == (-1.0 if mode == "train" else 0.0)  # closed: the policy always sees 0
    # the evaluator receives exactly what was sent, not a substituted or emptied answer
    assert env.submitted_findings() == sub
    before = len(ja.score_errors())
    score = ja.score_episode(d, env.submitted_findings(), env.inspection_steps())
    assert score == {**{k: None for k in METRICS}, "return": -1.0}
    assert "episode_id" in ja.score_errors()[before]
    # the episode is over: a corrected resubmit changes nothing
    again = env.step({"name": "submit", "args": {"findings": {"episode_id": eid, "findings": findings}}})
    assert again[0]["error"]["code"] == "episode_finished" and env.submitted_findings() == sub


@pytest.mark.parametrize("name", ["clean", "duplicate_invoice"])
@pytest.mark.parametrize("mode", ["closed", "train"])
def test_matching_episode_id_is_graded_normally(episodes, name, mode):
    d, eid, findings = episodes[name]
    env = ja.make_env()
    env.reset(0, {"mode": mode, "episode_dir": d, "team_config": {}})
    env.step({"name": "list_records", "args": {"table": "ledger", "cursor": None, "limit": 50}})
    obs, reward, terminated, _, _ = env.step({"name": "submit", "args": {"findings": {"episode_id": eid, "findings": findings}}})
    assert terminated and obs == {"result": {"accepted": True}, "error": None, "remaining_steps": 98}
    score = ja.score_episode(d, env.submitted_findings(), env.inspection_steps())
    assert score["f1"] == 1.0 and score["return"] == pytest.approx(0.998)
    assert reward == (pytest.approx(0.998) if mode == "train" else 0.0)


def test_foreign_episode_id_on_clean_books_is_not_scored_as_silence(episodes):
    """Regression: the evaluator used to get None for an invalid submit and fall back to empty findings,
    which on clean books is the best possible answer (return 0.998) instead of -1."""
    d, eid, _ = episodes["clean"]
    silence = ja.score_episode(d, {"episode_id": eid, "findings": []}, 1)
    assert silence["return"] > 0
    env = ja.make_env()
    env.reset(0, {"mode": "closed", "episode_dir": d, "team_config": {}})
    env.step({"name": "list_records", "args": {"table": "ledger", "cursor": None, "limit": 50}})
    env.step({"name": "submit", "args": {"findings": {"episode_id": "ep-other", "findings": []}}})
    assert env.submitted_findings() is not None
    assert ja.score_episode(d, env.submitted_findings(), env.inspection_steps())["return"] == -1.0


def test_direct_scoring_rejects_missing_or_foreign_episode_id(episodes):
    d, eid, findings = episodes["duplicate_invoice"]
    for variant in VARIANTS:
        assert ja.score_episode(d, submission(eid, findings, variant), 10)["return"] == -1.0, variant
    assert ja.score_episode(d, {"episode_id": eid, "findings": findings}, 10)["f1"] == 1.0


def test_truncation_still_means_empty_findings(episodes):
    d, _, _ = episodes["duplicate_invoice"]
    env = ja.make_env()
    env.reset(0, {"mode": "closed", "episode_dir": d, "team_config": {}})
    for _ in range(100):
        out = env.step({"name": "calculator", "args": {"expression": "1"}})
    assert out[3] is True and env.submitted_findings() is None
