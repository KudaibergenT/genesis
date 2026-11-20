"""Single offline verification command for the whole project.

    python scripts/run_all.py [--out DIR] [--quick] [--skip-tests] [--require-offline] [--require-limits]

Writes DIR/run_all.json, DIR/run_all.csv, per-policy trajectories/results and junit.xml.
Exit code 0 only if every check passes.
"""

import argparse
import copy
import csv
import importlib.metadata
import json
import os
import platform
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import traceback
import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
os.environ.setdefault("AUDITENV_ROOT", ROOT)

from auditenv import canon, checks  # noqa: E402
from auditenv.books import books_files  # noqa: E402
from auditenv.cli import split_episodes  # noqa: E402
from auditenv.env import ACTIONS, AuditEnv  # noqa: E402
from auditenv.evaluator import Grader  # noqa: E402
from auditenv.evaluator.analysis import shapes  # noqa: E402
from auditenv.generator import generate_episode  # noqa: E402
from auditenv.policies import OFFICIAL_BASELINES  # noqa: E402
from auditenv.runner import write_jsonl  # noqa: E402
from auditenv.sandbox.runner import run_isolated, run_official  # noqa: E402
from auditenv.version import ENV_VERSION, GENERATOR_VERSION  # noqa: E402

RESULTS: List[Dict[str, Any]] = []
FORBIDDEN_KEYS = ("truth", "provenance", "issue_id", "expected_amount_minor", "evidence_sets", "\"tp\"", "\"fp\"",
                  "\"fn\"", "precision", "recall", "\"f1\"", "seed", "mutations", "id_map")


def check(section: str, name: str, fn: Callable[[], Any]) -> Any:
    start = time.monotonic()
    try:
        value = fn()
        status, details = ("pass", value) if not isinstance(value, tuple) else value
    except AssertionError as exc:
        status, details = "fail", str(exc) or "assertion failed"
    except Exception as exc:  # report, never crash the whole run
        status, details = "fail", f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}"
    RESULTS.append({"section": section, "check": name, "status": status,
                    "duration_s": round(time.monotonic() - start, 3), "details": details})
    mark = {"pass": "PASS", "fail": "FAIL", "info": "INFO", "skip": "SKIP"}[status]
    print(f"[{mark}] {section}/{name} ({RESULTS[-1]['duration_s']}s)", flush=True)
    return details


# ---------------------------------------------------------------------------- 1. environment
def env_checks(args) -> None:
    def python():
        version = platform.python_version()
        assert version.startswith("3.11."), f"Python 3.11 required, found {version}"
        return {"python": version, "implementation": platform.python_implementation(), "platform": platform.platform(),
                "machine": platform.machine()}

    def dependencies():
        found = {}
        with open(os.path.join(ROOT, "requirements-dev.lock"), encoding="utf-8") as fh:
            for line in fh:
                if line.strip() and not line.startswith("#"):
                    name, version = line.split()[0].split("==")
                    found[name] = importlib.metadata.version(name)
                    assert found[name] == version, f"{name} {found[name]} != pinned {version}"
        return {"pinned_test_dependencies": found, "runtime_dependencies": "standard library only",
                "env_version": ENV_VERSION, "generator_version": GENERATOR_VERSION}

    def network():
        try:
            socket.create_connection(("1.1.1.1", 53), timeout=2).close()
            reachable = True
        except OSError:
            reachable = False
        if args.require_offline:
            assert not reachable, "network is reachable but offline evaluation was required"
            return {"internet_reachable": reachable}
        return "info", {"internet_reachable": reachable}

    def resources():
        info: Dict[str, Any] = {"os_cpu_count": os.cpu_count()}
        for name in ("cpu.max", "memory.max", "pids.max"):
            path = os.path.join("/sys/fs/cgroup", name)
            if os.path.exists(path):
                with open(path) as fh:
                    info[name] = fh.read().strip()
        if args.require_limits:
            quota, period = info["cpu.max"].split()
            assert quota != "max" and int(quota) / int(period) <= 2, f"cpu limit {info['cpu.max']}"
            assert info["memory.max"] != "max" and int(info["memory.max"]) <= 4 << 30, f"memory {info['memory.max']}"
            return info
        return "info", info

    def solution_size():
        total = 0
        for base, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in (".git", ".venv", "__pycache__", ".pytest_cache", ".hypothesis")]
            total += sum(os.path.getsize(os.path.join(base, f)) for f in files if not os.path.islink(os.path.join(base, f)))
        assert total <= 1 << 30, f"solution is {total} bytes"
        return {"bytes": total, "megabytes": round(total / (1 << 20), 1), "limit_megabytes": 1024}

    check("environment", "python_3_11", python)
    check("environment", "pinned_dependencies", dependencies)
    check("environment", "network", network)
    check("environment", "resource_limits", resources)
    check("environment", "solution_size", solution_size)


