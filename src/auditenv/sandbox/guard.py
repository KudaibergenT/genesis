"""Audit hook installed inside the agent process after the policy is loaded."""

import os
import sys
import sysconfig
from typing import Iterable, List, Tuple

BLOCKED_EVENTS = frozenset({
    "subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork", "os.forkpty",
    "os.kill", "os.killpg", "pty.spawn",
    "socket.__new__", "socket.connect", "socket.bind", "socket.getaddrinfo", "socket.gethostbyname",
    "socket.gethostbyaddr", "socket.sendto", "socket.sendmsg",
    "urllib.Request", "http.client.connect", "ftplib.connect", "smtplib.connect", "webbrowser.open",
    "ctypes.dlopen", "ctypes.dlsym", "ctypes.cdata", "ctypes.call_function", "ctypes.addressof",
    "os.chdir", "os.chmod", "os.chown", "os.chflags", "os.mkdir", "os.mkfifo",
    "os.mknod", "os.remove", "os.rename", "os.rmdir", "os.symlink", "os.link", "os.truncate", "os.utime",
    "os.putenv", "os.unsetenv", "shutil.rmtree", "shutil.copyfile", "shutil.copymode", "shutil.move",
    "glob.glob", "pathlib.Path.glob", "pathlib.Path.rglob", "mmap.__new__", "resource.setrlimit",
    "sys._current_frames", "gc.get_referrers", "gc.get_objects",
})

_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC


class SandboxViolation(PermissionError):
    pass


def default_read_roots() -> List[str]:
    paths = sysconfig.get_paths()
    roots = {paths[k] for k in ("stdlib", "platstdlib", "purelib", "platlib") if k in paths}
    roots.add(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # the auditenv package
    roots.add(os.path.dirname(os.__file__))
    return sorted(os.path.realpath(r) for r in roots)


def _is_write(mode, flags) -> bool:
    if isinstance(flags, int) and flags & _WRITE_FLAGS:
        return True
    return isinstance(mode, str) and any(c in mode for c in "wax+")


def install(read_roots: Iterable[str] = ()) -> None:
    roots: Tuple[str, ...] = tuple(os.path.realpath(r) for r in (list(read_roots) or default_read_roots()))

    def allowed(path) -> bool:
        if isinstance(path, int):
            return path in (0, 1, 2)
        real = os.path.realpath(os.fsdecode(path))
        return any(real == r or real.startswith(r + os.sep) for r in roots)

    def hook(event: str, args) -> None:
        if event == "open":
            path, mode, flags = (tuple(args) + (None, None, None))[:3]
            if _is_write(mode, flags) or not allowed(path):
                raise SandboxViolation(f"blocked by sandbox: open {path!r}")
        elif event in ("os.listdir", "os.scandir"):
            path = args[0] if args else "."
            if path is None or not allowed(path):
                raise SandboxViolation(f"blocked by sandbox: {event} {path!r}")
        elif event in BLOCKED_EVENTS or event.startswith("os.exec") or event.startswith("os.spawn"):
            raise SandboxViolation(f"blocked by sandbox: {event}")

    sys.addaudithook(hook)
