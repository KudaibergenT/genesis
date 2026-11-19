"""Official reward over k = 0..12: closed forms, dominance, and the four baseline policies."""

from fractions import Fraction

import pytest

from auditenv.evaluator import Grader
from auditenv.evaluator.analysis import mark_everything_nonnegative, padding_break_even, reward_for, shapes
from auditenv.evaluator.reward import STEP_COST
from auditenv.policies import POLICIES
from auditenv.runner import run_episode
from auditenv.suites import exactly

from conftest import episode

KS = range(13)


# ---------------------------------------------------------------------------- closed forms
@pytest.mark.parametrize("k", KS)
def test_closed_forms(k):
    s = shapes(k)
    assert s["oracle"] == 1
    assert s["silence"] == (1 if k == 0 else -1)
    assert s["mark_everything_blind"] == -1
    expected = Fraction(-1) if k == 0 else Fraction(3 * k - 30, 30 + k)
    assert s["mark_everything_including_truth"] == expected
    if k:
        assert s["truth_each_twice"] == Fraction(1, 3)
        assert s["truth_plus_one_fp"] == Fraction(2 * k - 1, 2 * k + 1)
    else:
        assert s["one_false_accusation"] == -1


@pytest.mark.parametrize("k", KS)
def test_mark_everything_sign_by_issue_count(k):
    assert mark_everything_nonnegative(k) == (k >= 10)
    assert (shapes(k)["mark_everything_including_truth"] > 0) == (k >= 11)
    if 10 <= k:
        assert not mark_everything_nonnegative(k, steps=15) or k >= 11


@pytest.mark.parametrize("k", KS)
def test_honest_complete_answer_strictly_dominates_every_other_answer(k):
    best = reward_for(k, k, k)
    for n in range(31):
        for t in range(min(k, n) + 1):
            if (n, t) != (k, k):
                assert reward_for(k, n, t) < best, (k, n, t)


@pytest.mark.parametrize("k", KS)
def test_false_positives_and_repeats_never_help(k):
    for n in range(k, 30):
        for t in range(k + 1):
            if t <= n:
                assert reward_for(k, n + 1, t) <= reward_for(k, n, t)


@pytest.mark.parametrize("k", range(1, 13))
def test_padding_break_even_is_twice_k(k):
    p = padding_break_even(k)
    assert reward_for(k, k + p, k) == 0 if k + p <= 30 else True
    if k + p + 1 <= 30:
        assert reward_for(k, k + p + 1, k) < 0


def test_step_cost_is_small_and_monotone():
    assert STEP_COST * 99 < Fraction(1, 5)
    for k in KS:
        values = [reward_for(k, k, k, s) for s in range(100)]
        assert all(a >= b for a, b in zip(values, values[1:]))
        assert values[0] - values[-1] <= Fraction(198, 1000)


# ---------------------------------------------------------------------------- policies on real episodes
@pytest.fixture(scope="module")
def episodes_by_k():
    out = {}
    for k in KS:
        ep = episode(6000 + 100 * k, exactly(k))
        assert len(ep.truth["issues"]) == k
        out[k] = ep
    return out


def _reward(name, ep, mode="eval"):
    return run_episode(POLICIES[name](), 6000, episode=ep, mode=mode).result


@pytest.mark.parametrize("k", KS)
def test_oracle_gets_exactly_the_maximum(episodes_by_k, k):
    ep = episodes_by_k[k]
    result = _reward("oracle", ep)
    assert result["reward"] == 1.0 and result["inspection_steps"] == 0
    assert result["metrics"]["tp"] == k and result["metrics"]["fp"] == 0
    train = run_episode(POLICIES["oracle"](), 0, episode=ep, mode="train")
    assert train.trajectory[-1]["reward"] == 1.0
    assert run_episode(POLICIES["oracle"](), 0, episode=ep, mode="eval").trajectory[-1]["reward"] == 0.0


@pytest.mark.parametrize("k", KS)
def test_mark_everything_policy_always_loses(episodes_by_k, k):
    result = _reward("flag_everything", episodes_by_k[k])
    assert result["reward"] == -1.0 and result["metrics"]["tp"] == 0


@pytest.mark.parametrize("k", KS)
def test_random_explorer_has_no_unjustified_advantage(episodes_by_k, k):
    for seed in range(5):
        result = run_episode(POLICIES["random_explorer"](), seed, episode=episodes_by_k[k], mode="eval").result
        assert result["metrics"]["tp"] == 0
        assert result["reward"] <= (1.0 if k == 0 else -1.0)


@pytest.mark.parametrize("k", KS)
def test_simple_reconciler_scores_only_real_matches(episodes_by_k, k):
    ep = episodes_by_k[k]
    result = _reward("simple_reconciler", ep)
    truth_ids = {i["issue_id"] for i in ep.truth["issues"]}
    tps = [f for f in result["details"]["findings"] if f["status"] == "TP"]
    assert all(f["issue_id"] in truth_ids for f in tps)
    assert result["reward"] < _reward("reference_solver", ep)["reward"] <= _reward("oracle", ep)["reward"]


@pytest.mark.parametrize("k", KS)
def test_constructed_attacks_match_the_formula_on_real_episodes(episodes_by_k, k):
    ep = episodes_by_k[k]
    grader = Grader(ep.truth)
    truth = [{"type": i["type"], "affected_ids": i["affected_ids"], "claimed_amount_minor": i["expected_amount_minor"],
              "currency": i["currency"], "evidence_ids": i["evidence_sets"][0], "explanation": ""} for i in ep.truth["issues"]]
    junk = [{"type": "period_cutoff", "affected_ids": [d["doc_id"]], "claimed_amount_minor": 1,
             "currency": ep.public.manifest["base_currency"], "evidence_ids": [d["doc_id"]], "explanation": ""}
            for d in ep.public.invoices[: 30 - k]]

    def g(findings):
        return grader.grade({"episode_id": ep.episode_id, "findings": findings}, 0).reward

    assert g(truth + junk) == pytest.approx(float(shapes(k)["mark_everything_including_truth"]))
    assert g(truth + junk) < g(truth) == 1.0
    if k:
        assert g(truth + truth) == pytest.approx(1 / 3)
