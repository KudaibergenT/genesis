"""Data audit: committed open episodes and a generated suite covering every case."""

import collections
import os

import pytest

from auditenv.audit import audit_episode, load_episode
from auditenv.cli import ROOT, split_episodes
from auditenv.config import ERROR_VARIANTS
from auditenv.generator import generate_episode
from auditenv.suites import audit_suite

OPEN = [(split, seed) for split in ("train", "dev") for seed, _ in split_episodes(split)]


@pytest.mark.parametrize("split, seed", OPEN)
def test_committed_open_episodes_pass_the_full_audit(split, seed):
    directory = os.path.join(ROOT, "episodes", split, f"seed_{seed}")
    report = audit_episode(load_episode(directory))
    assert report["problems"] == []


def test_open_episodes_cover_clean_books_and_interactions():
    stats = [audit_episode(load_episode(os.path.join(ROOT, "episodes", s, f"seed_{n}")), False)["stats"] for s, n in OPEN]
    assert sum(1 for st in stats if st["issues"] == 0) >= 4
    scenarios = {sc for st in stats for sc in st["scenarios"]}
    assert scenarios == {"fx_partial_at_cutoff", "allocation_with_fx"}
    types = collections.Counter(t for st in stats for t, n in st["types"].items() for _ in range(n))
    assert set(types) == set(ERROR_VARIANTS)


SUITE = list(audit_suite(seeds_per_case=1))


@pytest.mark.parametrize("case, seed, cfg", SUITE, ids=[f"{c}-{s}" for c, s, _ in SUITE])
def test_generated_suite_passes_the_full_audit(case, seed, cfg):
    ep = generate_episode(seed, cfg)
    report = audit_episode(ep)
    assert report["problems"] == []
    if case.startswith("k="):
        assert report["stats"]["issues"] == int(case[2:])
    if case.startswith("clean"):
        assert report["stats"]["issues"] == 0
    if case == "interactions":
        assert report["stats"]["scenarios"] == ["allocation_with_fx", "fx_partial_at_cutoff"]
