"""Negative controls for the clean-room block-2 verifier (jury_sim/verify.py).

A verifier that passes everything proves nothing. Each test generates real episodes through
judge_adapter, reads them with the clean-room loader only, corrupts one thing and requires the
verifier to report it. Covered: accounting equalities, every error type, cut-off by local date and
time zone, amounts, evidence sufficiency and the reconstructed pre-injection books.
"""

import copy
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

import judge_adapter as ja
from jury_sim.fileio import load, save
from jury_sim.verify import Books, equalities, verify

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SINGLE = ["duplicate_invoice", "payment_allocation_missing", "payment_allocation_partial", "fx_conversion",
          "period_cutoff"]


def scenario(name):
    with open(os.path.join(ROOT, "configs", "scenarios", f"{name}.json")) as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def episodes(tmp_path_factory):
    base = tmp_path_factory.mktemp("indep")
    out = {}
    for n, name in enumerate(["clean"] + SINGLE + ["interaction_fx_partial_at_cutoff",
                                                   "interaction_allocation_with_fx"]):
        d = str(base / name)
        ja.generate_episode(700 + n, scenario(name), d)
        out[name] = d
    return out


def fresh(episodes, name):
    return load(episodes[name])


@pytest.mark.parametrize("name", ["clean"] + SINGLE + ["interaction_fx_partial_at_cutoff",
                                                       "interaction_allocation_with_fx"])
def test_untouched_episodes_pass(episodes, name):
    assert verify(fresh(episodes, name)) == []


@pytest.mark.parametrize("name", SINGLE)
def test_every_injected_error_is_visible_in_the_books(episodes, name):
    """Hide each issue from truth: the books alone must expose an undeclared discrepancy."""
    data = fresh(episodes, name)
    assert data["truth"]["issues"]
    for k in range(len(data["truth"]["issues"])):
        d = copy.deepcopy(data)
        del d["truth"]["issues"][k]
        assert any("undeclared discrepancy" in p for p in verify(d)), (name, k)


@pytest.mark.parametrize("name", SINGLE)
def test_wrong_truth_amount_is_detected(episodes, name):
    data = fresh(episodes, name)
    data["truth"]["issues"][0]["expected_amount_minor"] += 5
    assert any("records show" in p or "derives" in p for p in verify(data))


@pytest.mark.parametrize("name", SINGLE)
def test_insufficient_evidence_is_detected(episodes, name):
    data = fresh(episodes, name)
    issue = data["truth"]["issues"][0]
    issue["evidence_sets"] = [s[:-1] for s in issue["evidence_sets"]]
    assert verify(data)


def test_unknown_evidence_is_detected(episodes):
    data = fresh(episodes, "duplicate_invoice")
    data["truth"]["issues"][0]["evidence_sets"].append(["NO-SUCH-ID"])
    assert any("not reachable" in p for p in verify(data))


def test_undeclared_amount_change_in_a_clean_ledger_is_detected(episodes):
    data = fresh(episodes, "clean")
    row = next(r for r in data["ledger"] if r["debit_minor"] > 0 and r["invoice_id"] and not r["payment_id"])
    row["debit_minor"] += 100
    codes = {p.split()[2] for p in verify(data) if p.startswith("undeclared")}
    assert {"entry_unbalanced", "invoice_conversion"} <= codes


def test_undeclared_cutoff_shift_in_a_clean_ledger_is_detected(episodes):
    """Move a whole invoice entry (both rows) across the period end: balanced, but in the wrong period."""
    data = fresh(episodes, "clean")
    b = Books(data)
    doc = next(d for d in data["invoices"] if b.period(d["issued_at"]) == "current" and d["total_minor"] > 0)
    rows = [r for r in data["ledger"] if r["invoice_id"] == doc["invoice_id"] and r["payment_id"] is None]
    later = datetime.combine(b.end + timedelta(days=1), datetime.min.time(), b.tz).replace(hour=10).isoformat()
    for r in rows:
        r["timestamp"] = later
    problems = verify(data)
    assert any(p.startswith("undeclared discrepancy invoice_period") and doc["doc_id"] in p for p in problems)


def test_cutoff_uses_the_company_local_date_not_utc(episodes):
    """The period is decided by the local date in the company zone, whatever offset the timestamp carries."""
    data = fresh(episodes, "clean")
    data["manifest"]["utc_offset"] = "-05:00"
    b = Books(data)
    late = datetime.combine(b.end, datetime.min.time(), b.tz).replace(hour=23, minute=30)  # UTC: next day 04:30
    assert late.astimezone(timezone.utc).date() > b.end
    assert b.period(late.isoformat()) == b.period(late.astimezone(timezone.utc).isoformat()) == "current"
    data["manifest"]["utc_offset"] = "+05:00"
    b = Books(data)
    early = datetime.combine(b.end + timedelta(days=1), datetime.min.time(), b.tz).replace(minute=30)  # UTC: 19:30
    assert early.astimezone(timezone.utc).date() == b.end
    assert b.period(early.isoformat()) == b.period(early.astimezone(timezone.utc).isoformat()) == "next"


def test_cutoff_issue_disappears_when_the_document_date_is_aligned(episodes):
    """Truth claims a cut-off error; once the document sits in the posting's period it is no longer real."""
    data = fresh(episodes, "period_cutoff")
    b = Books(data)
    issue = next(i for i in data["truth"]["issues"] if i["type"] == "period_cutoff")
    (doc_id,) = issue["affected_ids"]
    doc = next(d for d in data["invoices"] + data["payments"] if d["doc_id"] == doc_id)
    key = "issued_at" if "issued_at" in doc else "timestamp"
    posted = (b.invoice_postings(doc) if key == "issued_at" else b.cash_rows(doc))[0]
    doc[key] = posted["timestamp"]
    assert any(issue["issue_id"] in p for p in verify(data))


@pytest.mark.parametrize("name", SINGLE)
def test_tampered_provenance_breaks_the_pre_injection_check(episodes, name):
    """Forget one recorded change: the reconstructed 'before' books are no longer consistent."""
    data = fresh(episodes, name)
    changes = data["provenance"]["changes"]
    table, kind = next((t, k) for t in ("ledger", "invoices", "payments") for k in ("modified", "removed", "added")
                       if changes[t][k])
    changes[table][kind].pop()
    assert any(p.startswith("pre-injection violation") for p in verify(data)), (table, kind)


def test_clean_books_satisfy_every_equality_and_look_alikes_are_not_flagged(episodes):
    data = fresh(episodes, "clean")
    assert equalities(data) == []
    assert any(p["invoice_refs"] and len(p["invoice_refs"]) > 1 or p["amount_minor"] < 0 for p in data["payments"])


def test_command_line_verifier(episodes, tmp_path):
    bad = str(tmp_path / "bad")
    data = fresh(episodes, "fx_conversion")
    data["truth"]["issues"][0]["expected_amount_minor"] += 5
    save(bad, data)
    out = subprocess.run([sys.executable, "jury_sim/verify.py", episodes["clean"], episodes["fx_conversion"], bad],
                         cwd=ROOT, capture_output=True, text=True, timeout=300)
    lines = [json.loads(x) for x in out.stdout.splitlines()]
    assert out.returncode == 1 and [x["passed"] for x in lines] == [True, True, False]
