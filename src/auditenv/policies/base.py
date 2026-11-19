from typing import Any, Dict, List, Optional


class Policy:
    """A policy sees only observations and returns JSON actions."""

    name = "policy"
    requires_truth = False

    def reset(self, seed: int) -> None:
        self.seed = seed

    def act(self, observation: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError


def submit(episode_id: str, findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"action": "submit", "args": {"episode_id": episode_id, "findings": findings}}


def finding(type_: str, affected: List[str], amount: int, currency: str, evidence: List[str],
            explanation: str = "") -> Dict[str, Any]:
    return {
        "type": type_,
        "affected_ids": affected,
        "claimed_amount_minor": amount,
        "currency": currency,
        "evidence_ids": evidence,
        "explanation": explanation,
    }


class TableScanner:
    """Pages through the requested tables via list_records, collecting every row."""

    def __init__(self, tables: List[str]) -> None:
        self.pending = list(tables)
        self.rows: Dict[str, List[Dict[str, Any]]] = {t: [] for t in tables}
        self._cursor: Optional[str] = None
        self._awaiting: Optional[str] = None

    def feed(self, observation: Dict[str, Any]) -> None:
        if self._awaiting is None:
            return
        result = observation.get("result") or {}
        if observation.get("error") or result.get("table") != self._awaiting:
            self._cursor = None
            self.pending.pop(0)
        else:
            self.rows[self._awaiting].extend(result["records"])
            self._cursor = result["next_cursor"]
            if self._cursor is None:
                self.pending.pop(0)
        self._awaiting = None

    def skip_rest(self) -> None:
        self.pending.pop(0)
        self._cursor = None

    @property
    def done(self) -> bool:
        return not self.pending

    def next_action(self) -> Dict[str, Any]:
        table = self.pending[0]
        self._awaiting = table
        return {"action": "list_records", "args": {"table": table, "cursor": self._cursor, "limit": 50}}
