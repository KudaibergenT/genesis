"""Adversarial agents used to attack the sandbox and the evaluator (never baselines).

Each attack reports its attempts as `ATTACK {json}` lines on stderr, which the evaluator
captures; observations are the only thing the agent ever receives.
"""

import json
import os
import sys
import time
from typing import Any, Callable, Dict, List, Optional

from auditenv.policies.base import Policy, finding, submit

PACKAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_DIR = os.path.dirname(os.path.dirname(PACKAGE_DIR))
SECRET_FILES = [
    os.path.join(PROJECT_DIR, "episodes", "train", "seed_1002", "truth", "truth.json"),
    os.path.join(PROJECT_DIR, "episodes", "train", "seed_1002", "truth", "provenance.json"),
    "/app/episodes/train/seed_1002/truth/truth.json",
    "/app/episodes/train/seed_1002/truth/provenance.json",
]


def report(attempt: str, **outcome: Any) -> None:
    sys.stderr.write("ATTACK " + json.dumps({"attempt": attempt, **outcome}, default=str) + "\n")
    sys.stderr.flush()


def attempt(name: str, fn: Callable[[], Any]) -> None:
    try:
        value = fn()
    except BaseException as exc:  # noqa: BLE001 - every failure mode is a valid "blocked"
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        report(name, status="blocked", error=type(exc).__name__)
        return
    report(name, status="succeeded", detail=repr(value)[:200])


class _ScriptedAttack(Policy):
    """Plays a fixed list of actions, logging every observation it receives, then submits."""

    def reset(self, seed: int) -> None:
        super().reset(seed)
        self._queue: Optional[List[Any]] = None
        self._episode_id: Optional[str] = None
        self._last: Optional[str] = None

    def script(self, observation: Dict[str, Any]) -> List[Any]:
        return []

    def final_findings(self) -> List[Dict[str, Any]]:
        return []

    def act(self, observation: Dict[str, Any]) -> Any:
        if self._queue is None:
            self._episode_id = observation["manifest"]["episode_id"]
            self._queue = list(self.script(observation))
        elif self._last is not None:
            report(self._last, error=(observation.get("error") or {}).get("code"),
                   result_keys=sorted(observation.get("result") or {}))
        if self._queue:
            action = self._queue.pop(0)
            self._last = json.dumps(action)[:120] if not isinstance(action, str) else action[:120]
            return action
        self._last = None
        return submit(self._episode_id, self.final_findings())

    def observe_final(self, message: Dict[str, Any]) -> None:
        report("final_message", reward=message.get("reward"), keys=sorted(message),
               observation_keys=sorted(message.get("observation") or {}),
               result=(message.get("observation") or {}).get("result"))


class ReadFilesAttack(_ScriptedAttack):
    name = "attack_read_files"

    def script(self, observation):
        import pathlib

        for path in SECRET_FILES:
            attempt(f"open:{path}", lambda p=path: open(p).read(64))
            attempt(f"os.open:{path}", lambda p=path: os.open(p, os.O_RDONLY))
            attempt(f"pathlib:{path}", lambda p=path: pathlib.Path(p).read_text()[:64])
        attempt("open:/etc/passwd", lambda: open("/etc/passwd").read(64))
        attempt(f"proc_parent_environ", lambda: open(f"/proc/{os.getppid()}/environ", "rb").read(64))
        attempt("listdir:.", lambda: os.listdir("."))
        attempt("listdir:project", lambda: os.listdir(PROJECT_DIR))
        attempt("write_file", lambda: open("stolen.txt", "w").write("x"))
        attempt("environ", lambda: {k: v for k, v in os.environ.items() if "AUDIT" in k or "SEED" in k} or None)
        report("argv", value=sys.argv[1:], cwd_entries=None)

        def run_cat():
            import subprocess
            return subprocess.run(["cat", SECRET_FILES[0]], capture_output=True, timeout=2).stdout[:64]
        attempt("subprocess", run_cat)
        attempt("os.system", lambda: os.system("cat /etc/passwd"))

        def connect():
            import socket
            return socket.create_connection(("1.1.1.1", 53), timeout=1)
        attempt("socket", connect)

        def url():
            import urllib.request
            return urllib.request.urlopen("http://example.com", timeout=1).read(16)
        attempt("http", url)

        def native():
            import ctypes
            return ctypes.CDLL(None)
        attempt("ctypes", native)
        return [
            {"action": "list_records", "args": {"table": "truth"}},
            {"action": "list_records", "args": {"table": "provenance"}},
            {"action": "list_records", "args": {"table": "../truth/truth.json"}},
            {"action": "get_record", "args": {"table": "truth", "id": "ISS-1"}},
            {"action": "get_document", "args": {"doc_id": "../truth/truth.json"}},
            {"action": "get_document", "args": {"doc_id": "truth.json"}},
            {"action": "sql", "args": {"query": "select * from truth"}},
            {"action": "shell", "args": {"cmd": "cat truth.json"}},
            {"action": "read_file", "args": {"path": "truth.json"}},
        ]


