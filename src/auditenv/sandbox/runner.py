"""Evaluator-side runner that executes a policy in a separate, restricted OS process."""

import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from auditenv import canon
from auditenv.env import AuditEnv
from auditenv.generator import Episode, generate_episode
from auditenv.policies import POLICIES
from auditenv.runner import EPISODE_TIME_LIMIT_S, _record, run_episode
from auditenv.sandbox import AGENT_GID, AGENT_UID

MAX_LINE_BYTES = 1 << 20
STDERR_CAP = 64 << 10
SRC_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BOOTSTRAP = ("import sys; sys.path.insert(0, {src!r}); "
             "from auditenv.sandbox.agent_host import main; sys.exit(main(sys.argv[1:]))")


@dataclass
class IsolatedRun:
    episode_id: str
    policy: str
    seed: int
    trajectory: List[Dict[str, Any]]
    result: Dict[str, Any]
    steps: int
    wall_time_s: float
    outcome: str
    isolated: bool = True
    agent: Dict[str, Any] = field(default_factory=dict)
    sent_messages: List[str] = field(default_factory=list)


def _limits(time_limit_s: float, drop_privileges: bool):
    def apply() -> None:
        import resource

        cpu = int(time_limit_s) + 5
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 5))
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        if sys.platform.startswith("linux"):
            resource.setrlimit(resource.RLIMIT_AS, (1536 << 20, 1536 << 20))
            if drop_privileges:
                resource.setrlimit(resource.RLIMIT_NPROC, (16, 16))
    return apply


class _Agent:
    def __init__(self, policy: str, policy_seed: int, time_limit_s: float) -> None:
        self.workdir = tempfile.mkdtemp(prefix="auditenv-agent-")
        self.drop = hasattr(os, "geteuid") and os.geteuid() == 0
        kwargs: Dict[str, Any] = {}
        if self.drop:
            os.chmod(self.workdir, 0o711)
            kwargs.update(user=AGENT_UID, group=AGENT_GID, extra_groups=[])
        self.proc = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", BOOTSTRAP.format(src=SRC_DIR), "--policy", policy,
             "--policy-seed", str(policy_seed)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=self.workdir, env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONHASHSEED": "0"},
            close_fds=True, start_new_session=True, umask=0o077,
            preexec_fn=_limits(time_limit_s, self.drop), **kwargs,
        )
        self.selector = selectors.DefaultSelector()
        for stream in (self.proc.stdout, self.proc.stderr):
            os.set_blocking(stream.fileno(), False)
        self.selector.register(self.proc.stdout, selectors.EVENT_READ, "out")
        self.selector.register(self.proc.stderr, selectors.EVENT_READ, "err")
        os.set_blocking(self.proc.stdin.fileno(), False)
        self.out = b""
        self.err = b""
        self.out_open = True

    def _drain(self, timeout: float) -> None:
        for key, _ in self.selector.select(timeout=max(timeout, 0)):
            try:
                chunk = os.read(key.fileobj.fileno(), 65536)
            except (BlockingIOError, InterruptedError):
                continue
            if key.data == "out":
                if not chunk:
                    self.out_open = False
                    self.selector.unregister(key.fileobj)
                self.out += chunk
            else:
                if not chunk:
                    self.selector.unregister(key.fileobj)
                elif len(self.err) < STDERR_CAP:
                    self.err += chunk[: STDERR_CAP - len(self.err)]

    def send(self, payload: bytes, deadline: float) -> str:
        fd = self.proc.stdin.fileno()
        view = memoryview(payload)
        with selectors.DefaultSelector() as sel:
            sel.register(fd, selectors.EVENT_WRITE)
            while view:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return "timeout"
                if not sel.select(timeout=remaining):
                    continue
                try:
                    view = view[os.write(fd, view):]
                except BlockingIOError:
                    continue
                except (BrokenPipeError, OSError):
                    return "eof"
        return "ok"

    def read_line(self, deadline: float):
        while True:
            newline = self.out.find(b"\n")
            if newline >= 0:
                line, self.out = self.out[:newline], self.out[newline + 1:]
                if len(line) > MAX_LINE_BYTES:
                    return None, "oversized"
                return line, "ok"
            if len(self.out) > MAX_LINE_BYTES:
                return None, "oversized"
            if not self.out_open:
                return None, "eof"
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None, "timeout"
            self._drain(min(remaining, 1.0))

    def shutdown(self) -> Dict[str, Any]:
        if self.proc.poll() is None:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
            try:
                self.proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass
        group_alive = True
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            group_alive = False
        except PermissionError:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        self._drain(0)
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            try:
                stream.close()
            except OSError:
                pass
        self.selector.close()
        try:
            os.killpg(self.proc.pid, 0)
            group_empty = False
        except (ProcessLookupError, PermissionError):
            group_empty = True
        left = []
        for base, dirs, files in os.walk(self.workdir):
            left += [os.path.relpath(os.path.join(base, f), self.workdir) for f in files + dirs]
        shutil.rmtree(self.workdir, ignore_errors=True)
        return {
            "exit_code": self.proc.returncode,
            "killed_by_evaluator": group_alive,
            "process_exited": self.proc.returncode is not None,
            "process_group_empty": group_empty,
            "workdir_files_left": left,
            "workdir_removed": not os.path.exists(self.workdir),
            "privileges_dropped": self.drop,
            "stderr_tail": self.err[-4000:].decode("utf-8", errors="replace"),
        }


