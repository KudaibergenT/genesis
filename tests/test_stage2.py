"""Stage 2: FX conversion, period cut-off, interacting errors and clean episodes."""

import collections
import copy
from datetime import date, datetime, timedelta, timezone

import pytest

from auditenv import canon, checks, money, timeutil
from auditenv.books import books_files
from auditenv.config import resolve
from auditenv.suites import exactly
from auditenv.env import AuditEnv
from auditenv.evaluator import Grader
from auditenv.generator import generate_episode
from auditenv.policies import POLICIES
from auditenv.runner import run_episode

from conftest import ALL_TYPES, CLEAN_CONFIG, NO_INTERACTIONS, episode, only

SEEDS = range(30)
BOTH_INTERACTIONS = {"interactions": {"fx_partial_at_cutoff": {"count": 1}, "allocation_with_fx": {"count": 1}},
                     "errors": {t: {"count": 0} for t in ALL_TYPES}}
RENAMED = {"render": {"id_salt": 11, "order_salt": 5, "row_order": "shuffled"}}


def forced(issue_type, variant, count=1):
    cfg = only(**{issue_type: count})
    cfg["errors"][issue_type]["variants"] = [variant]
    return cfg


def with_render(cfg, render=RENAMED):
    out = copy.deepcopy(cfg)
    out.update(copy.deepcopy(render))
    return out


def mutations(ep, **match):
    return [m for m in ep.provenance["mutations"] if all(m.get(k) == v for k, v in match.items())]


def truth_by_id(ep):
    return {i["issue_id"]: i for i in ep.truth["issues"]}


def correct(ep, issues=None):
    return [
        {"type": i["type"], "affected_ids": list(i["affected_ids"]), "claimed_amount_minor": i["expected_amount_minor"],
         "currency": i["currency"], "evidence_ids": list(i["evidence_sets"][0]), "explanation": ""}
        for i in (ep.truth["issues"] if issues is None else issues)
    ]


def grade(ep, findings, steps=0):
    return Grader(ep.truth).grade({"episode_id": ep.episode_id, "findings": findings}, steps)


def produced(issue_type, variant, minimum):
    out = []
    for seed in SEEDS:
        ep = episode(seed, forced(issue_type, variant))
        for m in mutations(ep, operator=issue_type, variant=variant):
            out.append((ep, m, truth_by_id(ep)[m["issue_id"]]))
    assert len(out) >= minimum, f"{issue_type}/{variant} produced only {len(out)} times"
    return out


def doc_and_posting(ep, doc_id):
    ix = checks.Index(ep.public)
    if doc_id in ix.invoices:
        doc = ix.invoices[doc_id]
        return ix, "invoice", doc, ix.invoice_ar_row(doc), doc["issued_at"], doc["total_minor"]
    doc = ix.payments[doc_id]
    return ix, "payment", doc, ix.cash_rows(doc)[0], doc["timestamp"], doc["amount_minor"]


# ---------------------------------------------------------------------------- money and rounding
@pytest.mark.parametrize("amount, rate, mode, src, dst, expected", [
    (5, "0.5", "ROUND_HALF_EVEN", "USD", "EUR", 2),
    (5, "0.5", "ROUND_HALF_UP", "USD", "EUR", 3),
    (7, "0.5", "ROUND_HALF_EVEN", "USD", "EUR", 4),
    (-5, "0.5", "ROUND_HALF_UP", "USD", "EUR", -3),
    (-5, "0.5", "ROUND_HALF_EVEN", "USD", "EUR", -2),
    (12345, "161.237", "ROUND_HALF_EVEN", "EUR", "JPY", 19905),
    (1999, "0.0067", "ROUND_HALF_EVEN", "JPY", "USD", 1339),
    (100000, "0.307", "ROUND_HALF_UP", "USD", "KWD", 307000),
    (1234, "3.2575", "ROUND_HALF_EVEN", "KWD", "USD", 402),
])
def test_conversion_rounding(amount, rate, mode, src, dst, expected):
    assert money.convert(amount, src, dst, rate, mode) == expected


