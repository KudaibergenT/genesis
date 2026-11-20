"""Run the simulated jury procedure end to end.

    python jury_sim/run.py [--out results/jury_sim]

Uses only judge_adapter.py, the documented file formats and configs/scenarios/*.json.
"""

import argparse
import copy
import csv
import json
import os
import shutil
import sys
import tempfile
import time
from fractions import Fraction
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import judge_adapter as ja  # noqa: E402
from jury_sim.fileio import load, oracle_findings, save  # noqa: E402
from jury_sim.harness import run_policy  # noqa: E402
from jury_sim.transform import SPACES, transform  # noqa: E402
from jury_sim.verify import verify  # noqa: E402

MATRIX = (
    [("clean", s) for s in (301, 302, 303, 304, 305)]
    + [("duplicate_invoice", s) for s in (306, 307, 308)]
    + [("payment_allocation_missing", 309), ("payment_allocation_partial", 310), ("payment_allocation_missing", 311)]
    + [("fx_conversion", s) for s in (312, 313)]
    + [("period_cutoff", s) for s in (314, 315)]
    + [("interaction_fx_partial_at_cutoff", s) for s in (316, 317, 318)]
    + [("interaction_allocation_with_fx", s) for s in (319, 320)]
)
POLICIES = ("random_explorer", "simple_reconciliation", "mark_everything", "oracle")


def scenario(name: str) -> Dict[str, Any]:
    with open(os.path.join(ROOT, "configs", "scenarios", f"{name}.json"), encoding="utf-8") as fh:
        return json.load(fh)


def block2(work: str) -> List[Dict[str, Any]]:
    out = []
    for n, (name, seed) in enumerate(MATRIX, start=1):
        d = os.path.join(work, f"D{n:02d}")
        ja.generate_episode(seed, scenario(name), d)
        data = load(d)
        problems = verify(data)
        types = sorted(i["type"] for i in data["truth"]["issues"])
        expected_clean = name == "clean"
        if expected_clean and types:
            problems.append("clean scenario has issues")
        if not expected_clean and not types:
            problems.append("scenario produced no issue")
        out.append({"episode": f"D{n:02d}", "scenario": name, "seed": seed, "issues": types, "passed": not problems,
                    "problems": problems[:10]})
    return out


def policies(work: str, label: str) -> List[Dict[str, Any]]:
    rows = []
    for n in range(1, 21):
        d = os.path.join(work, f"{label}{n:02d}")
        for policy in POLICIES:
            transcript, score, run = run_policy(ja, policy, d)
            text = json.dumps(transcript)
            leaked = [i["issue_id"] for i in load(d)["truth"]["issues"] if i["issue_id"] in text]
            rewards_visible = {float(step[1]) for step in transcript[1:]}
            rows.append({"episode": f"{label}{n:02d}", "policy": policy, "isolated_process": run["isolated"],
                         "outcome": run["outcome"], "inspection_steps": run["inspection_steps"],
                         "wall_time_s": run["wall_time_s"], **score,
                         "closed_reward_visible": sorted(rewards_visible), "issue_ids_leaked": leaked,
                         "policy_stderr": run.get("policy_stderr", "")[-300:]})
    return rows


