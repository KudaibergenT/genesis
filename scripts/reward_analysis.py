"""Analytic and empirical behaviour of the official reward for k = 0..12 true issues.

Usage: PYTHONPATH=src python scripts/reward_analysis.py [--seeds N] [--out docs/reward_analysis.md]
"""

import argparse
import statistics
import sys
from typing import Any, Dict, List

from auditenv.evaluator import Grader
from auditenv.evaluator.analysis import mark_everything_nonnegative, padding_break_even, shapes
from auditenv.evaluator.schema import MAX_FINDINGS
from auditenv.generator import generate_episode
from auditenv.policies import POLICIES
from auditenv.runner import run_episode
from auditenv.suites import exactly

POLICY_ORDER = ["oracle", "reference_solver", "simple_reconciler", "silence", "random_explorer", "flag_everything"]
ATTACKS = ["mark_everything_including_truth", "truth_each_twice", "truth_plus_one_fp"]


def truth_findings(truth: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [{"type": i["type"], "affected_ids": list(i["affected_ids"]), "claimed_amount_minor": i["expected_amount_minor"],
             "currency": i["currency"], "evidence_ids": list(i["evidence_sets"][0]), "explanation": ""}
            for i in truth["issues"]]


def junk(ep, n: int) -> List[Dict[str, Any]]:
    docs = ep.public.invoices + ep.public.payments
    types = ("duplicate_invoice", "payment_allocation", "fx_conversion", "period_cutoff")
    out = []
    for i in range(n):
        d = docs[i % len(docs)]
        out.append({"type": types[i % 4], "affected_ids": [d["doc_id"]],
                    "claimed_amount_minor": abs(d.get("total_minor", d.get("amount_minor", 0))),
                    "currency": ep.public.manifest["base_currency"], "evidence_ids": [d["doc_id"]], "explanation": ""})
    return out


def attack_rewards(ep) -> Dict[str, float]:
    grader = Grader(ep.truth)
    truth = truth_findings(ep.truth)
    k = len(truth)

    def g(findings):
        return grader.grade({"episode_id": ep.episode_id, "findings": findings}, 0).reward

    out = {"mark_everything_including_truth": g(truth + junk(ep, MAX_FINDINGS - k))}
    if k:
        out["truth_each_twice"] = g(truth + truth)
        out["truth_plus_one_fp"] = g(truth + junk(ep, 1))
    return out


def fmt(x) -> str:
    return "—" if x is None else f"{float(x):+.3f}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=5)
    parser.add_argument("--out", default="docs/reward_analysis.md")
    args = parser.parse_args(argv)

    lines = ["# Анализ официальной функции награды", "",
             "`reward = clip(2·F1 − 1 − 0.002·inspection_steps, −1, 1)`; формула не изменялась.",
             "Файл сгенерирован `scripts/reward_analysis.py`.", "",
             "Обозначения: k — истинных ошибок, n — выводов (≤ 30), t — из них TP. Тогда F1 = 2t/(n + k),",
             "при k = n = 0 F1 = 1, при k = 0 < n F1 = 0. Награда положительна тогда и только тогда, когда",
             "F1 > 1/2 + 0.001·s.", "",
             "## 1. Аналитика (inspection_steps = 0)", ""]
    cols = ["oracle", "silence", "mark_everything_blind", "mark_everything_including_truth", "truth_each_twice",
            "truth_plus_one_fp", "truth_minus_one", "half_of_truth"]
    lines.append("| k | " + " | ".join(cols) + " | допустимо лишних FP при 2F1−1 ≥ 0 |")
    lines.append("|" + "---|" * (len(cols) + 2))
    for k in range(13):
        s = shapes(k)
        lines.append(f"| {k} | " + " | ".join(fmt(s.get(c)) for c in cols) + f" | {padding_break_even(k)} |")
    lines += ["", "При k = 0 любое обвинение даёт −1, молчание +1.", "",
              "## 2. Граница атаки «пометить всё»", "",
              "30 выводов, среди которых все k истинных (лучший случай для атаки; без знания эталона TP получить",
              "нельзя): reward = (3k − 30)/(30 + k) − 0.002·s.", "",
              "| k | reward (s=0) | reward (s=15) | ≥ 0 при s=0 |", "|---|---|---|---|"]
    for k in range(13):
        lines.append(f"| {k} | {fmt(shapes(k)['mark_everything_including_truth'])} | "
                     f"{fmt(shapes(k, 15)['mark_everything_including_truth'])} | "
                     f"{'да' if mark_everything_nonnegative(k) else 'нет'} |")
    lines += ["", "Вывод: при k ≤ 9 такая атака всегда отрицательна, при k = 10 даёт ровно 0, при k = 11–12 —",
              "+0.073 и +0.143. Это свойство официальной формулы (ограничение спецификации), а не генератора.",
              "При любом k она строго хуже честного полного ответа (+1.000) и хуже ответа с одним пропуском.", "",
              f"## 3. Эмпирика: эпизоды ровно с k ошибками ({args.seeds} сидов на k, режим eval)", ""]

    header = POLICY_ORDER + ATTACKS
    lines.append("| k | " + " | ".join(header) + " |")
    lines.append("|" + "---|" * (len(header) + 1))
    worst_gap = 0.0
    for k in range(13):
        acc: Dict[str, List[float]] = {h: [] for h in header}
        for s in range(args.seeds):
            seed = 6000 + 100 * k + s
            ep = generate_episode(seed, exactly(k))
            assert len(ep.truth["issues"]) == k, (seed, k)
            for name in POLICY_ORDER:
                acc[name].append(run_episode(POLICIES[name](), seed, episode=ep, mode="eval").result["reward"])
            for name, value in attack_rewards(ep).items():
                acc[name].append(value)
        for name in header:
            if name != "oracle" and acc[name]:
                worst_gap = max(worst_gap, max(acc[name]) - min(acc["oracle"]))
        lines.append(f"| {k} | " + " | ".join(fmt(statistics.fmean(acc[h])) if acc[h] else "—" for h in header) + " |")
    lines += ["", f"Ни одна стратегия не превысила oracle (наибольшая разница с oracle: {worst_gap:+.3f}).",
              "Базовые политики организаторов (random_explorer, simple_reconciler, flag_everything, oracle) реализованы",
              "в `src/auditenv/policies/`; атаки в последних трёх столбцах строятся оценщиком из эталона и",
              "показывают верхнюю границу для стратегий «добавить лишнее»."]
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
