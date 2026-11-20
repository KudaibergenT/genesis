"""The published jury checks (genesis-fintech-checks.md, 2026-11-20), run through judge_adapter.py."""

import copy
import csv
import io
import json
import os
import shutil
import time
from fractions import Fraction

import pytest

import judge_adapter as ja
from auditenv import checks
from auditenv.audit import load_episode, pre_injection_books
from auditenv.books import FX_COLUMNS, LEDGER_COLUMNS, fx_key
from auditenv.rng import Rng

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCENARIOS = os.path.join(ROOT, "configs", "scenarios")


def scenario(name):
    with open(os.path.join(SCENARIOS, f"{name}.json"), encoding="utf-8") as fh:
        return json.load(fh)


def read_dir(d):
    def text(name):
        with open(os.path.join(d, name), encoding="utf-8") as fh:
            return fh.read()
    ledger = list(csv.DictReader(io.StringIO(text("ledger.csv"))))
    for r in ledger:
        r["debit_minor"], r["credit_minor"] = int(r["debit_minor"]), int(r["credit_minor"])
        for k in ("entity_id", "invoice_id", "payment_id"):
            r[k] = r[k] or None
    return {
        "manifest": json.loads(text("manifest.json")), "policy": json.loads(text("policy.json")),
        "ledger": ledger, "invoices": [json.loads(x) for x in text("invoices.jsonl").splitlines()],
        "payments": [json.loads(x) for x in text("payments.jsonl").splitlines()],
        "fx": list(csv.DictReader(io.StringIO(text("fx.csv")))), "truth": json.loads(text("truth.json")),
    }


def write_dir(d, data):
    os.makedirs(d, exist_ok=True)
    for name, obj in (("manifest.json", data["manifest"]), ("policy.json", data["policy"]), ("truth.json", data["truth"])):
        with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
    for name, rows, cols in (("ledger.csv", data["ledger"], LEDGER_COLUMNS), ("fx.csv", data["fx"], FX_COLUMNS)):
        with open(os.path.join(d, name), "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(cols)
            for r in rows:
                w.writerow(["" if r[c] is None else r[c] for c in cols])
    for name in ("invoices", "payments"):
        with open(os.path.join(d, f"{name}.jsonl"), "w", encoding="utf-8") as fh:
            fh.write("".join(json.dumps(r) + "\n" for r in data[name]))


def oracle_findings(truth):
    return {"episode_id": truth["episode_id"], "findings": [
        {"type": i["type"], "affected_ids": list(i["affected_ids"]), "claimed_amount_minor": i["expected_amount_minor"],
         "currency": i["currency"], "evidence_ids": list(i["evidence_sets"][0]), "explanation": "oracle"}
        for i in truth["issues"]]}


def closed_env(d):
    env = ja.make_env()
    obs, info = env.reset(0, {"mode": "closed", "episode_dir": d, "team_config": {}})
    return env, obs, info


def lst(table, cursor=None, limit=50):
    return {"name": "list_records", "args": {"table": table, "cursor": cursor, "limit": limit}}


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory):
    base = tmp_path_factory.mktemp("judge")
    c, e = str(base / "C"), str(base / "E")
    ja.generate_episode(101, scenario("clean"), c)
    ja.generate_episode(102, scenario("duplicate_invoice"), e)
    data = read_dir(c)
    known = {r["row_id"] for r in data["ledger"]} | {d["doc_id"] for d in data["invoices"] + data["payments"]}
    return {"C": c, "E": e, "L": c, "R1": data["ledger"][7]["row_id"], "D1": data["invoices"][3]["doc_id"],
            "U": next(f"Z{n:09d}" for n in range(10 ** 6) if f"Z{n:09d}" not in known), "base": base}


# ---------------------------------------------------------------------------- block 1: contract C01-C20
def test_c01_reset_returns_json_pair(fixtures):
    env = ja.make_env()
    out = env.reset(0, {"mode": "closed", "episode_dir": fixtures["C"], "team_config": {}})
    assert isinstance(out, tuple) and len(out) == 2
    json.dumps(out[0]), json.dumps(out[1])


