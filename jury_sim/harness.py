"""Jury-side episode runner: the environment lives here, policies in separate processes."""

import json
import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Tuple

from jury_sim.fileio import oracle_findings

WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "policy_worker.py")
TIME_LIMIT_S = 60.0


def _read_line(proc: subprocess.Popen, deadline: float) -> str:
    buf = b""
    fd = proc.stdout.fileno()
    while not buf.endswith(b"\n"):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        ready, _, _ = select.select([fd], [], [], remaining)
        if not ready:
            continue
        chunk = os.read(fd, 65536)
        if not chunk:
            raise EOFError
        buf += chunk
    return buf.decode("utf-8")


def run_policy(ja, policy: str, episode_dir: str, policy_seed: int = 7, time_limit_s: float = TIME_LIMIT_S
               ) -> Tuple[List[Any], Dict[str, Any], Dict[str, Any]]:
    """Returns (transcript, score, run info). Oracle runs here, others in a child process."""
    env = ja.make_env()
    start = time.monotonic()
    deadline = start + time_limit_s
    obs, info = env.reset(0, {"mode": "closed", "episode_dir": episode_dir, "team_config": {}})
    transcript: List[Any] = [[obs, info]]
    run: Dict[str, Any] = {"policy": policy, "isolated": policy != "oracle", "outcome": None}
    if policy == "oracle":
        with open(os.path.join(episode_dir, "truth.json"), encoding="utf-8") as fh:
            step = env.step({"name": "submit", "args": {"findings": oracle_findings(json.load(fh))}})
        transcript.append(list(step))
        run["outcome"] = "submitted"
    else:
        workdir = tempfile.mkdtemp(prefix="jury-policy-")
        proc = subprocess.Popen([sys.executable, "-I", "-B", WORKER, policy, str(policy_seed)], cwd=workdir,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env={"PATH": "/usr/bin:/bin"}, start_new_session=True)
        try:
            while True:
                proc.stdin.write((json.dumps({"type": "observation", "observation": obs}) + "\n").encode())
                proc.stdin.flush()
                try:
                    line = _read_line(proc, deadline)
                except (TimeoutError, EOFError) as exc:
                    run["outcome"] = "timeout" if isinstance(exc, TimeoutError) else "policy_exited"
                    break
                step = env.step(line)
                transcript.append(list(step))
                obs, _, terminated, truncated, _ = step
                if terminated or truncated:
                    run["outcome"] = "submitted" if terminated else "truncated"
                    break
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            run["policy_stderr"] = proc.stderr.read().decode("utf-8", errors="replace")[-2000:]
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                stream.close()
            shutil.rmtree(workdir, ignore_errors=True)
    run["wall_time_s"] = round(time.monotonic() - start, 4)
    findings = env.submitted_findings() or {"episode_id": transcript[0][0]["manifest"]["episode_id"], "findings": []}
    steps = env.inspection_steps()
    score = ja.score_episode(episode_dir, findings, steps if steps is not None else len(transcript) - 1)
    run["inspection_steps"] = steps
    return transcript, score, run
