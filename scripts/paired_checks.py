"""Run the 20 jury-style paired checks (P01-P20) and save results.

Usage: python scripts/paired_checks.py [--out results/paired_checks] [--keep-episodes]
Writes paired_checks.json and paired_checks.csv; exit code 0 only if all 20 pairs pass.
"""

import argparse
import csv
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "src"), ROOT]

import judge_adapter as ja  # noqa: E402
from auditenv.paired import run_all_pairs  # noqa: E402

COLUMNS = ("pair", "episode", "scenario", "permute", "rename", "issue_count", "issue_types", "f1", "return",
           "ledger_rows_permuted", "documents_permuted", "ids_replaced", "company_renamed", "old_ids_absent_in_copy",
           "truth_equal_after_inverse_mapping", "evidence_valid_in_copy", "pre_injection_valid_in_copy",
           "oracle_uses_new_ids", "reproducible", "solver_findings_equal_after_inverse_mapping", "passed")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=os.path.join(ROOT, "results", "paired_checks"))
    parser.add_argument("--keep-episodes", action="store_true")
    args = parser.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    workdir = os.path.join(args.out, "episodes") if args.keep_episodes else tempfile.mkdtemp(prefix="auditenv-paired-")
    os.makedirs(workdir, exist_ok=True)
    try:
        results = run_all_pairs(ja, workdir)
    finally:
        if not args.keep_episodes:
            shutil.rmtree(workdir, ignore_errors=True)
    passed = sum(r["passed"] for r in results)
    with open(os.path.join(args.out, "paired_checks.json"), "w", encoding="utf-8") as fh:
        json.dump({"passed": passed, "total": len(results), "pairs": results}, fh, indent=2, default=str)
    with open(os.path.join(args.out, "paired_checks.csv"), "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(COLUMNS)
        for r in results:
            writer.writerow([json.dumps(r[c]) if isinstance(r[c], (dict, list, tuple)) else r[c] for c in COLUMNS])
    for r in results:
        print(f"{r['pair']} {r['scenario']:34s} permute={r['permute']!s:5} rename={r['rename']!s:5} "
              f"issues={r['issue_count']} f1={r['f1']} return={r['return']} {'PASS' if r['passed'] else 'FAIL'}")
    print(f"{passed}/{len(results)} pairs passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
