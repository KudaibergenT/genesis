"""Named generator configurations used by tests, audits and reports."""

import copy
from typing import Any, Dict, Iterator, List, Tuple

from auditenv.config import ERROR_VARIANTS, INTERACTIONS, ISSUE_TYPES
from auditenv.timeutil import ZONE_OFFSETS_MINUTES

NO_INTERACTIONS = {name: {"count": 0} for name in INTERACTIONS}
BOTH_INTERACTIONS = {name: {"count": 1} for name in INTERACTIONS}


def only(**counts: int) -> Dict[str, Any]:
    return {"errors": {t: {"count": counts.get(t, 0)} for t in ISSUE_TYPES}, "interactions": NO_INTERACTIONS}


CLEAN = only()


def exactly(k: int) -> Dict[str, Any]:
    """Config asking for exactly k issues: both interaction scenarios from k >= 4, rest round-robin."""
    if not 0 <= k <= 12:
        raise ValueError("k must be in [0, 12]")
    interactions = BOTH_INTERACTIONS if k >= 4 else NO_INTERACTIONS
    rest = k - 4 if k >= 4 else k
    counts = [0] * len(ISSUE_TYPES)
    for i in range(rest):
        counts[i % len(counts)] += 1
    return {"errors": {t: {"count": c} for t, c in zip(ISSUE_TYPES, counts)},
            "interactions": copy.deepcopy(interactions), "max_issues": 12}


def forced(issue_type: str, variant: str, count: int = 1) -> Dict[str, Any]:
    cfg = only(**{issue_type: count})
    cfg["errors"][issue_type]["variants"] = [variant]
    return cfg


def interactions_only() -> Dict[str, Any]:
    cfg = only()
    cfg["interactions"] = copy.deepcopy(BOTH_INTERACTIONS)
    return cfg


def clean_in(zone: str, payment_zone: str) -> Dict[str, Any]:
    cfg = copy.deepcopy(CLEAN)
    cfg["company"] = {"timezones": [zone], "payment_timestamp_zones": [payment_zone]}
    return cfg


def audit_suite(seeds_per_case: int = 5) -> Iterator[Tuple[str, int, Dict[str, Any]]]:
    """(case name, seed, config) covering every issue count, variant, scenario and time zone."""
    base = 5000
    for k in range(13):
        for s in range(seeds_per_case):
            yield f"k={k}", base + 100 * k + s, exactly(k)
    for issue_type in ISSUE_TYPES:
        for variant in ERROR_VARIANTS[issue_type]:
            for s in range(seeds_per_case):
                yield f"{issue_type}/{variant}", 7000 + s, forced(issue_type, variant)
    for s in range(2 * seeds_per_case):
        yield "interactions", 8000 + s, interactions_only()
    for zone in sorted(ZONE_OFFSETS_MINUTES):
        for payment_zone in ("company", "UTC"):
            yield f"clean/{zone}/{payment_zone}", 9000, clean_in(zone, payment_zone)


def case_names() -> List[str]:
    return sorted({name for name, _, _ in audit_suite(1)})
