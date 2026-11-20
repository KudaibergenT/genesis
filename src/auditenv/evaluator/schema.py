"""Structural validation of findings.json (published jury conventions).

Structural errors (whole submit invalid, reward -1): wrong value type, missing required field,
more than 30 findings, more than 10 evidence ids in one finding (counted before de-duplication),
explanation longer than 1000 Unicode code points, episode_id of another episode.
Everything else (unknown type string, any currency string, negative amounts, unknown or
non-existent ids, empty affected_ids) is structurally valid and simply cannot match: it is an FP.
Unknown extra keys are ignored.
"""

from typing import Any, Dict, List, Tuple

MAX_FINDINGS = 30
MAX_EVIDENCE_IDS = 10
MAX_EXPLANATION_CHARS = 1000

FINDING_FIELDS = ("type", "affected_ids", "claimed_amount_minor", "currency", "evidence_ids", "explanation")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def validate_submission(obj: Any, episode_id: str) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    if not isinstance(obj, dict):
        return False, ["submission must be an object"]
    for key in ("episode_id", "findings"):
        if key not in obj:
            errors.append(f"missing required field {key}")
    if errors:
        return False, errors
    if not isinstance(obj["episode_id"], str):
        errors.append("episode_id must be a string")
    elif obj["episode_id"] != episode_id:
        errors.append("episode_id does not match the current episode")
    findings = obj["findings"]
    if not isinstance(findings, list):
        return False, errors + ["findings must be a list"]
    if len(findings) > MAX_FINDINGS:
        errors.append(f"at most {MAX_FINDINGS} findings are allowed")
    for n, f in enumerate(findings):
        where = f"findings[{n}]"
        if not isinstance(f, dict):
            errors.append(f"{where} must be an object")
            continue
        missing = [k for k in FINDING_FIELDS if k not in f]
        if missing:
            errors.append(f"{where} misses required fields {missing}")
            continue
        if not isinstance(f["type"], str):
            errors.append(f"{where}.type must be a string")
        if not _string_list(f["affected_ids"]):
            errors.append(f"{where}.affected_ids must be a list of strings")
        if not _string_list(f["evidence_ids"]):
            errors.append(f"{where}.evidence_ids must be a list of strings")
        elif len(f["evidence_ids"]) > MAX_EVIDENCE_IDS:
            errors.append(f"{where}.evidence_ids has more than {MAX_EVIDENCE_IDS} ids")
        if not _is_int(f["claimed_amount_minor"]):
            errors.append(f"{where}.claimed_amount_minor must be a JSON integer")
        if not isinstance(f["currency"], str):
            errors.append(f"{where}.currency must be a string")
        if not isinstance(f["explanation"], str):
            errors.append(f"{where}.explanation must be a string")
        elif len(f["explanation"]) > MAX_EXPLANATION_CHARS:
            errors.append(f"{where}.explanation is longer than {MAX_EXPLANATION_CHARS} characters")
    return not errors, errors


def canonical_key(finding: Dict[str, Any]) -> Tuple[str, Tuple[str, ...]]:
    return finding["type"], tuple(sorted(set(finding["affected_ids"])))
