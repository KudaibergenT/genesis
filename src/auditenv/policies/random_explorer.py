"""Fixed-seed random explorer: random (often invalid) tool calls, then a random guess."""

from typing import Any, Dict, List

from auditenv.books import TABLES
from auditenv.config import ISSUE_TYPES
from auditenv.policies.base import Policy, finding, submit
from auditenv.rng import Rng


class RandomExplorerPolicy(Policy):
    name = "random_explorer"

    def reset(self, seed: int) -> None:
        super().reset(seed)
        self._rng = Rng("random-explorer", seed)
        self._budget = self._rng.randint(5, 40)
        self._episode_id = None
        self._ids: List[str] = []
        self._cursors: List[str] = []
        self._amounts: List[int] = []
        self._currencies: List[str] = []

    def _observe(self, observation: Dict[str, Any]) -> None:
        result = observation.get("result")
        if not isinstance(result, dict):
            return
        records = result.get("records") or ([result["record"]] if "record" in result else [])
        if "document" in result:
            records = [result["document"]]
        for r in records:
            for key in ("row_id", "doc_id"):
                if key in r:
                    self._ids.append(r[key])
            for key in ("total_minor", "amount_minor", "debit_minor", "credit_minor"):
                if isinstance(r.get(key), int) and r[key] > 0:
                    self._amounts.append(r[key])
            if "currency" in r:
                self._currencies.append(r["currency"])
        if result.get("next_cursor"):
            self._cursors.append(result["next_cursor"])

    def act(self, observation: Dict[str, Any]) -> Dict[str, Any]:
        rng = self._rng
        if self._episode_id is None:
            self._episode_id = observation["manifest"]["episode_id"]
        self._observe(observation)
        if self._budget <= 0:
            return self._guess()
        self._budget -= 1
        kind = rng.weighted((("list", 40), ("get", 20), ("doc", 15), ("calc", 10), ("junk", 15)))
        if kind == "list":
            cursor = rng.choice(self._cursors) if self._cursors and rng.chance(400) else None
            return {"action": "list_records", "args": {"table": rng.choice(TABLES + ("bogus",)),
                                                         "cursor": cursor, "limit": rng.randint(1, 60)}}
        if kind == "get":
            rid = rng.choice(self._ids) if self._ids and rng.chance(700) else f"X{rng.randint(0, 99999)}"
            return {"action": "get_record", "args": {"table": rng.choice(TABLES), "id": rid}}
        if kind == "doc":
            rid = rng.choice(self._ids) if self._ids and rng.chance(700) else f"D{rng.randint(0, 99999)}"
            return {"action": "get_document", "args": {"doc_id": rid}}
        if kind == "calc":
            a, b = rng.randint(1, 10 ** 6), rng.randint(0, 999)
            return {"action": "calculator", "args": {"expression": f"{a} {rng.choice('+-*/')} {b}"}}
        return rng.choice((
            {"action": "open_shell", "args": {}},
            {"action": "list_records", "args": {"table": "truth"}},
            {"action": "get_record"},
            "not json",
            {"action": "calculator", "args": {"expression": "__import__('os')"}},
        ))

    def _guess(self) -> Dict[str, Any]:
        rng = self._rng
        findings = []
        if self._ids and rng.chance(500):
            for _ in range(rng.randint(1, 3)):
                findings.append(finding(
                    rng.choice(ISSUE_TYPES),
                    [rng.choice(self._ids)],
                    rng.choice(self._amounts) if self._amounts else 0,
                    rng.choice(self._currencies) if self._currencies else "USD",
                    rng.sample(self._ids, min(len(self._ids), rng.randint(0, 4))),
                    "random guess",
                ))
        return submit(self._episode_id, findings)
