"""Independent checks over OPEN episode files only.

This module must never import `auditenv.generator`: it re-derives accounting equalities,
declared discrepancies and evidence sufficiency from what an agent could read.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from auditenv import money, timeutil
from auditenv.books import PublicBooks, fx_key

LIMITS = {"ledger_rows": (200, 2000), "documents": (20, 200), "issues": (0, 12), "evidence_ids": 10}


@dataclass(frozen=True)
class Violation:
    code: str
    ids: Tuple[str, ...]
    detail: str = ""


@dataclass(frozen=True)
class Detected:
    type: str
    affected_ids: Tuple[str, ...]
    amount_minor: int
    currency: str
    evidence_ids: Tuple[str, ...]


class EvidenceError(ValueError):
    pass


def invoice_signature(doc: Dict[str, Any]) -> Tuple[Any, ...]:
    lines = tuple(
        (li["description"], li["service_period"], li["order_ref"], li["quantity"], li["unit_price_minor"])
        for li in doc["line_items"]
    )
    return (doc["entity_id"], doc["currency"], lines)


def signed(row: Dict[str, Any]) -> int:
    """Debit-positive amount of a ledger row."""
    return row["debit_minor"] - row["credit_minor"]


class Index:
    def __init__(self, books: PublicBooks) -> None:
        self.books = books
        m = books.manifest
        self.base: str = m["base_currency"]
        self.zone: str = m["timezone"]
        self.rounding: str = m["rounding"]["mode"]
        self.period = (date.fromisoformat(m["period_start"]), date.fromisoformat(m["period_end"]))
        self.window_end = date.fromisoformat(m.get("review_window_end", m["period_end"]))
        self.acc = {role: name for name, role in books.policy["ledger"]["accounts"].items()}
        conventions = books.policy.get("issue_conventions", {})
        self.tolerance = {t: c["tolerance_minor"] for t, c in conventions.items()}
        self.rows = {r["row_id"]: r for r in books.ledger}
        self.invoices = {d["doc_id"]: d for d in books.invoices}
        self.payments = {d["doc_id"]: d for d in books.payments}
        self.payment_by_pid = {d["payment_id"]: d for d in books.payments}
        self.invoice_docs_by_number: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for d in books.invoices:
            self.invoice_docs_by_number[d["invoice_id"]].append(d)
        self.rows_by_number: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self.rows_by_pid: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for r in books.ledger:
            if r["invoice_id"] is not None:
                self.rows_by_number[r["invoice_id"]].append(r)
            if r["payment_id"] is not None:
                self.rows_by_pid[r["payment_id"]].append(r)
        self.fx_by_key = {fx_key(r): r for r in books.fx}
        self.fx: Dict[str, List[Tuple[date, str]]] = defaultdict(list)
        for r in books.fx:
            if r["target_currency"] == self.base:
                self.fx[r["source_currency"]].append((date.fromisoformat(r["date"]), r["rate"]))
        for series in self.fx.values():
            series.sort()

    # ---- time and periods
    def local_date(self, ts: str) -> date:
        return timeutil.local_date(timeutil.parse_iso(ts), self.zone)

    def period_class(self, ts: str) -> str:
        d = self.local_date(ts)
        if self.period[0] <= d <= self.period[1]:
            return "current"
        if self.period[1] < d <= self.window_end:
            return "next"
        return "outside"

    # ---- FX
    def rate_entry(self, currency: str, on: date) -> Optional[Tuple[date, str]]:
        best = None
        for d, rate in self.fx.get(currency, []):
            if d <= on:
                best = (d, rate)
            else:
                break
        return best

    def rate(self, currency: str, on: date) -> str:
        if currency == self.base:
            return "1"
        entry = self.rate_entry(currency, on)
        if entry is None:
            raise LookupError(f"no rate for {currency} on {on}")
        return entry[1]

    def policy_fx_key(self, currency: str, ts: str) -> Optional[str]:
        entry = self.rate_entry(currency, self.local_date(ts))
        return None if entry is None else f"{entry[0].isoformat()}|{currency}|{self.base}"

    def to_base(self, amount: int, currency: str, ts: str) -> int:
        return money.convert(amount, currency, self.base, self.rate(currency, self.local_date(ts)), self.rounding)

    # ---- postings
    def is_role(self, row: Dict[str, Any], role: str) -> bool:
        return row["account"] == self.acc.get(role)

    def invoice_postings(self, doc: Dict[str, Any], pool: Optional[Sequence[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        """Rows of the document's own posting: same invoice_id, no payment_id (instant if the number is shared)."""
        rows = [r for r in (pool if pool is not None else self.rows_by_number[doc["invoice_id"]])
                if r["invoice_id"] == doc["invoice_id"] and r["payment_id"] is None]
        if len(self.invoice_docs_by_number[doc["invoice_id"]]) > 1:
            when = timeutil.parse_iso(doc["issued_at"])
            rows = [r for r in rows if timeutil.parse_iso(r["timestamp"]) == when]
        return rows

    def invoice_ar_row(self, doc: Dict[str, Any], pool=None) -> Optional[Dict[str, Any]]:
        rows = [r for r in self.invoice_postings(doc, pool) if self.is_role(r, "ar")]
        return rows[0] if len(rows) == 1 else None

    def invoice_counter_row(self, doc: Dict[str, Any], pool=None) -> Optional[Dict[str, Any]]:
        rows = [r for r in self.invoice_postings(doc, pool) if self.is_role(r, "revenue") or self.is_role(r, "returns")]
        return rows[0] if len(rows) == 1 else None

    def cash_rows(self, pay: Dict[str, Any], pool=None) -> List[Dict[str, Any]]:
        rows = pool if pool is not None else self.rows_by_pid[pay["payment_id"]]
        return [r for r in rows if r["payment_id"] == pay["payment_id"] and self.is_role(r, "cash")]


