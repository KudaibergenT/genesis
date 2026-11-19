"""One-to-one matching of findings to truth issues.

Normalization: affected_ids are de-duplicated and sorted; type, currency and IDs are
compared exactly. The truth guarantees (type, affected_ids) is unique per issue, so each
finding has at most one candidate. Findings are scanned in submission order and the
first finding that fully satisfies an unmatched issue becomes its TP; this greedy pass
is a maximum matching under that uniqueness and its tie-break is deterministic.
`explanation` is never read.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from auditenv.evaluator.schema import canonical_key


@dataclass
class MatchResult:
    tp: int
    fp: int
    fn: int
    findings: List[Dict[str, Any]] = field(default_factory=list)
    missed: List[str] = field(default_factory=list)


def _check(finding: Dict[str, Any], issue: Dict[str, Any]) -> Optional[str]:
    if finding["currency"] != issue["currency"]:
        return "currency"
    if abs(finding["claimed_amount_minor"] - issue["expected_amount_minor"]) > issue["tolerance_minor"]:
        return "amount"
    evidence = set(finding["evidence_ids"])
    if not any(set(allowed) <= evidence for allowed in issue["evidence_sets"]):
        return "evidence"
    return None


def match(findings: List[Dict[str, Any]], issues: List[Dict[str, Any]]) -> MatchResult:
    by_key = {(i["type"], tuple(i["affected_ids"])): i for i in issues}
    if len(by_key) != len(issues):
        raise ValueError("truth keys must be unique")
    matched: Dict[str, int] = {}
    report = []
    for n, finding in enumerate(findings):
        issue = by_key.get(canonical_key(finding))
        if issue is None:
            report.append({"index": n, "status": "FP", "reason": "no_matching_issue"})
            continue
        if issue["issue_id"] in matched:
            report.append({"index": n, "status": "FP", "reason": "repeat", "issue_id": issue["issue_id"]})
            continue
        reason = _check(finding, issue)
        if reason is not None:
            report.append({"index": n, "status": "FP", "reason": reason, "issue_id": issue["issue_id"]})
            continue
        matched[issue["issue_id"]] = n
        report.append({"index": n, "status": "TP", "issue_id": issue["issue_id"]})
    tp = len(matched)
    missed = sorted(i["issue_id"] for i in issues if i["issue_id"] not in matched)
    return MatchResult(tp=tp, fp=len(findings) - tp, fn=len(missed), findings=report, missed=missed)