# ---------------------------------------------------------------------------- 2. generation
def generation_checks(args) -> None:
    def golden():
        with open(os.path.join(ROOT, "episodes", "index.json"), encoding="utf-8") as fh:
            index = {(e["split"], e["seed"]): e for e in json.load(fh)["episodes"]}
        n = 0
        for split in ("train", "dev"):
            for seed, cfg in split_episodes(split):
                ep = generate_episode(seed, cfg)
                assert canon.hash_obj(books_files(ep.public)) == index[(split, seed)]["books_sha256"], (split, seed)
                assert canon.hash_obj(ep.truth) == index[(split, seed)]["truth_sha256"], (split, seed)
                assert checks.verify_episode(ep.public, ep.truth) == [], (split, seed)
                n += 1
        return {"episodes_regenerated_byte_identical": n}

    def stage2():
        counts = []
        for seed, cfg in split_episodes("stage2_eval"):
            ep = generate_episode(seed, cfg)
            assert checks.verify_episode(ep.public, ep.truth) == [], seed
            counts.append(len(ep.truth["issues"]))
        return {"episodes": len(counts), "issues": sum(counts), "clean": counts.count(0)}

    def all_issue_counts():
        from auditenv.suites import exactly
        for k in range(13):
            ep = generate_episode(9500 + k, exactly(k))
            assert len(ep.truth["issues"]) == k and checks.verify_episode(ep.public, ep.truth) == [], k
        return {"issue_counts_supported": "0..12"}

    check("generation", "train_dev_golden_and_verified", golden)
    check("generation", "stage2_eval_verified", stage2)
    check("generation", "issue_counts_0_to_12", all_issue_counts)


# ---------------------------------------------------------------------------- 3. contract
def contract_checks(args) -> None:
    ep = generate_episode(1002, split_episodes("train")[1][1])

    def make():
        return AuditEnv(episode_factory=lambda s, c: ep)

    def reset_step():
        env = make()
        obs, info = env.reset(1002, {"mode": "train"})
        assert set(obs) == {"task", "manifest", "policy", "tables", "actions", "steps_remaining"} and obs["steps_remaining"] == 100
        assert set(ACTIONS) == {"list_records", "get_record", "get_document", "calculator", "submit"}
        out = env.step({"action": "list_records", "args": {"table": "ledger", "limit": 500}})
        assert len(out) == 5 and isinstance(out[0], dict) and type(out[1]) is float
        assert isinstance(out[2], bool) and isinstance(out[3], bool) and isinstance(out[4], dict)
        assert len(out[0]["result"]["records"]) == 50
        json.dumps(out[0]), json.dumps(info)
        return {"actions": sorted(ACTIONS)}

    def invalid_input():
        env = make()
        env.reset(1002)
        codes = []
        for action in ("{bad", {"action": "shell"}, {"action": "get_record", "args": {"table": "truth", "id": "x"}}, None):
            obs, reward, term, trunc, _ = env.step(action)
            assert obs["error"] and not term and not trunc and reward == 0.0
            codes.append(obs["error"]["code"])
        assert obs["steps_remaining"] == 96
        return {"error_codes": codes}

    def step_limit():
        env = make()
        env.reset(1002)
        for _ in range(100):
            out = env.step({"action": "calculator", "args": {"expression": "1+1"}})
        assert out[3] is True and out[2] is False
        assert env.step({"action": "calculator", "args": {"expression": "1"}})[0]["error"]["code"] == "episode_finished"
        return {"truncated_at_step": 100}

    def submit_and_replay():
        def transcript():
            env = make()
            parts = [canon.dumps(env.reset(1002, {"mode": "train"}))]
            for a in ({"action": "list_records", "args": {"table": "invoices"}},
                      {"action": "submit", "args": {"episode_id": ep.episode_id, "findings": []}}):
                parts.append(canon.dumps(env.step(a)))
            return parts
        a, b = transcript(), transcript()
        assert a == b and json.loads(a[-1])[2] is True
        return {"replay_identical": True}

    check("contract", "reset_step_shapes", reset_step)
    check("contract", "invalid_input_bounded_errors", invalid_input)
    check("contract", "step_limit_100", step_limit)
    check("contract", "submit_terminates_and_replay_is_exact", submit_and_replay)