def clean_invariants(books: PublicBooks) -> List[Violation]:
    """Accounting equalities that hold on every clean ledger."""
    ix = Index(books)
    out: List[Violation] = []

    entries: Dict[datetime, List[Dict[str, Any]]] = defaultdict(list)
    for r in books.ledger:
        d, c = r["debit_minor"], r["credit_minor"]
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (d, c)) or d < 0 or c < 0 or (d > 0) == (c > 0):
            out.append(Violation("row_amounts", (r["row_id"],)))
        if r["currency"] != ix.base:
            out.append(Violation("row_currency", (r["row_id"],)))
        if r["invoice_id"] is not None and r["invoice_id"] not in ix.invoice_docs_by_number:
            out.append(Violation("dangling_invoice_ref", (r["row_id"],)))
        if r["payment_id"] is not None and r["payment_id"] not in ix.payment_by_pid:
            out.append(Violation("dangling_payment_ref", (r["row_id"],)))
        if ix.period_class(r["timestamp"]) == "outside":
            out.append(Violation("row_out_of_scope", (r["row_id"],)))
        entries[timeutil.parse_iso(r["timestamp"])].append(r)
    for rows in entries.values():
        if sum(r["debit_minor"] for r in rows) != sum(r["credit_minor"] for r in rows):
            out.append(Violation("entry_unbalanced", tuple(sorted(r["row_id"] for r in rows))))
    if sum(r["debit_minor"] for r in books.ledger) != sum(r["credit_minor"] for r in books.ledger):
        out.append(Violation("ledger_unbalanced", ()))

    for doc in books.invoices:
        did = doc["doc_id"]
        if any(li["amount_minor"] != li["quantity"] * li["unit_price_minor"] for li in doc["line_items"]) or \
                doc["total_minor"] != sum(li["amount_minor"] for li in doc["line_items"]) or doc["total_minor"] == 0:
            out.append(Violation("invoice_arithmetic", (did,)))
        if ix.period_class(doc["issued_at"]) == "outside":
            out.append(Violation("invoice_out_of_scope", (did,)))
        postings = ix.invoice_postings(doc)
        ar, counter = ix.invoice_ar_row(doc), ix.invoice_counter_row(doc)
        row_ids = tuple(r["row_id"] for r in postings)
        expected_counter = "revenue" if doc["total_minor"] > 0 else "returns"
        if len(postings) != 2 or ar is None or counter is None or not ix.is_role(counter, expected_counter):
            out.append(Violation("invoice_posting_shape", (did,) + row_ids))
            continue
        if signed(ar) != ix.to_base(doc["total_minor"], doc["currency"], doc["issued_at"]) or signed(counter) != -signed(ar):
            out.append(Violation("invoice_conversion", (did,) + row_ids))
        if timeutil.parse_iso(ar["timestamp"]) != timeutil.parse_iso(doc["issued_at"]):
            out.append(Violation("invoice_posting_time", (did,) + row_ids))
        if ix.period_class(ar["timestamp"]) != ix.period_class(doc["issued_at"]):
            out.append(Violation("invoice_cutoff", (did,) + row_ids))
        if ar["entity_id"] != doc["entity_id"]:
            out.append(Violation("entity_mismatch", (did, ar["row_id"])))

    referenced: Dict[str, int] = defaultdict(int)
    referencing: Dict[str, List[str]] = defaultdict(list)
    for pay in books.payments:
        pid, did = pay["payment_id"], pay["doc_id"]
        rows = ix.rows_by_pid[pid]
        cash = ix.cash_rows(pay)
        unapplied = [r for r in rows if ix.is_role(r, "unapplied")]
        if ix.period_class(pay["timestamp"]) == "outside":
            out.append(Violation("payment_out_of_scope", (did,)))
        if len(cash) != 1:
            out.append(Violation("payment_cash_shape", (did,) + tuple(r["row_id"] for r in cash)))
            continue
        cash_row = cash[0]
        if signed(cash_row) != ix.to_base(pay["amount_minor"], pay["currency"], pay["timestamp"]):
            out.append(Violation("receipt_conversion", (did, cash_row["row_id"])))
        if timeutil.parse_iso(cash_row["timestamp"]) != timeutil.parse_iso(pay["timestamp"]):
            out.append(Violation("receipt_posting_time", (did, cash_row["row_id"])))
        if ix.period_class(cash_row["timestamp"]) != ix.period_class(pay["timestamp"]):
            out.append(Violation("receipt_cutoff", (did, cash_row["row_id"])))
        refs = pay["invoice_refs"]
        if refs is None:
            if -sum(signed(r) for r in unapplied) != signed(cash_row):
                out.append(Violation("unapplied_mismatch", (did,) + tuple(r["row_id"] for r in unapplied)))
            continue
        if sum(ref["amount_minor"] for ref in refs) != pay["amount_minor"]:
            out.append(Violation("payment_refs_sum", (did,)))
        if unapplied:
            out.append(Violation("unapplied_on_referenced_payment", (did,) + tuple(r["row_id"] for r in unapplied)))
        for ref in refs:
            number = ref["invoice_id"]
            referenced[number] += ref["amount_minor"]
            referencing[number].append(did)
            docs = ix.invoice_docs_by_number.get(number, [])
            doc_ids = tuple(d["doc_id"] for d in docs)
            if not docs:
                out.append(Violation("payment_ref_unknown_invoice", (did,)))
                continue
            settle = [r for r in rows if ix.is_role(r, "ar") and r["invoice_id"] == number]
            if not settle:
                out.append(Violation("missing_ar_credit", (did,) + doc_ids))
                continue
            inv = docs[0]
            expected = ix.to_base(ref["amount_minor"], inv["currency"], inv["issued_at"])
            if abs(-sum(signed(r) for r in settle) - expected) > 1:
                out.append(Violation("ar_credit_amount", (did,) + doc_ids + tuple(r["row_id"] for r in settle)))

    for number, docs in ix.invoice_docs_by_number.items():
        rows = [r for r in ix.rows_by_number[number] if ix.is_role(r, "ar")]
        balance = sum(signed(r) for r in rows)
        total = sum(d["total_minor"] for d in docs)
        ids = tuple(sorted(d["doc_id"] for d in docs)) + tuple(sorted(set(referencing[number])))
        if balance * (1 if total > 0 else -1) < 0:
            out.append(Violation("ar_overcredited", ids))
        if referenced[number] == total and balance != 0:
            out.append(Violation("ar_open_after_full_payment", ids))
        if abs(referenced[number]) > abs(total) or referenced[number] * total < 0:
            out.append(Violation("invoice_overpaid", ids))

    groups: Dict[Tuple[Any, ...], List[str]] = defaultdict(list)
    for doc in books.invoices:
        groups[invoice_signature(doc)].append(doc["doc_id"])
    for doc_ids in groups.values():
        if len(doc_ids) > 1:
            out.append(Violation("duplicate_signature", tuple(sorted(doc_ids))))
    return out


