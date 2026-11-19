"""Evaluator: holds the closed truth and grades a submission. Lives outside the agent."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

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


class Grader:
    def __init__(self, truth: Dict[str, Any]) -> None:
        self._truth = truth
        self._episode_id = truth["episode_id"]

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
        result = match(findings, issues)
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


__all__ = ["Grade", "Grader", "match", "metrics", "official_reward", "validate_submission"]
