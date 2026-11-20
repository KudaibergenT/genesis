"""The single verification command runs end to end (real subprocess, quick mode, no nested pytest)."""

import csv
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_run_all_quick(tmp_path):
    out = tmp_path / "report"
    proc = subprocess.run([sys.executable, "scripts/run_all.py", "--quick", "--skip-tests", "--skip-baselines",
                           "--timeout-seconds", "2", "--out", str(out)],
                          cwd=ROOT, capture_output=True, text=True, timeout=300,
                          env=dict(os.environ, PYTHONHASHSEED="0"))
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    report = json.loads((out / "run_all.json").read_text())
    assert report["summary"]["ok"] and report["summary"]["failed"] == 0
    sections = {c["section"] for c in report["checks"]}
    assert {"environment", "generation", "contract", "matcher_reward", "isolation", "timeout"} <= sections
    rows = list(csv.DictReader((out / "run_all.csv").open()))
    assert len(rows) == len(report["checks"]) and {r["status"] for r in rows} <= {"pass", "info", "skip"}