# ---------------------------------------------------------------------------- 4. matcher and reward
def reward_checks(args) -> None:
    erroneous = generate_episode(2003, split_episodes("dev")[2][1])
    clean = generate_episode(2001, split_episodes("dev")[0][1])

    def findings(ep):
        return [{"type": i["type"], "affected_ids": i["affected_ids"], "claimed_amount_minor": i["expected_amount_minor"],
                 "currency": i["currency"], "evidence_ids": i["evidence_sets"][0], "explanation": ""}
                for i in ep.truth["issues"]]

    def g(ep, f, steps=0):
        return Grader(ep.truth).grade({"episode_id": ep.episode_id, "findings": f}, steps)

    def properties():
        f = findings(erroneous)
        doc = clean.public.invoices[0]
        accuse = [{"type": "duplicate_invoice", "affected_ids": [doc["doc_id"]], "claimed_amount_minor": doc["total_minor"],
                   "currency": doc["currency"], "evidence_ids": [doc["doc_id"]], "explanation": ""}]
        junk = [dict(accuse[0], affected_ids=[d["doc_id"]], evidence_ids=[d["doc_id"]]) for d in erroneous.public.invoices]
        out = {
            "correct_vs_silence_on_erroneous": (g(erroneous, f, 30).reward, g(erroneous, []).reward),
            "silence_vs_false_accusation_on_clean": (g(clean, []).reward, g(clean, accuse).reward),
            "duplicates": (g(erroneous, f).reward, g(erroneous, f + f).reward),
            "mark_everything": g(erroneous, junk[:30]).reward,
            "step_savings": (g(erroneous, f, 10).reward, g(erroneous, f, 40).reward),
            "malformed": g(erroneous, [{"type": "x"}]).reward,
        }
        assert out["correct_vs_silence_on_erroneous"][0] > out["correct_vs_silence_on_erroneous"][1]
        assert out["silence_vs_false_accusation_on_clean"][0] > out["silence_vs_false_accusation_on_clean"][1]
        assert out["duplicates"][1] < out["duplicates"][0]
        assert out["mark_everything"] == -1.0
        assert 0 < out["step_savings"][0] - out["step_savings"][1] <= 0.0600001
        assert out["malformed"] == -1.0
        return out

    def formula_by_k():
        table = {k: round(float(shapes(k)["mark_everything_including_truth"]), 3) for k in range(13)}
        assert all((v >= 0) == (k >= 10) for k, v in table.items())
        return {"mark_everything_including_truth_by_k": table,
                "note": "official formula; k>=10 non-negative is a documented specification limitation"}

    check("matcher_reward", "required_properties", properties)
    check("matcher_reward", "official_formula_k_0_to_12", formula_by_k)


