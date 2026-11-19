"""Silence, oracle and flag-everything baselines."""

from typing import Any, Dict, List

from auditenv.config import ISSUE_TYPES
from auditenv.evaluator.schema import MAX_FINDINGS
from auditenv.policies.base import Policy, TableScanner, finding, submit


class SilencePolicy(Policy):
    name = "silence"

    def act(self, observation: Dict[str, Any]) -> Dict[str, Any]:
        return submit(self.episode_id(observation), [])

    def reset(self, seed: int) -> None:
        super().reset(seed)
        self._episode_id = None

    def episode_id(self, observation: Dict[str, Any]) -> str:
        if self._episode_id is None:
            self._episode_id = observation["manifest"]["episode_id"]
        return self._episode_id


class OraclePolicy(Policy):
    """Evaluator-only: submits the truth with its first allowed evidence set."""

    name = "oracle"
    requires_truth = True

    def bind_truth(self, truth: Dict[str, Any]) -> None:
        self._truth = truth

    def act(self, observation: Dict[str, Any]) -> Dict[str, Any]:
        findings = [
            finding(i["type"], list(i["affected_ids"]), i["expected_amount_minor"], i["currency"],
                    list(i["evidence_sets"][0]), "oracle")
            for i in self._truth["issues"]
        ]
        return submit(self._truth["episode_id"], findings)


class FlagEverythingPolicy(Policy):
    """Attack: reads the first page of documents and accuses every document of every issue type."""

    name = "flag_everything"

    def reset(self, seed: int) -> None:
        super().reset(seed)
        self._episode_id = None
        self._scanner = TableScanner(["invoices", "payments"])
        self._first = True

    def act(self, observation: Dict[str, Any]) -> Dict[str, Any]:
        if self._first:
            self._episode_id = observation["manifest"]["episode_id"]
            self._first = False
        else:
            self._scanner.feed(observation)
            if self._scanner.pending and self._scanner.rows[self._scanner.pending[0]]:
                self._scanner.skip_rest()
        if not self._scanner.done:
            return self._scanner.next_action()
        findings: List[Dict[str, Any]] = []
        docs = self._scanner.rows["invoices"] + self._scanner.rows["payments"]
        for doc in docs:
            amount = doc.get("total_minor", doc.get("amount_minor", 0))
            for issue_type in ISSUE_TYPES:
                findings.append(finding(issue_type, [doc["doc_id"]], amount, doc["currency"], [doc["doc_id"]], "flagged"))
        return submit(self._episode_id, findings[:MAX_FINDINGS])
