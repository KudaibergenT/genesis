"""One-to-one matching of findings to truth issues (published jury conventions).

An edge finding -> issue exists only if ALL hold:
  type equal (exact string); set(affected_ids) == the issue's canonical affected_ids;
  currency equal (exact string); |claimed - expected| <= tolerance_minor (integers);
  every evidence id exists in the open data (when the open id set is known); and the set of
  evidence ids contains at least one complete allowed evidence set.
`explanation` is never read.

A maximum-cardinality matching is chosen; among maximum matchings the one whose sequence of
(issue_id, finding index) pairs, sorted by issue_id, is lexicographically smallest. Repeated
findings stay separate vertices, so a repeat of a matched finding is an FP.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional

from auditenv.evaluator.schema import canonical_key


@dataclass
class MatchResult:
    tp: int
    fp: int
    fn: int
    findings: List[Dict[str, Any]] = field(default_factory=list)
    missed: List[str] = field(default_factory=list)


def _reject_reason(finding: Dict[str, Any], issue: Dict[str, Any], open_ids: Optional[FrozenSet[str]]) -> Optional[str]:
    if finding["currency"] != issue["currency"]:
        return "currency"
    if abs(finding["claimed_amount_minor"] - issue["expected_amount_minor"]) > issue["tolerance_minor"]:
        return "amount"
    evidence = set(finding["evidence_ids"])
    if open_ids is not None and not evidence <= open_ids:
        return "unknown_evidence"
    if not any(set(allowed) <= evidence for allowed in issue["evidence_sets"]):
        return "evidence"
    return None


def _max_matching(order: List[str], adj: Dict[str, List[int]], start: int, used: FrozenSet[int]) -> int:
    """Size of a maximum matching between issues order[start:] and findings not in `used` (Kuhn)."""
    owner: Dict[int, str] = {}

    def augment(issue: str, seen: set) -> bool:
        for f in adj[issue]:
            if f in used or f in seen:
                continue
            seen.add(f)
            if f not in owner or augment(owner[f], seen):
                owner[f] = issue
                return True
        return False

    return sum(1 for issue in order[start:] if augment(issue, set()))


def match(findings: List[Dict[str, Any]], issues: List[Dict[str, Any]],
          open_ids: Optional[FrozenSet[str]] = None) -> MatchResult:
    by_id = {i["issue_id"]: i for i in issues}
    order = sorted(by_id)
    reasons: Dict[int, str] = {}
    adj: Dict[str, List[int]] = {iid: [] for iid in order}
    for n, finding in enumerate(findings):
        key = canonical_key(finding)
        candidates = [i for i in issues if (i["type"], tuple(sorted(set(i["affected_ids"])))) == key]
        if not candidates:
            reasons[n] = "no_matching_issue"
        for issue in candidates:
            reason = _reject_reason(finding, issue, open_ids)
            if reason is None:
                adj[issue["issue_id"]].append(n)
            else:
                reasons.setdefault(n, reason)

    target = _max_matching(order, adj, 0, frozenset())
    assigned: Dict[str, int] = {}
    used: set = set()
    for k, iid in enumerate(order):
        for f in adj[iid]:
            if f in used:
                continue
            if len(assigned) + 1 + _max_matching(order, adj, k + 1, frozenset(used | {f})) == target:
                assigned[iid] = f
                used.add(f)
                break

    by_finding = {f: iid for iid, f in assigned.items()}
    report = []
    for n in range(len(findings)):
        if n in by_finding:
            report.append({"index": n, "status": "TP", "issue_id": by_finding[n]})
        elif any(n in adj[iid] for iid in order):
            report.append({"index": n, "status": "FP", "reason": "repeat"})
        else:
            report.append({"index": n, "status": "FP", "reason": reasons.get(n, "no_matching_issue")})
    tp = len(assigned)
    missed = sorted(iid for iid in order if iid not in assigned)
    return MatchResult(tp=tp, fp=len(findings) - tp, fn=len(missed), findings=report, missed=missed)
