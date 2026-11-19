"""Official terminal reward: clip(2*F1 - 1 - 0.002*inspection_steps, -1, 1).

Arithmetic is exact (fractions); floats appear only in the reported values.
"""

from fractions import Fraction
from typing import Dict, Tuple

STEP_COST = Fraction(2, 1000)


def prf1(tp: int, fp: int, fn: int, n_truth: int, n_findings: int) -> Tuple[Fraction, Fraction, Fraction]:
    p = Fraction(tp, tp + fp) if tp + fp else Fraction(0)
    r = Fraction(tp, tp + fn) if tp + fn else Fraction(0)
    if n_truth == 0 and n_findings == 0:
        f1 = Fraction(1)
    elif n_truth == 0:
        f1 = Fraction(0)
    else:
        f1 = Fraction(2 * tp, 2 * tp + fp + fn)
    return p, r, f1


def official_reward(f1: Fraction, inspection_steps: int) -> Fraction:
    value = 2 * f1 - 1 - STEP_COST * inspection_steps
    return max(Fraction(-1), min(Fraction(1), value))


def metrics(tp: int, fp: int, fn: int, n_truth: int, n_findings: int, inspection_steps: int) -> Dict[str, float]:
    p, r, f1 = prf1(tp, fp, fn, n_truth, n_findings)
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": float(p),
        "recall": float(r),
        "f1": float(f1),
        "reward": float(official_reward(f1, inspection_steps)),
    }
