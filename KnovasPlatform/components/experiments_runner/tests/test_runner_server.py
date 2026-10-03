"""The runner server end to end, with Python jobs over its unix socket."""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time

import pytest

import runner as R
from conftest import RUNNER_DIR, needs_numpy, pid_alive, wait_dead, wait_for_file


# -- health and the socket -------------------------------------------------------------


def test_health_reports_languages_and_capacity(py_runner):
    health = py_runner.health()
    assert health["ok"] is True
    assert health["busy"] == 0
    assert health["max_concurrent"] == 2
    assert health["languages"]["python"].startswith("%d.%d." % sys.version_info[:2])
    assert "julia" not in health["languages"]


def test_socket_is_group_accessible_only(py_runner):
    info = os.lstat(py_runner.socket_path)
    assert stat.S_ISSOCK(info.st_mode)
    assert stat.S_IMODE(info.st_mode) == 0o660


def test_unknown_paths_and_methods(py_runner):
    assert py_runner.request("GET", "/nothing")[0] == 404
    assert py_runner.request("GET", "/v1/run")[0] == 404
    assert py_runner.request("POST", "/health", b"{}", {"Content-Length": "2"})[0] == 404
    assert py_runner.request("DELETE", "/v1/run")[0] == 501


def test_stale_socket_is_replaced_at_start(start_runner, tmp_path):
    first = start_runner()
    path = first.socket_path
    first.proc.send_signal(signal.SIGKILL)       # leaves the socket file behind
    first.proc.wait(timeout=5)
    assert os.path.exists(path)
    second = start_runner({"RUNNER_LISTEN": "unix:" + path})
    assert second.health()["ok"] is True


def test_refuses_to_replace_a_file_that_is_not_a_socket(start_runner):
    directory = tempfile.mkdtemp(prefix="xr-", dir="/tmp")
    try:
        target = os.path.join(directory, "not-a-socket")
        with open(target, "w") as handle:
            handle.write("keep me")
        runner = start_runner({"RUNNER_LISTEN": "unix:" + target}, wait=False)
        assert runner.proc.wait(timeout=60) == 1
        with open(target) as handle:
            assert handle.read() == "keep me"
        assert "is not a socket" in runner.log()
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_missing_socket_directory_is_reported(start_runner):
    runner = start_runner({"RUNNER_LISTEN": "unix:/nonexistent-dir/runner.sock"}, wait=False)
    assert runner.proc.wait(timeout=60) == 1
    assert "does not exist" in runner.log()


def test_exits_when_the_socket_file_disappears(py_runner):
    os.unlink(py_runner.socket_path)
    assert py_runner.proc.wait(timeout=10) == 3
    assert "socket file was removed or replaced" in py_runner.log()