def run_isolated(policy: str, seed: int, config: Optional[Dict[str, Any]] = None, mode: str = "eval",
                 time_limit_s: float = EPISODE_TIME_LIMIT_S, policy_seed: int = 0,
                 episode: Optional[Episode] = None) -> IsolatedRun:
    gen_config = {k: v for k, v in (config or {}).items() if k != "mode"}
    if episode is None:
        episode = generate_episode(seed, gen_config)
    env = AuditEnv(episode_factory=lambda _seed, _cfg: episode)
    obs, info = env.reset(seed, {"mode": mode})
    episode_id = info["episode_id"]
    trajectory = [_record(episode_id, 0, None, obs, 0.0, False, False)]
    sent: List[str] = []

    agent = _Agent(policy, policy_seed, time_limit_s)
    start = time.monotonic()
    deadline = start + time_limit_s

    def deliver(observation, reward, terminated, truncated) -> str:
        message = canon.dumps({"type": "observation", "observation": observation, "reward": reward,
                               "terminated": terminated, "truncated": truncated})
        sent.append(message)
        return agent.send(message.encode("utf-8") + b"\n", deadline)

    outcome = "running"
    status = deliver(obs, 0.0, False, False)
    steps = 0
    try:
        while True:
            if status != "ok":
                outcome = {"timeout": "timeout", "eof": "agent_exited"}[status]
                break
            line, status = agent.read_line(deadline)
            if status != "ok":
                outcome = {"timeout": "timeout", "eof": "agent_exited", "oversized": "protocol_violation"}[status]
                break
            text = line.decode("utf-8", errors="replace")
            obs, reward, terminated, truncated, info = env.step(text)
            steps = info["step"]
            try:
                logged_action: Any = json.loads(text)
            except (ValueError, RecursionError):
                logged_action = text[:2000]
            trajectory.append(_record(episode_id, steps, logged_action, obs, reward, terminated, truncated))
            status = deliver(obs, reward, terminated, truncated)
            if terminated or truncated:
                outcome = "submitted" if terminated else "step_limit"
                break
    finally:
        if outcome not in ("submitted", "step_limit"):
            obs, reward, terminated, truncated, info = env.timeout()
            steps = info["step"]
            trajectory.append(_record(episode_id, steps, None, obs, reward, terminated, truncated))
            deliver(obs, reward, terminated, truncated)
        wall = time.monotonic() - start
        if agent.proc.poll() is None:
            agent.send(b'{"type":"end"}\n', time.monotonic() + 0.2)
        report = agent.shutdown()
    return IsolatedRun(episode_id, policy, seed, trajectory, env.closed_result() or {}, steps, wall, outcome,
                       agent=report, sent_messages=sent)


def run_official(policy: str, seed: int, config: Optional[Dict[str, Any]] = None, mode: str = "eval",
                 time_limit_s: float = EPISODE_TIME_LIMIT_S, policy_seed: int = 0,
                 episode: Optional[Episode] = None):
    """Baselines run isolated; the oracle needs the truth and runs only inside the evaluator."""
    if POLICIES.get(policy) is not None and POLICIES[policy].requires_truth:
        run = run_episode(POLICIES[policy](), seed, config, mode=mode, time_limit_s=time_limit_s,
                          episode=episode, policy_seed=policy_seed)
        return IsolatedRun(run.episode_id, policy, seed, run.trajectory, run.result, run.steps, run.wall_time_s,
                           "submitted" if run.trajectory[-1]["terminated"] else "step_limit", isolated=False)
    return run_isolated(policy, seed, config, mode, time_limit_s, policy_seed, episode)