def test_conversion_is_symmetric_for_refunds():
    for mode in ("ROUND_HALF_EVEN", "ROUND_HALF_UP"):
        for amount in (1, 5, 12345, 999999):
            assert money.convert(-amount, "EUR", "KZT", "548.215", mode) == -money.convert(amount, "EUR", "KZT", "548.215", mode)


def test_unscaled_conversion_is_off_by_the_exponent_difference():
    assert money.convert(19905, "JPY", "USD", "0.0067", "ROUND_HALF_EVEN") == 13336
    assert money.convert_unscaled(19905, "0.0067", "ROUND_HALF_EVEN") == 133


def test_rates_are_decimal_strings_never_floats():
    with pytest.raises(TypeError):
        money.convert(10.0, "USD", "EUR", "0.9", "ROUND_HALF_EVEN")
    assert money.convert(10, "USD", "EUR", "0.9", "ROUND_HALF_EVEN") == 9


def test_clean_books_use_the_episode_rounding_mode():
    modes = set()
    for seed in SEEDS:
        ep = episode(seed, CLEAN_CONFIG)
        ix = checks.Index(ep.public)
        modes.add(ix.rounding)
        for doc in ep.public.invoices:
            if doc["currency"] != ix.base:
                assert checks.signed(ix.invoice_ar_row(doc)) == ix.to_base(doc["total_minor"], doc["currency"], doc["issued_at"])
    assert modes == {"ROUND_HALF_EVEN", "ROUND_HALF_UP"}


def test_weekend_documents_use_the_latest_published_rate():
    weekend = 0
    for seed in SEEDS:
        ep = episode(seed, CLEAN_CONFIG)
        ix = checks.Index(ep.public)
        published = {(r["date"], r["source_currency"]) for r in ep.public.fx}
        for doc in ep.public.invoices:
            day = ix.local_date(doc["issued_at"])
            if doc["currency"] == ix.base or day.weekday() < 5:
                continue
            weekend += 1
            assert (day.isoformat(), doc["currency"]) not in published
            friday = day - timedelta(days=day.weekday() - 4)
            rate = next(r["rate"] for r in ep.public.fx if r["date"] == friday.isoformat() and r["source_currency"] == doc["currency"])
            assert checks.signed(ix.invoice_ar_row(doc)) == money.convert(doc["total_minor"], doc["currency"], ix.base, rate, ix.rounding)
        assert checks.detect_issues(ep.public) == []
    assert weekend >= 10


def test_currencies_are_never_compared_without_conversion():
    for seed in range(10):
        ep = episode(seed, CLEAN_CONFIG)
        ix = checks.Index(ep.public)
        foreign = [d for d in ep.public.invoices if d["currency"] != ix.base]
        assert foreign
        assert any(checks.signed(ix.invoice_ar_row(d)) != d["total_minor"] for d in foreign)
        assert all(r["currency"] == ix.base for r in ep.public.ledger)


# ---------------------------------------------------------------------------- FX errors
def _independent_fx_amount(ep, doc_id):
    ix, kind, doc, row, ts, amount = doc_and_posting(ep, doc_id)
    day = ix.local_date(ts)
    rate = max((r for r in ep.public.fx if r["source_currency"] == doc["currency"] and date.fromisoformat(r["date"]) <= day),
               key=lambda r: r["date"])["rate"]
    return abs(abs(checks.signed(row)) - abs(money.convert(amount, doc["currency"], ix.base, rate, ix.rounding))), rate


@pytest.mark.parametrize("variant", ["invoice_wrong_date", "invoice_wrong_rate", "invoice_wrong_scale",
                                     "receipt_wrong_date", "receipt_wrong_rate"])
