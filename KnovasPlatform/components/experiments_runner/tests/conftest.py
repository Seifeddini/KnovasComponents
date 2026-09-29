"""Fixtures for the experiments-runner tests.

Most tests start the real server as a subprocess on a unix socket and talk
HTTP to it, the way the Platform does: process sessions, the stray sweep and
the socket watchdog only mean something in a process of their own. Julia
tests skip without ``julia`` on PATH; numpy tests skip without numpy.
"""

from __future__ import annotations

import http.client
import json
import os
import pathlib
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

import pytest

RUNNER_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(RUNNER_DIR) not in sys.path:
    sys.path.insert(0, str(RUNNER_DIR))

import runner as runner_module  # noqa: E402

JULIA = shutil.which("julia")
needs_julia = pytest.mark.skipif(JULIA is None, reason="julia is not on PATH")


def _has_numpy() -> bool:
    probe = subprocess.run([sys.executable, "-I", "-c", "import numpy"], capture_output=True)
    return probe.returncode == 0


needs_numpy = pytest.mark.skipif(not _has_numpy(), reason="numpy is not installed")

#: CI sets this: a missing julia or numpy must fail the run there, not turn
#: the harness tests that matter most into quiet skips.
REQUIRE_ALL = os.environ.get("RUNNER_TESTS_REQUIRE_ALL", "").strip().lower() in ("1", "true", "yes")


def pytest_sessionstart(session):
    if not REQUIRE_ALL:
        return
    missing = [name for name, present in (("julia", JULIA is not None), ("numpy", _has_numpy()))
               if not present]
    if missing:
        raise pytest.UsageError("RUNNER_TESTS_REQUIRE_ALL is set but %s is missing" % " and ".join(missing))


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._path)
        self.sock = sock


class RunnerProcess:
    """One runner server in a subprocess, listening on its own socket."""

    def __init__(self, base: pathlib.Path, env: dict | None = None, *, wait: bool = True) -> None:
        # AF_UNIX paths are limited to 107 bytes; pytest's tmp_path can be longer.
        self.sock_dir = tempfile.mkdtemp(prefix="xr-", dir="/tmp")
        self.socket_path = os.path.join(self.sock_dir, "runner.sock")
        self.job_root = base / "jobs"
        self.log_path = base / "runner.log"
        full_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "RUNNER_LISTEN": "unix:" + self.socket_path,
            "RUNNER_TMP_DIR": str(self.job_root),
            "RUNNER_PYTHON": sys.executable,
            "RUNNER_JULIA": "/nonexistent/julia",
            "RUNNER_JULIA_DEPOT": str(base / "shared-depot"),
            "RUNNER_MIN_FREE_MB": "1",
        }
        full_env.update(env or {})
        self.env = full_env
        listen = full_env["RUNNER_LISTEN"]
        if listen.startswith("unix:"):
            self.socket_path = listen[len("unix:"):]
        self._log = open(self.log_path, "wb")
        self.proc = subprocess.Popen(
            [sys.executable, "-I", str(RUNNER_DIR / "runner.py")],
            env=full_env, stdout=self._log, stderr=subprocess.STDOUT, start_new_session=True,
        )
        if wait:
            self.wait_ready()

    def wait_ready(self, timeout: float = 60.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError("runner exited early:\n" + self.log())
            if os.path.exists(self.socket_path):
                try:
                    status, _ = self.request("GET", "/health", timeout=2)
                    if status == 200:
                        return
                except OSError:
                    pass
            time.sleep(0.05)
        raise AssertionError("runner did not come up:\n" + self.log())

    def log(self) -> str:
        if not self._log.closed:
            self._log.flush()
        return self.log_path.read_text(encoding="utf-8", errors="replace")

    def request(self, method: str, path: str, body: bytes | None = None,
                headers: dict | None = None, timeout: float = 120.0):
        conn = UnixHTTPConnection(self.socket_path, timeout=timeout)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            raw = response.read()
        finally:
            conn.close()
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            payload = raw
        return response.status, payload

    def post_run(self, payload: dict, timeout: float = 120.0):
        body = json.dumps(payload).encode("utf-8")
        return self.request("POST", "/v1/run", body,
                            {"Content-Type": "application/json"}, timeout=timeout)

    def run(self, language: str, code: str, data: dict | None = None,
            timeout_seconds: int = 60) -> dict:
        status, payload = self.post_run({"language": language, "code": code,
                                         "data": data if data is not None else {},
                                         "timeout_seconds": timeout_seconds},
                                        timeout=timeout_seconds + 60)
        assert status == 200, (status, payload, self.log())
        assert set(payload) == {"ok", "output", "error", "logs", "duration_ms"}, payload
        return payload

    def health(self) -> dict:
        status, payload = self.request("GET", "/health", timeout=5)
        assert status == 200, (status, payload)
        return payload

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait(timeout=5)
        self._log.close()
        shutil.rmtree(self.sock_dir, ignore_errors=True)


@pytest.fixture
def start_runner(tmp_path):
    """Factory: start_runner(env={...}) -> RunnerProcess, stopped at teardown."""
    started = []

    def _start(env: dict | None = None, *, wait: bool = True, name: str = "r") -> RunnerProcess:
        base = tmp_path / ("%s%d" % (name, len(started)))
        base.mkdir()
        proc = RunnerProcess(base, env, wait=wait)
        started.append(proc)
        return proc

    yield _start
    for proc in started:
        proc.stop()


@pytest.fixture
def py_runner(start_runner):
    """A runner with Python only (Julia switched off for speed)."""
    return start_runner()


@pytest.fixture
def jl_runner(start_runner):
    if JULIA is None:
        pytest.skip("julia is not on PATH")
    return start_runner({"RUNNER_JULIA": JULIA})


def pid_alive(pid: int) -> bool:
    """True while the process exists and is not a zombie."""
    try:
        with open("/proc/%d/stat" % pid, "rb") as handle:
            raw = handle.read()
    except OSError:
        return False
    return raw[raw.rindex(b")") + 2:].split()[0] not in (b"Z", b"X")


def wait_dead(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.05)
    return not pid_alive(pid)


def wait_for_file(path: pathlib.Path, timeout: float = 10.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            text = path.read_text()
        except OSError:
            text = ""
        if text.strip():
            return text.strip()
        time.sleep(0.05)
    raise AssertionError("%s was never written" % path)
