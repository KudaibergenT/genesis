"""Audit committed open episodes and a generated suite; writes docs/data_audit.md.

Usage: PYTHONPATH=src python scripts/audit_episodes.py [--seeds N] [--out docs/data_audit.md]
"""

import argparse
import collections
import os
import sys

from auditenv.audit import audit_episode, load_episode
from auditenv.cli import ROOT, split_episodes
from auditenv.generator import generate_episode
from auditenv.suites import audit_suite


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=5)
    parser.add_argument("--out", default="docs/data_audit.md")
    args = parser.parse_args(argv)

    rows, problems = [], []
    for split in ("train", "dev"):
        for seed, _ in split_episodes(split):
            report = audit_episode(load_episode(os.path.join(ROOT, "episodes", split, f"seed_{seed}")))
            rows.append((f"{split}/{seed}", report))
            problems += [f"{split}/{seed}: {p}" for p in report["problems"]]

    cases = collections.defaultdict(list)
    for case, seed, cfg in audit_suite(args.seeds):
        report = audit_episode(generate_episode(seed, cfg))
        cases[case].append(report)
        problems += [f"{case}/{seed}: {p}" for p in report["problems"]]

    variants = collections.Counter()
    scenarios = collections.Counter()
    types = collections.Counter()
    total_issues = 0
    for _, report in rows:
        for v, n in report["stats"]["variants"].items():
            variants[v] += n
    for reports in cases.values():
        for r in reports:
            total_issues += r["stats"]["issues"]
            for v, n in r["stats"]["variants"].items():
                variants[v] += n
            for t, n in r["stats"]["types"].items():
                types[t] += n
            for sc in r["stats"]["scenarios"]:
                scenarios[sc] += 1

    n_generated = sum(len(v) for v in cases.values())
    lines = ["# Аудит данных", "", "Файл сгенерирован `scripts/audit_episodes.py`.", "",
             "Для каждого эпизода проверяется:",
             "1. `checks.verify_episode` по открытым файлам: размеры, достаточность каждого набора доказательств,",
             "   детектор находит ровно эталон, каждое нарушение инвариантов объяснено эталонной ошибкой;",
             "2. внесение ошибок не ломает двойную запись (проводки сбалансированы, ссылки целы, суммы целые);",
             "3. та же компания без ошибок (`max_issues: 0`) проходит все бухгалтерские равенства и не даёт находок;",
             "4. provenance ↔ truth: число, тип и affected_ids каждой мутации, взаимность связей сценариев;",
             "5. truth: суммы > 0, валюты из manifest, допуски из опубликованных конвенций, минимальность каждого",
             "   набора доказательств (удаление любого элемента ломает вывод);",
             "6. в открытых файлах нет внутренних ключей и ID эталона.", "",
             f"**Итого: {len(rows)} открытых эпизодов + {n_generated} сгенерированных, {total_issues} ошибок в "
             f"сгенерированных, проблем найдено: {len(problems)}.**", "",
             "## Открытые train/dev эпизоды", "",
             "| эпизод | ошибок | типы | сценарии | строк | документов | пояс | база | округление | проблемы |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for name, r in rows:
        st = r["stats"]
        lines.append(f"| {name} | {st['issues']} | {', '.join(f'{k}×{v}' for k, v in sorted(st['types'].items())) or '—'} | "
                     f"{', '.join(st['scenarios']) or '—'} | {st['ledger_rows']} | {st['documents']} | {st['timezone']} | "
                     f"{st['base_currency']} | {st['rounding']} | {len(r['problems'])} |")
    lines += ["", f"## Сгенерированный набор ({args.seeds} сидов на случай, кроме чистых по поясам)", "",
              "| случай | эпизодов | ошибок всего | ошибок на эпизод | проблемы |", "|---|---|---|---|---|"]
    for case in sorted(cases, key=lambda c: (c.split("=")[0], int(c[2:]) if c.startswith("k=") else 0, c)):
        reports = cases[case]
        counts = sorted({r["stats"]["issues"] for r in reports})
        lines.append(f"| {case} | {len(reports)} | {sum(r['stats']['issues'] for r in reports)} | "
                     f"{', '.join(map(str, counts))} | {sum(len(r['problems']) for r in reports)} |")
    lines += ["", "## Покрытие", "", "| вариант | раз |", "|---|---|"]
    lines += [f"| {v} | {n} |" for v, n in sorted(variants.items())]
    lines += ["", f"Сценарии взаимодействия в сгенерированном наборе: " +
              ", ".join(f"{k} — {v} эпизодов" for k, v in sorted(scenarios.items())), ""]
    if problems:
        lines += ["## Проблемы", ""] + [f"- {p}" for p in problems]
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines[:16]))
    print(f"... written to {args.out}; problems: {len(problems)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
