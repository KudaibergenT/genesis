"""Independent checks over OPEN episode files only.

This module must never import `auditenv.generator`: it re-derives accounting equalities,
declared discrepancies and evidence sufficiency from what an agent could read.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from auditenv import money, timeutil
from auditenv.books import PublicBooks

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


class Index:
    def __init__(self, books: PublicBooks) -> None:
        self.books = books
        m = books.manifest
        self.base: str = m["base_currency"]
        self.zone: str = m["timezone"]
        self.rounding: str = m["rounding"]["mode"]
        self.period = (date.fromisoformat(m["period_start"]), date.fromisoformat(m["period_end"]))
        self.acc = {role: name for name, role in books.policy["ledger"]["accounts"].items()}
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
        self.fx: Dict[str, List[Tuple[date, str]]] = defaultdict(list)
        for r in books.fx:
            if r["target_currency"] == self.base:
                self.fx[r["source_currency"]].append((date.fromisoformat(r["date"]), r["rate"]))
        for series in self.fx.values():
            series.sort()

    def local_date(self, ts: str) -> date:
        return timeutil.local_date(timeutil.parse_iso(ts), self.zone)

    def rate(self, currency: str, on: date) -> str:
        if currency == self.base:
            return "1"
        best = None
        for d, rate in self.fx.get(currency, []):
            if d <= on:
                best = rate
            else:
                break
        if best is None:
            raise LookupError(f"no rate for {currency} on {on}")
        return best

    def to_base(self, amount: int, currency: str, ts: str) -> int:
        return money.convert(amount, currency, self.base, self.rate(currency, self.local_date(ts)), self.rounding)

    def is_role(self, row: Dict[str, Any], role: str) -> bool:
        return row["account"] == self.acc[role]


def _same_instant(a: str, b: str) -> bool:
    return timeutil.parse_iso(a) == timeutil.parse_iso(b)


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
        entries[timeutil.parse_iso(r["timestamp"])].append(r)
    for rows in entries.values():
        if sum(r["debit_minor"] for r in rows) != sum(r["credit_minor"] for r in rows):
            out.append(Violation("entry_unbalanced", tuple(sorted(r["row_id"] for r in rows))))
    if sum(r["debit_minor"] for r in books.ledger) != sum(r["credit_minor"] for r in books.ledger):
        out.append(Violation("ledger_unbalanced", ()))

    for doc in books.invoices:
        if any(li["amount_minor"] != li["quantity"] * li["unit_price_minor"] for li in doc["line_items"]) or \
                doc["total_minor"] != sum(li["amount_minor"] for li in doc["line_items"]):
            out.append(Violation("invoice_arithmetic", (doc["doc_id"],)))
        if not ix.period[0] <= ix.local_date(doc["issued_at"]) <= ix.period[1]:
            out.append(Violation("invoice_out_of_period", (doc["doc_id"],)))

    for number, docs in ix.invoice_docs_by_number.items():
        doc_ids = tuple(sorted(d["doc_id"] for d in docs))
        rows = ix.rows_by_number[number]
        ar_debits = [r for r in rows if ix.is_role(r, "ar") and r["debit_minor"] > 0]
        revenue = [r for r in rows if ix.is_role(r, "revenue")]
        if len(ar_debits) != len(docs):
            out.append(Violation("invoice_posting_count", doc_ids + tuple(r["row_id"] for r in ar_debits)))
        expected = sorted(ix.to_base(d["total_minor"], d["currency"], d["issued_at"]) for d in docs)
        if sorted(r["debit_minor"] for r in ar_debits) != expected:
            out.append(Violation("invoice_posting_amount", doc_ids))
        if sum(r["credit_minor"] for r in revenue) != sum(r["debit_minor"] for r in ar_debits):
            out.append(Violation("revenue_mismatch", doc_ids))
        for r in ar_debits:
            if r["entity_id"] != docs[0]["entity_id"]:
                out.append(Violation("entity_mismatch", doc_ids + (r["row_id"],)))

    referenced: Dict[str, int] = defaultdict(int)
    referencing: Dict[str, List[str]] = defaultdict(list)
    for pay in books.payments:
        pid, doc_id = pay["payment_id"], pay["doc_id"]
        rows = ix.rows_by_pid[pid]
        cash = [r for r in rows if ix.is_role(r, "cash")]
        unapplied = [r for r in rows if ix.is_role(r, "unapplied")]
        if not ix.period[0] <= ix.local_date(pay["timestamp"]) <= ix.period[1]:
            out.append(Violation("payment_out_of_period", (doc_id,)))
        if len(cash) != 1 or cash[0]["debit_minor"] != ix.to_base(pay["amount_minor"], pay["currency"], pay["timestamp"]):
            out.append(Violation("payment_cash", (doc_id,) + tuple(r["row_id"] for r in cash)))
        refs = pay["invoice_refs"]
        if refs is None:
            if sum(r["credit_minor"] for r in unapplied) != sum(r["debit_minor"] for r in cash):
                out.append(Violation("unapplied_mismatch", (doc_id,) + tuple(r["row_id"] for r in unapplied)))
            continue
        if sum(ref["amount_minor"] for ref in refs) != pay["amount_minor"]:
            out.append(Violation("payment_refs_sum", (doc_id,)))
        if unapplied:
            out.append(Violation("unapplied_on_referenced_payment", (doc_id,) + tuple(r["row_id"] for r in unapplied)))
        for ref in refs:
            number = ref["invoice_id"]
            referenced[number] += ref["amount_minor"]
            referencing[number].append(doc_id)
            docs = ix.invoice_docs_by_number.get(number, [])
            doc_ids = tuple(d["doc_id"] for d in docs)
            credits = [r for r in rows if ix.is_role(r, "ar") and r["credit_minor"] > 0 and r["invoice_id"] == number]
            if not docs:
                out.append(Violation("payment_ref_unknown_invoice", (doc_id,)))
                continue
            if not credits:
                out.append(Violation("missing_ar_credit", (doc_id,) + doc_ids))
                continue
            inv = docs[0]
            expected = ix.to_base(ref["amount_minor"], inv["currency"], inv["issued_at"])
            if abs(sum(r["credit_minor"] for r in credits) - expected) > 1:
                out.append(Violation("ar_credit_amount", (doc_id,) + doc_ids + tuple(r["row_id"] for r in credits)))

    for number, docs in ix.invoice_docs_by_number.items():
        rows = [r for r in ix.rows_by_number[number] if ix.is_role(r, "ar")]
        balance = sum(r["debit_minor"] for r in rows) - sum(r["credit_minor"] for r in rows)
        ids = tuple(sorted(d["doc_id"] for d in docs)) + tuple(sorted(set(referencing[number])))
        if balance < 0:
            out.append(Violation("ar_overcredited", ids))
        if referenced[number] == sum(d["total_minor"] for d in docs) and balance != 0:
            out.append(Violation("ar_open_after_full_payment", ids))
        if referenced[number] > sum(d["total_minor"] for d in docs):
            out.append(Violation("invoice_overpaid", ids))

    groups: Dict[Tuple[Any, ...], List[str]] = defaultdict(list)
    for doc in books.invoices:
        groups[invoice_signature(doc)].append(doc["doc_id"])
    for doc_ids in groups.values():
        if len(doc_ids) > 1:
            out.append(Violation("duplicate_signature", tuple(sorted(doc_ids))))
    return out


def _posting_row_for(ix: Index, doc: Dict[str, Any], candidates: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for r in candidates:
        if r["invoice_id"] != doc["invoice_id"] or not _same_instant(r["timestamp"], doc["issued_at"]):
            continue
        if (ix.is_role(r, "ar") and r["debit_minor"] > 0) or (ix.is_role(r, "revenue") and r["credit_minor"] > 0):
            return r
    return None


def detect_issues(books: PublicBooks) -> List[Detected]:
    """Reference detector over the full open data (evaluator-side self-check)."""
    ix = Index(books)
    found: List[Detected] = []

    groups: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = defaultdict(list)
    for doc in books.invoices:
        groups[invoice_signature(doc)].append(doc)
    for docs in groups.values():
        if len(docs) != 2:
            continue
        evidence = [d["doc_id"] for d in docs]
        for d in docs:
            row = _posting_row_for(ix, d, (r for r in ix.rows_by_number[d["invoice_id"]] if ix.is_role(r, "ar")))
            if row is not None:
                evidence.append(row["row_id"])
        found.append(Detected("duplicate_invoice", tuple(sorted(d["doc_id"] for d in docs)),
                              docs[0]["total_minor"], docs[0]["currency"], tuple(sorted(evidence))))

    for pay in books.payments:
        refs = pay["invoice_refs"]
        if refs is None or sum(ref["amount_minor"] for ref in refs) != pay["amount_minor"]:
            continue
        unapplied = [r for r in ix.rows_by_pid[pay["payment_id"]] if ix.is_role(r, "unapplied") and r["credit_minor"] > 0]
        if unapplied:
            found.append(Detected("payment_allocation", (pay["doc_id"],), sum(r["credit_minor"] for r in unapplied),
                                  ix.base, tuple(sorted([pay["doc_id"]] + [r["row_id"] for r in unapplied]))))
    return sorted(found, key=lambda f: (f.type, f.affected_ids))


def verify_evidence(books: PublicBooks, issue_type: str, evidence_ids: Sequence[str]) -> Tuple[Tuple[str, ...], int, str]:
    """Re-derive (affected_ids, amount, currency) from the evidence records alone."""
    ix = Index(books)
    unknown = [e for e in evidence_ids if e not in ix.rows and e not in ix.invoices and e not in ix.payments]
    if unknown:
        raise EvidenceError(f"unknown evidence ids {unknown}")
    rows = [ix.rows[e] for e in evidence_ids if e in ix.rows]
    if issue_type == "duplicate_invoice":
        docs = [ix.invoices[e] for e in evidence_ids if e in ix.invoices]
        if len(docs) != 2 or invoice_signature(docs[0]) != invoice_signature(docs[1]):
            raise EvidenceError("duplicate evidence needs two invoices with identical content")
        for d in docs:
            if _posting_row_for(ix, d, rows) is None:
                raise EvidenceError("duplicate evidence must show the posting of each copy")
        posted = {_posting_row_for(ix, d, rows)["row_id"] for d in docs}  # type: ignore[index]
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
        amount = sum(r["credit_minor"] for r in unapplied)
        if amount <= 0:
            raise EvidenceError("allocation evidence must show the unapplied booking")
        return (pay["doc_id"],), amount, ix.base
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
