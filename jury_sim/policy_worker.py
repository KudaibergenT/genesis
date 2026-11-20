"""Policy process: JSONL observations on stdin, JSONL actions on stdout.

Started as `python -I -B policy_worker.py <policy> <seed>` from an empty directory: it has no
access to the evaluator package or the episode directory path.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from policies import POLICIES  # noqa: E402


def main() -> int:
    policy = POLICIES[sys.argv[1]]()
    policy.reset(int(sys.argv[2]))
    for line in sys.stdin:
        message = json.loads(line)
        if message.get("type") == "end":
            break
        action = policy.act(message["observation"])
        sys.stdout.write(json.dumps(action) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