def block3(work: str) -> List[Dict[str, Any]]:
    src = os.path.join(work, "D01")
    data = load(src)
    docs = [d["doc_id"] for d in data["invoices"]]
    rows = [r["row_id"] for r in data["ledger"]]
    eid = data["manifest"]["episode_id"]
    r1, d1, d2 = docs[0], rows[0], rows[1]

    def fixture(name, issues):
        d = os.path.join(work, name)
        shutil.copytree(src, d)
        fx = copy.deepcopy(data)
        fx["truth"] = {"schema_version": "1.0", "episode_id": eid, "issues": issues}
        save(d, fx)
        return d

    def A(iid="ISS-A", affected=None):
        return {"issue_id": iid, "type": "duplicate_invoice", "affected_ids": affected or [r1],
                "expected_amount_minor": 10000, "currency": "KZT", "tolerance_minor": 0, "evidence_sets": [[d1, d2]]}

    def F(**kw):
        f = {"type": "duplicate_invoice", "affected_ids": [r1], "claimed_amount_minor": 10000, "currency": "KZT",
             "evidence_ids": [d1, d2], "explanation": "ok"}
        f.update(kw)
        return f

    one, empty = fixture("T_A", [A()]), fixture("T_EMPTY", [])
    two = fixture("T_AB", [A(), A("ISS-B", [docs[1]])])
    five = [F(affected_ids=[x], type=t) for x in docs[:5]
            for t in ("duplicate_invoice", "payment_allocation", "fx_conversion", "period_cutoff")]
    cases = [
        (1, one, [F()], 10, (1, 0, 0, 1, 1, 1, Fraction(98, 100))),
        (2, one, [], 10, (0, 0, 1, 0, 0, 0, -1)),
        (3, empty, [], 10, (0, 0, 0, 0, 0, 1, Fraction(98, 100))),
        (4, empty, [F()], 10, (0, 1, 0, 0, 0, 0, -1)),
        (5, one, [F(), F()], 10, (1, 1, 0, Fraction(1, 2), 1, Fraction(2, 3), Fraction(47, 150))),
        (6, one, [F(type="fx_conversion")], 10, (0, 1, 1, 0, 0, 0, -1)),
        (7, one, [F(affected_ids=[docs[2]])], 10, (0, 1, 1, 0, 0, 0, -1)),
        (8, one, [F(claimed_amount_minor=10001)], 10, (0, 1, 1, 0, 0, 0, -1)),
        (9, one, [F(currency="USD")], 10, (0, 1, 1, 0, 0, 0, -1)),
        (10, one, [F(evidence_ids=[d1])], 10, (0, 1, 1, 0, 0, 0, -1)),
        (11, one, [F(evidence_ids=[d1, "NOT-IN-FIXTURE"])], 10, (0, 1, 1, 0, 0, 0, -1)),
        (12, one, five, 10, (1, 19, 0, Fraction(1, 20), 1, Fraction(2, 21), Fraction(-871, 1050))),
        (13, two, [F()], 10, (1, 0, 1, 1, Fraction(1, 2), Fraction(2, 3), Fraction(47, 150))),
        (14, two, [F(), F(affected_ids=[docs[3]])], 10, (1, 1, 1, Fraction(1, 2), Fraction(1, 2), Fraction(1, 2), Fraction(-2, 100))),
        (15, one, [F()], 0, (1, 0, 0, 1, 1, 1, 1)),
        (16, one, [F()], 50, (1, 0, 0, 1, 1, 1, Fraction(90, 100))),
        (17, one, [F()], 100, (1, 0, 0, 1, 1, 1, Fraction(80, 100))),
        (18, one, [F()] * 31, 10, None),
        (19, one, [F(evidence_ids=[d1, d2] + [f"X{i}" for i in range(9)])], 10, None),
        (20, one, [F(explanation="a" * 1001)], 10, None),
    ]
    out = []
    for n, d, findings, steps, expected in cases:
        got = ja.score_episode(d, {"episode_id": eid, "findings": findings}, steps)
        if expected is None:
            ok = got["return"] == -1 and all(got[k] is None for k in ("tp", "fp", "fn", "precision", "recall", "f1"))
        else:
            keys = ("tp", "fp", "fn", "precision", "recall", "f1", "return")
            ok = all(abs(float(got[k]) - float(v)) <= 1e-9 for k, v in zip(keys, expected))
        out.append({"test": n, "passed": ok, "got": got})
    return out