# ---------------------------------------------------------------------------- 5. baselines
def baseline_checks(args, out_dir: str) -> None:
    splits = ["train", "dev"] + ([] if args.quick else ["stage2_eval"])
    episodes = [(seed, generate_episode(seed, cfg)) for s in splits for seed, cfg in split_episodes(s)]
    table = {}
    for name in OFFICIAL_BASELINES:
        def run(name=name):
            runs = [run_official(name, seed, mode="eval", episode=ep, policy_seed=0) for seed, ep in episodes]
            write_jsonl(os.path.join(out_dir, f"trajectories_{name}.jsonl"), [t for r in runs for t in r.trajectory])
            write_jsonl(os.path.join(out_dir, f"results_{name}.jsonl"),
                        [{"episode_id": r.episode_id, "seed": r.seed, "outcome": r.outcome, "isolated": r.isolated,
                          "wall_time_s": round(r.wall_time_s, 4), **r.result} for r in runs])
            tp, fp, fn = (sum(r.result["metrics"][k] for r in runs) for k in ("tp", "fp", "fn"))
            row = {
                "episodes": len(runs),
                "isolated_process": runs[0].isolated,
                "precision": round(tp / (tp + fp), 3) if tp + fp else 0.0,
                "recall": round(tp / (tp + fn), 3) if tp + fn else 0.0,
                "f1": round(2 * tp / (2 * tp + fp + fn), 3) if tp + fp + fn else 1.0,
                "mean_reward": round(statistics.fmean(r.result["reward"] for r in runs), 3),
                "mean_inspection_steps": round(statistics.fmean(r.result["inspection_steps"] for r in runs), 1),
                "mean_wall_time_s": round(statistics.fmean(r.wall_time_s for r in runs), 4),
                "outcomes": {o: sum(1 for r in runs if r.outcome == o) for o in sorted({r.outcome for r in runs})},
            }
            if runs[0].isolated:
                assert all(r.agent["process_group_empty"] and r.agent["workdir_removed"] for r in runs), "agent cleanup"
                row["privileges_dropped"] = runs[0].agent["privileges_dropped"]
            for r in runs:  # in eval mode the agent never sees a non-zero reward
                assert all(t["reward"] == 0.0 for t in r.trajectory), "reward leaked in eval mode"
            table[name] = row
            return row
        check("baselines", name, run)

    def sanity():
        assert table["oracle"]["mean_reward"] == 1.0 and table["oracle"]["f1"] == 1.0
        assert table["flag_everything"]["mean_reward"] == -1.0
        assert table["random_explorer"]["mean_reward"] < table["oracle"]["mean_reward"]
        assert table["simple_reconciler"]["mean_reward"] < table["oracle"]["mean_reward"]
        return {"ordering": "oracle > simple_reconciler, random_explorer; flag_everything = -1"}
    check("baselines", "sanity", sanity)
    RESULTS.append({"section": "baselines", "check": "table", "status": "info", "duration_s": 0, "details": table})


