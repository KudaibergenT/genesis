"""Process isolation: the agent runs in a separate OS process and talks JSONL.

Layers (from strongest):
1. The closed truth never exists in the agent process: the environment and the grader live in
   the parent; the agent only receives observation messages.
2. In the official container the agent process runs as an unprivileged user (uid 10001) while
   truth files are readable by root only, the root filesystem is read-only and there is no network.
3. Inside the agent process a Python audit hook blocks subprocesses, sockets, ctypes, directory
   listing, writes and reads outside the interpreter and package code (defence in depth; audit
   hooks are not a sandbox against native code on their own).
4. Resource limits: wall-clock deadline in the parent, RLIMIT_CPU / RLIMIT_FSIZE / RLIMIT_NOFILE
   (and RLIMIT_AS, RLIMIT_NPROC on Linux) in the child, process-group kill on exit.
"""

AGENT_UID = 10001
AGENT_GID = 10001
