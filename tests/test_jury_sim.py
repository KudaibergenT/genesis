"""The clean-room jury simulator and the demo, run as real subprocesses."""

import ast
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIM = os.path.join(ROOT, "jury_sim")


def test_simulator_never_imports_the_environment_package():
    for name in os.listdir(SIM):
        if name.endswith(".py"):
            tree = ast.parse(open(os.path.join(SIM, name), encoding="utf-8").read())
            for node in ast.walk(tree):
                modules = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                    [node.module or ""] if isinstance(node, ast.ImportFrom) else []
                assert not any(m.split(".")[0] == "auditenv" for m in modules), (name, modules)


def test_policy_process_cannot_import_the_evaluator():
    code = ("import sys, os; sys.path.insert(0, os.path.abspath('jury_sim'))\n"
            "import policies\n"
            "try:\n    import auditenv\n    print('IMPORTED')\nexcept ModuleNotFoundError:\n    print('BLOCKED')\n"
            "try:\n    import judge_adapter\n    print('IMPORTED')\nexcept ModuleNotFoundError:\n    print('BLOCKED')")
    out = subprocess.run([sys.executable, "-I", "-c", code], cwd=ROOT, capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin"})
    assert out.stdout.split() == ["BLOCKED", "BLOCKED"], out.stderr


def test_simulated_jury_procedure_passes(tmp_path):
    out = subprocess.run([sys.executable, "jury_sim/run.py", "--out", str(tmp_path)], cwd=ROOT, capture_output=True,
                         text=True, timeout=900, env=dict(os.environ, PYTHONHASHSEED="0"))
    assert out.returncode == 0, out.stdout[-3000:] + out.stderr[-3000:]
    report = json.loads((tmp_path / "jury_sim.json").read_text())
    s = report["summary"]
    assert (s["block2_passed"], s["block3_passed"], s["block4_passed"]) == (20, 20, 20)
    assert s["policy_runs"] == 160 and s["closed_mode_secrecy"] and s["oracle_max_reward_everywhere"]
    for name in ("random_explorer", "simple_reconciliation", "mark_everything"):
        p = s["policies"][name]
        assert p["isolated_process"] and p["timeouts"] == 0 and p["max_wall_time_s"] < 60
        assert p["mean_return"] < s["policies"]["oracle"]["mean_return"]
    assert s["policies"]["mark_everything"]["mean_return"] == -1.0
    assert all(r["passed"] for r in report["block4"])


def test_demo_runs_and_solves_the_episode(tmp_path):
    out = subprocess.run([sys.executable, "scripts/demo.py", "--out", str(tmp_path / "demo.md")], cwd=ROOT,
                         capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    text = (tmp_path / "demo.md").read_text()
    assert "F1=1.0" in text and "fx_conversion" in text and "period_cutoff" in text