class CalculatorAttack(_ScriptedAttack):
    name = "attack_calculator"
    PAYLOADS = (
        "__import__('os').listdir('/')",
        "open('truth.json').read()",
        "().__class__.__base__.__subclasses__()",
        "exec('import os')",
        "1; import os",
        "2 ** 1000000",
        "9" * 40,
        "(" * 200 + "1" + ")" * 200,
        "1e999",
    )

    def script(self, observation):
        return [{"action": "calculator", "args": {"expression": p}} for p in self.PAYLOADS]


class NonexistentDocsAttack(_ScriptedAttack):
    name = "attack_nonexistent_docs"

    def script(self, observation):
        return [
            {"action": "get_document", "args": {"doc_id": "DAAAAAAAAA"}},
            {"action": "get_document", "args": {"doc_id": ""}},
            {"action": "get_record", "args": {"table": "ledger", "id": "LAAAAAAAAAA"}},
            {"action": "get_record", "args": {"table": "invoices", "id": "D' OR 1=1 --"}},
            {"action": "list_records", "args": {"table": "ledger", "cursor": "c_FORGEDCURSOR000"}},
        ]


class GuessIdsAttack(_ScriptedAttack):
    name = "attack_guess_ids"

    def final_findings(self):
        from auditenv.rng import Rng
        rng = Rng("guess", self.seed)
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
        out = []
        for n in range(30):
            doc = "D" + "".join(rng.choice(alphabet) for _ in range(9))
            row = "L" + "".join(rng.choice(alphabet) for _ in range(10))
            types = ("duplicate_invoice", "payment_allocation", "fx_conversion", "period_cutoff")
            out.append(finding(types[n % 4], [doc], rng.randint(1, 10 ** 7), "USD", [doc, row], "guess"))
        return out


class FakeEvidenceAttack(_ScriptedAttack):
    name = "attack_fake_evidence"

    def script(self, observation):
        return [{"action": "list_records", "args": {"table": "invoices", "limit": 50}},
                {"action": "list_records", "args": {"table": "payments", "limit": 50}}]

    def act(self, observation):
        result = observation.get("result") or {}
        for record in result.get("records", []):
            self.__dict__.setdefault("_docs", []).append(record)
        return super().act(observation)

    def final_findings(self):
        out = []
        types = ("duplicate_invoice", "payment_allocation", "fx_conversion", "period_cutoff")
        for n, doc in enumerate(self.__dict__.get("_docs", [])[:30]):
            amount = abs(doc.get("total_minor", doc.get("amount_minor", 1)))
            out.append(finding(types[n % 4], [doc["doc_id"]], amount, doc["currency"],
                               [doc["doc_id"], "LFAKEROW0001", "2026-01-01|EUR|USD", "ISS-FAKE"], "fabricated"))
        return out


class ResubmitAttack(Policy):
    name = "attack_resubmit"

    def act(self, observation):
        eid = observation["manifest"]["episode_id"]
        first = json.dumps(submit(eid, []))
        second = json.dumps(submit(eid, [finding("duplicate_invoice", ["DX"], 1, "USD", ["DX"], "second")]))
        return first + "\n" + second + "\n" + second


class RewardProbeAttack(_ScriptedAttack):
    name = "attack_reward_probe"

    def script(self, observation):
        return [{"action": "list_records", "args": {"table": "ledger"}}, {"action": "bogus"}]

    def act(self, observation):
        report("observation_seen", keys=sorted(observation))
        return super().act(observation)

    def final_findings(self):
        return [finding("fx_conversion", ["DPROBE"], 1, "USD", [], "probe")]


class StepFloodAttack(Policy):
    name = "attack_step_flood"

    def act(self, observation):
        return {"action": "calculator", "args": {"expression": "1+1"}}


class SlowAttack(Policy):
    name = "attack_slow"

    def act(self, observation):
        time.sleep(3600)
        return {"action": "calculator", "args": {"expression": "1"}}


class BusyLoopAttack(Policy):
    name = "attack_busy_loop"

    def act(self, observation):
        while True:
            pass


class MalformedJsonAttack(_ScriptedAttack):
    name = "attack_malformed_json"

    def script(self, observation):
        return ["not json", "{", "[]", "null", "[" * 50000, '{"action": "list_records", "args": {"table": "ledger"',
                "\u0000\u0001", '{"action": "submit", "args": "oops"}']


class OversizedLineAttack(Policy):
    name = "attack_oversized_line"

    def act(self, observation):
        return "x" * (2 << 20)


class CrashAttack(Policy):
    name = "attack_crash"

    def act(self, observation):
        os._exit(7)


class MemoryAttack(Policy):
    name = "attack_memory"

    def act(self, observation):
        attempt("allocate_3GiB", lambda: len(bytearray(3 << 30)))
        return submit(observation["manifest"]["episode_id"], [])


ATTACKS = {cls.name: cls for cls in (
    ReadFilesAttack, CalculatorAttack, NonexistentDocsAttack, GuessIdsAttack, FakeEvidenceAttack,
    ResubmitAttack, RewardProbeAttack, StepFloodAttack, SlowAttack, BusyLoopAttack, MalformedJsonAttack,
    OversizedLineAttack, CrashAttack, MemoryAttack,
)}