def test_fx_errors_are_real_and_recomputable_from_open_files(variant):
    for ep, m, issue in produced("fx_conversion", variant, minimum=5):
        assert issue["currency"] == ep.public.manifest["base_currency"]
        doc_id, = issue["affected_ids"]
        amount, policy_rate = _independent_fx_amount(ep, doc_id)
        assert amount == issue["expected_amount_minor"] >= 20 * issue["tolerance_minor"]
        assert m["params"]["policy_rate"] == policy_rate
        assert abs(m["params"]["booked_base_minor"] - m["params"]["correct_base_minor"]) == amount
        if variant != "invoice_wrong_scale":
            assert m["params"]["rate_used"] != policy_rate


def test_wrong_fx_date_uses_a_rate_published_for_another_date():
    for ep, m, issue in produced("fx_conversion", "invoice_wrong_date", minimum=5):
        ix, _, doc, row, ts, amount = doc_and_posting(ep, issue["affected_ids"][0])
        used = m["params"]["rate_date_used"]
        assert used != ix.rate_entry(doc["currency"], ix.local_date(ts))[0].isoformat()
        published = {(r["date"], r["source_currency"]): r["rate"] for r in ep.public.fx}
        assert published[(used, doc["currency"])] == m["params"]["rate_used"]
        assert checks.signed(row) == money.convert(amount, doc["currency"], ix.base, m["params"]["rate_used"], ix.rounding)


def test_wrong_fx_rate_matches_no_policy_conversion():
    for ep, m, issue in produced("fx_conversion", "invoice_wrong_rate", minimum=5):
        ix, _, doc, row, ts, amount = doc_and_posting(ep, issue["affected_ids"][0])
        assert checks.signed(row) == money.convert(amount, doc["currency"], ix.base, m["params"]["rate_used"], ix.rounding)
        assert checks.signed(row) != ix.to_base(amount, doc["currency"], ts)


def test_fx_tolerance_is_one_minor_unit():
    ep, _, issue = produced("fx_conversion", "invoice_wrong_rate", minimum=1)[0]
    base = correct(ep, [issue])[0]
    for delta, status in ((0, "TP"), (1, "TP"), (-1, "TP"), (2, "FP"), (-2, "FP")):
        f = dict(base, claimed_amount_minor=issue["expected_amount_minor"] + delta)
        assert grade(ep, [f]).details["findings"][0]["status"] == status


def test_fx_findings_need_ledger_currency_and_the_policy_rate():
    ep, _, issue = produced("fx_conversion", "receipt_wrong_rate", minimum=1)[0]
    ix, _, doc, row, ts, _ = doc_and_posting(ep, issue["affected_ids"][0])
    base = correct(ep, [issue])[0]
    assert grade(ep, [dict(base, currency=doc["currency"])]).details["findings"][0]["reason"] == "currency"
    no_rate = [e for e in base["evidence_ids"] if "|" not in e]
    assert grade(ep, [dict(base, evidence_ids=no_rate)]).details["findings"][0]["reason"] == "evidence"
    other = next(fx for fx in ep.public.fx if fx["source_currency"] == doc["currency"]
                 and f"{fx['date']}|{fx['source_currency']}|{fx['target_currency']}" not in base["evidence_ids"])
    wrong_rate = no_rate + [f"{other['date']}|{other['source_currency']}|{other['target_currency']}"]
    assert grade(ep, [dict(base, evidence_ids=wrong_rate)]).details["findings"][0]["reason"] == "evidence"


# ---------------------------------------------------------------------------- period cut-off
@pytest.mark.parametrize("variant", ["early", "late", "tz_shift", "receipt_early"])
def test_cutoff_errors_cross_the_period_boundary(variant):
    for ep, m, issue in produced("period_cutoff", variant, minimum=4):
        ix, kind, doc, row, ts, _ = doc_and_posting(ep, issue["affected_ids"][0])
        assert {ix.period_class(ts), ix.period_class(row["timestamp"])} == {"current", "next"}
        assert issue["expected_amount_minor"] == abs(checks.signed(row))
        assert timeutil.parse_iso(row["timestamp"]).isoformat() == m["params"]["booked_time"]
        if variant == "early" or variant == "receipt_early":
            assert ix.period_class(ts) == "next" and ix.period_class(row["timestamp"]) == "current"
        if variant == "late":
            assert ix.period_class(ts) == "current" and ix.period_class(row["timestamp"]) == "next"


