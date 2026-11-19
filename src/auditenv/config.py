"""Generator configuration: defaults, strict validation, hashing."""

import copy
from typing import Any, Dict

from auditenv import canon, money, timeutil
from auditenv.version import SCHEMA_VERSION

ISSUE_TYPES = ("duplicate_invoice", "payment_allocation", "fx_conversion", "period_cutoff")

ERROR_VARIANTS = {
    "duplicate_invoice": ("same_number", "new_number"),
    "payment_allocation": ("missing", "partial"),
    "fx_conversion": ("invoice_wrong_date", "invoice_wrong_rate", "invoice_wrong_scale",
                      "receipt_wrong_date", "receipt_wrong_rate"),
    "period_cutoff": ("early", "late", "tz_shift", "receipt_early"),
}

# Each interaction scenario injects two causally linked issues on one document.
INTERACTIONS = ("fx_partial_at_cutoff", "allocation_with_fx")
ISSUES_PER_INTERACTION = 2

MAX_TOTAL_ISSUES = 12
DEFAULT_MAX_ISSUES = MAX_TOTAL_ISSUES

DEFAULT_CONFIG: Dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "company": {
        "base_currencies": ["KZT", "USD", "EUR"],
        "timezones": ["Asia/Almaty", "Asia/Dubai", "Asia/Kolkata", "Asia/Tokyo", "America/Bogota", "UTC"],
        "year": 2026,
        "months": [1, 12],
        "review_window_days": 5,
        "payment_timestamp_zones": ["company", "UTC"],
    },
    "size": {
        "customers": [6, 12],
        "invoices": [28, 64],
        "background_entries": [90, 240],
    },
    "currencies": {
        "foreign_pool": ["USD", "EUR", "GBP", "JPY", "KWD", "CNY"],
        "foreign_count": [1, 2],
        "foreign_customer_per_mille": 350,
    },
    "rounding_modes": ["ROUND_HALF_EVEN", "ROUND_HALF_UP"],
    "distractors": {
        "weekly_subscriptions": True,
        "split_payments": True,
        "partial_payments": True,
        "unidentified_payments": True,
        "bulk_payments": True,
        "refunds": True,
        "boundary_documents": True,
    },
    "errors": {
        "duplicate_invoice": {"count": [0, 2], "variants": list(ERROR_VARIANTS["duplicate_invoice"])},
        "payment_allocation": {"count": [0, 2], "variants": list(ERROR_VARIANTS["payment_allocation"])},
        "fx_conversion": {"count": [0, 2], "variants": list(ERROR_VARIANTS["fx_conversion"])},
        "period_cutoff": {"count": [0, 2], "variants": list(ERROR_VARIANTS["period_cutoff"])},
    },
    "interactions": {name: {"count": [0, 1]} for name in INTERACTIONS},
    "max_issues": DEFAULT_MAX_ISSUES,
    "render": {"id_salt": 0, "order_salt": 0, "row_order": "chronological"},
}


class ConfigError(ValueError):
    pass


def _merge(base: Dict[str, Any], override: Dict[str, Any], path: str) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in override.items():
        where = f"{path}.{key}" if path else key
        if key not in base:
            raise ConfigError(f"unknown config key: {where}")
        if isinstance(base[key], dict):
            if not isinstance(value, dict):
                raise ConfigError(f"config key {where} must be an object")
            out[key] = _merge(base[key], value, where)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _check_count(value: Any, where: str, lo_bound: int, hi_bound: int) -> None:
    if isinstance(value, bool):
        raise ConfigError(f"{where} must be an int or [lo, hi]")
    if isinstance(value, int):
        lo = hi = value
    elif (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(v, int) and not isinstance(v, bool) for v in value)
    ):
        lo, hi = value
    else:
        raise ConfigError(f"{where} must be an int or [lo, hi]")
    if not (lo_bound <= lo <= hi <= hi_bound):
        raise ConfigError(f"{where} out of bounds [{lo_bound}, {hi_bound}]")


def _upper(value: Any) -> int:
    return value if isinstance(value, int) else value[1]