def test_c02_first_observation(fixtures):
    _, obs, info = closed_env(fixtures["E"])
    assert set(obs) == {"task", "manifest", "policy", "remaining_steps"} and obs["remaining_steps"] == 100
    text = json.dumps([obs, info])
    truth = read_dir(fixtures["E"])["truth"]
    assert "seed" not in text and not any(i["issue_id"] in text for i in truth["issues"])


def test_c03_manifest(fixtures):
    data = read_dir(fixtures["C"])
    m = data["manifest"]
    for key in ("schema_version", "episode_id", "base_currency", "period_start", "period_end", "timezone", "rounding"):
        assert key in m
    assert m["counts"]["ledger_rows"] == len(data["ledger"]) and m["counts"]["invoices"] == len(data["invoices"])
    assert m["counts"]["payments"] == len(data["payments"]) and m["counts"]["fx_rates"] == len(data["fx"])
    assert m["counts"]["documents"] == len(data["invoices"]) + len(data["payments"])


def test_c04_list_records_step_tuple(fixtures):
    env, _, _ = closed_env(fixtures["C"])
    obs, reward, terminated, truncated, info = env.step(lst("ledger"))
    assert type(terminated) is bool and type(truncated) is bool and isinstance(info, dict)
    json.dumps([obs, reward, info])


def test_c05_pagination_three_pages_complete(fixtures):
    env, _, _ = closed_env(fixtures["L"])
    rows, cursor, pages = [], None, 0
    while True:
        result = env.step(lst("ledger", cursor))[0]["result"]
        assert len(result["records"]) <= 50
        rows += result["records"]
        pages += 1
        cursor = result["next_cursor"]
        if cursor is None:
            break
    assert pages >= 3 and rows == read_dir(fixtures["L"])["ledger"]


def test_c06_same_cursor_same_page(fixtures):
    env, _, _ = closed_env(fixtures["L"])
    cursor = env.step(lst("ledger"))[0]["result"]["next_cursor"]
    a, b = env.step(lst("ledger", cursor))[0], env.step(lst("ledger", cursor))[0]
    assert a["result"] == b["result"] and a["remaining_steps"] == b["remaining_steps"] + 1


def test_c07_get_record(fixtures):
    env, _, _ = closed_env(fixtures["C"])
    row = next(r for r in read_dir(fixtures["C"])["ledger"] if r["row_id"] == fixtures["R1"])
    assert env.step({"name": "get_record", "args": {"table": "ledger", "id": fixtures["R1"]}})[0]["result"] == row


def test_c08_get_document(fixtures):
    env, _, _ = closed_env(fixtures["C"])
    doc = next(d for d in read_dir(fixtures["C"])["invoices"] if d["doc_id"] == fixtures["D1"])
    assert env.step({"name": "get_document", "args": {"doc_id": fixtures["D1"]}})[0]["result"] == doc


def test_c09_calculator_decimal(fixtures):
    env, _, _ = closed_env(fixtures["C"])
    assert env.step({"name": "calculator", "args": {"expression": "0.1+0.2"}})[0]["result"] == "0.3"


def test_c10_calculator_refuses_code(fixtures):
    env, _, _ = closed_env(fixtures["C"])
    obs = env.step({"name": "calculator", "args": {"expression": "open('/etc/passwd').read()"}})[0]
    assert obs["result"] is None and obs["error"]["code"] == "calculator_error"


@pytest.mark.parametrize("action", ["get_record", "get_document"])
def test_c11_c12_unknown_id(fixtures, action):
    env, _, _ = closed_env(fixtures["C"])
    args = {"table": "ledger", "id": fixtures["U"]} if action == "get_record" else {"doc_id": fixtures["U"]}
    obs = env.step({"name": action, "args": args})[0]
    assert obs["result"] is None and obs["error"]["code"] == "not_found" and obs["remaining_steps"] == 99
    assert env.step(lst("ledger", limit=1))[0]["result"]["records"]


