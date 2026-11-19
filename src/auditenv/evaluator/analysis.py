"""Closed-form behaviour of the official reward for canonical answer shapes.

With k true issues, n submitted findings and t of them true positives,
F1 = 2t / (n + k) (F1 = 1 when k = n = 0; F1 = 0 when k = 0 < n), and
reward = clip(2*F1 - 1 - 0.002*s, -1, 1). Everything here is exact (Fraction).
"""

from fractions import Fraction
from typing import Dict

from auditenv.evaluator.reward import official_reward, prf1
from auditenv.evaluator.schema import MAX_FINDINGS


def reward_for(k: int, n: int, t: int, steps: int = 0) -> Fraction:
    if not 0 <= t <= min(k, n):
        raise ValueError("need 0 <= t <= min(k, n)")
    _, _, f1 = prf1(t, n - t, k - t, n_truth=k, n_findings=n)
    return official_reward(f1, steps)


def shapes(k: int, steps: int = 0) -> Dict[str, Fraction]:
    """Reward of each canonical strategy on an episode with k true issues."""
    out = {
        "oracle": reward_for(k, k, k, steps),
        "silence": reward_for(k, 0, 0, steps),
        "mark_everything_blind": reward_for(k, MAX_FINDINGS, 0, steps),
        "mark_everything_including_truth": reward_for(k, MAX_FINDINGS, k, steps) if k <= MAX_FINDINGS else None,
    }
    if k:
        out["truth_each_twice"] = reward_for(k, 2 * k, k, steps)
        out["truth_plus_one_fp"] = reward_for(k, k + 1, k, steps)
        out["truth_minus_one"] = reward_for(k, k - 1, k - 1, steps)
        out["half_of_truth"] = reward_for(k, k // 2, k // 2, steps)
    else:
        out["one_false_accusation"] = reward_for(0, 1, 0, steps)
    return out


def padding_break_even(k: int) -> int:
    """Largest number of extra false positives p on top of a perfect answer that still
    keeps 2*F1 - 1 >= 0 (steps = 0): F1 = 2k/(2k+p) >= 1/2  <=>  p <= 2k."""
    return 2 * k


def mark_everything_nonnegative(k: int, steps: int = 0) -> bool:
    """30 findings containing all k truths: (3k - 30)/(30 + k) - 0.002*steps >= 0."""
    return reward_for(k, MAX_FINDINGS, k, steps) >= 0
