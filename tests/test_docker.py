"""Real Docker checks (no mocks). Skipped only when no Docker daemon is reachable,
e.g. inside the evaluation container itself."""

import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGE = "genesis-auditenv:test"
LIMITS = ["--network", "none", "--cpus", "2", "--memory", "4g", "--memory-swap", "4g", "--pids-limit", "512",
          "--read-only", "--tmpfs", "/tmp:rw,size=1g", "--cap-drop", "ALL", "--cap-add", "SETUID",
          "--cap-add", "SETGID", "--cap-add", "KILL", "--security-opt", "no-new-privileges"]


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


pytestmark = pytest.mark.skipif(not _docker_available(), reason="no reachable Docker daemon")


@pytest.fixture(scope="module")
def image():
    build = subprocess.run(["docker", "build", "-q", "-t", IMAGE, "."], cwd=ROOT, capture_output=True, text=True,
                           timeout=900)
    assert build.returncode == 0, build.stderr[-3000:]
    return IMAGE


def run_python(image, code, user=None, timeout=300):
    cmd = ["docker", "run", "--rm", *LIMITS]
    if user:
        cmd += ["--user", user]
    cmd += ["--entrypoint", "python", image, "-c", code]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def test_image_is_small_and_uses_python_311(image):
    size = int(subprocess.run(["docker", "image", "inspect", image, "--format", "{{.Size}}"],
                              capture_output=True, text=True, check=True).stdout)
    assert size <= 1 << 30
    out = run_python(image, "import platform; print(platform.python_version())")
    assert out.returncode == 0 and out.stdout.strip().startswith("3.11.")


def test_container_has_no_network_and_the_official_limits(image):
    code = ("import json, socket\n"
            "def reach():\n"
            "    try:\n        socket.create_connection(('1.1.1.1', 53), timeout=2); return True\n"
            "    except OSError:\n        return False\n"
            "print(json.dumps({'net': reach(), 'cpu': open('/sys/fs/cgroup/cpu.max').read().split(),"
            " 'mem': open('/sys/fs/cgroup/memory.max').read().strip()}))")
    out = run_python(image, code)
    info = json.loads(out.stdout)
    assert info["net"] is False
    assert int(info["cpu"][0]) / int(info["cpu"][1]) == 2
    assert int(info["mem"]) == 4 << 30


def test_root_filesystem_is_read_only(image):
    out = run_python(image, "open('/app/written.txt', 'w').write('x')")
    assert out.returncode != 0 and "Read-only file system" in out.stderr


def test_truth_files_are_unreadable_for_the_agent_user(image):
    code = ("import glob\n"
            "try:\n    open('/app/episodes/train/seed_1002/truth/truth.json').read(); print('READ')\n"
            "except PermissionError:\n    print('DENIED')\n"
            "print(len(glob.glob('/app/episodes/*/*/truth/*.json')))")
    out = run_python(image, code, user="10001:10001")
    lines = out.stdout.split()
    assert lines[0] == "DENIED" and lines[1] == "0"
    assert run_python(image, "import os; print(os.path.exists('/app/episodes/train/seed_1002/books/ledger.csv'))",
                      user="10001:10001").stdout.strip() == "True"


def test_isolated_agent_runs_unprivileged_inside_the_container(image):
    code = ("import json\n"
            "from auditenv.generator import generate_episode\n"
            "from auditenv.sandbox.runner import run_isolated\n"
            "ep = generate_episode(1002)\n"
            "r = run_isolated('attack_read_files', 1002, episode=ep, mode='eval', time_limit_s=20)\n"
            "att = [json.loads(l[7:]) for l in r.agent['stderr_tail'].splitlines() if l.startswith('ATTACK ')]\n"
            "print(json.dumps({'dropped': r.agent['privileges_dropped'], 'clean': r.agent['process_group_empty'],"
            " 'local': [a['status'] for a in att if 'status' in a and a['attempt'] != 'environ'],"
            " 'reward_seen': [json.loads(m)['reward'] for m in r.sent_messages]}))")
    out = run_python(image, code)
    assert out.returncode == 0, out.stderr[-2000:]
    info = json.loads(out.stdout.strip().splitlines()[-1])
    assert info["dropped"] is True and info["clean"] is True
    assert info["local"] and set(info["local"]) == {"blocked"}
    assert set(info["reward_seen"]) == {0.0}


@pytest.mark.parametrize("mode, writable", [("0755", False), ("0777", True)])
def test_report_directory_owned_by_another_user(image, mode, writable):
    """On Linux CI the bind-mounted report directory belongs to the runner user (uid 1001); the
    evaluator is root without CAP_DAC_OVERRIDE, so docker_run_all.sh makes the directory 0777."""
    volume = f"auditenv-perm-{mode}"
    subprocess.run(["docker", "volume", "rm", "-f", volume], capture_output=True)
    subprocess.run(["docker", "volume", "create", volume], capture_output=True, check=True)
    try:
        subprocess.run(["docker", "run", "--rm", "--entrypoint", "sh", "-v", f"{volume}:/out", image, "-c",
                        f"chown 1001:1001 /out && chmod {mode} /out"], capture_output=True, check=True)
        out = subprocess.run(["docker", "run", "--rm", *LIMITS, "-v", f"{volume}:/out", "--entrypoint", "python", image,
                              "-c", "open('/out/report.json', 'w').write('{}'); print('WROTE')"],
                             capture_output=True, text=True)
        assert ("WROTE" in out.stdout) is writable, out.stderr[-500:]
        if not writable:
            assert "Permission denied" in out.stderr
    finally:
        subprocess.run(["docker", "volume", "rm", "-f", volume], capture_output=True)
    with open(os.path.join(ROOT, "scripts", "docker_run_all.sh"), encoding="utf-8") as fh:
        assert 'chmod 0777 "$OUT"' in fh.read()


def test_quick_offline_evaluation_passes_inside_the_container(image):
    cmd = ["docker", "run", "--rm", *LIMITS, image, "--out", "/tmp/report", "--quick", "--skip-tests",
           "--require-offline", "--require-limits"]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    assert out.returncode == 0, out.stdout[-3000:]
    assert '"ok": true' in out.stdout and "[FAIL]" not in out.stdout