def test_c13_unknown_action(fixtures):
    env, _, _ = closed_env(fixtures["C"])
    obs = env.step({"name": "run_shell", "args": {}})[0]
    assert obs["error"] and obs["remaining_steps"] == 99


def test_c14_errors_cost_a_step_and_do_not_break_access(fixtures):
    env, _, _ = closed_env(fixtures["C"])
    a = env.step({"name": "get_record", "args": {"table": "ledger", "id": fixtures["R1"]}})[0]
    b = env.step({"name": "get_record", "args": {"table": "ledger", "id": fixtures["U"]}})[0]
    c = env.step({"name": "get_record", "args": {"table": "ledger", "id": fixtures["R1"]}})[0]
    assert (a["remaining_steps"], b["remaining_steps"], c["remaining_steps"]) == (99, 98, 97)
    assert a["result"] == c["result"] and b["error"]


def test_c15_submit_is_final(fixtures):
    env, _, _ = closed_env(fixtures["E"])
    truth = read_dir(fixtures["E"])["truth"]
    obs, reward, terminated, truncated, _ = env.step({"name": "submit", "args": {"findings": oracle_findings(truth)}})
    assert terminated and not truncated and obs["result"] == {"accepted": True}
    before = (env.submitted_findings(), env.inspection_steps())
    score = ja.score_episode(fixtures["E"], before[0], before[1])
    for action in ({"name": "submit", "args": {"findings": {"episode_id": truth["episode_id"], "findings": []}}}, lst("ledger")):
        assert env.step(action)[0]["error"]["code"] == "episode_finished"
    assert (env.submitted_findings(), env.inspection_steps()) == before
    assert ja.score_episode(fixtures["E"], *before) == score


def test_c16_submit_as_action_100(fixtures):
    env, _, _ = closed_env(fixtures["C"])
    for _ in range(99):
        assert not env.step({"name": "calculator", "args": {"expression": "1"}})[2]
    obs, _, terminated, truncated, _ = env.step({"name": "submit", "args": {"findings": {
        "episode_id": read_dir(fixtures["C"])["manifest"]["episode_id"], "findings": []}}})
    assert terminated and not truncated and obs["result"] == {"accepted": True} and env.inspection_steps() == 99


def test_c17_hundred_actions_truncate(fixtures):
    env, _, _ = closed_env(fixtures["C"])
    for _ in range(100):
        out = env.step({"name": "calculator", "args": {"expression": "1"}})
    assert out[3] is True and out[2] is False and env.submitted_findings() is None
    assert env.step({"name": "submit", "args": {"findings": {}}})[0]["error"]["code"] == "episode_finished"
    assert env.submitted_findings() is None


def test_c18_time_limit_truncates_with_empty_findings(fixtures, monkeypatch):
    monkeypatch.setattr(ja, "TIME_LIMIT_S", 1.0)
    env, _, _ = closed_env(fixtures["C"])
    env.step(lst("ledger"))
    time.sleep(1.2)
    obs, _, terminated, truncated, _ = env.step(lst("ledger"))
    assert truncated and not terminated and obs["error"]["code"] == "timeout" and env.submitted_findings() is None


def test_c19_closed_mode_reward_is_zero_and_hidden(fixtures):
    env, _, _ = closed_env(fixtures["E"])
    truth = read_dir(fixtures["E"])["truth"]
    rewards, infos, observations = [], [], []
    for action in (lst("invoices"), lst("ledger"), {"name": "submit", "args": {"findings": oracle_findings(truth)}}):
        obs, reward, _, _, info = env.step(action)
        rewards.append(reward), infos.append(info), observations.append(obs)
    assert rewards == [0.0, 0.0, 0.0] and all(type(r) is float for r in rewards)
    text = json.dumps([infos, observations[-1]])
    for key in ("tp", "fp", "fn", "f1", "precision", "recall", "return", "issue_id"):
        assert f'"{key}"' not in text
    score = ja.score_episode(fixtures["E"], env.submitted_findings(), env.inspection_steps())
    assert score["tp"] == 1 and score["return"] == pytest.approx(1 - 0.002 * 2)