# ---------------------------------------------------------------------------- 6. isolation and attacks
def isolation_checks(args) -> None:
    ep = generate_episode(1002, split_episodes("train")[1][1])
    secret = [i["issue_id"] for i in ep.truth["issues"]]
    before = {d for d in os.listdir(tempfile.gettempdir()) if d.startswith("auditenv-agent-")}

    def attack(name: str, limit: float = 15.0):
        run = run_isolated(name, 1002, mode="eval", episode=ep, time_limit_s=limit)
        assert run.agent["process_group_empty"] and run.agent["workdir_removed"], "agent process not cleaned up"
        text = "".join(run.sent_messages)
        assert not any(s in text for s in secret), "issue id leaked to the agent"
        assert not any(k in text for k in FORBIDDEN_KEYS), "closed field leaked to the agent"
        assert all(json.loads(m)["reward"] == 0.0 for m in run.sent_messages), "reward visible in eval mode"
        attempts = [json.loads(line[7:]) for line in run.agent["stderr_tail"].splitlines() if line.startswith("ATTACK ")]
        return run, attempts

    def read_files():
        run, attempts = attack("attack_read_files")
        local = [a for a in attempts if "status" in a and a["attempt"] != "environ"]
        leaked = [a for a in local if a["status"] != "blocked"]
        assert local and not leaked, f"not blocked: {leaked}"
        tools = [a for a in attempts if "error" in a and "status" not in a]
        assert tools and all(a["error"] in ("unknown_table", "not_found", "unknown_action") for a in tools)
        return {"blocked_local_attempts": len(local), "tool_attempts_rejected": len(tools),
                "privileges_dropped": run.agent["privileges_dropped"]}

    def calculator():
        _, attempts = attack("attack_calculator")
        errors = [a["error"] for a in attempts if a.get("attempt", "").startswith("{")]
        assert errors and set(errors) == {"calculator_error"}
        return {"payloads_rejected": len(errors)}

    def nonexistent():
        _, attempts = attack("attack_nonexistent_docs")
        errors = [a["error"] for a in attempts if a.get("attempt", "").startswith("{")]
        assert errors and set(errors) <= {"not_found", "invalid_cursor"}
        return {"errors": errors}

    def guessed():
        run, _ = attack("attack_guess_ids")
        assert run.result["metrics"]["tp"] == 0 and run.result["reward"] == -1.0
        return {"tp": 0, "fp": run.result["metrics"]["fp"]}

    def fake_evidence():
        run, _ = attack("attack_fake_evidence")
        assert run.result["metrics"]["tp"] == 0
        return {"tp": 0, "fp": run.result["metrics"]["fp"]}

    def resubmit():
        run, _ = attack("attack_resubmit")
        assert run.steps == 1 and run.result["metrics"]["fp"] == 0 and run.outcome == "submitted"
        return {"graded_submissions": 1}

    def reward_probe():
        run, attempts = attack("attack_reward_probe")
        final = [a for a in attempts if a["attempt"] == "final_message"]
        assert final and final[0]["reward"] == 0.0 and final[0]["result"] == {"status": "received"}
        assert run.result["reward"] == -1.0
        return {"visible_reward": 0.0, "closed_reward": run.result["reward"]}

    def step_flood():
        run, _ = attack("attack_step_flood")
        assert run.outcome == "step_limit" and run.steps == 100
        return {"steps": run.steps}

    def malformed():
        run, attempts = attack("attack_malformed_json")
        assert run.result["outcome"] == "malformed"
        return {"errors": [a.get("error") for a in attempts if "error" in a]}

    def oversized():
        run, _ = attack("attack_oversized_line")
        assert run.outcome == "protocol_violation" and run.trajectory[-1]["truncated"]
        return {"outcome": run.outcome}

    def crash():
        run, _ = attack("attack_crash")
        assert run.outcome == "agent_exited" and run.trajectory[-1]["truncated"]
        return {"exit_code": run.agent["exit_code"]}

    def memory():
        if not sys.platform.startswith("linux"):
            return "skip", "RLIMIT_AS is enforced on Linux only"
        run, attempts = attack("attack_memory")
        alloc = [a for a in attempts if a["attempt"] == "allocate_3GiB"]
        assert alloc and alloc[0]["status"] == "blocked"
        return {"allocation": alloc[0]}

    def temp_files():
        after = {d for d in os.listdir(tempfile.gettempdir()) if d.startswith("auditenv-agent-")}
        assert after <= before, f"leftover agent directories {sorted(after - before)}"
        return {"leftover_agent_dirs": 0}

    for name, fn in (("read_truth_provenance_and_filesystem", read_files), ("calculator_filesystem_access", calculator),
                     ("nonexistent_documents", nonexistent), ("guessed_ids", guessed), ("fake_evidence", fake_evidence),
                     ("resubmission", resubmit), ("reward_during_evaluation", reward_probe),
                     ("step_limit_flood", step_flood), ("malformed_json", malformed), ("oversized_line", oversized),
                     ("agent_crash", crash), ("memory_exhaustion", memory), ("temporary_files", temp_files)):
        check("isolation", name, fn)