def test_tz_shift_books_the_utc_wall_clock_with_the_local_offset():
    for ep, m, issue in produced("period_cutoff", "tz_shift", minimum=4):
        ix, _, doc, row, ts, _ = doc_and_posting(ep, issue["affected_ids"][0])
        utc = timeutil.parse_iso(ts).astimezone(timezone.utc)
        booked = timeutil.parse_iso(row["timestamp"])
        assert booked.replace(tzinfo=None) == utc.replace(tzinfo=None)
        assert booked.utcoffset() == timedelta(minutes=timeutil.ZONE_OFFSETS_MINUTES[ix.zone])


def test_cutoff_finding_needs_the_misdated_row():
    ep, _, issue = produced("period_cutoff", "early", minimum=1)[0]
    base = correct(ep, [issue])[0]
    assert grade(ep, [base]).metrics["tp"] == 1
    only_doc = [e for e in base["evidence_ids"] if e.startswith("D")]
    assert grade(ep, [dict(base, evidence_ids=only_doc)]).details["findings"][0]["reason"] == "evidence"


@pytest.mark.parametrize("zone", sorted(timeutil.ZONE_OFFSETS_MINUTES))
@pytest.mark.parametrize("payment_zone", ["company", "UTC"])
def test_every_timezone_generates_clean_books_without_false_cutoffs(zone, payment_zone):
    cfg = copy.deepcopy(CLEAN_CONFIG)
    cfg["company"] = {"timezones": [zone], "payment_timestamp_zones": [payment_zone]}
    for seed in range(4):
        ep = generate_episode(seed, cfg)
        assert ep.public.manifest["timezone"] == zone
        assert checks.clean_invariants(ep.public) == [] and checks.detect_issues(ep.public) == []
        if payment_zone == "UTC":
            assert all(p["timestamp"].endswith("+00:00") for p in ep.public.payments)


def test_raw_timestamp_dates_differ_from_local_dates_in_clean_books():
    mismatched = 0
    for seed in SEEDS:
        ep = episode(seed, CLEAN_CONFIG)
        ix = checks.Index(ep.public)
        for ts in [d["issued_at"] for d in ep.public.invoices] + [p["timestamp"] for p in ep.public.payments]:
            mismatched += ts[:10] != timeutil.local_date(timeutil.parse_iso(ts), ix.zone).isoformat()
            mismatched += ts[:10] != timeutil.parse_iso(ts).astimezone(timezone.utc).date().isoformat()
        assert checks.detect_issues(ep.public) == []
    assert mismatched >= 20


def test_review_window_holds_correct_next_period_items():
    for seed in range(10):
        ep = episode(seed, CLEAN_CONFIG)
        ix = checks.Index(ep.public)
        window_docs = [d for d in ep.public.invoices if ix.period_class(d["issued_at"]) == "next"]
        window_rows = [r for r in ep.public.ledger if ix.period_class(r["timestamp"]) == "next"]
        assert window_docs and window_rows
        for d in window_docs:
            assert ix.period_class(ix.invoice_ar_row(d)["timestamp"]) == "next"
        assert ep.public.policy["period"]["review_window_end"] == ep.public.manifest["review_window_end"]


# ---------------------------------------------------------------------------- interacting errors
def _scenario_pairs(name, minimum=20):
    out = []
    for seed in SEEDS:
        ep = episode(seed, BOTH_INTERACTIONS)
        ms = mutations(ep, scenario=name)
        if ms:
            out.append((ep, ms))
    assert len(out) >= minimum
    return out