def test_c20_nothing_closed_reaches_the_policy(fixtures):
    for name in ("C", "E"):
        d = fixtures[name]
        env, first, info = closed_env(d)
        seen = [first, info]
        for table in ("ledger", "invoices", "payments", "fx"):
            cursor = None
            while True:
                obs, _, _, _, step_info = env.step(lst(table, cursor))
                seen += [obs, step_info]
                cursor = obs["result"]["next_cursor"]
                if cursor is None:
                    break
        for action in ({"name": "get_document", "args": {"doc_id": "../truth.json"}},
                       {"name": "get_record", "args": {"table": "truth", "id": "x"}},
                       {"name": "shell", "args": {"cmd": "ls"}}, {"name": "sql", "args": {"q": "select 1"}},
                       {"name": "calculator", "args": {"expression": "__import__('socket')"}}):
            obs, _, _, _, step_info = env.step(action)
            assert obs["error"] is not None
            seen += [obs, step_info]
        text = json.dumps(seen)
        truth = read_dir(d)["truth"]
        assert not any(i["issue_id"] in text for i in truth["issues"])
        for forbidden in ('"seed"', "expected_amount_minor", "evidence_sets", "provenance", d, "truth.json\""):
            assert forbidden not in text


# ---------------------------------------------------------------------------- block 3: matcher and reward
def make_fixture(fixtures, name, issues):
    src, dst = fixtures["C"], os.path.join(str(fixtures["base"]), name)
    if not os.path.exists(dst):
        shutil.copytree(src, dst)
    data = read_dir(dst)
    data["truth"] = {"schema_version": "1.0", "episode_id": data["manifest"]["episode_id"], "issues": issues}
    write_dir(dst, data)
    return dst, data


def ids(fixtures):
    data = read_dir(fixtures["C"])
    docs = [d["doc_id"] for d in data["invoices"]]
    rows = [r["row_id"] for r in data["ledger"]]
    return {"R": docs[:5], "D1": rows[0], "D2": rows[1], "D3": rows[2], "eid": data["manifest"]["episode_id"]}


def issue(issue_id, ids_, affected, evidence=None, tol=0, t="duplicate_invoice"):
    return {"issue_id": issue_id, "type": t, "affected_ids": sorted(affected), "expected_amount_minor": 10000,
            "currency": "KZT", "tolerance_minor": tol, "evidence_sets": [sorted(evidence or [ids_["D1"], ids_["D2"]])]}


def f(ids_, affected, **kw):
    out = {"type": "duplicate_invoice", "affected_ids": affected, "claimed_amount_minor": 10000, "currency": "KZT",
           "evidence_ids": [ids_["D1"], ids_["D2"]], "explanation": "ok"}
    out.update(kw)
    return out


def score(d, ids_, findings, steps=10):
    return ja.score_episode(d, {"episode_id": ids_["eid"], "findings": findings}, steps)


def expect(result, tp, fp, fn, p, r, f1, ret):
    assert (result["tp"], result["fp"], result["fn"]) == (tp, fp, fn)
    for key, value in (("precision", p), ("recall", r), ("f1", f1), ("return", ret)):
        assert abs(result[key] - float(value)) <= 1e-9, (key, result[key], value)


@pytest.fixture(scope="module")
def one_issue(fixtures):
    i = ids(fixtures)
    d, _ = make_fixture(fixtures, "A", [issue("ISS-A", i, [i["R"][0]])])
    return d, i


@pytest.fixture(scope="module")
def no_issue(fixtures):
    i = ids(fixtures)
    d, _ = make_fixture(fixtures, "EMPTY", [])
    return d, i


@pytest.fixture(scope="module")
def two_issues(fixtures):
    i = ids(fixtures)
    d, _ = make_fixture(fixtures, "AB", [issue("ISS-A", i, [i["R"][0]]), issue("ISS-B", i, [i["R"][1]])])
    return d, i