def _deep_tree(top: str, name: str, depth: int) -> None:
    """``depth`` nested directories under top/name, built through descriptors
    (the full path is far longer than PATH_MAX), every other one mode 000."""
    fd = os.open(top, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for level in range(depth):
            child = name if level == 0 else "d"
            os.mkdir(child, 0o700, dir_fd=fd)
            with open(os.open("f", os.O_WRONLY | os.O_CREAT, 0o600, dir_fd=fd), "w") as handle:
                handle.write("x")
            nxt = os.open(child, os.O_RDONLY | os.O_DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = nxt
    finally:
        os.close(fd)


def test_exclusive_socket_directory_is_emptied_at_start(start_runner, tmp_path):
    # What a job can leave in the socket volume (it runs under the server's
    # uid) must not keep the next start from binding: review-security-2.
    directory = tempfile.mkdtemp(prefix="xr-", dir="/tmp")
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me")
    try:
        socket_path = os.path.join(directory, "runner.sock")
        os.mkdir(socket_path)                                   # a directory where the socket belongs
        with open(os.path.join(socket_path, "inner"), "w") as handle:
            handle.write("x")
        _deep_tree(directory, "deep", 1500)                     # deeper than any recursion limit
        for n in range(200):
            with open(os.path.join(directory, "junk%d" % n), "w") as handle:
                handle.write("x")
        os.symlink(str(outside), os.path.join(directory, "link"))
        os.symlink(str(tmp_path), os.path.join(directory, "dirlink"))
        os.mkfifo(os.path.join(directory, "fifo"))
        locked = os.path.join(directory, "locked")
        os.makedirs(os.path.join(locked, "a", "b"))
        os.chmod(os.path.join(locked, "a"), 0)
        os.chmod(locked, 0)
        os.chmod(directory, 0o707)                              # and the directory's own mode

        runner = start_runner({"RUNNER_LISTEN": "unix:" + socket_path,
                               "RUNNER_SOCKET_DIR_EXCLUSIVE": "true"})
        assert runner.health()["ok"] is True
        assert os.listdir(directory) == ["runner.sock"]
        assert stat.S_ISSOCK(os.lstat(socket_path).st_mode)
        assert stat.S_IMODE(os.stat(directory).st_mode) == R.SOCKET_DIR_MODE
        # Links were removed, never followed.
        assert outside.read_text() == "keep me"
        assert tmp_path.is_dir()
        assert "Emptied the socket directory" in runner.log()
    finally:
        R.remove_tree(directory)


def test_a_job_that_plants_a_directory_at_the_socket_path_does_not_outlast_a_restart(
        start_runner, tmp_path):
    # The attack of review-security-2: unlink the socket and put a directory
    # there. The server exits (watchdog); the next start must come up again
    # instead of refusing "exists and is not a socket" for ever.
    first = start_runner({"RUNNER_SOCKET_DIR_EXCLUSIVE": "true"})
    code = (
        "import os\n"
        "\n"
        "def evaluate(data):\n"
        "    os.unlink(data['socket'])\n"
        "    os.mkdir(data['socket'])\n"
        "    for n in range(50):\n"
        "        open(os.path.join(data['socket'], 'f%d' % n), 'w').close()\n"
        "    return {'headline': 'ok'}\n"
    )
    worker = threading.Thread(target=lambda: _swallow(first.post_run,
        {"language": "python", "code": code, "data": {"socket": first.socket_path},
         "timeout_seconds": 30}, timeout=60), daemon=True)
    worker.start()
    assert first.proc.wait(timeout=20) == 3
    assert os.path.isdir(first.socket_path)

    second = start_runner({"RUNNER_LISTEN": "unix:" + first.socket_path,
                           "RUNNER_SOCKET_DIR_EXCLUSIVE": "true"})
    assert second.health()["ok"] is True
    assert stat.S_ISSOCK(os.lstat(first.socket_path).st_mode)
    assert os.listdir(os.path.dirname(first.socket_path)) == ["runner.sock"]


def _can_mount_tmpfs() -> bool:
    if os.geteuid() != 0 or shutil.which("unshare") is None:
        return False
    probe = subprocess.run(["unshare", "-m", "--propagation", "private", "sh", "-c",
                            "d=$(mktemp -d) && mount -t tmpfs -o size=64k tmpfs \"$d\" "
                            "&& umount \"$d\"; rmdir \"$d\""], capture_output=True)
    return probe.returncode == 0


_INODE_FLOOD = r'''
import errno, http.client, json, os, socket, subprocess, sys, tempfile, time

runner_py, python, exclusive = sys.argv[1], sys.argv[2], sys.argv[3]
mnt = tempfile.mkdtemp(prefix="xr-flood-")
subprocess.run(["mount", "-t", "tmpfs", "-o", "size=1m,nr_inodes=64,mode=0770", "tmpfs", mnt],
               check=True)
planted = 0
try:
    while True:
        os.mkdir(os.path.join(mnt, "p%d" % planted))
        planted += 1
except OSError as exc:
    assert exc.errno == errno.ENOSPC, exc
jobs = tempfile.mkdtemp(prefix="xr-jobs-")
env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
       "RUNNER_LISTEN": "unix:" + os.path.join(mnt, "runner.sock"),
       "RUNNER_SOCKET_DIR_EXCLUSIVE": exclusive, "RUNNER_TMP_DIR": jobs,
       "RUNNER_PYTHON": python, "RUNNER_JULIA": "/nonexistent/julia", "RUNNER_MIN_FREE_MB": "1"}
proc = subprocess.Popen([python, "-I", runner_py], env=env, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT)
healthy = False
deadline = time.monotonic() + 60
while time.monotonic() < deadline and proc.poll() is None:
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(2)
        sock.connect(os.path.join(mnt, "runner.sock"))
        conn = http.client.HTTPConnection("localhost")
        conn.sock = sock
        conn.request("GET", "/health")
        healthy = json.loads(conn.getresponse().read()).get("ok") is True
        conn.close()
        if healthy:
            break
    except OSError:
        time.sleep(0.1)
if proc.poll() is None:
    proc.terminate()
out = proc.communicate(timeout=30)[0].decode("utf-8", "replace")
left = sorted(os.listdir(mnt))
subprocess.run(["umount", mnt])
print(json.dumps({"planted": planted, "healthy": healthy, "code": proc.returncode,
                  "left": left, "log": out[-2000:]}))
'''


@pytest.mark.skipif(not _can_mount_tmpfs(), reason="needs root and unshare to mount a tmpfs")
@pytest.mark.parametrize("exclusive", ["true", "false"])
def test_a_socket_volume_without_free_inodes(tmp_path, exclusive):
    # The other form of the attack: every inode of the small socket tmpfs is
    # used up, so bind() fails with ENOSPC. Emptying the directory frees them;
    # without it (the control run) the start fails, as it did before.
    script = tmp_path / "flood.py"
    script.write_text(_INODE_FLOOD)
    done = subprocess.run(["unshare", "-m", "--propagation", "private", sys.executable,
                           str(script), str(RUNNER_DIR / "runner.py"), sys.executable, exclusive],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    result = json.loads(done.stdout.strip().splitlines()[-1])
    assert result["planted"] > 10
    if exclusive == "true":
        assert result["healthy"] is True, result["log"]
        assert result["left"] == [], result["left"]           # the socket went at shutdown
    else:
        assert result["healthy"] is False
        assert result["code"] == 1, result["log"]
        assert "Cannot listen" in result["log"]


def test_healthcheck_command(py_runner, tmp_path):
    env = dict(py_runner.env)
    ok = subprocess.run([sys.executable, "-I", str(RUNNER_DIR / "runner.py"), "--healthcheck"],
                        env=env, capture_output=True, text=True, timeout=30)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    env["RUNNER_LISTEN"] = "unix:" + str(tmp_path / "missing.sock")
    down = subprocess.run([sys.executable, "-I", str(RUNNER_DIR / "runner.py"), "--healthcheck"],
                          env=env, capture_output=True, text=True, timeout=30)
    assert down.returncode == 1
    assert "unhealthy" in down.stdout


# -- requests ---------------------------------------------------------------------------


def _payload(**overrides):
    payload = {"language": "python", "code": "def evaluate(data):\n    return {}\n",
               "data": {}, "timeout_seconds": 10}
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("body", [
    b"not json",
    json.dumps(_payload(language="ruby")).encode(),
    json.dumps(_payload(code=None)).encode(),
    json.dumps(_payload(code="#" * (R.MAX_CODE_CHARS + 1))).encode(),
    json.dumps(_payload(data=[])).encode(),
    json.dumps(_payload(timeout_seconds=0)).encode(),
    b'{"language": "python", "code": "", "data": {"x": NaN}, "timeout_seconds": 5}',
])
def test_bad_requests_are_refused_with_400(py_runner, body):
    status, payload = py_runner.request("POST", "/v1/run", body,
                                        {"Content-Type": "application/json"})
    assert status == 400
    assert payload == {"ok": False, "error": R.MSG_BAD_REQUEST}


def test_body_over_64_mb_is_refused_without_reading_it(py_runner):
    conn = __import__("conftest").UnixHTTPConnection(py_runner.socket_path, timeout=10)
    try:
        conn.putrequest("POST", "/v1/run")
        conn.putheader("Content-Length", str(R.MAX_BODY_BYTES + 1))
        conn.endheaders()
        response = conn.getresponse()
        assert response.status == 413
        assert json.loads(response.read())["error"] == R.MSG_BODY_TOO_LARGE
    finally:
        conn.close()


def test_body_without_length_or_chunked_is_refused(py_runner):
    conn = __import__("conftest").UnixHTTPConnection(py_runner.socket_path, timeout=10)
    try:
        conn.putrequest("POST", "/v1/run")
        conn.endheaders()
        assert conn.getresponse().status == 411
    finally:
        conn.close()
    status, _ = py_runner.request("POST", "/v1/run", b"0\r\n\r\n",
                                  {"Transfer-Encoding": "chunked"})
    assert status == 400


def test_a_short_body_is_refused(py_runner):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(15)
    sock.connect(py_runner.socket_path)
    sock.sendall(b"POST /v1/run HTTP/1.0\r\nContent-Length: 100\r\n\r\n{}")
    sock.shutdown(socket.SHUT_WR)
    answer = sock.recv(4096)
    sock.close()
    assert answer.startswith(b"HTTP/1.0 400")


def test_julia_request_without_julia(py_runner):
    result = py_runner.run("julia", "evaluate(data) = Dict()")
    assert result["ok"] is False
    assert result["error"] == "Julia ist in dieser Rechenumgebung nicht verf\u00fcgbar."


# -- Python jobs ---------------------------------------------------------------------------


def test_python_happy_path(py_runner):
    code = (
        "import statistics\n"
        "\n"
        "def evaluate(data):\n"
        "    print('rows:', len(data['rows']))\n"
        "    values = [r['value'] for r in data['rows']]\n"
        "    return {'verdict': 'n/a', 'headline': 'Mittel %.1f' % statistics.mean(values),\n"
        "            'values': {'mean': statistics.mean(values), 'nan': float('nan')},\n"
        "            'variants': [(1, 2)], 'umlaut': data['text']}\n"
    )
    data = {"rows": [{"value": 1.0}, {"value": 2.0}, {"value": 4.5}], "text": "Gr\u00fcsse \U0001F600"}
    result = py_runner.run("python", code, data)
    assert result["ok"] is True, result
    assert result["error"] is None
    assert result["output"] == {"verdict": "n/a", "headline": "Mittel 2.5",
                                "values": {"mean": 2.5, "nan": None}, "variants": [[1, 2]],
                                "umlaut": "Gr\u00fcsse \U0001F600"}
    assert result["logs"] == "rows: 3\n"
    assert isinstance(result["duration_ms"], int) and result["duration_ms"] >= 0


def test_python_exception_gives_the_traceback_in_the_logs(py_runner):
    code = "def evaluate(data):\n    x = 1\n    raise ValueError('kaputt ' + str(x))\n"
    result = py_runner.run("python", code)
    assert result["ok"] is False
    assert result["output"] is None
    assert result["error"] == "Der Auswerter ist mit einem Fehler abgebrochen."
    assert 'File "evaluator.py", line 3, in evaluate' in result["logs"]
    assert "raise ValueError('kaputt ' + str(x))" in result["logs"]
    assert "ValueError: kaputt 1" in result["logs"]
    # The traceback starts at the user's code, not inside the harness.
    assert "harness.py" not in result["logs"]


@pytest.mark.parametrize("code, message, needle", [
    ("def evaluate(data:\n", R.MSG_EXCEPTION, "SyntaxError"),
    ("raise RuntimeError('beim Laden')\n", R.MSG_EXCEPTION, "RuntimeError: beim Laden"),
    ("import sys\ndef evaluate(data):\n    sys.exit(0)\n", R.MSG_EXCEPTION, "SystemExit"),
    ("x = 1\n", R.MSG_NO_EVALUATE, "defines no function evaluate"),
    ("evaluate = 5\n", R.MSG_NO_EVALUATE, "defines no function evaluate"),
    ("def evaluate(data):\n    return [1, 2]\n", R.MSG_NOT_DICT, "returned list"),
    ("def evaluate(data):\n    return None\n", R.MSG_NOT_DICT, "returned NoneType"),
    ("def evaluate(data):\n    return {'f': object()}\n", R.MSG_NOT_SERIALIZABLE, "object"),
    ("def evaluate(data):\n    d = {}\n    d['self'] = d\n    return d\n", R.MSG_NOT_SERIALIZABLE,
     "nesting"),
    ("import os\ndef evaluate(data):\n    os._exit(0)\n", R.MSG_NO_OUTPUT, "ohne Ergebnis"),
    ("import os, signal\ndef evaluate(data):\n    os.kill(os.getpid(), signal.SIGKILL)\n",
     R.MSG_LIMIT, "SIGKILL"),
    ("import os, signal\ndef evaluate(data):\n    os.kill(os.getpid(), signal.SIGSEGV)\n",
     R.MSG_CRASHED, "SIGSEGV"),
    ("def evaluate(data):\n    return {'big': bytearray(2 * 1024 ** 3)}\n", R.MSG_EXCEPTION,
     "MemoryError"),
])
def test_python_failures_have_fixed_messages(py_runner, code, message, needle):
    result = py_runner.run("python", code)
    assert result["ok"] is False
    assert result["output"] is None
    assert result["error"] == message
    assert needle in result["logs"], result["logs"]


def test_a_refused_fork_is_explained_in_the_logs(py_runner):
    # RLIMIT_NPROC counts every process and thread of the runner's uid, so a
    # job can meet EAGAIN because of other jobs (review-deploy-2). The log
    # then says so instead of leaving the author to suspect the code.
    code = ("import errno, os\n\ndef evaluate(data):\n"
            "    raise BlockingIOError(errno.EAGAIN, os.strerror(errno.EAGAIN))\n")
    result = py_runner.run("python", code)
    assert result["ok"] is False
    assert result["error"] == R.MSG_EXCEPTION
    assert "Resource temporarily unavailable" in result["logs"]
    assert result["logs"].endswith(R.NOTE_EAGAIN)
    # Not for other failures.
    other = py_runner.run("python", "def evaluate(data):\n    raise ValueError('x')\n")
    assert R.NOTE_EAGAIN not in other["logs"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root is exempt from RLIMIT_NPROC")
def test_a_fork_over_the_process_limit_is_explained_in_the_logs(py_runner):
    code = ("import os, resource\n\ndef evaluate(data):\n"
            "    resource.setrlimit(resource.RLIMIT_NPROC, (1, 1))\n"
            "    pid = os.fork()\n"
            "    if pid == 0:\n        os._exit(0)\n"
            "    return {}\n")
    result = py_runner.run("python", code)
    assert result["ok"] is False
    assert R.NOTE_EAGAIN in result["logs"], result["logs"]


def test_python_output_over_8_mb_is_refused(py_runner):
    result = py_runner.run("python", "def evaluate(data):\n    return {'x': 'a' * (9 * 1024 * 1024)}\n")
    assert result["ok"] is False
    assert result["error"] == "Die Ausgabe des Auswerters ist gr\u00f6sser als 8 MB."


def test_python_output_limit_counts_utf8_bytes(py_runner):
    # 6 MB as UTF-8 (it would be 18 MB as ASCII escapes): accepted.
    result = py_runner.run("python", "def evaluate(data):\n    return {'x': '\\u00fc' * (3 * 1024 * 1024)}\n")
    assert result["ok"] is True, result["error"]
    assert result["output"]["x"] == "\u00fc" * (3 * 1024 * 1024)


def _plant_output(kind: str) -> str:
    # The user code runs inside the harness and can replace how it writes
    # output.json: the runner must not trust what it finds there.
    return (
        "import os, sys\n"
        "\n"
        "def evaluate(data):\n"
        "    main = sys.modules['__main__']\n"
        "    def planted(job_dir, envelope):\n"
        "        target = os.path.join(job_dir, 'output.json')\n"
        "        kind = %r\n"
        "        if kind == 'symlink':\n"
        "            os.symlink(data['secret'], target)\n"
        "        elif kind == 'fifo':\n"
        "            os.mkfifo(target)\n"
        "        elif kind == 'directory':\n"
        "            os.mkdir(target)\n"
        "    main._write_envelope = planted\n"
        "    return {'x': 1}\n" % kind
    )


@pytest.mark.parametrize("kind", ["symlink", "fifo", "directory"])
def test_planted_output_is_refused(py_runner, tmp_path, kind):
    secret = tmp_path / "secret.json"
    secret.write_text('{"ok": true, "result": {"secret": "Mandantendaten"}}')
    started = time.monotonic()
    result = py_runner.run("python", _plant_output(kind), {"secret": str(secret)})
    assert time.monotonic() - started < 20
    assert result["ok"] is False
    assert result["output"] is None
    assert result["error"] == R.MSG_OUTPUT_INVALID
    assert "Mandantendaten" not in json.dumps(result)


def test_job_environment_directory_and_limits(start_runner):
    runner = start_runner({"SECRET_TOKEN": "must-not-leak", "PLATFORM_DB_PASSWORD": "nope",
                           "RUNNER_MAX_SECONDS": "7"})
    code = (
        "import os, resource, stat\n"
        "\n"
        "def evaluate(data):\n"
        "    limits = {name: list(resource.getrlimit(getattr(resource, name)))\n"
        "              for name in ('RLIMIT_CPU', 'RLIMIT_AS', 'RLIMIT_FSIZE', 'RLIMIT_NOFILE',\n"
        "                           'RLIMIT_NPROC', 'RLIMIT_CORE')}\n"
        "    with open('/proc/self/oom_score_adj') as f:\n"
        "        oom = f.read().strip()\n"
        "    cwd = os.getcwd()\n"
        "    return {'env': dict(os.environ), 'cwd': cwd, 'limits': limits, 'oom': oom,\n"
        "            'mode': stat.S_IMODE(os.stat(cwd).st_mode),\n"
        "            'session_leader': os.getsid(0) == os.getpid(),\n"
        "            'files': sorted(os.listdir(cwd)), 'umask': os.umask(0o077)}\n"
    )
    result = runner.run("python", code, timeout_seconds=1000)
    assert result["ok"] is True, result
    out = result["output"]
    cwd = out["cwd"]
    assert os.path.dirname(cwd) == str(runner.job_root)
    assert os.path.basename(cwd).startswith("xrun-")
    assert out["env"] == {
        "PATH": runner.env["PATH"], "HOME": cwd, "TMPDIR": cwd + "/tmp", "LANG": "C.UTF-8",
        "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
        "JULIA_NUM_THREADS": "1", "JULIA_DEPOT_PATH": cwd + "/depot:" + runner.env["RUNNER_JULIA_DEPOT"] + ":",
        "JULIA_LOAD_PATH": "@:@v#.#:@stdlib",
    }
    # timeout 1000 was capped to RUNNER_MAX_SECONDS=7: CPU = 7 + 5 s.
    assert out["limits"]["RLIMIT_CPU"] == [12, 13]
    assert out["limits"]["RLIMIT_AS"][0] == 1536 * 1024 * 1024
    assert out["limits"]["RLIMIT_FSIZE"][0] == 64 * 1024 * 1024
    assert out["limits"]["RLIMIT_NOFILE"][0] == 256
    assert out["limits"]["RLIMIT_NPROC"][0] == 128
    assert out["limits"]["RLIMIT_CORE"] == [0, 0]
    assert out["oom"] == "1000"
    assert out["mode"] == 0o700
    assert out["session_leader"] is True
    assert out["umask"] == 0o077
    # code and input.json are gone before the user code runs.
    assert out["files"] == ["depot", "tmp"]
    # And the whole job directory is gone after the job.
    assert not os.path.exists(cwd)
    assert os.listdir(runner.job_root) == []


def test_timeout_kills_the_job_and_its_children(py_runner, tmp_path):
    pidfile = tmp_path / "child.pid"
    code = (
        "import subprocess, sys, time\n"
        "\n"
        "def evaluate(data):\n"
        "    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])\n"
        "    with open(data['pidfile'], 'w') as f:\n"
        "        f.write(str(child.pid))\n"
        "    while True:\n"
        "        pass\n"
    )
    started = time.monotonic()
    result = py_runner.run("python", code, {"pidfile": str(pidfile)}, timeout_seconds=2)
    assert time.monotonic() - started < 15
    assert result["ok"] is False
    assert result["error"] == "Zeitlimit \u00fcberschritten."
    assert "Zeitlimit von 2 s erreicht" in result["logs"]
    assert wait_dead(int(wait_for_file(pidfile)))


def test_double_forked_survivor_is_killed_after_the_job(py_runner, tmp_path):
    pidfile = tmp_path / "daemon.pid"
    code = (
        "import os, time\n"
        "\n"
        "def evaluate(data):\n"
        "    first = os.fork()\n"
        "    if first == 0:\n"
        "        os.setsid()\n"
        "        if os.fork() == 0:\n"
        "            with open(data['pidfile'], 'w') as f:\n"
        "                f.write(str(os.getpid()))\n"
        "            time.sleep(300)\n"
        "        os._exit(0)\n"
        "    os.waitpid(first, 0)\n"
        "    while not os.path.exists(data['pidfile']) or not open(data['pidfile']).read():\n"
        "        time.sleep(0.01)\n"
        "    return {'daemon': int(open(data['pidfile']).read())}\n"
    )
    result = py_runner.run("python", code, {"pidfile": str(pidfile)})
    assert result["ok"] is True, result
    daemon = result["output"]["daemon"]
    assert wait_dead(daemon), "the double-forked process survived its job"
    assert "a job left behind" in py_runner.log()


def test_process_in_another_group_of_the_session_is_killed(py_runner, tmp_path):
    pidfile = tmp_path / "grouped.pid"
    code = (
        "import os, subprocess, sys\n"
        "\n"
        "def evaluate(data):\n"
        "    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'],\n"
        "                             process_group=0)\n"
        "    return {'pid': child.pid, 'sid': os.getsid(child.pid), 'me': os.getsid(0)}\n"
    )
    result = py_runner.run("python", code)
    assert result["ok"] is True, result
    assert result["output"]["sid"] == result["output"]["me"]
    assert wait_dead(result["output"]["pid"])


def test_background_threads_do_not_hold_the_job(py_runner):
    code = (
        "import threading, time\n"
        "\n"
        "def evaluate(data):\n"
        "    threading.Thread(target=time.sleep, args=(300,)).start()\n"
        "    return {'done': True}\n"
    )
    started = time.monotonic()
    result = py_runner.run("python", code, timeout_seconds=30)
    assert result == {**result, "ok": True, "output": {"done": True}}
    assert time.monotonic() - started < 10


def test_logs_are_capped_at_64_kb_keeping_head_and_tail(py_runner):
    code = (
        "import sys\n"
        "\n"
        "def evaluate(data):\n"
        "    print('ANFANG')\n"
        "    for i in range(20000):\n"
        "        print('Zeile %05d ' % i + 'x' * 60)\n"
        "    print('a\\x00b')\n"
        "    print('ENDE', file=sys.stderr)\n"
        "    return {}\n"
    )
    result = py_runner.run("python", code)
    assert result["ok"] is True
    logs = result["logs"]
    assert len(logs) <= 64 * 1024
    assert logs.startswith("ANFANG\n")
    assert logs.rstrip().endswith("ENDE")
    assert "Bytes der Ausgabe ausgelassen" in logs
    assert "\x00" not in logs and "a\ufffdb" in logs


def test_output_strings_are_made_safe_for_postgres(py_runner):
    result = py_runner.run("python", "def evaluate(data):\n    return {'s': 'a\\x00b', 'u': 'x\\ud800', 'k\\x00': 1}\n")
    assert result["ok"] is True, result
    assert result["output"] == {"s": "a\ufffdb", "u": "x\ufffd", "k\ufffd": 1}


@needs_numpy
def test_numpy_values_are_converted(py_runner):
    code = (
        "import numpy as np\n"
        "\n"
        "def evaluate(data):\n"
        "    a = np.array(data['x'], dtype=float)\n"
        "    return {'mean': a.mean(), 'n': np.int64(a.size), 'arr': a * 2, 'nan': np.float32('nan'),\n"
        "            'flag': np.bool_(True), 'm': np.eye(2), 'k': {np.int64(3): 'drei'}}\n"
    )
    result = py_runner.run("python", code, {"x": [1, 2, 3]})
    assert result["ok"] is True, result
    assert result["output"] == {"mean": 2.0, "n": 3, "arr": [2.0, 4.0, 6.0], "nan": None,
                                "flag": True, "m": [[1.0, 0.0], [0.0, 1.0]], "k": {"3": "drei"}}


def test_concurrent_jobs_get_separate_directories(py_runner):
    code = "import os, time\n\ndef evaluate(data):\n    time.sleep(1)\n    return {'cwd': os.getcwd()}\n"
    results = []
    threads = [threading.Thread(target=lambda: results.append(py_runner.run("python", code)))
               for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert [r["ok"] for r in results] == [True, True]
    assert results[0]["output"]["cwd"] != results[1]["output"]["cwd"]


# -- capacity ----------------------------------------------------------------------------


def _wait_busy(runner, busy: int, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if runner.health()["busy"] == busy:
            return
        time.sleep(0.05)
    raise AssertionError("runner never reached busy=%d" % busy)


def test_health_answers_while_a_job_runs(py_runner):
    code = "import time\n\ndef evaluate(data):\n    time.sleep(3)\n    return {}\n"
    worker = threading.Thread(target=py_runner.run, args=("python", code))
    worker.start()
    try:
        _wait_busy(py_runner, 1)
        started = time.monotonic()
        health = py_runner.health()
        assert time.monotonic() - started < 1.0
        assert health["busy"] == 1 and health["ok"] is True
    finally:
        worker.join()
    assert py_runner.health()["busy"] == 0


def test_503_when_every_slot_stays_busy(start_runner):
    runner = start_runner({"RUNNER_MAX_CONCURRENT": "1", "RUNNER_SLOT_WAIT_SECONDS": "1"})
    code = "import time\n\ndef evaluate(data):\n    time.sleep(4)\n    return {}\n"
    worker = threading.Thread(target=runner.run, args=("python", code))
    worker.start()
    try:
        _wait_busy(runner, 1)
        started = time.monotonic()
        status, payload = runner.post_run(_payload())
        assert status == 503
        assert payload == {"ok": False, "error": R.MSG_BUSY}
        assert time.monotonic() - started < 3.5
    finally:
        worker.join()
    assert runner.run("python", _payload()["code"])["ok"] is True


def test_queued_requests_get_503_within_the_slot_wait(start_runner):
    # One running job, four more requests: the ones queued behind the intake
    # limit must not wait twice (intake, then slot) before their 503.
    runner = start_runner({"RUNNER_MAX_CONCURRENT": "1", "RUNNER_SLOT_WAIT_SECONDS": "2"})
    code = "import time\n\ndef evaluate(data):\n    time.sleep(8)\n    return {}\n"
    worker = threading.Thread(target=runner.run, args=("python", code))
    worker.start()
    try:
        _wait_busy(runner, 1)
        outcomes = []

        def request():
            started = time.monotonic()
            status, _ = runner.post_run(_payload())
            outcomes.append((status, time.monotonic() - started))

        threads = [threading.Thread(target=request) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert [status for status, _ in outcomes] == [503] * 4
        assert max(elapsed for _, elapsed in outcomes) < 3.5
    finally:
        worker.join()


def test_503_and_not_ok_when_job_space_is_short(start_runner):
    runner = start_runner({"RUNNER_MIN_FREE_MB": str(1 << 29)})
    assert runner.health()["ok"] is False
    status, payload = runner.post_run(_payload())
    assert status == 503
    assert payload == {"ok": False, "error": R.MSG_LOW_SPACE}


def test_sigterm_stops_the_server_and_its_jobs(py_runner, tmp_path):
    pidfile = tmp_path / "job.pid"
    code = ("import os, time\n\ndef evaluate(data):\n"
            "    open(data['pidfile'], 'w').write(str(os.getpid()))\n    time.sleep(300)\n")
    worker = threading.Thread(target=lambda: _swallow(py_runner.post_run,
        {"language": "python", "code": code, "data": {"pidfile": str(pidfile)},
         "timeout_seconds": 300}, timeout=60), daemon=True)
    worker.start()
    job = int(wait_for_file(pidfile))
    py_runner.proc.send_signal(signal.SIGTERM)
    assert py_runner.proc.wait(timeout=15) == 0
    assert wait_dead(job)
    assert not os.path.exists(py_runner.socket_path)


def test_a_job_that_hijacks_the_socket_brings_the_server_down(py_runner, tmp_path):
    pidfile = tmp_path / "hijack.pid"
    code = (
        "import os, socket, time\n"
        "\n"
        "def evaluate(data):\n"
        "    open(data['pidfile'], 'w').write(str(os.getpid()))\n"
        "    os.unlink(data['socket'])\n"
        "    fake = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
        "    fake.bind(data['socket'])\n"
        "    fake.listen(5)\n"
        "    time.sleep(300)\n"
    )
    worker = threading.Thread(target=lambda: _swallow(py_runner.post_run,
        {"language": "python", "code": code,
         "data": {"socket": py_runner.socket_path, "pidfile": str(pidfile)},
         "timeout_seconds": 300}, timeout=60), daemon=True)
    worker.start()
    job = int(wait_for_file(pidfile))
    assert py_runner.proc.wait(timeout=10) == 3
    assert wait_dead(job)


def _swallow(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except OSError:
        pass


def test_job_space_that_stays_full_while_idle_makes_the_server_exit(start_runner, tmp_path):
    # A job can fill the job space outside its own directory. Once no job
    # runs, the server exits, so that Docker restarts it on a fresh tmpfs.
    info = os.statvfs(tmp_path)
    free_mb = info.f_bavail * info.f_frsize // (1024 * 1024)
    runner = start_runner({"RUNNER_MIN_FREE_MB": str(free_mb - 30)})
    assert runner.health()["ok"] is True
    code = (
        "import os\n"
        "\n"
        "def evaluate(data):\n"
        "    block = b'x' * (1024 * 1024)\n"
        "    for n in range(8):\n"
        "        with open(os.path.join(data['root'], 'junk%d' % n), 'wb') as f:\n"
        "            for _ in range(50):\n"
        "                f.write(block)\n"
        "    return {}\n"
    )
    try:
        assert runner.run("python", code, {"root": str(runner.job_root)})["ok"] is True
        assert runner.proc.wait(timeout=40) == 3
        assert "job space stays full" in runner.log()
    finally:
        for junk in runner.job_root.glob("junk*"):
            junk.unlink()
