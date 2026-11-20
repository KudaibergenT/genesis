"""Agent-side process: reads observation messages (JSONL on stdin), writes actions (JSONL on stdout).

Messages from the evaluator:
  {"type": "observation", "observation": {...}, "reward": float, "terminated": bool, "truncated": bool}
  {"type": "end"}
The policy never receives the episode seed, the truth or any grading diagnostics.
"""

import argparse
import importlib
import json
import sys
import traceback
from typing import Any, List, Optional

from auditenv.policies import POLICIES
from auditenv.policies.adversarial import ATTACKS
from auditenv.sandbox import guard


def load_policy(spec: str):
    if spec in POLICIES:
        cls = POLICIES[spec]
    elif spec in ATTACKS:
        cls = ATTACKS[spec]
    elif ":" in spec:
        module, attr = spec.split(":", 1)
        cls = getattr(importlib.import_module(module), attr)
    else:
        raise SystemExit(f"unknown policy {spec!r}")
    if getattr(cls, "requires_truth", False):
        raise SystemExit("policies that require the truth run only on the evaluator side")
    return cls()


def _encode(action: Any) -> bytes:
    if isinstance(action, bytes):
        return action.rstrip(b"\n") + b"\n"
    if isinstance(action, str):
        return action.rstrip("\n").encode("utf-8", errors="replace") + b"\n"
    try:
        return json.dumps(action, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    except (TypeError, ValueError):
        return b'{"action":"policy_error"}\n'


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="auditenv-agent")
    parser.add_argument("--policy", required=True)
    parser.add_argument("--policy-seed", type=int, default=0)
    parser.add_argument("--no-guard", action="store_true", help="development only")
    args = parser.parse_args(argv)

    policy = load_policy(args.policy)
    policy.reset(args.policy_seed)
    if not args.no_guard:
        guard.install()

    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    for raw in stdin:
        try:
            message = json.loads(raw)
        except ValueError:
            continue
        kind = message.get("type") if isinstance(message, dict) else None
        if kind == "end":
            break
        if kind != "observation":
            continue
        if message.get("terminated") or message.get("truncated"):
            final = getattr(policy, "observe_final", None)
            if final is not None:
                try:
                    final(message)
                except Exception:
                    traceback.print_exc(file=sys.stderr)
            continue
        try:
            action = policy.act(message["observation"])
        except Exception:
            traceback.print_exc(file=sys.stderr)
            action = {"action": "policy_error"}
        stdout.write(_encode(action))
        stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