def test_fx_partial_at_cutoff_scenario():
    for ep, ms in _scenario_pairs("fx_partial_at_cutoff"):
        assert sorted(m["operator"] for m in ms) == ["fx_conversion", "period_cutoff"]
        a, b = ms
        assert a["affected_ids"] == b["affected_ids"] and len(a["affected_ids"]) == 1
        assert a["linked_issue_id"] == b["issue_id"] and b["linked_issue_id"] == a["issue_id"]
        ix, kind, pay, row, ts, amount = doc_and_posting(ep, a["affected_ids"][0])
        assert kind == "payment" and pay["currency"] != ix.base
        assert ix.local_date(ts) == ix.period[1] + timedelta(days=1)
        assert ix.period_class(row["timestamp"]) == "current"
        ref, = pay["invoice_refs"]
        inv, = ix.invoice_docs_by_number[ref["invoice_id"]]
        assert ref["amount_minor"] == amount < inv["total_minor"]
        issues = {i["type"]: i for i in ep.truth["issues"] if i["affected_ids"] == a["affected_ids"]}
        assert issues["fx_conversion"]["expected_amount_minor"] == _independent_fx_amount(ep, pay["doc_id"])[0]
        assert issues["period_cutoff"]["expected_amount_minor"] == abs(checks.signed(row))


def test_allocation_with_fx_scenario():
    for ep, ms in _scenario_pairs("allocation_with_fx"):
        assert sorted(m["operator"] for m in ms) == ["fx_conversion", "payment_allocation"]
        doc_id, = ms[0]["affected_ids"]
        ix, kind, pay, row, ts, _ = doc_and_posting(ep, doc_id)
        assert kind == "payment" and pay["currency"] != ix.base
        unapplied = [r for r in ix.rows_by_pid[pay["payment_id"]] if ix.is_role(r, "unapplied")]
        alloc = next(i for i in ep.truth["issues"] if i["type"] == "payment_allocation" and i["affected_ids"] == [doc_id])
        assert alloc["expected_amount_minor"] == unapplied[0]["credit_minor"]
        fx = next(i for i in ep.truth["issues"] if i["type"] == "fx_conversion" and i["affected_ids"] == [doc_id])
        assert fx["expected_amount_minor"] == _independent_fx_amount(ep, doc_id)[0]


@pytest.mark.parametrize("name", ["fx_partial_at_cutoff", "allocation_with_fx"])
def test_both_interacting_issues_are_required_for_full_reward(name):
    for ep, ms in _scenario_pairs(name)[:5]:
        pair = [truth_by_id(ep)[m["issue_id"]] for m in ms]
        rest = [i for i in ep.truth["issues"] if i not in pair]
        full = grade(ep, correct(ep, pair + rest))
        half = grade(ep, correct(ep, pair[:1] + rest))
        assert full.reward == 1.0 and full.metrics["tp"] == len(ep.truth["issues"])
        assert half.reward < full.reward and half.metrics["fn"] == 1


def test_reference_solver_finds_both_interacting_issues_through_tools():
    for seed in range(10):
        ep = episode(seed, BOTH_INTERACTIONS)
        run = run_episode(POLICIES["reference_solver"](), seed, episode=ep)
        assert run.result["metrics"]["f1"] == 1.0 and run.steps <= 60


# ---------------------------------------------------------------------------- clean episodes
@pytest.mark.parametrize("seed", SEEDS)
def test_clean_company_has_zero_issues_and_no_false_discrepancies(seed):
    ep = episode(seed, CLEAN_CONFIG)
    assert ep.truth["issues"] == [] and ep.provenance["mutations"] == []
    assert checks.clean_invariants(ep.public) == [] and checks.detect_issues(ep.public) == []
    assert checks.verify_episode(ep.public, ep.truth) == []


def test_max_issues_zero_is_a_clean_company():
    for seed in range(10):
        ep = episode(seed, {"max_issues": 0})
        assert ep.truth["issues"] == [] and checks.detect_issues(ep.public) == []
        assert books_files(ep.public) == books_files(episode(seed, CLEAN_CONFIG).public)


