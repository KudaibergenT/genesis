"""Strict validation of findings.json. Any violation makes the whole submission malformed."""

import re
from typing import Any, Dict, List, Tuple

from auditenv.config import ISSUE_TYPES

MAX_FINDINGS = 30
MAX_EVIDENCE_IDS = 10
MAX_EXPLANATION_CHARS = 1000
MAX_AFFECTED_IDS = 20
MAX_ID_CHARS = 64
MAX_AMOUNT_MINOR = 10 ** 15

SUBMISSION_KEYS = frozenset({"episode_id", "findings"})
FINDING_KEYS = frozenset({"type", "affected_ids", "claimed_amount_minor", "currency", "evidence_ids", "explanation"})
_CURRENCY = re.compile(r"^[A-Z]{3}$")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _id_list(value: Any, where: str, min_len: int, max_len: int, errors: List[str]) -> None:
    if not isinstance(value, list):
        errors.append(f"{where} must be a list")
        return
    if not min_len <= len(value) <= max_len:
        errors.append(f"{where} must contain {min_len}..{max_len} ids")
    for item in value:
        if not isinstance(item, str) or not 1 <= len(item) <= MAX_ID_CHARS:
            errors.append(f"{where} must contain non-empty strings of at most {MAX_ID_CHARS} chars")
            return


def validate_submission(obj: Any, episode_id: str) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    if not isinstance(obj, dict):
        return False, ["submission must be an object"]
    if set(obj) != SUBMISSION_KEYS:
        return False, [f"submission keys must be exactly {sorted(SUBMISSION_KEYS)}"]
    if obj["episode_id"] != episode_id:
        errors.append("episode_id does not match the current episode")
    findings = obj["findings"]
    if not isinstance(findings, list):
        return False, errors + ["findings must be a list"]
    if len(findings) > MAX_FINDINGS:
        errors.append(f"at most {MAX_FINDINGS} findings are allowed")
    for n, f in enumerate(findings):
        where = f"findings[{n}]"
        if not isinstance(f, dict) or set(f) != FINDING_KEYS:
            errors.append(f"{where} keys must be exactly {sorted(FINDING_KEYS)}")
            continue
        if f["type"] not in ISSUE_TYPES:
            errors.append(f"{where}.type must be one of {list(ISSUE_TYPES)}")
        _id_list(f["affected_ids"], f"{where}.affected_ids", 1, MAX_AFFECTED_IDS, errors)
        _id_list(f["evidence_ids"], f"{where}.evidence_ids", 0, MAX_EVIDENCE_IDS, errors)
        amount = f["claimed_amount_minor"]
        if not _is_int(amount) or not 0 <= amount <= MAX_AMOUNT_MINOR:
            errors.append(f"{where}.claimed_amount_minor must be an integer in [0, {MAX_AMOUNT_MINOR}]")
        if not isinstance(f["currency"], str) or not _CURRENCY.match(f["currency"]):
            errors.append(f"{where}.currency must be a 3-letter ISO code")
        if not isinstance(f["explanation"], str) or len(f["explanation"]) > MAX_EXPLANATION_CHARS:
            errors.append(f"{where}.explanation must be a string of at most {MAX_EXPLANATION_CHARS} chars")
    return not errors, errors


def canonical_key(finding: Dict[str, Any]) -> Tuple[str, Tuple[str, ...]]:
    return finding["type"], tuple(sorted(set(finding["affected_ids"])))