def test_m1_correct(one_issue):
    d, i = one_issue
    expect(score(d, i, [f(i, [i["R"][0]])]), 1, 0, 0, 1, 1, 1, Fraction(98, 100))


def test_m2_empty_on_error(one_issue):
    d, i = one_issue
    expect(score(d, i, []), 0, 0, 1, 0, 0, 0, -1)


def test_m3_empty_on_clean(no_issue):
    d, i = no_issue
    expect(score(d, i, []), 0, 0, 0, 0, 0, 1, Fraction(98, 100))


def test_m4_false_finding_on_clean(no_issue):
    d, i = no_issue
    expect(score(d, i, [f(i, [i["R"][0]])]), 0, 1, 0, 0, 0, 0, -1)


def test_m5_duplicate_findings(one_issue):
    d, i = one_issue
    expect(score(d, i, [f(i, [i["R"][0]])] * 2), 1, 1, 0, Fraction(1, 2), 1, Fraction(2, 3), Fraction(47, 150))


@pytest.mark.parametrize("mutation", [
    {"type": "fx_conversion"}, {"affected_ids": "OTHER"}, {"claimed_amount_minor": 10001}, {"currency": "USD"},
    {"evidence_ids": "ONLY_D1"}, {"evidence_ids": "U_INSTEAD_OF_D2"},
])
def test_m6_to_m11_single_wrong_field(one_issue, mutation):
    d, i = one_issue
    kw = dict(mutation)
    if kw.get("affected_ids") == "OTHER":
        kw["affected_ids"] = [i["R"][2]]
    if kw.get("evidence_ids") == "ONLY_D1":
        kw["evidence_ids"] = [i["D1"]]
    if kw.get("evidence_ids") == "U_INSTEAD_OF_D2":
        kw["evidence_ids"] = [i["D1"], "UNKNOWN-ID-0001"]
    expect(score(d, i, [f(i, [i["R"][0]], **kw)]), 0, 1, 1, 0, 0, 0, -1)


def test_m12_mark_everything(one_issue):
    d, i = one_issue
    findings = [f(i, [r], type=t) for r in i["R"] for t in ("duplicate_invoice", "payment_allocation", "fx_conversion", "period_cutoff")]
    assert len(findings) == 20
    expect(score(d, i, findings), 1, 19, 0, Fraction(1, 20), 1, Fraction(2, 21), Fraction(-871, 1050))


def test_m13_m14_two_issues(two_issues):
    d, i = two_issues
    expect(score(d, i, [f(i, [i["R"][0]])]), 1, 0, 1, 1, Fraction(1, 2), Fraction(2, 3), Fraction(47, 150))
    expect(score(d, i, [f(i, [i["R"][0]]), f(i, [i["R"][3]])]), 1, 1, 1, Fraction(1, 2), Fraction(1, 2), Fraction(1, 2),
           Fraction(-2, 100))


@pytest.mark.parametrize("steps, reward", [(0, 1), (50, Fraction(90, 100)), (100, Fraction(80, 100))])
def test_m15_to_m17_step_cost(one_issue, steps, reward):
    d, i = one_issue
    expect(score(d, i, [f(i, [i["R"][0]])], steps), 1, 0, 0, 1, 1, 1, reward)


@pytest.mark.parametrize("bad", ["31_findings", "11_evidence", "1001_chars"])
def test_m18_to_m20_structural_limits(one_issue, bad):
    d, i = one_issue
    if bad == "31_findings":
        findings = [f(i, [i["R"][0]])] * 31
    elif bad == "11_evidence":
        findings = [f(i, [i["R"][0]], evidence_ids=[i["D1"], i["D2"]] + [f"X{n}" for n in range(9)])]
    else:
        findings = [f(i, [i["R"][0]], explanation="a" * 1001)]
    result = score(d, i, findings)
    assert result == {"tp": None, "fp": None, "fn": None, "precision": None, "recall": None, "f1": None, "return": -1.0}
    assert ja.score_errors()