def test_clean_companies_contain_all_legitimate_look_alikes():
    seen = collections.Counter()
    for seed in SEEDS:
        books = episode(seed, CLEAN_CONFIG).public
        ix = checks.Index(books)
        refs = collections.Counter(r["invoice_id"] for p in books.payments for r in (p["invoice_refs"] or []))
        seen["split_payments"] += any(n > 1 for n in refs.values())
        seen["refunds"] += any(p["amount_minor"] < 0 for p in books.payments) and any(d["total_minor"] < 0 for d in books.invoices)
        groups = collections.defaultdict(set)
        for d in books.invoices:
            groups[(d["entity_id"], d["total_minor"])].add(d["doc_id"])
        seen["repeated_amounts"] += any(len(v) > 1 for v in groups.values())
        paid = collections.Counter()
        for p in books.payments:
            for r in p["invoice_refs"] or []:
                paid[r["invoice_id"]] += r["amount_minor"]
        seen["legit_partials"] += any(0 < paid[d["invoice_id"]] < d["total_minor"] for d in books.invoices)
        seen["multi_currency_receipts"] += any(p["currency"] != ix.base and p["amount_minor"] > 0 for p in books.payments)
        seen["fx_gain_loss_rows"] += any(ix.is_role(r, "fx") for r in books.ledger)
        seen["unreferenced_receipts"] += any(p["invoice_refs"] is None for p in books.payments)
    for feature in ("split_payments", "refunds", "repeated_amounts", "legit_partials", "multi_currency_receipts",
                    "fx_gain_loss_rows", "unreferenced_receipts"):
        assert seen[feature] >= 15, (feature, seen[feature])


def test_silence_is_optimal_on_clean_companies():
    for seed in range(10):
        ep = episode(seed, CLEAN_CONFIG)
        assert run_episode(POLICIES["silence"](), seed, episode=ep).result["reward"] == 1.0
        assert run_episode(POLICIES["reference_solver"](), seed, episode=ep).result["metrics"]["fp"] == 0


@pytest.mark.parametrize("issue_type", ALL_TYPES)
def test_false_accusations_on_clean_companies_score_minus_one(issue_type):
    for seed in range(5):
        ep = episode(seed, CLEAN_CONFIG)
        ix = checks.Index(ep.public)
        doc = next(d for d in ep.public.invoices if d["currency"] != ix.base)
        ar = ix.invoice_ar_row(doc)
        fx = ix.policy_fx_key(doc["currency"], doc["issued_at"])
        accusation = {"type": issue_type, "affected_ids": [doc["doc_id"]], "claimed_amount_minor": ar["debit_minor"],
                      "currency": ix.base, "evidence_ids": [doc["doc_id"], ar["row_id"], fx], "explanation": "suspicious"}
        assert grade(ep, [accusation]).reward == -1.0
        assert grade(ep, []).reward == 1.0


# ---------------------------------------------------------------------------- generator quality
@pytest.mark.parametrize("seed", SEEDS)
def test_every_injected_change_is_causal_real_and_provable(seed):
    ep = episode(seed, BOTH_INTERACTIONS if seed % 2 else None)
    ix = checks.Index(ep.public)
    by_id = truth_by_id(ep)
    assert len(ep.provenance["mutations"]) == len(ep.truth["issues"]) <= ep.provenance["issue_budget"]["max_issues"] <= 12
    for m in ep.provenance["mutations"]:
        issue = by_id[m["issue_id"]]
        assert m["operator"] == issue["type"] and m["affected_ids"] == issue["affected_ids"]
        assert issue["expected_amount_minor"] > 0
        for doc_id in issue["affected_ids"]:
            assert doc_id in ix.invoices or doc_id in ix.payments
        for evidence in issue["evidence_sets"]:
            derived = checks.verify_evidence(ep.public, issue["type"], evidence)
            assert derived[0] == tuple(issue["affected_ids"]) and derived[2] == issue["currency"]
            assert abs(derived[1] - issue["expected_amount_minor"]) <= issue["tolerance_minor"]
            for k in range(len(evidence)):
                smaller = evidence[:k] + evidence[k + 1:]
                with pytest.raises(checks.EvidenceError):
                    checks.verify_evidence(ep.public, issue["type"], smaller)
    keys = [(i["type"], tuple(i["affected_ids"])) for i in ep.truth["issues"]]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("k", range(13))
