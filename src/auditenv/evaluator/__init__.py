"""Evaluator: holds the closed truth and grades a submission. Lives outside the agent."""

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional

from auditenv.evaluator.matcher import match
from auditenv.evaluator.reward import metrics, official_reward
from auditenv.evaluator.schema import validate_submission


@dataclass
class Grade:
    reward: float
    valid: bool
    outcome: str
    inspection_steps: int
    metrics: Dict[str, Any]
    errors: List[str] = field(default_factory=list)
    details: Optional[Dict[str, Any]] = None
    experimental: Dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "reward": self.reward,
            "valid": self.valid,
            "outcome": self.outcome,
            "inspection_steps": self.inspection_steps,
            "metrics": self.metrics,
            "errors": self.errors,
            "details": self.details,
            "experimental": self.experimental,
        }


def open_ids(public) -> FrozenSet[str]:
    """Every id an agent can reach through the tools (evidence ids must be among them)."""
    from auditenv.books import fx_key

    ids = {r["row_id"] for r in public.ledger}
    ids.update(d["doc_id"] for d in public.invoices)
    ids.update(d["doc_id"] for d in public.payments)
    ids.update(fx_key(r) for r in public.fx)
    return frozenset(ids)


class Grader:
    def __init__(self, truth: Any, known_ids: Optional[FrozenSet[str]] = None,
                 episode_id: Optional[str] = None) -> None:
        from auditenv.truth import normalize_truth

        self._truth = normalize_truth(truth, episode_id)
        self._episode_id = self._truth["episode_id"]
        self._known_ids = known_ids

    def grade(self, submission: Any, inspection_steps: int, outcome: str = "submitted") -> Grade:
        """outcome: 'submitted' or 'truncated' (step/time limit: empty final answer)."""
        issues = self._truth["issues"]
        if outcome == "truncated":
            submission = {"episode_id": self._episode_id, "findings": []}
        ok, errors = validate_submission(submission, self._episode_id)
        if not ok:
            return Grade(
                reward=-1.0,
                valid=False,
                outcome="malformed",
                inspection_steps=inspection_steps,
                metrics={"tp": 0, "fp": 0, "fn": len(issues), "precision": 0.0, "recall": 0.0, "f1": 0.0, "reward": -1.0},
                errors=errors,
                experimental={"strict_timeout_reward": -1.0},
            )
        findings = submission["findings"]
        result = match(findings, issues, self._known_ids)
        m = metrics(result.tp, result.fp, result.fn, len(issues), len(findings), inspection_steps)
        strict = -1.0 if outcome == "truncated" else m["reward"]
        return Grade(
            reward=m["reward"],
            valid=True,
            outcome=outcome,
            inspection_steps=inspection_steps,
            metrics=m,
            details={"findings": result.findings, "missed": result.missed},
            experimental={"strict_timeout_reward": strict},
        )


__all__ = ["Grade", "Grader", "match", "metrics", "official_reward", "open_ids", "validate_submission"]
