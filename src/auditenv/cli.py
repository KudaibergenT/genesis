"""Command line: generate | run | baselines."""

import argparse
import copy
import json
import os
import statistics
import sys
from typing import Any, Dict, List, Tuple

from auditenv import canon, config as config_mod
from auditenv.books import books_files
from auditenv.generator import generate_episode, write_episode
from auditenv.version import GENERATOR_VERSION
from auditenv.policies import OFFICIAL_BASELINES, POLICIES
from auditenv.runner import run_episode, write_jsonl

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in override.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


def split_episodes(split: str) -> List[Tuple[int, Dict[str, Any]]]:
    with open(os.path.join(ROOT, "configs", "splits.json"), encoding="utf-8") as fh:
        spec = json.load(fh)["splits"][split]
    with open(os.path.join(ROOT, spec["config"]), encoding="utf-8") as fh:
        base = json.load(fh)
    overrides = spec.get("overrides", {})
    return [(seed, _deep_merge(base, overrides.get(str(seed), {}))) for seed in spec["seeds"]]


def cmd_generate(args: argparse.Namespace) -> int:
    index = []
    for split in args.split:
        for seed, cfg in split_episodes(split):
            ep = generate_episode(seed, cfg)
            directory = os.path.join(args.out, split, f"seed_{seed}")
            write_episode(ep, directory)
            index.append({
                "split": split,
                "seed": seed,
                "episode_id": ep.episode_id,
                "config_hash": config_mod.config_hash(config_mod.resolve(cfg)),
                "issues": len(ep.truth["issues"]),
                "books_sha256": canon.hash_obj(books_files(ep.public)),
                "truth_sha256": canon.hash_obj(ep.truth),
            })
            print(f"{split} seed={seed} {ep.episode_id} issues={len(ep.truth['issues'])}")
    with open(os.path.join(args.out, "index.json"), "w", encoding="utf-8") as fh:
        json.dump({"generator_version": GENERATOR_VERSION, "episodes": index}, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    policy = POLICIES[args.policy]()
    cfg = json.loads(args.config) if args.config else None
    run = run_episode(policy, args.seed, cfg, mode=args.mode)
    if args.trajectory:
        write_jsonl(args.trajectory, run.trajectory)
    print(json.dumps({"episode_id": run.episode_id, "policy": run.policy, "steps": run.steps,
                      "wall_time_s": round(run.wall_time_s, 3), "result": run.result["metrics"]}, indent=2))
    return 0


def cmd_baselines(args: argparse.Namespace) -> int:
    os.makedirs(args.out, exist_ok=True)
    names = args.policy or list(OFFICIAL_BASELINES) + ["silence", "reference_solver"]
    episodes = [(seed, cfg) for split in args.split for seed, cfg in split_episodes(split)]
    summary = []
    for name in names:
        trajectories, results = [], []
        for seed, cfg in episodes:
            run = run_episode(POLICIES[name](), seed, cfg, mode=args.mode)
            trajectories.extend(run.trajectory)
            results.append({"episode_id": run.episode_id, "seed": seed, "steps": run.steps,
                            "wall_time_s": round(run.wall_time_s, 4), **run.result})
        write_jsonl(os.path.join(args.out, f"trajectories_{name}.jsonl"), trajectories)
        write_jsonl(os.path.join(args.out, f"results_{name}.jsonl"), results)
        mean = lambda key: statistics.fmean(r["metrics"][key] for r in results)  # noqa: E731
        summary.append({
            "policy": name,
            "episodes": len(results),
            "precision": round(mean("precision"), 3),
            "recall": round(mean("recall"), 3),
            "f1": round(mean("f1"), 3),
            "return": round(mean("reward"), 3),
            "steps": round(statistics.fmean(r["steps"] for r in results), 1),
            "wall_time_s": round(statistics.fmean(r["wall_time_s"] for r in results), 4),
        })
    with open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
        fh.write("\n")
    cols = ("policy", "episodes", "precision", "recall", "f1", "return", "steps", "wall_time_s")
    print(" | ".join(cols))
    for row in summary:
        print(" | ".join(str(row[c]) for c in cols))
    return 0


def main(argv: List[str] = None) -> int:
    parser = argparse.ArgumentParser(prog="auditenv")
    sub = parser.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="write open train/dev episodes")
    g.add_argument("--split", nargs="+", default=["train", "dev"], choices=["train", "dev"])
    g.add_argument("--out", default=os.path.join(ROOT, "episodes"))
    g.set_defaults(func=cmd_generate)

    r = sub.add_parser("run", help="run one policy on one seed")
    r.add_argument("--policy", required=True, choices=sorted(POLICIES))
    r.add_argument("--seed", type=int, required=True)
    r.add_argument("--config", help="JSON generator config")
    r.add_argument("--mode", default="train", choices=["train", "eval"])
    r.add_argument("--trajectory", help="write trajectory JSONL here")
    r.set_defaults(func=cmd_run)

    b = sub.add_parser("baselines", help="run baseline policies on open splits")
    b.add_argument("--split", nargs="+", default=["train", "dev"], choices=["train", "dev"])
    b.add_argument("--policy", nargs="*", choices=sorted(POLICIES))
    b.add_argument("--mode", default="eval", choices=["train", "eval"])
    b.add_argument("--out", default=os.path.join(ROOT, "results"))
    b.set_defaults(func=cmd_baselines)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