def test_generator_supports_every_issue_count_from_0_to_12(k):
    for seed in range(4):
        ep = episode(seed, exactly(k))
        assert len(ep.truth["issues"]) == k
        assert ep.provenance["issue_budget"]["injected"] == k
        assert checks.verify_episode(ep.public, ep.truth) == []


def test_default_config_allows_up_to_twelve_issues_and_max_issues_still_caps():
    assert resolve()["max_issues"] == 12
    big = exactly(12)
    assert len(episode(0, big).truth["issues"]) == 12
    capped = episode(0, dict(big, max_issues=5))
    assert len(capped.truth["issues"]) == 5
    assert capped.provenance["issue_budget"]["max_issues"] == 5


def test_each_error_type_is_independently_configurable():
    for seed in range(10):
        for t in ("fx_conversion", "period_cutoff"):
            counts = collections.Counter(i["type"] for i in episode(seed, only(**{t: 2})).truth["issues"])
            assert set(counts) == {t} and 1 <= counts[t] <= 2


# ---------------------------------------------------------------------------- reproducibility and invariance
@pytest.mark.parametrize("cfg", [None, BOTH_INTERACTIONS, CLEAN_CONFIG, forced("period_cutoff", "tz_shift", 2)])
def test_same_seed_reproduces_everything(cfg):
    a, b = generate_episode(21, cfg), generate_episode(21, cfg)
    assert books_files(a.public) == books_files(b.public)
    assert canon.dumps(a.truth) == canon.dumps(b.truth) and canon.dumps(a.provenance) == canon.dumps(b.provenance)


def test_env_replay_is_exact_on_interaction_episodes():
    def transcript():
        env = AuditEnv()
        out = [canon.dumps(env.reset(7, dict(BOTH_INTERACTIONS, mode="train")))]
        page = env.step({"action": "list_records", "args": {"table": "payments"}})
        out.append(canon.dumps(page))
        doc = page[0]["result"]["records"][0]["doc_id"]
        for action in ({"action": "get_document", "args": {"doc_id": doc}},
                       {"action": "list_records", "args": {"table": "fx", "limit": 3}},
                       {"action": "submit", "args": {"episode_id": env.episode_id, "findings": []}}):
            out.append(canon.dumps(env.step(action)))
        out.append(canon.dumps(env.closed_result()))
        return out

    assert transcript() == transcript()


def _canonical(ep, mapping):
    return sorted(
        (i["type"], tuple(sorted(mapping.get(x, x) for x in i["affected_ids"])), i["expected_amount_minor"], i["currency"],
         tuple(sorted(tuple(sorted(mapping.get(x, x) for x in s)) for s in i["evidence_sets"])))
        for i in ep.truth["issues"]
    )


@pytest.mark.parametrize("cfg", [BOTH_INTERACTIONS, only(fx_conversion=2, period_cutoff=2)])
@pytest.mark.parametrize("seed", range(8))
def test_id_and_order_changes_preserve_truth_and_rewards(cfg, seed):
    a, b = episode(seed, cfg), episode(seed, with_render(cfg))
    inv = {v: k for k, v in a.provenance["id_map"].items()}
    mapping = {s: b.provenance["id_map"][k] for s, k in inv.items()}
    assert _canonical(a, mapping) == _canonical(b, {})
    assert [r["row_id"] for r in b.public.ledger] != [mapping[r["row_id"]] for r in a.public.ledger]
    for name in ("oracle", "reference_solver", "simple_reconciler"):
        ra = run_episode(POLICIES[name](), seed, episode=a).result
        rb = run_episode(POLICIES[name](), seed, episode=b).result
        assert ra["metrics"] == rb["metrics"], name