# ---------------------------------------------------------------------------- 7. timeout
def timeout_checks(args) -> None:
    ep = generate_episode(2001, split_episodes("dev")[0][1])

    def slow(name: str, limit: float):
        def fn():
            run = run_isolated(name, 2001, mode="eval", episode=ep, time_limit_s=limit)
            assert run.outcome == "timeout", run.outcome
            assert limit <= run.wall_time_s <= limit + 3, run.wall_time_s
            assert run.agent["killed_by_evaluator"] and run.agent["process_group_empty"] and run.agent["workdir_removed"]
            assert run.trajectory[-1]["truncated"] and run.result["outcome"] == "truncated"
            return {"limit_s": limit, "wall_time_s": round(run.wall_time_s, 2), "exit_code": run.agent["exit_code"],
                    "closed_reward_empty_answer": run.result["reward"]}
        return fn

    check("timeout", f"sleeping_agent_{int(args.timeout_seconds)}s", slow("attack_slow", args.timeout_seconds))
    check("timeout", "busy_loop_agent_3s", slow("attack_busy_loop", 3.0))


# ---------------------------------------------------------------------------- 8. jury definitions
def judge_checks(args, out_dir: str) -> None:
    sys.path.insert(0, ROOT)
    import judge_adapter as ja

    def definitions():
        junit = os.path.join(out_dir, "junit_judge.xml")
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([os.path.join(ROOT, "src"), ROOT]), PYTHONHASHSEED="0")
        proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts=",
                               f"--junitxml={junit}", "tests/test_judge_checks.py"], cwd=ROOT, env=env,
                              capture_output=True, text=True)
        blocks: Dict[str, Dict[str, int]] = {}
        for case in ET.parse(junit).getroot().iter("testcase"):
            name = case.get("name", "")
            block = ("block1_contract_C01_C20" if name.startswith("test_c") else
                     "block2_matrix_D01_D20" if name.startswith("test_matrix") else
                     "block4_paired_P01_P20" if name.startswith("test_paired") else "block3_matcher_reward_and_M01_M12")
            failed = case.find("failure") is not None or case.find("error") is not None
            entry = blocks.setdefault(block, {"passed": 0, "failed": 0})
            entry["failed" if failed else "passed"] += 1
        assert proc.returncode == 0 and all(b["failed"] == 0 for b in blocks.values()), blocks
        return blocks

    def adapter_time_limit():
        directory = tempfile.mkdtemp(prefix="auditenv-judge-")
        try:
            ja.generate_episode(2001, {"max_issues": 0}, directory)
            env = ja.make_env()
            limit = 2.0 if args.quick else ja.TIME_LIMIT_S
            saved, ja.TIME_LIMIT_S = ja.TIME_LIMIT_S, limit
            try:
                env.reset(0, {"mode": "closed", "episode_dir": directory, "team_config": {}})
            finally:
                ja.TIME_LIMIT_S = saved
            env.step({"name": "list_records", "args": {"table": "ledger", "cursor": None, "limit": 50}})
            time.sleep(limit + 0.5)
            obs, reward, terminated, truncated, _ = env.step({"name": "calculator", "args": {"expression": "1"}})
            assert truncated and not terminated and reward == 0.0 and env.submitted_findings() is None
            return {"limit_s": limit, "error": obs["error"]["code"], "findings": "empty"}
        finally:
            import shutil
            shutil.rmtree(directory, ignore_errors=True)

    def paired():
        from auditenv.paired import run_all_pairs
        workdir = tempfile.mkdtemp(prefix="auditenv-paired-")
        try:
            pairs = run_all_pairs(ja, workdir)
        finally:
            import shutil
            shutil.rmtree(workdir, ignore_errors=True)
        with open(os.path.join(out_dir, "paired_checks.json"), "w", encoding="utf-8") as fh:
            json.dump(pairs, fh, indent=2, default=str)
        failed = [p["pair"] for p in pairs if not p["passed"]]
        assert not failed, f"failed pairs: {failed}"
        return {"pairs_passed": len(pairs), "f1_and_return_equal": all(p["f1"][0] == p["f1"][1] and
                                                                         p["return"][0] == p["return"][1] for p in pairs)}

    def simulator():
        sim_out = os.path.join(out_dir, "jury_sim")
        proc = subprocess.run([sys.executable, "jury_sim/run.py", "--out", sim_out], cwd=ROOT, capture_output=True,
                              text=True, env=dict(os.environ, PYTHONHASHSEED="0"))
        with open(os.path.join(sim_out, "jury_sim.json"), encoding="utf-8") as fh:
            summary = json.load(fh)["summary"]
        assert proc.returncode == 0, summary
        return summary

    check("judge", "published_definitions", definitions)
    check("judge", "clean_room_jury_simulator", simulator)
    check("judge", "paired_checks_P01_P20", paired)
    check("judge", "adapter_time_limit", adapter_time_limit)


