import functools

import pytest

from auditenv.generator import generate_episode

SEEDS_BOTH_TYPES = (2, 3, 7, 8)
ALL_TYPES = ("duplicate_invoice", "payment_allocation", "fx_conversion", "period_cutoff")
NO_INTERACTIONS = {"fx_partial_at_cutoff": {"count": 0}, "allocation_with_fx": {"count": 0}}
CLEAN_CONFIG = {"errors": {t: {"count": 0} for t in ALL_TYPES}, "interactions": NO_INTERACTIONS}


def only(**counts):
    """Config that injects exactly the given per-type counts and nothing else."""
    return {"errors": {t: {"count": counts.get(t, 0)} for t in ALL_TYPES}, "interactions": NO_INTERACTIONS}


@functools.lru_cache(maxsize=None)
def _episode(seed: int, config_key: str):
    import json

    return generate_episode(seed, json.loads(config_key) if config_key else None)


def episode(seed: int, config=None):
    import json

    return _episode(seed, json.dumps(config, sort_keys=True) if config else "")


@pytest.fixture
def erroneous_episode():
    return episode(SEEDS_BOTH_TYPES[0])


@pytest.fixture
def clean_episode():
    return episode(5, CLEAN_CONFIG)
