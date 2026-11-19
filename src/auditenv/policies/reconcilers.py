"""Scripted auditors that obtain every fact through the tool interface.

SimpleReconcilerPolicy: naive heuristics (same customer + amount => duplicate; any
unapplied receipt => allocation error). It is fooled by legitimate recurring invoices
and unreferenced receipts, which is exactly what the distractors are for.

ReferenceSolverPolicy: reads all open tables, then applies the published issue
conventions. Demonstrates that every declared issue is solvable within the step budget.
"""

from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from auditenv import checks, money
from auditenv.books import PublicBooks
from auditenv.policies.base import Policy, TableScanner, finding, submit


class _ScanningPolicy(Policy):
    tables = ["invoices", "payments", "ledger", "fx"]

    def reset(self, seed: int) -> None:
        super().reset(seed)
        self._scanner = TableScanner(list(self.tables))
        self._reset_obs: Optional[Dict[str, Any]] = None

    def act(self, observation: Dict[str, Any]) -> Dict[str, Any]:
        if self._reset_obs is None:
            self._reset_obs = observation
        else:
            self._scanner.feed(observation)
        if not self._scanner.done:
            return self._scanner.next_action()
        books = PublicBooks(
            manifest=self._reset_obs["manifest"],
            policy=self._reset_obs["policy"],
            ledger=self._scanner.rows.get("ledger", []),
            invoices=self._scanner.rows.get("invoices", []),
            payments=self._scanner.rows.get("payments", []),
            fx=self._scanner.rows.get("fx", []),
        )
        return submit(books.manifest["episode_id"], self.findings(books))

    def findings(self, books: PublicBooks) -> List[Dict[str, Any]]:
        raise NotImplementedError


class ReferenceSolverPolicy(_ScanningPolicy):
    name = "reference_solver"

    def findings(self, books: PublicBooks) -> List[Dict[str, Any]]:
        return [
            finding(d.type, list(d.affected_ids), d.amount_minor, d.currency, list(d.evidence_ids),
                    "identified from open records per policy.issue_conventions")
            for d in checks.detect_issues(books)
        ]


class SimpleReconcilerPolicy(_ScanningPolicy):
    name = "simple_reconciler"
    tables = ["invoices", "payments", "ledger", "fx"]

    def findings(self, books: PublicBooks) -> List[Dict[str, Any]]:
        ar = unapplied = None
        for name, role in books.policy["ledger"]["accounts"].items():
            if role == "ar":
                ar = name
            elif role == "unapplied":
                unapplied = name
        ar_debits: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for r in books.ledger:
            if r["account"] == ar and r["debit_minor"] > 0 and r["invoice_id"]:
                ar_debits[r["invoice_id"]].append(r)

        def posting(doc: Dict[str, Any]) -> List[str]:
            when = datetime.fromisoformat(doc["issued_at"])
            return [r["row_id"] for r in ar_debits[doc["invoice_id"]] if datetime.fromisoformat(r["timestamp"]) == when][:1]

        out: List[Dict[str, Any]] = []
        groups: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
        for doc in books.invoices:
            groups[(doc["entity_id"], doc["currency"], doc["total_minor"])].append(doc)
        for docs in groups.values():
            docs = sorted(docs, key=lambda d: (d["issued_at"], d["doc_id"]))
            for a, b in zip(docs, docs[1:]):
                out.append(finding("duplicate_invoice", sorted([a["doc_id"], b["doc_id"]]), a["total_minor"],
                                   a["currency"], [a["doc_id"], b["doc_id"]] + posting(a) + posting(b),
                                   "same customer and amount"))
        pay_by_pid = {p["payment_id"]: p for p in books.payments}
        for r in books.ledger:
            if r["account"] == unapplied and r["credit_minor"] > 0 and r["payment_id"] in pay_by_pid:
                doc = pay_by_pid[r["payment_id"]]
                out.append(finding("payment_allocation", [doc["doc_id"]], r["credit_minor"], r["currency"],
                                   [doc["doc_id"], r["row_id"]], "receipt held as unapplied"))
        out.extend(self._naive_fx_and_cutoff(books, ar))
        return out[:30]

    @staticmethod
    def _naive_fx_and_cutoff(books: PublicBooks, ar: str) -> List[Dict[str, Any]]:
        """Naive rules: rate of the exact calendar date in the timestamp string (or the next
        published one), and periods judged from the raw date prefix, ignoring offsets."""
        base = books.manifest["base_currency"]
        period_end = books.manifest["period_end"]
        rates = {(r["date"], r["source_currency"]): r for r in books.fx}
        dates = sorted({r["date"] for r in books.fx})
        cash = None
        for name, role in books.policy["ledger"]["accounts"].items():
            if role == "cash":
                cash = name
        posting: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
        for r in books.ledger:
            if r["account"] == ar and r["invoice_id"] and r["payment_id"] is None:
                posting[("inv", r["invoice_id"])].append(r)
            elif r["account"] == cash and r["payment_id"]:
                posting[("pay", r["payment_id"])].append(r)
        out: List[Dict[str, Any]] = []
        docs = [("inv", d["invoice_id"], d, d["issued_at"], d["total_minor"]) for d in books.invoices]
        docs += [("pay", p["payment_id"], p, p["timestamp"], p["amount_minor"]) for p in books.payments]
        for kind, ref, doc, ts, amount in docs:
            rows = sorted(posting.get((kind, ref), []), key=lambda r: datetime.fromisoformat(r["timestamp"]))
            same_instant = [r for r in rows if datetime.fromisoformat(r["timestamp"]) == datetime.fromisoformat(ts)]
            row = (same_instant or rows or [None])[0]
            if row is None:
                continue
            booked = row["debit_minor"] or row["credit_minor"]
            if doc["currency"] != base and amount > 0:
                day = ts[:10]
                fx = rates.get((day, doc["currency"])) or next(
                    (rates[(d, doc["currency"])] for d in dates if d > day and (d, doc["currency"]) in rates), None)
                if fx is not None:
                    expected = money.convert(amount, doc["currency"], base, fx["rate"], "ROUND_HALF_UP")
                    if expected != booked:
                        key = f"{fx['date']}|{fx['source_currency']}|{fx['target_currency']}"
                        out.append(finding("fx_conversion", [doc["doc_id"]], abs(expected - booked), base,
                                           [doc["doc_id"], row["row_id"], key], "booked amount differs from rate"))
            if (ts[:10] <= period_end) != (row["timestamp"][:10] <= period_end):
                out.append(finding("period_cutoff", [doc["doc_id"]], booked, base, [doc["doc_id"], row["row_id"]],
                                   "posting date on the other side of period end"))
        return out