# ---------------------------------------------------------------------------- open examples M01-M12
@pytest.fixture(scope="module")
def pair_issue(fixtures):
    i = ids(fixtures)
    a = issue("ISS-A", i, [i["R"][0], i["R"][1]])
    d, _ = make_fixture(fixtures, "M", [a])
    return d, i


def test_examples_m01_to_m09(pair_issue, fixtures):
    d, i = pair_issue
    r1, r2, r3 = i["R"][:3]
    assert score(d, i, [f(i, [r2, r1, r1])])["tp"] == 1                                        # M01
    expect(score(d, i, [f(i, [r1, r2, r3])]), 0, 1, 1, 0, 0, 0, -1)                             # M02
    assert score(d, i, [f(i, [r1, r2], evidence_ids=[i["D2"], i["D1"], i["D3"]])])["tp"] == 1    # M03
    expect(score(d, i, [f(i, [r1, r2], evidence_ids=[i["D1"], i["D2"], "UNKNOWN"])]), 0, 1, 1, 0, 0, 0, -1)  # M04
    assert score(d, i, [f(i, [r1, r2], evidence_ids=[i["D2"]] + [i["D1"]] * 10)])["return"] == -1.0       # M05
    for cur in ("kzt", "KZT ", " KZT"):                                                          # M06
        expect(score(d, i, [f(i, [r1, r2], currency=cur)]), 0, 1, 1, 0, 0, 0, -1)
    expect(score(d, i, [f(i, [r1, r2], claimed_amount_minor=10001)]), 0, 1, 1, 0, 0, 0, -1)      # M07
    for bad in ("10000", True):                                                                  # M09
        assert score(d, i, [f(i, [r1, r2], claimed_amount_minor=bad)])["return"] == -1.0


def test_example_m08_tolerance_one(fixtures):
    i = ids(fixtures)
    d, _ = make_fixture(fixtures, "M08", [issue("ISS-A", i, [i["R"][0], i["R"][1]], tol=1)])
    assert score(d, i, [f(i, [i["R"][0], i["R"][1]], claimed_amount_minor=10001)])["tp"] == 1
    assert score(d, i, [f(i, [i["R"][0], i["R"][1]], claimed_amount_minor=10002)])["tp"] == 0


def test_example_m10_maximum_matching_beats_greedy(fixtures):
    i = ids(fixtures)
    aff = [i["R"][0]]
    d, _ = make_fixture(fixtures, "M10", [issue("ISS-A", i, aff, evidence=[i["D1"]]),
                                          issue("ISS-B", i, aff, evidence=[i["D2"]])])
    f0 = f(i, aff, evidence_ids=[i["D1"], i["D2"]])   # fits A and B
    f1 = f(i, aff, evidence_ids=[i["D1"]])            # fits A only
    expect(score(d, i, [f0, f1]), 2, 0, 0, 1, 1, 1, Fraction(98, 100))
    from auditenv.evaluator.matcher import match
    truth = read_dir(d)["truth"]["issues"]
    pairs = {r["issue_id"]: r["index"] for r in match([f0, f1], truth).findings if r["status"] == "TP"}
    assert pairs == {"ISS-A": 1, "ISS-B": 0}


def test_example_m11_deterministic_tie_break(fixtures):
    i = ids(fixtures)
    aff = [i["R"][0]]
    d, _ = make_fixture(fixtures, "M11", [issue("ISS-B", i, aff), issue("ISS-A", i, aff)])
    from auditenv.evaluator.matcher import match
    truth = read_dir(d)["truth"]["issues"]
    f0, f1 = f(i, aff, explanation="0"), f(i, aff, explanation="1")
    pairs = {r["issue_id"]: r["index"] for r in match([f0, f1], truth).findings if r["status"] == "TP"}
    assert pairs == {"ISS-A": 0, "ISS-B": 1}
    assert score(d, i, [f1, f0]) == score(d, i, [f0, f1])


def test_example_m12_identical_findings(one_issue):
    d, i = one_issue
    expect(score(d, i, [f(i, [i["R"][0]])] * 2), 1, 1, 0, Fraction(1, 2), 1, Fraction(2, 3), Fraction(47, 150))


