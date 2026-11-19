"""Paired checks: permuting record order and renaming IDs preserves truth, oracle and reward."""

import pytest

from auditenv.policies import POLICIES
from auditenv.runner import run_episode

from conftest import episode

RENAMED = {"render": {"id_salt": 7, "order_salt": 3, "row_order": "shuffled"}}


def bijection(a, b):
    """Map surface ids of render a to render b through shared internal keys."""
    inv_a = {v: k for k, v in a.provenance["id_map"].items()}
    return {surface: b.provenance["id_map"][internal] for surface, internal in inv_a.items()}


def canonical_truth(ep, mapping=None):
    m = mapping or {}
    out = []
    for i in ep.truth["issues"]:
        out.append((
            i["type"],
            tuple(sorted(m.get(x, x) for x in i["affected_ids"])),
            i["expected_amount_minor"],
            i["currency"],
            tuple(sorted(tuple(sorted(m.get(x, x) for x in s)) for s in i["evidence_sets"])),
        ))
    return sorted(out)


@pytest.mark.parametrize("seed", range(20))
def test_truth_is_invariant_under_permutation_and_renaming(seed):
    a, b = episode(seed), episode(seed, RENAMED)
    assert a.episode_id != b.episode_id
    m = bijection(a, b)
    assert canonical_truth(a, m) == canonical_truth(b)
    assert [r["row_id"] for r in b.public.ledger] != [m[r["row_id"]] for r in a.public.ledger]
    assert sorted(m[r["row_id"]] for r in a.public.ledger) == sorted(r["row_id"] for r in b.public.ledger)


@pytest.mark.parametrize("seed", range(20))
@pytest.mark.parametrize("name", ["oracle", "reference_solver", "silence"])
def test_reward_is_invariant_under_permutation_and_renaming(seed, name):
    a, b = episode(seed), episode(seed, RENAMED)
    ra = run_episode(POLICIES[name](), seed, episode=a)
    rb = run_episode(POLICIES[name](), seed, episode=b)
    assert ra.result["metrics"] == rb.result["metrics"]
    assert ra.result["reward"] == rb.result["reward"]


@pytest.mark.parametrize("seed", range(10))
def test_reference_findings_map_through_the_bijection(seed):
    a, b = episode(seed), episode(seed, RENAMED)
    m = bijection(a, b)
    fa = run_episode(POLICIES["reference_solver"](), seed, episode=a).trajectory[-1]["action"]["args"]["findings"]
    fb = run_episode(POLICIES["reference_solver"](), seed, episode=b).trajectory[-1]["action"]["args"]["findings"]
    key = lambda f, mp: (f["type"], tuple(sorted(mp.get(x, x) for x in f["affected_ids"])), f["claimed_amount_minor"])  # noqa: E731
    assert sorted(key(f, m) for f in fa) == sorted(key(f, {}) for f in fb)