def detect_issues(books: PublicBooks) -> List[Detected]:
    """Reference detector over the full open data (evaluator-side self-check)."""
    ix = Index(books)
    found: List[Detected] = []
    fx_tol = ix.tolerance.get("fx_conversion", 1)

    groups: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = defaultdict(list)
    for doc in books.invoices:
        groups[invoice_signature(doc)].append(doc)
    for docs in groups.values():
        if len(docs) != 2:
            continue
        evidence = [d["doc_id"] for d in docs]
        for d in docs:
            ar = ix.invoice_ar_row(d)
            if ar is not None:
                evidence.append(ar["row_id"])
        found.append(Detected("duplicate_invoice", tuple(sorted(d["doc_id"] for d in docs)),
                              docs[0]["total_minor"], docs[0]["currency"], tuple(sorted(evidence))))

    for pay in books.payments:
        refs = pay["invoice_refs"]
        if refs is None or sum(ref["amount_minor"] for ref in refs) != pay["amount_minor"]:
            continue
        unapplied = [r for r in ix.rows_by_pid[pay["payment_id"]] if ix.is_role(r, "unapplied")]
        amount = -sum(signed(r) for r in unapplied)
        if unapplied and amount > 0:
            found.append(Detected("payment_allocation", (pay["doc_id"],), amount, ix.base,
                                  tuple(sorted([pay["doc_id"]] + [r["row_id"] for r in unapplied]))))

    for doc in books.invoices:
        ar = ix.invoice_ar_row(doc)
        if ar is None:
            continue
        if doc["currency"] != ix.base:
            diff = abs(abs(signed(ar)) - abs(ix.to_base(doc["total_minor"], doc["currency"], doc["issued_at"])))
            if diff > fx_tol:
                found.append(Detected("fx_conversion", (doc["doc_id"],), diff, ix.base,
                                      tuple(sorted((doc["doc_id"], ar["row_id"], ix.policy_fx_key(doc["currency"], doc["issued_at"]))))))
        if ix.period_class(ar["timestamp"]) != ix.period_class(doc["issued_at"]):
            found.append(Detected("period_cutoff", (doc["doc_id"],), abs(signed(ar)), ix.base,
                                  tuple(sorted((doc["doc_id"], ar["row_id"])))))

    for pay in books.payments:
        cash = ix.cash_rows(pay)
        if len(cash) != 1:
            continue
        row = cash[0]
        if pay["currency"] != ix.base:
            diff = abs(abs(signed(row)) - abs(ix.to_base(pay["amount_minor"], pay["currency"], pay["timestamp"])))
            if diff > fx_tol:
                found.append(Detected("fx_conversion", (pay["doc_id"],), diff, ix.base,
                                      tuple(sorted((pay["doc_id"], row["row_id"], ix.policy_fx_key(pay["currency"], pay["timestamp"]))))))
        if ix.period_class(row["timestamp"]) != ix.period_class(pay["timestamp"]):
            found.append(Detected("period_cutoff", (pay["doc_id"],), abs(signed(row)), ix.base,
                                  tuple(sorted((pay["doc_id"], row["row_id"])))))
    return sorted(found, key=lambda f: (f.type, f.affected_ids))