# ---------------------------------------------------------------------------- 9. tests
def test_checks(args, out_dir: str) -> None:
    def pytest_run():
        junit = os.path.join(out_dir, "junit.xml")
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([os.path.join(ROOT, "src"), ROOT]), PYTHONHASHSEED="0")
        proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts=",
                               f"--junitxml={junit}", "tests"], cwd=ROOT, env=env, capture_output=True, text=True)
        suite = ET.parse(junit).getroot()
        suite = suite if suite.tag == "testsuite" else suite.find("testsuite")
        summary = {k: int(suite.get(k, 0)) for k in ("tests", "failures", "errors", "skipped")}
        summary["passed"] = summary["tests"] - summary["failures"] - summary["errors"] - summary["skipped"]
        summary["tail"] = proc.stdout.strip().splitlines()[-1:]
        assert proc.returncode == 0 and summary["failures"] == summary["errors"] == 0, summary
        return summary
    check("tests", "pytest_full_suite", pytest_run)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=os.path.join(ROOT, "results", "run_all"))
    parser.add_argument("--quick", action="store_true", help="skip stage2_eval baselines and use a 5 s timeout check")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip-baselines", action="store_true")
    parser.add_argument("--require-offline", action="store_true")
    parser.add_argument("--require-limits", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=None)
    args = parser.parse_args(argv)
    if args.timeout_seconds is None:
        args.timeout_seconds = 5.0 if args.quick else 60.0
    os.makedirs(args.out, exist_ok=True)
    start = time.monotonic()

    env_checks(args)
    generation_checks(args)
    contract_checks(args)
    reward_checks(args)
    if not args.skip_baselines:
        baseline_checks(args, args.out)
    isolation_checks(args)
    timeout_checks(args)
    judge_checks(args, args.out)
    if not args.skip_tests:
        test_checks(args, args.out)

    failed = [r for r in RESULTS if r["status"] == "fail"]
    report = {
        "summary": {
            "passed": sum(r["status"] == "pass" for r in RESULTS), "failed": len(failed),
            "skipped": sum(r["status"] == "skip" for r in RESULTS), "info": sum(r["status"] == "info" for r in RESULTS),
            "ok": not failed, "duration_s": round(time.monotonic() - start, 1),
            "env_version": ENV_VERSION, "generator_version": GENERATOR_VERSION, "options": vars(args),
        },
        "checks": RESULTS,
    }
    with open(os.path.join(args.out, "run_all.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)
    with open(os.path.join(args.out, "run_all.csv"), "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["section", "check", "status", "duration_s", "details"])
        for r in RESULTS:
            writer.writerow([r["section"], r["check"], r["status"], r["duration_s"],
                             json.dumps(r["details"], default=str)[:2000]])
    print(json.dumps(report["summary"], indent=2, default=str))
    for r in failed:
        print(f"FAILED {r['section']}/{r['check']}: {str(r['details'])[:800]}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