def block4(work: str) -> List[Dict[str, Any]]:
    out = []
    for n in range(1, 21):
        permute, rename = (n <= 5 or n > 10), n > 5
        src, dst = os.path.join(work, f"D{n:02d}"), os.path.join(work, f"P{n:02d}")
        mapping, counts = transform(src, dst, permute, rename, seed=1000 + n)
        inverse = {v: k for k, v in mapping.items()}
        a, b = load(src), load(dst)
        t1, s1, _ = run_policy(ja, "oracle", src)
        t2, s2, _ = run_policy(ja, "oracle", src)
        t3, s3, _ = run_policy(ja, "oracle", dst)

        def canon(truth, m):
            return sorted((i["type"], sorted(m.get(x, x) for x in i["affected_ids"]), i["expected_amount_minor"],
                           i["currency"], sorted(sorted(m.get(x, x) for x in s) for s in i["evidence_sets"]))
                          for i in truth["issues"])
        oracle_b = oracle_findings(b["truth"])
        new_ids = all(x in set(inverse) for f in oracle_b["findings"] for x in f["affected_ids"]) if rename else True
        checks = {
            "replay_exact": json.dumps(t1) == json.dumps(t2) and s1 == s2,
            "truth_equal_after_inverse_mapping": canon(b["truth"], inverse) == canon(a["truth"], {}),
            "issue_count_equal": len(a["truth"]["issues"]) == len(b["truth"]["issues"]),
            "oracle_uses_new_ids": new_ids,
            "metrics_and_reward_equal": s1 == s3,
            "evidence_still_valid": not verify(b),
            "ids_replaced": (not rename) or all(counts[s] > 0 for s in SPACES),
            "company_renamed": (not rename) or b["manifest"]["company"] != a["manifest"]["company"],
            "rows_permuted": (not permute) or [inverse.get(r["row_id"], r["row_id"]) for r in b["ledger"]]
                             != [r["row_id"] for r in a["ledger"]],
        }
        out.append({"pair": f"P{n:02d}", "permute": permute, "rename": rename, "score": s1, "score_transformed": s3,
                    **checks, "passed": all(checks.values())})
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=os.path.join(ROOT, "results", "jury_sim"))
    args = parser.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    work = tempfile.mkdtemp(prefix="jury-sim-")
    started = time.monotonic()
    try:
        b2 = block2(work)
        b4 = block4(work)
        b3 = block3(work)
        runs = policies(work, "D") + policies(work, "P")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    by_policy: Dict[str, Dict[str, Any]] = {}
    for p in POLICIES:
        rows = [r for r in runs if r["policy"] == p and r["tp"] is not None]
        tp, fp, fn = (sum(r[k] for r in rows) for k in ("tp", "fp", "fn"))
        by_policy[p] = {"episodes": len([r for r in runs if r["policy"] == p]),
                        "isolated_process": all(r["isolated_process"] for r in runs if r["policy"] == p),
                        "precision": round(tp / (tp + fp), 3) if tp + fp else 0.0,
                        "recall": round(tp / (tp + fn), 3) if tp + fn else 0.0,
                        "f1": round(2 * tp / (2 * tp + fp + fn), 3) if tp + fp + fn else 1.0,
                        "mean_return": round(sum(r["return"] for r in runs if r["policy"] == p) / len(rows), 3) if rows else None,
                        "max_wall_time_s": max(r["wall_time_s"] for r in runs if r["policy"] == p),
                        "timeouts": sum(r["outcome"] == "timeout" for r in runs if r["policy"] == p)}
    secrecy_ok = all(r["closed_reward_visible"] == [0.0] and not r["issue_ids_leaked"] for r in runs)
    oracle_ok = all(r["return"] > 0.99 and r["f1"] == 1.0 for r in runs if r["policy"] == "oracle")
    summary = {
        "block2_passed": sum(r["passed"] for r in b2), "block3_passed": sum(r["passed"] for r in b3),
        "block4_passed": sum(r["passed"] for r in b4), "policy_runs": len(runs), "closed_mode_secrecy": secrecy_ok,
        "oracle_max_reward_everywhere": oracle_ok, "policies": by_policy,
        "duration_s": round(time.monotonic() - started, 1),
    }
    report = {"summary": summary, "block2": b2, "block3": b3, "block4": b4, "policy_runs": runs}
    with open(os.path.join(args.out, "jury_sim.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)
    with open(os.path.join(args.out, "policy_runs.csv"), "w", encoding="utf-8", newline="") as fh:
        cols = ["episode", "policy", "isolated_process", "outcome", "inspection_steps", "wall_time_s", "tp", "fp", "fn",
                "precision", "recall", "f1", "return"]
        writer = csv.writer(fh)
        writer.writerow(cols)
        for r in runs:
            writer.writerow([r[c] for c in cols])
    print(json.dumps(summary, indent=2))
    for r in b2 + b4:
        if not r["passed"]:
            print("FAILED", json.dumps(r, default=str)[:1200])
    ok = (summary["block2_passed"] == 20 and summary["block3_passed"] == 20 and summary["block4_passed"] == 20
          and secrecy_ok and oracle_ok)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
