"""Independent block-2 verification from the open files and the documented posting rules.

Documented rules used (README / policy.json): ledger in the base currency; invoices post AR against
revenue (credit notes against 4100 Sales Returns) at their own timestamp; receipts post cash at
their own timestamp, credit AR per invoice_refs at the invoice carrying rate, park unreferenced
receipts in 2400; conversion = document amount x latest published rate on or before the local
document date, rounded once with policy rounding; an item belongs to the period of the local date
of its document timestamp; missing references are explicit null.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Set, Tuple

from jury_sim.fileio import all_ids, fx_id

ROUNDING = {"ROUND_HALF_EVEN": ROUND_HALF_EVEN, "ROUND_HALF_UP": ROUND_HALF_UP}


class Books:
    def __init__(self, data: Dict[str, Any]) -> None:
        m = data["manifest"]
        self.data = data
        self.base = m["base_currency"]
        sign = -1 if m["utc_offset"][0] == "-" else 1
        hours, minutes = m["utc_offset"][1:].split(":")
        self.tz = timezone(sign * timedelta(hours=int(hours), minutes=int(minutes)))
        self.start, self.end = date.fromisoformat(m["period_start"]), date.fromisoformat(m["period_end"])
        self.window_end = date.fromisoformat(m["review_window_end"])
        self.exp = m["currency_exponents"]
        self.rounding = ROUNDING[m["rounding"]["mode"]]
        self.role = {name: role for name, role in data["policy"]["ledger"]["accounts"].items()}
        self.docs_by_number: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for d in data["invoices"]:
            self.docs_by_number[d["invoice_id"]].append(d)
        self.rates: Dict[str, List[Tuple[date, str, str]]] = defaultdict(list)
        for r in data["fx"]:
            if r["target_currency"] == self.base:
                self.rates[r["source_currency"]].append((date.fromisoformat(r["date"]), r["rate"], fx_id(r)))
        for series in self.rates.values():
            series.sort()

    def day(self, ts: str) -> date:
        return datetime.fromisoformat(ts).astimezone(self.tz).date()

    def period(self, ts: str) -> str:
        d = self.day(ts)
        return "current" if self.start <= d <= self.end else "next" if self.end < d <= self.window_end else "outside"

    def rate(self, currency: str, ts: str) -> Tuple[str, str]:
        if currency == self.base:
            return "1", ""
        best = [(r, key) for d, r, key in self.rates[currency] if d <= self.day(ts)]
        return best[-1]

    def convert(self, amount: int, currency: str, ts: str, rate: str = None) -> int:
        rate = rate if rate is not None else self.rate(currency, ts)[0]
        value = Decimal(amount) * Decimal(rate) * (Decimal(10) ** (self.exp[self.base] - self.exp[currency]))
        return int(value.quantize(Decimal(1), rounding=self.rounding))

    def signed(self, row: Dict[str, Any]) -> int:
        return row["debit_minor"] - row["credit_minor"]

    def invoice_postings(self, doc: Dict[str, Any], rows=None) -> List[Dict[str, Any]]:
        rows = [r for r in (rows if rows is not None else self.data["ledger"])
                if r["invoice_id"] == doc["invoice_id"] and r["payment_id"] is None]
        if len(self.docs_by_number[doc["invoice_id"]]) > 1:
            instant = datetime.fromisoformat(doc["issued_at"])
            rows = [r for r in rows if datetime.fromisoformat(r["timestamp"]) == instant]
        return rows

    def cash_rows(self, pay: Dict[str, Any], rows=None) -> List[Dict[str, Any]]:
        return [r for r in (rows if rows is not None else self.data["ledger"])
                if r["payment_id"] == pay["payment_id"] and self.role.get(r["account"]) == "cash"]


def content(doc: Dict[str, Any]) -> Any:
    return (doc["entity_id"], doc["currency"], [(li["description"], li["service_period"], li["order_ref"],
                                                 li["quantity"], li["unit_price_minor"]) for li in doc["line_items"]])


def equalities(data: Dict[str, Any]) -> List[Tuple[str, Set[str]]]:
    """Accounting equalities of a consistent ledger; each violation lists the documents involved."""
    b = Books(data)
    out: List[Tuple[str, Set[str]]] = []
    entries: Dict[datetime, List[Dict[str, Any]]] = defaultdict(list)
    for r in data["ledger"]:
        entries[datetime.fromisoformat(r["timestamp"])].append(r)
        if (r["debit_minor"] > 0) == (r["credit_minor"] > 0) or r["currency"] != b.base:
            out.append(("row_shape", set()))
    for rows in entries.values():
        if sum(b.signed(r) for r in rows) != 0:
            numbers = {r["invoice_id"] for r in rows if r["invoice_id"]}
            pays = {r["payment_id"] for r in rows if r["payment_id"]}
            out.append(("entry_unbalanced", {d["doc_id"] for n in numbers for d in b.docs_by_number[n]}
                        | {p["doc_id"] for p in data["payments"] if p["payment_id"] in pays}))
    for doc in data["invoices"]:
        rows = b.invoice_postings(doc)
        ar = [r for r in rows if b.role.get(r["account"]) == "ar"]
        if len(rows) != 2 or len(ar) != 1:
            out.append(("invoice_posting", {doc["doc_id"]}))
            continue
        if b.signed(ar[0]) != b.convert(doc["total_minor"], doc["currency"], doc["issued_at"]):
            out.append(("invoice_conversion", {doc["doc_id"]}))
        if b.period(ar[0]["timestamp"]) != b.period(doc["issued_at"]):
            out.append(("invoice_period", {doc["doc_id"]}))
    referenced: Dict[str, int] = defaultdict(int)
    referencing: Dict[str, Set[str]] = defaultdict(set)
    for pay in data["payments"]:
        cash = b.cash_rows(pay)
        if len(cash) != 1:
            out.append(("receipt_posting", {pay["doc_id"]}))
            continue
        if b.signed(cash[0]) != b.convert(pay["amount_minor"], pay["currency"], pay["timestamp"]):
            out.append(("receipt_conversion", {pay["doc_id"]}))
        if b.period(cash[0]["timestamp"]) != b.period(pay["timestamp"]):
            out.append(("receipt_period", {pay["doc_id"]}))
        parked = [r for r in data["ledger"] if r["payment_id"] == pay["payment_id"] and b.role.get(r["account"]) == "unapplied"]
        if pay["invoice_refs"] is None:
            if -sum(b.signed(r) for r in parked) != b.signed(cash[0]):
                out.append(("unreferenced_receipt", {pay["doc_id"]}))
            continue
        if sum(x["amount_minor"] for x in pay["invoice_refs"]) != pay["amount_minor"] or parked:
            out.append(("receipt_allocation", {pay["doc_id"]}))
        for ref in pay["invoice_refs"]:
            referenced[ref["invoice_id"]] += ref["amount_minor"]
            referencing[ref["invoice_id"]].add(pay["doc_id"])
    for number, docs in b.docs_by_number.items():
        balance = sum(b.signed(r) for r in data["ledger"] if r["invoice_id"] == number and b.role.get(r["account"]) == "ar")
        if referenced[number] == sum(d["total_minor"] for d in docs) and balance != 0:
            out.append(("receivable_open", {d["doc_id"] for d in docs} | referencing[number]))
    seen: Dict[Any, str] = {}
    for doc in data["invoices"]:
        key = repr(content(doc))
        if key in seen:
            out.append(("identical_invoices", {doc["doc_id"], seen[key]}))
        seen[key] = doc["doc_id"]
    return out


def before_injection(data: Dict[str, Any]) -> Dict[str, Any]:
    """Undo provenance["changes"] (documented: added / removed / modified records with full values)."""
    changes = data["provenance"]["changes"]
    out = dict(data)
    for table, key in (("ledger", "row_id"), ("invoices", "doc_id"), ("payments", "doc_id")):
        added = {r[key] for r in changes[table]["added"]}
        before = {m["after"][key]: m["before"] for m in changes[table]["modified"]}
        out[table] = [before.get(r[key], r) for r in data[table] if r[key] not in added] + changes[table]["removed"]
    return out


def declared(b: Books, issue: Dict[str, Any], records: Dict[str, Dict[str, Any]]) -> Tuple[Set[str], int, str]:
    """Re-derive (affected set, amount, currency) of an issue from the given records only."""
    invoices = [r for r in records.values() if "issued_at" in r]
    payments = [r for r in records.values() if "invoice_refs" in r]
    rows = [r for r in records.values() if "row_id" in r]
    t = issue["type"]
    if t == "duplicate_invoice":
        assert len(invoices) == 2 and content(invoices[0]) == content(invoices[1]), "two identical invoices"
        assert all(b.invoice_postings(d, rows) for d in invoices), "both copies posted"
        return {d["doc_id"] for d in invoices}, invoices[0]["total_minor"], invoices[0]["currency"]
    if t == "payment_allocation":
        (pay,) = payments
        assert pay["invoice_refs"] and sum(x["amount_minor"] for x in pay["invoice_refs"]) == pay["amount_minor"]
        parked = [r for r in rows if r["payment_id"] == pay["payment_id"] and b.role.get(r["account"]) == "unapplied"]
        assert parked, "unapplied booking shown"
        return {pay["doc_id"]}, -sum(b.signed(r) for r in parked), b.base
    (doc,) = invoices + payments
    ts = doc.get("issued_at") or doc["timestamp"]
    amount = doc.get("total_minor", doc.get("amount_minor"))
    posted = ([r for r in b.invoice_postings(doc, rows)] if "issued_at" in doc else b.cash_rows(doc, rows))
    assert posted, "posting row shown"
    row = posted[0]
    if t == "fx_conversion":
        rate, key = b.rate(doc["currency"], ts)
        assert key in records, "policy rate cited"
        return {doc["doc_id"]}, abs(abs(b.signed(row)) - abs(b.convert(amount, doc["currency"], ts, rate))), b.base
    if t == "period_cutoff":
        assert b.period(row["timestamp"]) != b.period(ts), "posting in another period"
        return {doc["doc_id"]}, abs(b.signed(row)), b.base
    raise AssertionError(f"unknown type {t}")


def verify(data: Dict[str, Any]) -> List[str]:
    """Problems with an episode under the six block-2 conditions; empty list = pass."""
    problems: List[str] = []
    b = Books(data)
    truth = data["truth"]["issues"]
    # formats: explicit null token, offsets, integer money, decimal-string rates
    for name, text in data["raw_csv"].items():
        if any(field == "" for line in text.splitlines()[1:] for field in line.split(",")):
            problems.append(f"{name}: empty field instead of explicit null")
    for d in data["invoices"] + data["payments"]:
        ts = d.get("issued_at") or d["timestamp"]
        if datetime.fromisoformat(ts).tzinfo is None:
            problems.append("timestamp without offset")
    for r in data["fx"]:
        Decimal(r["rate"])
    # 1. consistent before injection
    pre = before_injection(data)
    for code, docs in equalities(pre):
        problems.append(f"pre-injection violation {code} {sorted(docs)}")
    # 2./3. declared discrepancy is real, truth matches it, nothing undeclared
    related: Set[str] = set()
    index = {r["row_id"]: r for r in data["ledger"]}
    index.update({d["doc_id"]: d for d in data["invoices"] + data["payments"]})
    index.update({fx_id(r): r for r in data["fx"]})
    for issue in truth:
        related |= set(issue["affected_ids"])
        for s in issue["evidence_sets"]:
            related |= {x for x in s if x in index and "doc_id" in index[x]}
        for doc_id in issue["affected_ids"]:
            doc = index.get(doc_id, {})
            if "invoice_refs" in doc:
                related |= {d["doc_id"] for x in doc["invoice_refs"] or [] for d in b.docs_by_number[x["invoice_id"]]}
            if "invoice_id" in doc and "issued_at" in doc:
                related |= {p["doc_id"] for p in data["payments"]
                            if any(x["invoice_id"] == doc["invoice_id"] for x in p["invoice_refs"] or [])}
        try:
            got = declared(b, issue, {k: index[k] for k in set().union(*issue["evidence_sets"])})
            if got[0] != set(issue["affected_ids"]) or got[2] != issue["currency"] or \
                    abs(got[1] - issue["expected_amount_minor"]) > issue["tolerance_minor"] or got[1] <= issue["tolerance_minor"]:
                problems.append(f"{issue['issue_id']}: records show {got}, truth says {issue['expected_amount_minor']}")
        except (AssertionError, KeyError, ValueError) as exc:
            problems.append(f"{issue['issue_id']}: declared discrepancy not reproducible: {exc}")
        # 4. every allowed evidence set alone suffices and is reachable
        known = set(all_ids(data))
        for s in issue["evidence_sets"]:
            if not set(s) <= known:
                problems.append(f"{issue['issue_id']}: evidence {s} not reachable")
                continue
            try:
                got = declared(b, issue, {k: index[k] for k in s})
                if got[0] != set(issue["affected_ids"]) or abs(got[1] - issue["expected_amount_minor"]) > issue["tolerance_minor"]:
                    problems.append(f"{issue['issue_id']}: evidence {s} derives {got}")
            except (AssertionError, ValueError) as exc:
                problems.append(f"{issue['issue_id']}: evidence {s} insufficient: {exc}")
    for code, docs in equalities(data):
        if not docs & related:
            problems.append(f"undeclared discrepancy {code} {sorted(docs)}")
    return problems