def resolve(config: Any = None) -> Dict[str, Any]:
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise ConfigError("config must be an object")
    cfg = _merge(DEFAULT_CONFIG, config, "")
    if cfg["schema_version"] != SCHEMA_VERSION:
        raise ConfigError(f"unsupported schema_version {cfg['schema_version']!r}")

    company = cfg["company"]
    if not company["base_currencies"] or any(c not in money.EXPONENTS for c in company["base_currencies"]):
        raise ConfigError("company.base_currencies must be non-empty known ISO codes")
    if not company["timezones"] or any(z not in timeutil.ZONE_OFFSETS_MINUTES for z in company["timezones"]):
        raise ConfigError("company.timezones must be non-empty and supported")
    _check_count(company["months"], "company.months", 1, 12)
    if not isinstance(company["year"], int) or not 2000 <= company["year"] <= 2099:
        raise ConfigError("company.year out of range")
    window = company["review_window_days"]
    if isinstance(window, bool) or not isinstance(window, int) or not 3 <= window <= 10:
        raise ConfigError("company.review_window_days must be an int in [3, 10]")
    zones = company["payment_timestamp_zones"]
    if not isinstance(zones, list) or not zones or any(z not in ("company", "UTC") for z in zones):
        raise ConfigError("company.payment_timestamp_zones must be a non-empty subset of ['company', 'UTC']")

    size = cfg["size"]
    _check_count(size["customers"], "size.customers", 3, 40)
    _check_count(size["invoices"], "size.invoices", 12, 120)
    _check_count(size["background_entries"], "size.background_entries", 40, 600)

    cur = cfg["currencies"]
    if any(c not in money.EXPONENTS for c in cur["foreign_pool"]):
        raise ConfigError("currencies.foreign_pool contains unknown codes")
    _check_count(cur["foreign_count"], "currencies.foreign_count", 0, 4)
    if not isinstance(cur["foreign_customer_per_mille"], int) or not 0 <= cur["foreign_customer_per_mille"] <= 1000:
        raise ConfigError("currencies.foreign_customer_per_mille must be in [0, 1000]")

    if not cfg["rounding_modes"] or any(m not in money.ROUNDING_MODES for m in cfg["rounding_modes"]):
        raise ConfigError("rounding_modes must be non-empty and supported")

    for key, value in cfg["distractors"].items():
        if not isinstance(value, bool):
            raise ConfigError(f"distractors.{key} must be boolean")

    total_upper = 0
    for issue_type in ISSUE_TYPES:
        spec = cfg["errors"][issue_type]
        _check_count(spec["count"], f"errors.{issue_type}.count", 0, MAX_TOTAL_ISSUES)
        if not isinstance(spec["variants"], list) or any(v not in ERROR_VARIANTS[issue_type] for v in spec["variants"]):
            raise ConfigError(f"errors.{issue_type}.variants invalid")
        if _upper(spec["count"]) > 0 and not spec["variants"]:
            raise ConfigError(f"errors.{issue_type}.variants must be non-empty when count > 0")
        total_upper += _upper(spec["count"])
    for name in INTERACTIONS:
        _check_count(cfg["interactions"][name]["count"], f"interactions.{name}.count", 0, MAX_TOTAL_ISSUES)
        total_upper += ISSUES_PER_INTERACTION * _upper(cfg["interactions"][name]["count"])
    if total_upper > MAX_TOTAL_ISSUES:
        raise ConfigError(f"total error count upper bound exceeds {MAX_TOTAL_ISSUES}")
    cap = cfg["max_issues"]
    if isinstance(cap, bool) or not isinstance(cap, int) or not 0 <= cap <= MAX_TOTAL_ISSUES:
        raise ConfigError(f"max_issues must be an int in [0, {MAX_TOTAL_ISSUES}]")

    render = cfg["render"]
    for key in ("id_salt", "order_salt"):
        if isinstance(render[key], bool) or not isinstance(render[key], int) or render[key] < 0:
            raise ConfigError(f"render.{key} must be a non-negative int")
    if render["row_order"] not in ("chronological", "shuffled"):
        raise ConfigError("render.row_order must be 'chronological' or 'shuffled'")
    return cfg


def world_part(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Config subset that determines the clean company history."""
    return {k: v for k, v in cfg.items() if k not in ("errors", "interactions", "max_issues", "render")}


def config_hash(cfg: Dict[str, Any]) -> str:
    return canon.hash_obj(cfg)
