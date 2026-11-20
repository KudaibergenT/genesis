"""Organizer policies written against the published action envelope only (no auditenv import).

Each policy: reset(seed) and act(observation) -> {"name": ..., "args": {...}}.
"""

import random
from typing import Any, Dict, List, Optional

TYPES = ("duplicate_invoice", "payment_allocation", "fx_conversion", "period_cutoff")
TABLES = ("ledger", "invoices", "payments", "fx")


def submit(episode_id: str, findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"name": "submit", "args": {"findings": {"episode_id": episode_id, "findings": findings}}}


def finding(t: str, affected: List[str], amount: int, currency: str, evidence: List[str]) -> Dict[str, Any]:
    return {"type": t, "affected_ids": affected, "claimed_amount_minor": amount, "currency": currency,
            "evidence_ids": evidence, "explanation": "jury baseline"}


class Scanner:
    """Reads tables page by page through list_records."""

    def __init__(self, tables, first_page_only: bool = False) -> None:
        self.pending = list(tables)
        self.rows: Dict[str, List[Dict[str, Any]]] = {t: [] for t in tables}
        self.cursor: Optional[str] = None
        self.first_only = first_page_only
        self.waiting: Optional[str] = None

    def feed(self, obs: Dict[str, Any]) -> None:
        if self.waiting is None:
            return
        result = obs.get("result")
        if obs.get("error") or not isinstance(result, dict):
            self.pending.pop(0)
            self.cursor = None
        else:
            self.rows[self.waiting] += result["records"]
            self.cursor = result["next_cursor"]
            if self.cursor is None or self.first_only:
                self.pending.pop(0)
                self.cursor = None
        self.waiting = None

    def next(self) -> Optional[Dict[str, Any]]:
        if not self.pending:
            return None
        self.waiting = self.pending[0]
        return {"name": "list_records", "args": {"table": self.waiting, "cursor": self.cursor, "limit": 50}}


class Base:
    def reset(self, seed: int) -> None:
        self.seed = seed
        self.episode_id: Optional[str] = None
        self.base: Optional[str] = None

    def first(self, obs: Dict[str, Any]) -> bool:
        if self.episode_id is None and "manifest" in obs:
            self.episode_id = obs["manifest"]["episode_id"]
            self.base = obs["manifest"]["base_currency"]
            return True
        return False


class RandomExplorer(Base):
    name = "random_explorer"

    def reset(self, seed: int) -> None:
        super().reset(seed)
        self.rng = random.Random(seed)
        self.budget = self.rng.randint(10, 30)
        self.ids: List[str] = []

    def act(self, obs: Dict[str, Any]) -> Dict[str, Any]:
        self.first(obs)
        result = obs.get("result")
        records = result.get("records", []) if isinstance(result, dict) and "records" in result else (
            [result] if isinstance(result, dict) else [])
        for r in records:
            for key in ("row_id", "doc_id"):
                if key in r:
                    self.ids.append(r[key])
        if self.budget <= 0:
            found = []
            for _ in range(self.rng.randint(0, 3) if self.ids else 0):
                doc = self.rng.choice(self.ids)
                found.append(finding(self.rng.choice(TYPES), [doc], self.rng.randint(1, 10 ** 6), self.base, [doc]))
            return submit(self.episode_id, found)
        self.budget -= 1
        kind = self.rng.choice(("list", "list", "get", "doc", "calc"))
        if kind == "list":
            return {"name": "list_records", "args": {"table": self.rng.choice(TABLES), "cursor": None,
                                                      "limit": self.rng.randint(1, 50)}}
        target = self.rng.choice(self.ids) if self.ids else "UNKNOWN"
        if kind == "get":
            return {"name": "get_record", "args": {"table": self.rng.choice(TABLES), "id": target}}
        if kind == "doc":
            return {"name": "get_document", "args": {"doc_id": target}}
        return {"name": "calculator", "args": {"expression": f"{self.rng.randint(1, 999)}/{self.rng.randint(1, 99)}"}}


class SimpleReconciliation(Base):
    """Reads everything, then flags equal-amount invoices of one customer and receipts parked in the
    unapplied-receipts account although their remittance names invoices."""
    name = "simple_reconciliation"

    def reset(self, seed: int) -> None:
        super().reset(seed)
        self.scan = Scanner(("invoices", "payments", "ledger"))
        self.policy: Dict[str, Any] = {}

    def act(self, obs: Dict[str, Any]) -> Dict[str, Any]:
        if self.first(obs):
            self.policy = obs["policy"]
        else:
            self.scan.feed(obs)
        action = self.scan.next()
        if action:
            return action
        roles = {role: name for name, role in self.policy["ledger"]["accounts"].items()}
        found: List[Dict[str, Any]] = []
        groups: Dict[Any, List[Dict[str, Any]]] = {}
        for d in self.scan.rows["invoices"]:
            groups.setdefault((d["entity_id"], d["currency"], d["total_minor"]), []).append(d)
        for docs in groups.values():
            for a, b in zip(docs, docs[1:]):
                found.append(finding("duplicate_invoice", sorted([a["doc_id"], b["doc_id"]]), a["total_minor"],
                                     a["currency"], [a["doc_id"], b["doc_id"]]))
        pay = {p["payment_id"]: p for p in self.scan.rows["payments"]}
        for r in self.scan.rows["ledger"]:
            p = pay.get(r["payment_id"])
            if r["account"] == roles.get("unapplied") and r["credit_minor"] > 0 and p and p["invoice_refs"]:
                found.append(finding("payment_allocation", [p["doc_id"]], r["credit_minor"], r["currency"],
                                     [p["doc_id"], r["row_id"]]))
        return submit(self.episode_id, found[:30])


class MarkEverything(Base):
    name = "mark_everything"

    def reset(self, seed: int) -> None:
        super().reset(seed)
        self.scan = Scanner(("invoices", "payments"), first_page_only=True)

    def act(self, obs: Dict[str, Any]) -> Dict[str, Any]:
        if not self.first(obs):
            self.scan.feed(obs)
        action = self.scan.next()
        if action:
            return action
        found = []
        for d in self.scan.rows["invoices"] + self.scan.rows["payments"]:
            for t in TYPES:
                found.append(finding(t, [d["doc_id"]], abs(d.get("total_minor", d.get("amount_minor", 0))),
                                     d["currency"], [d["doc_id"]]))
        return submit(self.episode_id, found[:30])


POLICIES = {cls.name: cls for cls in (RandomExplorer, SimpleReconciliation, MarkEverything)}
