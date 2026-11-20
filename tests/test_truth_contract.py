"""truth.json contract: generator output, evaluator input (case §03 minimum), closed-mode isolation."""

import copy
import glob
import json
import os
import shutil
from fractions import Fraction

import pytest

import judge_adapter as ja
from auditenv.audit import load_episode
from auditenv.evaluator import Grader
from auditenv.jsonschema_lite import validate_named
from auditenv.suites import JUDGE_MATRIX, exactly
from auditenv.truth import TruthFormatError, normalize_truth

from conftest import episode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECTION_03_FIELDS = {"issue_id", "type", "affected_ids", "expected_amount_minor", "currency", "evidence_sets"}


def generated_episodes():
    for path in sorted(glob.glob(os.path.join(ROOT, "episodes", "*", "seed_*"))):
        yield os.path.relpath(path, ROOT), load_episode(path)
    for k in range(13):
        yield f"exactly({k})", episode(6000 + 100 * k, exactly(k))
    for n, (name, seed) in enumerate(JUDGE_MATRIX, start=1):
        with open(os.path.join(ROOT, "configs", "scenarios", f"{name}.json")) as fh:
            yield f"D{n:02d}", episode(seed, json.load(fh))


@pytest.mark.parametrize("label, ep", list(generated_episodes()), ids=lambda x: x if isinstance(x, str) else "")
def test_generator_writes_the_canonical_truth(label, ep):
    assert validate_named(ep.truth, "truth") == [], label
    conventions = ep.public.policy["issue_conventions"]
    for issue in ep.truth["issues"]:
        assert issue["tolerance_minor"] == conventions[issue["type"]]["tolerance_minor"]
    assert ep.truth["episode_id"] == ep.public.manifest["episode_id"]
    assert normalize_truth(ep.truth, ep.public.manifest["episode_id"]) == ep.truth  # canonical is a fixed point


@pytest.fixture(scope="module")
def section03_fixture(tmp_path_factory):
    """A jury-style fixture: only the §03 fields, no tolerance_minor, no episode_id, bare list."""
    d = str(tmp_path_factory.mktemp("s03") / "C")
    ja.generate_episode(101, {"max_issues": 0}, d)
    data = load_episode(d)
    docs = [x["doc_id"] for x in data.public.invoices]
    rows = [r["row_id"] for r in data.public.ledger]
    for t in ("duplicate_invoice", "fx_conversion"):  # fx: the type whose published tolerance is 1
        issue = {"issue_id": "ISS-A", "type": t, "affected_ids": [docs[0]], "expected_amount_minor": 10000,
                 "currency": "KZT", "evidence_sets": [[rows[0], rows[1]]]}
        assert set(issue) == SECTION_03_FIELDS
        sub = os.path.join(os.path.dirname(d), t)
        shutil.copytree(d, sub)
        with open(os.path.join(sub, "truth.json"), "w") as fh:
            json.dump([issue], fh)
    return {"dir": os.path.dirname(d), "eid": data.public.manifest["episode_id"], "R1": docs[0], "R2": docs[2],
            "D1": rows[0], "D2": rows[1]}


def F(fx, t, **kw):
    out = {"type": t, "affected_ids": [fx["R1"]], "claimed_amount_minor": 10000, "currency": "KZT",
           "evidence_ids": [fx["D1"], fx["D2"]], "explanation": "ok"}
    out.update(kw)
    return out


@pytest.mark.parametrize("t", ["duplicate_invoice", "fx_conversion"])
@pytest.mark.parametrize("case, findings, steps, expected", [
    (1, "correct", 10, (1, 0, 0, Fraction(98, 100))),
    (2, "empty", 10, (0, 0, 1, -1)),
    (5, "twice", 10, (1, 1, 0, Fraction(47, 150))),
    (6, "wrong_type", 10, (0, 1, 1, -1)),
    (7, "other_record", 10, (0, 1, 1, -1)),
    (8, "amount_10001", 10, (0, 1, 1, -1)),
    (9, "other_currency", 10, (0, 1, 1, -1)),
    (10, "only_d1", 10, (0, 1, 1, -1)),
    (11, "unknown_evidence", 10, (0, 1, 1, -1)),
    (15, "correct", 0, (1, 0, 0, 1)),
    (16, "correct", 50, (1, 0, 0, Fraction(90, 100))),
    (17, "correct", 100, (1, 0, 0, Fraction(80, 100))),
])
def test_section03_fixture_scores_like_the_published_block3(section03_fixture, t, case, findings, steps, expected):
    fx = section03_fixture
    other = "payment_allocation" if t != "payment_allocation" else "duplicate_invoice"
    variants = {
        "correct": [F(fx, t)], "empty": [], "twice": [F(fx, t), F(fx, t)], "wrong_type": [F(fx, other)],
        "other_record": [F(fx, t, affected_ids=[fx["R2"]])], "amount_10001": [F(fx, t, claimed_amount_minor=10001)],
        "other_currency": [F(fx, t, currency="USD")], "only_d1": [F(fx, t, evidence_ids=[fx["D1"]])],
        "unknown_evidence": [F(fx, t, evidence_ids=[fx["D1"], "NOT-IN-FIXTURE"])],
    }
    result = ja.score_episode(os.path.join(fx["dir"], t), {"episode_id": fx["eid"], "findings": variants[findings]}, steps)
    assert validate_named(result, "score_result") == []
    assert (result["tp"], result["fp"], result["fn"]) == expected[:3], case
    assert abs(result["return"] - float(expected[3])) <= 1e-9, case