def _single_document(ix: Index, evidence_ids: Sequence[str]) -> Tuple[str, Dict[str, Any]]:
    docs = [("invoice", ix.invoices[e]) for e in evidence_ids if e in ix.invoices]
    docs += [("payment", ix.payments[e]) for e in evidence_ids if e in ix.payments]
    if len(docs) != 1:
        raise EvidenceError("evidence must contain exactly one document")
    return docs[0]


def _posted_row(ix: Index, kind: str, doc: Dict[str, Any], rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if kind == "invoice":
        row = ix.invoice_ar_row(doc, rows) or ix.invoice_counter_row(doc, rows)
    else:
        cash = ix.cash_rows(doc, rows)
        row = cash[0] if len(cash) == 1 else None
    if row is None:
        raise EvidenceError("evidence must include the document's posting row")
    return row


def verify_evidence(books: PublicBooks, issue_type: str, evidence_ids: Sequence[str]) -> Tuple[Tuple[str, ...], int, str]:
    """Re-derive (affected_ids, amount, currency) from the evidence records alone
    (plus manifest/policy and, for FX, the published rate table as reference data)."""
    ix = Index(books)
    unknown = [e for e in evidence_ids
               if e not in ix.rows and e not in ix.invoices and e not in ix.payments and e not in ix.fx_by_key]
    if unknown:
        raise EvidenceError(f"unknown evidence ids {unknown}")
    rows = [ix.rows[e] for e in evidence_ids if e in ix.rows]

    if issue_type == "duplicate_invoice":
        docs = [ix.invoices[e] for e in evidence_ids if e in ix.invoices]
        if len(docs) != 2 or invoice_signature(docs[0]) != invoice_signature(docs[1]):
            raise EvidenceError("duplicate evidence needs two invoices with identical content")
        posted = set()
        for d in docs:
            row = ix.invoice_ar_row(d, rows) or ix.invoice_counter_row(d, rows)
            if row is None:
                raise EvidenceError("duplicate evidence must show the posting of each copy")
            posted.add(row["row_id"])
        if len(posted) != 2:
            raise EvidenceError("both copies must be shown posted by distinct rows")
        return tuple(sorted(d["doc_id"] for d in docs)), docs[0]["total_minor"], docs[0]["currency"]

    if issue_type == "payment_allocation":
        pays = [ix.payments[e] for e in evidence_ids if e in ix.payments]
        if len(pays) != 1:
            raise EvidenceError("allocation evidence needs exactly one payment document")
        pay = pays[0]
        refs = pay["invoice_refs"]
        if refs is None or sum(ref["amount_minor"] for ref in refs) != pay["amount_minor"]:
            raise EvidenceError("payment must be fully referenced to invoices")
        unapplied = [r for r in rows if r["payment_id"] == pay["payment_id"] and ix.is_role(r, "unapplied")]
        amount = -sum(signed(r) for r in unapplied)
        if amount <= 0:
            raise EvidenceError("allocation evidence must show the unapplied booking")
        return (pay["doc_id"],), amount, ix.base

    if issue_type == "fx_conversion":
        kind, doc = _single_document(ix, evidence_ids)
        if doc["currency"] == ix.base:
            raise EvidenceError("document is in the ledger currency")
        row = _posted_row(ix, kind, doc, rows)
        ts = doc["issued_at"] if kind == "invoice" else doc["timestamp"]
        amount = doc["total_minor"] if kind == "invoice" else doc["amount_minor"]
        fx_ids = [e for e in evidence_ids if e in ix.fx_by_key]
        if fx_ids != [ix.policy_fx_key(doc["currency"], ts)]:
            raise EvidenceError("evidence must cite exactly the policy rate for the document date")
        rate = ix.fx_by_key[fx_ids[0]]["rate"]
        correct = money.convert(amount, doc["currency"], ix.base, rate, ix.rounding)
        diff = abs(abs(signed(row)) - abs(correct))
        if diff <= ix.tolerance.get("fx_conversion", 1):
            raise EvidenceError("booked amount matches the policy conversion")
        return (doc["doc_id"],), diff, ix.base

    if issue_type == "period_cutoff":
        kind, doc = _single_document(ix, evidence_ids)
        row = _posted_row(ix, kind, doc, rows)
        ts = doc["issued_at"] if kind == "invoice" else doc["timestamp"]
        if ix.period_class(row["timestamp"]) == ix.period_class(ts):
            raise EvidenceError("posting is in the document's own period")
        return (doc["doc_id"],), abs(signed(row)), ix.base

    raise EvidenceError(f"no evidence rule for {issue_type}")


def _issue_ids(issue: Dict[str, Any]) -> set:
    ids = set(issue["affected_ids"])
    for s in issue["evidence_sets"]:
        ids.update(s)
    return ids


def verify_episode(books: PublicBooks, truth: Dict[str, Any]) -> List[str]:
    """Problems found in a generated episode; an empty list means it is valid."""
    problems: List[str] = []
    issues = truth["issues"]
    n_rows = len(books.ledger)
    n_docs = len(books.invoices) + len(books.payments)
    if not LIMITS["ledger_rows"][0] <= n_rows <= LIMITS["ledger_rows"][1]:
        problems.append(f"ledger size {n_rows} outside {LIMITS['ledger_rows']}")
    if not LIMITS["documents"][0] <= n_docs <= LIMITS["documents"][1]:
        problems.append(f"document count {n_docs} outside {LIMITS['documents']}")
    if not LIMITS["issues"][0] <= len(issues) <= LIMITS["issues"][1]:
        problems.append(f"issue count {len(issues)} outside {LIMITS['issues']}")

    keys = [(i["type"], tuple(i["affected_ids"])) for i in issues]
    if len(set(keys)) != len(keys):
        problems.append("truth keys (type, affected_ids) are not unique")
    for issue in issues:
        if list(issue["affected_ids"]) != sorted(set(issue["affected_ids"])):
            problems.append(f"{issue['issue_id']}: affected_ids not canonical")
        if not issue["evidence_sets"]:
            problems.append(f"{issue['issue_id']}: no evidence sets")
        for evidence in issue["evidence_sets"]:
            if len(evidence) > LIMITS["evidence_ids"]:
                problems.append(f"{issue['issue_id']}: evidence set larger than {LIMITS['evidence_ids']}")
            try:
                derived = verify_evidence(books, issue["type"], evidence)
            except EvidenceError as exc:
                problems.append(f"{issue['issue_id']}: insufficient evidence {evidence}: {exc}")
                continue
            claimed = (tuple(issue["affected_ids"]), issue["expected_amount_minor"], issue["currency"])
            if derived[0] != claimed[0] or derived[2] != claimed[2] or abs(derived[1] - claimed[1]) > issue["tolerance_minor"]:
                problems.append(f"{issue['issue_id']}: evidence derives {derived}, truth says {claimed}")

    detected = {(d.type, d.affected_ids): d for d in detect_issues(books)}
    truth_by_key = {(i["type"], tuple(i["affected_ids"])): i for i in issues}
    for key in sorted(set(detected) - set(truth_by_key)):
        problems.append(f"undeclared discrepancy detected: {key}")
    for key in sorted(set(truth_by_key) - set(detected)):
        problems.append(f"declared issue not observable: {key}")
    for key in sorted(set(detected) & set(truth_by_key)):
        d, t = detected[key], truth_by_key[key]
        if d.currency != t["currency"] or abs(d.amount_minor - t["expected_amount_minor"]) > t["tolerance_minor"]:
            problems.append(f"{t['issue_id']}: observed amount {d.amount_minor} {d.currency} differs from truth")

    explained = [_issue_ids(i) for i in issues]
    for v in clean_invariants(books):
        if not any(set(v.ids) & ids for ids in explained):
            problems.append(f"unexplained violation {v.code} {v.ids}")
    return problems
