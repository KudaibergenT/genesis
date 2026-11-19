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

from auditenv import checks
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
    tables = ["invoices", "payments", "ledger"]

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
        return out[:30]