# ---------------------------------------------------------------------------- block 2: matrix D01-D20
MATRIX = (
    [("clean", s) for s in (201, 202, 203, 204, 205)]
    + [("duplicate_invoice", s) for s in (206, 207, 208)]
    + [("payment_allocation_missing", 209), ("payment_allocation_partial", 210), ("payment_allocation_partial", 211)]
    + [("fx_conversion", s) for s in (212, 213)]
    + [("period_cutoff", s) for s in (214, 215)]
    + [("interaction_fx_partial_at_cutoff", s) for s in (216, 217, 218)]
    + [("interaction_allocation_with_fx", s) for s in (219, 220)]
)
EXPECTED_TYPES = {
    "clean": [], "duplicate_invoice": ["duplicate_invoice"], "payment_allocation_missing": ["payment_allocation"],
    "payment_allocation_partial": ["payment_allocation"], "fx_conversion": ["fx_conversion"],
    "period_cutoff": ["period_cutoff"], "interaction_fx_partial_at_cutoff": ["fx_conversion", "period_cutoff"],
    "interaction_allocation_with_fx": ["fx_conversion", "payment_allocation"],
}


@pytest.fixture(scope="module")
def matrix(tmp_path_factory):
    base = tmp_path_factory.mktemp("matrix")
    out = []
    for n, (name, seed) in enumerate(MATRIX, start=1):
        d = str(base / f"D{n:02d}")
        ja.generate_episode(seed, scenario(name), d)
        out.append((f"D{n:02d}", name, d))
    return out


@pytest.mark.parametrize("index", range(20))
def test_matrix_episode_is_correct(matrix, index):
    label, name, d = matrix[index]
    ep = load_episode(d)
    assert sorted(i["type"] for i in ep.truth["issues"]) == EXPECTED_TYPES[name], label
    assert checks.verify_episode(ep.public, ep.truth) == []
    before = pre_injection_books(ep.public, ep.provenance["changes"])
    assert checks.clean_invariants(before) == [] and checks.detect_issues(before) == []
    if name == "payment_allocation_missing":
        assert ep.provenance["mutations"][0]["variant"] == "missing"
    if name == "payment_allocation_partial":
        assert ep.provenance["mutations"][0]["variant"] == "partial"


@pytest.mark.parametrize("index", range(5))
def test_matrix_clean_companies_contain_the_named_look_alikes(matrix, index):
    _, _, d = matrix[index]
    data = read_dir(d)
    ix = checks.Index(load_episode(d).public)
    refs = {}
    for p in data["payments"]:
        for r in p["invoice_refs"] or []:
            refs[r["invoice_id"]] = refs.get(r["invoice_id"], 0) + 1
    assert any(n > 1 for n in refs.values())                                                    # D02 split payment
    assert any(p["amount_minor"] < 0 for p in data["payments"])                                  # D03 refund
    amounts = [(d_["entity_id"], d_["total_minor"]) for d_ in data["invoices"]]
    assert len(amounts) > len(set(amounts))                                                      # D04 repeated amounts
    if data["manifest"]["currency_exponents"].keys() - {data["manifest"]["base_currency"]}:      # D05 boundary
        assert any(doc["currency"] != ix.base and ix.local_date(doc["issued_at"]) == ix.period[1]
                   and timeutil_hour(doc["issued_at"], ix.zone) >= 19 for doc in data["invoices"])


def timeutil_hour(ts, zone):
    from auditenv import timeutil
    return timeutil.parse_iso(ts).astimezone(timeutil.tzinfo_for(zone)).hour