def test_explicit_tolerance_is_respected(section03_fixture):
    fx = section03_fixture
    d = os.path.join(fx["dir"], "fx_conversion")
    with open(os.path.join(d, "truth.json")) as fh:
        issues = json.load(fh)
    issues[0]["tolerance_minor"] = 1
    with open(os.path.join(d, "truth.json"), "w") as fh:
        json.dump(issues, fh)
    try:
        ok = ja.score_episode(d, {"episode_id": fx["eid"], "findings": [F(fx, "fx_conversion", claimed_amount_minor=10001)]}, 10)
        no = ja.score_episode(d, {"episode_id": fx["eid"], "findings": [F(fx, "fx_conversion", claimed_amount_minor=10002)]}, 10)
        assert (ok["tp"], no["tp"]) == (1, 0)  # M08
    finally:
        del issues[0]["tolerance_minor"]
        with open(os.path.join(d, "truth.json"), "w") as fh:
            json.dump(issues, fh)


def _valid_issue():
    return {"issue_id": "I1", "type": "duplicate_invoice", "affected_ids": ["D1"], "expected_amount_minor": 5,
            "currency": "KZT", "evidence_sets": [["A", "B"]]}


@pytest.mark.parametrize("mutate, message", [
    (lambda i: i.pop("evidence_sets"), "evidence_sets"),
    (lambda i: i.pop("expected_amount_minor"), "expected_amount_minor"),
    (lambda i: i.update(expected_amount_minor="5"), "expected_amount_minor"),
    (lambda i: i.update(expected_amount_minor=True), "expected_amount_minor"),
    (lambda i: i.update(affected_ids="D1"), "affected_ids"),
    (lambda i: i.update(tolerance_minor=-1), "tolerance_minor"),
    (lambda i: i.update(evidence_sets=[["X"] * 11]), "evidence_sets"),
])
def test_malformed_truth_is_a_clear_format_error(mutate, message):
    issue = _valid_issue()
    mutate(issue)
    with pytest.raises(TruthFormatError, match=message):
        normalize_truth([issue], "ep-1")


def test_truth_forms_and_identity_rules():
    issue = _valid_issue()
    assert normalize_truth({"issues": [issue]}, "ep-1")["issues"][0]["tolerance_minor"] == 0
    assert normalize_truth({"episode_id": "ep-1", "issues": [issue]})["episode_id"] == "ep-1"
    with pytest.raises(TruthFormatError, match="differs from manifest"):
        normalize_truth({"episode_id": "ep-2", "issues": [issue]}, "ep-1")
    with pytest.raises(TruthFormatError, match="unique"):
        normalize_truth([issue, dict(issue)], "ep-1")
    with pytest.raises(TruthFormatError, match="episode_id is unknown"):
        normalize_truth([issue])
    assert Grader([issue], episode_id="ep-1").grade({"episode_id": "ep-1", "findings": []}, 0).metrics["fn"] == 1


def test_closed_mode_never_opens_truth_or_provenance(tmp_path):
    d = str(tmp_path / "E")
    with open(os.path.join(ROOT, "configs", "scenarios", "duplicate_invoice.json")) as fh:
        ja.generate_episode(102, json.load(fh), d)
    with open(os.path.join(d, "truth.json")) as fh:
        truth = json.load(fh)
    hidden = str(tmp_path / "evaluator_only")
    os.makedirs(hidden)
    for name in ("truth.json", "provenance.json"):
        shutil.move(os.path.join(d, name), os.path.join(hidden, name))
    env = ja.make_env()
    obs, info = env.reset(0, {"mode": "closed", "episode_dir": d, "team_config": {}})
    for table in ("ledger", "invoices", "payments", "fx"):
        env.step({"name": "list_records", "args": {"table": table, "cursor": None, "limit": 50}})
    findings = {"episode_id": truth["episode_id"], "findings": [
        {"type": i["type"], "affected_ids": i["affected_ids"], "claimed_amount_minor": i["expected_amount_minor"],
         "currency": i["currency"], "evidence_ids": i["evidence_sets"][0], "explanation": ""} for i in truth["issues"]]}
    obs, reward, terminated, _, _ = env.step({"name": "submit", "args": {"findings": findings}})
    assert terminated and reward == 0.0 and obs["result"] == {"accepted": True}
    with pytest.raises(FileNotFoundError):
        make = ja.make_env()
        make.reset(0, {"mode": "train", "episode_dir": d, "team_config": {}})
    for name in ("truth.json", "provenance.json"):
        shutil.move(os.path.join(hidden, name), os.path.join(d, name))
    score = ja.score_episode(d, env.submitted_findings(), env.inspection_steps())
    assert score["tp"] == len(truth["issues"]) and score["return"] == pytest.approx(1 - 0.002 * 4)
