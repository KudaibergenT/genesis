"""Machine-readable schemas (src/auditenv/schemas, linked as ./schemas) and conformance of all outputs."""

import glob
import json
import os

import pytest

import judge_adapter as ja
from auditenv.audit import load_episode
from auditenv.evaluator.schema import validate_submission
from auditenv.jsonschema_lite import SCHEMA_DIR, load, validate, validate_named
from auditenv.suites import JUDGE_MATRIX, exactly

from conftest import episode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMAS = sorted(os.path.basename(p)[: -len(".schema.json")] for p in glob.glob(os.path.join(SCHEMA_DIR, "*.schema.json")))


def test_schema_set_is_complete_and_linked_at_the_root():
    assert set(SCHEMAS) >= {"manifest", "policy", "ledger_row", "invoice", "payment", "fx_row", "truth", "truth_input",
                            "provenance", "findings", "adapter_action", "adapter_first_observation",
                            "adapter_observation", "score_result"}
    assert os.path.realpath(os.path.join(ROOT, "schemas")) == os.path.realpath(SCHEMA_DIR)


@pytest.mark.parametrize("name", SCHEMAS)
def test_every_schema_uses_only_supported_keywords(name):
    validate({}, load(name))  # raises on unsupported keywords


def test_validator_semantics():
    s = {"type": "object", "required": ["a"], "additionalProperties": False,
         "properties": {"a": {"type": "integer", "minimum": 0}, "b": {"type": ["string", "null"], "pattern": "^x"},
                        "c": {"type": "array", "maxItems": 2, "items": {"enum": [1, 2]}}, "d": {"type": "string", "maxLength": 3}}}
    assert validate({"a": 1, "b": "xy", "c": [1, 2], "d": "жжж"}, s) == []
    assert validate({"a": 1, "b": None}, s) == []
    for bad in ({}, {"a": True}, {"a": 1.0}, {"a": -1}, {"a": 1, "b": "y"}, {"a": 1, "c": [1, 2, 1]}, {"a": 1, "c": [3]},
                {"a": 1, "e": 0}, {"a": 1, "d": "жжжж"}, []):
        assert validate(bad, s), bad
    with pytest.raises(ValueError):
        validate(1, {"type": "integer", "multipleOf": 2})


def episodes_to_check():
    for path in sorted(glob.glob(os.path.join(ROOT, "episodes", "*", "seed_*"))):
        yield os.path.relpath(path, ROOT), load_episode(path)
    for k in range(13):
        yield f"exactly({k})", episode(6000 + 100 * k, exactly(k))
    for n, (name, seed) in enumerate(JUDGE_MATRIX, start=1):
        with open(os.path.join(ROOT, "configs", "scenarios", f"{name}.json")) as fh:
            yield f"D{n:02d}", episode(seed, json.load(fh))


@pytest.mark.parametrize("label, ep", list(episodes_to_check()), ids=lambda x: x if isinstance(x, str) else "")
def test_episode_files_conform(label, ep):
    problems = []
    problems += validate_named(ep.public.manifest, "manifest")
    problems += validate_named(ep.public.policy, "policy")
    problems += validate_named(ep.truth, "truth")
    problems += validate_named(ep.provenance, "provenance")
    for name, rows in (("ledger_row", ep.public.ledger), ("invoice", ep.public.invoices),
                       ("payment", ep.public.payments), ("fx_row", ep.public.fx)):
        for row in rows:
            problems += validate_named(row, name)
    assert problems == [], (label, problems[:5])


def test_adapter_exchange_conforms(tmp_path):
    d = str(tmp_path / "E")
    with open(os.path.join(ROOT, "configs", "scenarios", "interaction_allocation_with_fx.json")) as fh:
        ja.generate_episode(219, json.load(fh), d)
    env = ja.make_env()
    obs, info = env.reset(0, {"mode": "closed", "episode_dir": d, "team_config": {}})
    assert validate_named(obs, "adapter_first_observation") == []
    first = env.step({"name": "list_records", "args": {"table": "ledger", "cursor": None, "limit": 50}})[0]
    row_id = first["result"]["records"][0]["row_id"]
    doc_id = env.step({"name": "list_records", "args": {"table": "invoices", "cursor": None, "limit": 5}})[0]["result"]["records"][0]["doc_id"]
    actions = [
        {"name": "list_records", "args": {"table": "fx", "cursor": None, "limit": 3}},
        {"name": "get_record", "args": {"table": "ledger", "id": row_id}},
        {"name": "get_document", "args": {"doc_id": doc_id}},
        {"name": "calculator", "args": {"expression": "0.1+0.2"}},
        {"name": "calculator", "args": {"expression": "open('x')"}},
        {"name": "get_record", "args": {"table": "ledger", "id": "NOPE"}},
        {"name": "unknown", "args": {}},
    ]
    for action in actions:
        assert validate_named(action, "adapter_action") == [] or action["name"] == "unknown"
        assert validate_named(env.step(action)[0], "adapter_observation") == []
    with open(os.path.join(d, "truth.json")) as fh:
        truth = json.load(fh)
    findings = {"episode_id": truth["episode_id"], "findings": [
        {"type": i["type"], "affected_ids": i["affected_ids"], "claimed_amount_minor": i["expected_amount_minor"],
         "currency": i["currency"], "evidence_ids": i["evidence_sets"][0], "explanation": ""} for i in truth["issues"]]}
    assert validate_named(findings, "findings") == []
    done = env.step({"name": "submit", "args": {"findings": findings}})[0]
    assert validate_named(done, "adapter_observation") == [] and done["result"] == {"accepted": True}
    assert validate_named(env.step({"name": "calculator", "args": {"expression": "1"}})[0], "adapter_observation") == []
    for result in (ja.score_episode(d, env.submitted_findings(), env.inspection_steps()),
                   ja.score_episode(d, {"episode_id": truth["episode_id"], "findings": [{}]}, 3)):
        assert validate_named(result, "score_result") == []


def _finding(**kw):
    out = {"type": "duplicate_invoice", "affected_ids": ["D1"], "claimed_amount_minor": 1, "currency": "KZT",
           "evidence_ids": ["L1"], "explanation": ""}
    out.update(kw)
    return out


@pytest.mark.parametrize("findings, structurally_valid", [
    ([_finding()], True),
    ([_finding(type="fraud")], True),
    ([_finding(currency="kzt")], True),
    ([_finding(claimed_amount_minor=-3)], True),
    ([_finding(affected_ids=[])], True),
    ([dict(_finding(), confidence=0.5)], True),
    ([_finding(explanation="ж" * 1000)], True),
    ([_finding(claimed_amount_minor=True)], False),
    ([_finding(claimed_amount_minor=1.0)], False),
    ([_finding(claimed_amount_minor="1")], False),
    ([_finding(evidence_ids=["L"] * 11)], False),
    ([_finding(explanation="ж" * 1001)], False),
    ([_finding(affected_ids="D1")], False),
    ([{k: v for k, v in _finding().items() if k != "currency"}], False),
    ([_finding()] * 31, False),
])
def test_findings_schema_agrees_with_the_matcher_structure_rules(findings, structurally_valid):
    submission = {"episode_id": "ep-1", "findings": findings}
    assert (validate_named(submission, "findings") == []) is structurally_valid
    assert validate_submission(submission, "ep-1")[0] is structurally_valid