# ---------------------------------------------------------------------------- block 4: paired checks P01-P20
def transform(src, dst, permute, rename, seed):
    data = read_dir(src)
    rng = Rng("jury-transform", seed)
    if rename:
        spaces = {"row": set(), "doc": set(), "inv": set(), "pay": set(), "ent": set()}
        for r in data["ledger"]:
            spaces["row"].add(r["row_id"])
            for k, s in (("entity_id", "ent"), ("invoice_id", "inv"), ("payment_id", "pay")):
                if r[k]:
                    spaces[s].add(r[k])
        for d_ in data["invoices"]:
            spaces["doc"].add(d_["doc_id"]), spaces["inv"].add(d_["invoice_id"]), spaces["ent"].add(d_["entity_id"])
        for p in data["payments"]:
            spaces["doc"].add(p["doc_id"]), spaces["pay"].add(p["payment_id"])
        maps = {}
        for space, values in spaces.items():
            fresh = [f"{space.upper()}-{n:06d}" for n in range(len(values))]
            rng.shuffle(fresh)
            maps[space] = dict(zip(sorted(values), fresh))
        for r in data["ledger"]:
            r["row_id"] = maps["row"][r["row_id"]]
            for k, s in (("entity_id", "ent"), ("invoice_id", "inv"), ("payment_id", "pay")):
                r[k] = maps[s][r[k]] if r[k] else None
        for d_ in data["invoices"]:
            d_["doc_id"], d_["invoice_id"], d_["entity_id"] = maps["doc"][d_["doc_id"]], maps["inv"][d_["invoice_id"]], maps["ent"][d_["entity_id"]]
        for p in data["payments"]:
            p["doc_id"], p["payment_id"] = maps["doc"][p["doc_id"]], maps["pay"][p["payment_id"]]
            for ref in p["invoice_refs"] or []:
                ref["invoice_id"] = maps["inv"][ref["invoice_id"]]
        any_id = {**maps["row"], **maps["doc"]}
        for i in data["truth"]["issues"]:
            i["affected_ids"] = sorted(any_id.get(x, x) for x in i["affected_ids"])
            i["evidence_sets"] = [sorted(any_id.get(x, x) for x in s) for s in i["evidence_sets"]]
        inverse = {v: k for k, v in any_id.items()}
    else:
        inverse = {}
    if permute:
        for table in ("ledger", "invoices", "payments", "fx"):
            rng.shuffle(data[table])
    write_dir(dst, data)
    return inverse


PAIRS = [(n, n <= 5 or n > 10, n > 5) for n in range(1, 21)]  # (index, permute, rename)


@pytest.mark.parametrize("n, permute, rename", PAIRS)
def test_paired_check(matrix, tmp_path, n, permute, rename):
    _, _, src = matrix[n - 1]
    dst = str(tmp_path / f"P{n:02d}")
    inverse = transform(src, dst, permute, rename, seed=n)

    def run(d):
        env, first, _ = closed_env(d)
        transcript = [first]
        for action in (lst("ledger"), lst("invoices"), {"name": "calculator", "args": {"expression": "2/3"}}):
            transcript.append(env.step(action))
        truth = read_dir(d)["truth"]
        transcript.append(env.step({"name": "submit", "args": {"findings": oracle_findings(truth)}}))
        return transcript, ja.score_episode(d, env.submitted_findings(), env.inspection_steps()), truth

    original_a, score_a, truth_a = run(src)
    original_b, score_b, _ = run(src)
    assert json.dumps(original_a) == json.dumps(original_b) and score_a == score_b      # exact replay
    _, score_t, truth_t = run(dst)
    assert score_t == score_a and score_a["f1"] == 1.0                                  # same metrics and reward

    def canon(issues, mapping):
        return sorted((i["type"], sorted(mapping.get(x, x) for x in i["affected_ids"]), i["expected_amount_minor"],
                       i["currency"], sorted(sorted(mapping.get(x, x) for x in s) for s in i["evidence_sets"]))
                      for i in issues)
    assert canon(truth_t["issues"], inverse) == canon(truth_a["issues"], {})
    data_t = read_dir(dst)
    assert data_t["manifest"]["counts"]["ledger_rows"] == len(data_t["ledger"])
    ep_t = load_episode(dst)
    assert checks.verify_episode(ep_t.public, ep_t.truth) == []                          # no broken evidence
