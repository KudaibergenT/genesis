import statistics

import pytest

from auditenv.cli import split_episodes
from auditenv.generator import generate_episode
from auditenv.policies import POLICIES
from auditenv.runner import run_episode

TRAJECTORY_KEYS = {"episode_id", "step", "action", "observation", "reward", "terminated", "truncated"}


@pytest.fixture(scope="module")
def open_episodes():
    return [(seed, generate_episode(seed, cfg)) for split in ("train", "dev") for seed, cfg in split_episodes(split)]


@pytest.fixture(scope="module")
def runs(open_episodes):
    out = {}
    for name, cls in POLICIES.items():
        out[name] = [run_episode(cls(), seed, mode="eval", episode=ep) for seed, ep in open_episodes]
    return out


def rewards(runs, name):
    return [r.result["reward"] for r in runs[name]]


def test_open_splits_have_ten_episodes_each_and_include_clean_books(open_episodes):
    assert len(open_episodes) == 20
    assert sum(1 for _, ep in open_episodes if not ep.truth["issues"]) >= 2


def test_oracle_reaches_maximum_everywhere(runs):
    assert rewards(runs, "oracle") == [1.0] * 20


def test_reference_solver_solves_every_episode_through_tools(runs):
    for r in runs["reference_solver"]:
        assert r.result["metrics"]["f1"] == 1.0 and r.steps <= 60


def test_flag_everything_attack_loses(runs):
    assert max(rewards(runs, "flag_everything")) <= -0.9


def test_silence_is_rewarded_only_on_clean_books(runs, open_episodes):
    for r, (_, ep) in zip(runs["silence"], open_episodes):
        assert r.result["reward"] == (1.0 if not ep.truth["issues"] else -1.0)


def test_policy_ordering(runs):
    mean = lambda n: statistics.fmean(rewards(runs, n))  # noqa: E731
    assert mean("oracle") >= mean("reference_solver") > mean("simple_reconciler") > mean("flag_everything")
    assert mean("random_explorer") < 0


def test_runs_respect_limits_and_log_required_fields(runs):
    for name, episode_runs in runs.items():
        for r in episode_runs:
            assert r.steps <= 100 and r.wall_time_s < 60
            assert all(set(t) == TRAJECTORY_KEYS for t in r.trajectory)
            assert r.trajectory[-1]["terminated"] or r.trajectory[-1]["truncated"]
            assert [t["step"] for t in r.trajectory] == list(range(len(r.trajectory)))
