"""Demo: a scripted auditor solves one episode with interacting errors in an isolated process.

    python scripts/demo.py [--seed 216] [--out docs/demo_transcript.md]

Generates the episode through judge_adapter, runs the scripted reference solver in a separate
process in closed mode, prints every step and the evaluator-side grade, and saves the transcript.
"""

import argparse
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "src"), ROOT]

import judge_adapter as ja  # noqa: E402
from auditenv.audit import load_episode  # noqa: E402
from auditenv.sandbox.runner import run_isolated  # noqa: E402


def describe(action, obs) -> str:
    if action is None:
        return "reset → task, manifest, policy, 100 steps"
    name, args = action.get("action"), action.get("args", {})
    if name == "list_records":
        res = obs["result"]
        return f"list_records({args.get('table')}) → {len(res['records'])} rows, next_cursor={'yes' if res['next_cursor'] else 'none'}"
    if name == "submit":
        return f"submit({len(args['findings'])} findings) → {obs['result']}"
    return f"{name}({json.dumps(args)[:60]}) → {json.dumps(obs['result'] or obs['error'])[:80]}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=216)
    parser.add_argument("--scenario", default="interaction_fx_partial_at_cutoff")
    parser.add_argument("--out", default=os.path.join(ROOT, "docs", "demo_transcript.md"))
    args = parser.parse_args(argv)
    directory = tempfile.mkdtemp(prefix="auditenv-demo-")
    with open(os.path.join(ROOT, "configs", "scenarios", f"{args.scenario}.json"), encoding="utf-8") as fh:
        ja.generate_episode(args.seed, json.load(fh), directory)
    episode = load_episode(directory)
    run = run_isolated("reference_solver", 0, episode=episode, mode="eval")
    m = episode.public.manifest
    lines = [f"# Демо: {args.scenario}, seed {args.seed}", "",
             f"Компания `{m['company']}`, период {m['period_start']} – {m['period_end']}, базовая валюта "
             f"{m['base_currency']}, пояс {m['timezone']}; {m['counts']['ledger_rows']} строк журнала, "
             f"{m['counts']['documents']} документов. Политика — скриптовый `reference_solver` в отдельном "
             f"процессе (uid сброшен: {run.agent['privileges_dropped']}), режим closed.", "", "## Шаги", ""]
    for t in run.trajectory:
        lines.append(f"{t['step']:>3}. {describe(t['action'], t['observation'])} (reward {t['reward']})")
    findings = run.trajectory[-1]["action"]["args"]["findings"]
    lines += ["", "## Выводы агента", ""]
    for f in findings:
        lines.append(f"- `{f['type']}` {f['affected_ids']} — {f['claimed_amount_minor']} {f['currency']}, "
                     f"доказательства {f['evidence_ids']}")
    lines += ["", "## Эталон (только у оценщика)", ""]
    for i in episode.truth["issues"]:
        lines.append(f"- `{i['type']}` {i['affected_ids']} — {i['expected_amount_minor']} {i['currency']}")
    r = run.result
    lines += ["", "## Оценка (только у оценщика)", "",
              f"TP={r['metrics']['tp']}, FP={r['metrics']['fp']}, FN={r['metrics']['fn']}, F1={r['metrics']['f1']}, "
              f"inspection_steps={r['inspection_steps']}, reward={r['reward']}. Политике во всех шагах была видна "
              f"награда 0.0.", ""]
    text = "\n".join(lines)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(text)
    return 0 if r["metrics"]["f1"] == 1.0 else 1


if __name__ == "__main__":
    sys.exit(main())
