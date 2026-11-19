import functools

import pytest

from auditenv.generator import generate_episode

SEEDS_BOTH_TYPES = (2, 3, 7, 8)
CLEAN_CONFIG = {"errors": {"duplicate_invoice": {"count": 0}, "payment_allocation": {"count": 0}}}


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
