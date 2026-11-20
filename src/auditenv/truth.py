"""The truth.json contract between generator, evaluator and jury fixtures.

Written by the generator (schemas/truth.schema.json): {"schema_version", "episode_id", "issues": [...]},
each issue with issue_id, type, affected_ids, expected_amount_minor, currency, tolerance_minor
(= policy.issue_conventions[type].tolerance_minor) and evidence_sets.

Accepted by the evaluator (schemas/truth_input.schema.json): the fields of case section 03 are enough.
  - tolerance_minor absent -> 0 (the jury default: "по умолчанию ... τ=0");
  - episode_id absent -> the episode_id of manifest.json; present but different -> error;
  - a bare list of issues is accepted as {"issues": list}.
Malformed truth raises TruthFormatError with the schema violations (never a KeyError mid-matching).
"""

import copy
from typing import Any, Dict, List, Optional

from auditenv.jsonschema_lite import load, validate
from auditenv.version import SCHEMA_VERSION

DEFAULT_TOLERANCE_MINOR = 0


class TruthFormatError(ValueError):
    pass


def normalize_truth(raw: Any, manifest_episode_id: Optional[str] = None) -> Dict[str, Any]:
    schema = load("truth_input")
    wrapped = {"issues": raw} if isinstance(raw, list) else raw
    errors = validate(wrapped, {"$defs": schema["$defs"], "$ref": "#/$defs/truth_object"})
    if errors:
        raise TruthFormatError("truth.json does not match schemas/truth_input.schema.json: " + "; ".join(errors[:8]))
    issues: List[Dict[str, Any]] = raw if isinstance(raw, list) else raw["issues"]
    declared = raw.get("episode_id") if isinstance(raw, dict) else None
    if manifest_episode_id is not None and declared is not None and declared != manifest_episode_id:
        raise TruthFormatError(f"truth.json episode_id {declared!r} differs from manifest {manifest_episode_id!r}")
    episode_id = manifest_episode_id if manifest_episode_id is not None else declared
    if episode_id is None:
        raise TruthFormatError("episode_id is unknown: give it in truth.json or provide the manifest")
    out = []
    for issue in issues:
        item = copy.deepcopy(issue)
        item.setdefault("tolerance_minor", DEFAULT_TOLERANCE_MINOR)
        out.append(item)
    ids = [i["issue_id"] for i in out]
    if len(ids) != len(set(ids)):
        raise TruthFormatError("issue_id values must be unique")
    schema_version = raw.get("schema_version", SCHEMA_VERSION) if isinstance(raw, dict) else SCHEMA_VERSION
    return {"schema_version": schema_version, "episode_id": episode_id, "issues": out}
