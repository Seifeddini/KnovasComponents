"""experiments-runner: the sandbox that runs user-written evaluators.

Experiments managers can write evaluators in Python or Julia. That code is
never executed inside the Platform; the Platform sends it here, together
with the evaluator input, and gets back the output. This service is the only
place user code runs, so everything about it is arranged around containing
that code:

* The container has no network at all (``network_mode: none``). The Platform
  reaches this server over a unix socket on a shared volume, nothing else
  can reach it, and the code cannot reach anything.
* The container is non-root, read-only, without capabilities, with
  no-new-privileges and CPU/memory/pid limits (docker-compose.yml).
* Each job is its own process in its own session, with rlimits (CPU seconds,
  address space, file size, open files, processes, no core files), a high
  OOM score so the kernel kills a job before this server, an environment
  that holds nothing but a few fixed variables, and a fresh 0700 directory.
* A job that runs past its time is killed with its whole process group. After
  every job, every process that descends from this server and belongs to
  neither this server's session nor a still running job's is killed as well:
  a double-forked daemon does not survive its job. This server is a child
  subreaper, so every process a job starts stays its descendant whatever the
  process does to detach itself.
* Output is read with O_NOFOLLOW from a regular file of at most 8 MB; logs
  are capped at 64 KB. The job directory is removed afterwards.

The server is standard library only and speaks plain HTTP/1.0:

    GET  /health   -> {"ok", "languages": {"python", "julia"}, "busy", "max_concurrent"}
    POST /v1/run   {"language", "code", "data", "timeout_seconds"}
                   -> 200 {"ok", "output", "error", "logs", "duration_ms"}
                      400 bad request, 411 no Content-Length, 413 body too large,
                      503 all slots busy for 10 s or the job space is short

Error messages are German and fixed: the Platform stores and shows them as
they are. What the user code printed, and its traceback, goes to ``logs``.

Configuration (environment):
    RUNNER_LISTEN           unix:/run/experiments-runner/runner.sock (default)
                            or tcp:127.0.0.1:8090 (development only: no
                            authentication, never publish it)
    RUNNER_MAX_CONCURRENT   jobs at once (default 2)
    RUNNER_MAX_SECONDS      upper bound for a job's time limit (default 600)
    RUNNER_TMP_DIR          where job directories are made (default /tmp)
    RUNNER_PYTHON           interpreter for Python jobs (default: this one)
    RUNNER_JULIA            julia binary (default: julia on PATH)
    RUNNER_JULIA_DEPOT      read-only Julia depot with the packages
                            (default /opt/julia-depot)
    RUNNER_MIN_FREE_MB      free job space below which jobs are refused (256)
    RUNNER_SLOT_WAIT_SECONDS  how long a request waits for a free slot (10)

Usage:
    python3 -I runner.py                 serve
    python3 -I runner.py --healthcheck   probe a running server (Docker healthcheck)
    python3 -I runner.py --self-test     run a Python and a Julia evaluator once
"""

from __future__ import annotations

import collections
import ctypes
import errno
import http.client
import http.server
import json
import logging
import math
import os
import re
import resource
import shutil
import signal
import socket
import socketserver
import stat
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger("experiments_runner")

VERSION = "0.1.0"
HERE = os.path.dirname(os.path.abspath(__file__))
PY_HARNESS = os.path.join(HERE, "harness.py")
JL_HARNESS = os.path.join(HERE, "harness.jl")

LANGUAGES = ("python", "julia")
MiB = 1024 * 1024

#: Request limits (the Platform's runner_client applies the same).
MAX_CODE_CHARS = 200_000
MAX_BODY_BYTES = 64 * MiB
#: output.json a job may leave behind, and how deeply it may nest.
MAX_OUTPUT_BYTES = 8 * MiB
MAX_OUTPUT_DEPTH = 128
#: Captured stdout+stderr: the first LOG_HEAD_BYTES and the rest from the end.
MAX_LOG_CHARS = 64 * 1024
LOG_HEAD_BYTES = 8 * 1024
LOG_NOTE_RESERVE = 1024

#: Per-job rlimits. Julia reserves a large address range at start-up (and is
#: told to keep its heap near 1 GB), so its limit is higher; real memory is
#: bounded by the container's mem_limit either way.
PYTHON_AS_BYTES = 1536 * MiB
JULIA_AS_BYTES = 6 * 1024 * MiB
JULIA_HEAP_SIZE_HINT = "1G"
FSIZE_BYTES = 64 * MiB
NOFILE_LIMIT = 256
NPROC_LIMIT = 128
CPU_GRACE_SECONDS = 5
OOM_SCORE_ADJ = b"1000"

JOB_DIR_PREFIX = "xrun-"
#: Concurrent connections; health probes and queued requests included.
MAX_CONNECTIONS = 32
SOCKET_TIMEOUT_SECONDS = 60
WATCH_INTERVAL_SECONDS = 1.0
SPACE_CHECK_EVERY = 10

PR_SET_DUMPABLE = 4
PR_SET_CHILD_SUBREAPER = 36

MSG_EXCEPTION = "Der Auswerter ist mit einem Fehler abgebrochen."
MSG_TIMEOUT = "Zeitlimit \u00fcberschritten."
MSG_NOT_DICT = "Der Auswerter hat kein Objekt zur\u00fcckgegeben."
MSG_NO_EVALUATE = "Der Code definiert keine Funktion evaluate(data)."
MSG_NOT_SERIALIZABLE = "Das Ergebnis des Auswerters l\u00e4sst sich nicht als JSON schreiben."
MSG_BAD_INPUT = "Die Eingabedaten konnten im Auswerter nicht gelesen werden."
MSG_OUTPUT_TOO_LARGE = "Die Ausgabe des Auswerters ist gr\u00f6sser als 8 MB."
MSG_OUTPUT_INVALID = "Der Auswerter hat keine g\u00fcltige Ausgabe hinterlassen."
MSG_NO_OUTPUT = "Der Auswerter hat kein Ergebnis geliefert."
MSG_LIMIT = "Der Auswerter hat ein Rechenzeit- oder Speicherlimit \u00fcberschritten."
MSG_CRASHED = "Der Auswerter ist abgest\u00fcrzt."
MSG_START_FAILED = "Der Auswerter konnte nicht gestartet werden."
MSG_LANGUAGE_MISSING = "{name} ist in dieser Rechenumgebung nicht verf\u00fcgbar."
MSG_BUSY = "Die Rechenumgebung ist ausgelastet."
MSG_LOW_SPACE = "Die Rechenumgebung hat zu wenig freien Speicherplatz."
MSG_BAD_REQUEST = "Ung\u00fcltige Anfrage an die Rechenumgebung."
MSG_BODY_TOO_LARGE = "Die Anfrage ist gr\u00f6sser als 64 MB."
MSG_NOT_FOUND = "Nicht gefunden."
MSG_INTERNAL = "Interner Fehler der Rechenumgebung."

#: error_code from the harness envelope -> German message.
HARNESS_ERRORS = {
    "exception": MSG_EXCEPTION,
    "no_evaluate": MSG_NO_EVALUATE,
    "not_dict": MSG_NOT_DICT,
    "not_serializable": MSG_NOT_SERIALIZABLE,
    "bad_input": MSG_BAD_INPUT,
}
LANGUAGE_NAMES = {"python": "Python", "julia": "Julia"}
#: Signals that mean "a limit was hit" rather than "the program crashed".
LIMIT_SIGNALS = frozenset({signal.SIGKILL, signal.SIGXCPU, signal.SIGXFSZ})

DEFAULT_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


class Busy(Exception):
    """The job cannot start now (HTTP 503); nothing ran."""


class BadRequest(Exception):
    """The request is malformed (HTTP 400)."""


# -- configuration -------------------------------------------------------------


def _env_int(env: Dict[str, str], name: str, default: int, lo: int, hi: int) -> int:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s=%r is not a number; using %s.", name, raw, default)
        return default
    return max(lo, min(hi, value))


@dataclass(frozen=True)
class Config:
    listen: str = "unix:/run/experiments-runner/runner.sock"
    max_concurrent: int = 2
    max_seconds: int = 600
    job_root: str = "/tmp"
    python: str = ""
    julia: str = ""
    julia_depot: str = "/opt/julia-depot"
    min_free_bytes: int = 256 * MiB
    slot_wait_seconds: float = 10.0
    path: str = DEFAULT_PATH

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "Config":
        env = dict(os.environ if env is None else env)
        path = env.get("PATH") or DEFAULT_PATH
        julia = (env.get("RUNNER_JULIA") or "").strip()
        if not julia:
            julia = shutil.which("julia", path=path) or ""
        slot_wait = _env_int(env, "RUNNER_SLOT_WAIT_SECONDS", 10, 0, 300)
        return cls(
            listen=(env.get("RUNNER_LISTEN") or cls.listen).strip(),
            max_concurrent=_env_int(env, "RUNNER_MAX_CONCURRENT", 2, 1, 16),
            max_seconds=_env_int(env, "RUNNER_MAX_SECONDS", 600, 5, 3600),
            job_root=(env.get("RUNNER_TMP_DIR") or "/tmp").strip(),
            python=(env.get("RUNNER_PYTHON") or "").strip() or sys.executable,
            julia=julia,
            julia_depot=(env.get("RUNNER_JULIA_DEPOT") or "/opt/julia-depot").strip(),
            min_free_bytes=_env_int(env, "RUNNER_MIN_FREE_MB", 256, 0, 1 << 30) * MiB,
            slot_wait_seconds=float(slot_wait),
            path=path,
        )


def parse_listen(spec: str) -> Tuple[str, Any]:
    """``unix:/path`` (also ``unix:///path``) or ``tcp:host:port``."""
    spec = (spec or "").strip()
    if spec.startswith("unix:"):
        path = spec[len("unix:"):]
        if path.startswith("//"):
            path = path[2:]
        if not path.startswith("/") or "\x00" in path:
            raise ValueError("RUNNER_LISTEN: a unix socket needs an absolute path")
        if len(path.encode()) > 107:
            raise ValueError("RUNNER_LISTEN: the socket path is too long")
        return "unix", path
    if spec.startswith("tcp:"):
        host, sep, port = spec[len("tcp:"):].rpartition(":")
        if not sep or not host or not port.isdigit() or not 0 < int(port) < 65536:
            raise ValueError("RUNNER_LISTEN: expected tcp:host:port")
        return "tcp", (host.strip("[]"), int(port))
    raise ValueError("RUNNER_LISTEN: expected unix:/path or tcp:host:port")


# -- processes -------------------------------------------------------------------


@dataclass(frozen=True)
class ProcInfo:
    pid: int
    ppid: int
    sid: int
    state: str
    starttime: int
    uid: int


def read_proc(pid: int) -> Optional[ProcInfo]:
    """One process from /proc, or None when it is gone."""
    try:
        with open("/proc/%d/stat" % pid, "rb") as handle:
            raw = handle.read()
        with open("/proc/%d/status" % pid, "rb") as handle:
            status = handle.read()
    except OSError:
        return None
    try:
        # comm (field 2) may hold spaces and parentheses: split after the last ')'.
        fields = raw[raw.rindex(b")") + 2:].split()
        uid_line = re.search(rb"^Uid:\s+(\d+)", status, re.M)
        return ProcInfo(
            pid=pid,
            ppid=int(fields[1]),
            sid=int(fields[3]),
            state=fields[0].decode("ascii", "replace"),
            starttime=int(fields[19]),
            uid=int(uid_line.group(1)) if uid_line else -1,
        )
    except (ValueError, IndexError):
        return None


def process_table() -> Dict[int, ProcInfo]:
    table: Dict[int, ProcInfo] = {}
    try:
        names = os.listdir("/proc")
    except OSError:
        return table
    for name in names:
        if name.isdigit():
            info = read_proc(int(name))
            if info is not None:
                table[info.pid] = info
    return table


def descendants(table: Dict[int, ProcInfo], root: int) -> Set[int]:
    children: Dict[int, List[int]] = collections.defaultdict(list)
    for info in table.values():
        children[info.ppid].append(info.pid)
    found: Set[int] = set()
    todo = list(children.get(root, ()))
    while todo:
        pid = todo.pop()
        if pid in found or pid == root:
            continue
        found.add(pid)
        todo.extend(children.get(pid, ()))
    return found


def kill_verified(info: ProcInfo) -> bool:
    """SIGKILL ``info`` unless its pid now names another process.

    The pidfd is taken before the identity check, so a pid that is reused in
    between cannot receive the signal.
    """
    pidfd = None
    try:
        try:
            pidfd = os.pidfd_open(info.pid)
        except ProcessLookupError:
            return False
        except (AttributeError, OSError):
            pidfd = None
        now = read_proc(info.pid)
        if now is None or now.starttime != info.starttime or now.sid != info.sid:
            return False
        if pidfd is not None:
            try:
                signal.pidfd_send_signal(pidfd, signal.SIGKILL)
                return True
            except ProcessLookupError:
                return False
            except (AttributeError, OSError):
                pass
        os.kill(info.pid, signal.SIGKILL)
        return True
    except ProcessLookupError:
        return False
    except OSError as exc:
        logger.warning("Could not kill process %s: %s", info.pid, exc)
        return False
    finally:
        if pidfd is not None:
            os.close(pidfd)


def _libc_prctl():
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        prctl = libc.prctl
        prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
        prctl.restype = ctypes.c_int
        return prctl
    except (OSError, AttributeError):
        return None


_PRCTL = _libc_prctl()


def _prctl(option: int, value: int) -> bool:
    if _PRCTL is None:
        return False
    return _PRCTL(option, value, 0, 0, 0) == 0


def _child_setup(limits: Sequence[Tuple[int, int, int]]) -> None:
    """Runs in the forked child before exec: only async-signal-tolerant calls.

    A limit that cannot be applied raises, and Popen then fails the job
    instead of running it with less containment.
    """
    for which, soft, hard in limits:
        resource.setrlimit(which, (soft, hard))
    # The server is non-dumpable, which the child inherits until exec; while
    # it lasts, /proc/self belongs to root and the OOM score could not be set.
    _prctl(PR_SET_DUMPABLE, 1)
    try:
        fd = os.open("/proc/self/oom_score_adj", os.O_WRONLY)
        try:
            os.write(fd, OOM_SCORE_ADJ)
        finally:
            os.close(fd)
    except OSError:
        pass
    os.umask(0o077)


def job_limits(language: str, timeout_seconds: int) -> List[Tuple[int, int, int]]:
    """rlimits for one job, never above the limits the server itself has."""
    cpu = int(timeout_seconds) + CPU_GRACE_SECONDS
    wanted = [
        # SIGXCPU at the soft limit, SIGKILL one second later.
        (resource.RLIMIT_CPU, cpu, cpu + 1),
        (resource.RLIMIT_AS, PYTHON_AS_BYTES if language == "python" else JULIA_AS_BYTES, None),
        (resource.RLIMIT_FSIZE, FSIZE_BYTES, None),
        (resource.RLIMIT_NOFILE, NOFILE_LIMIT, None),
        (resource.RLIMIT_NPROC, NPROC_LIMIT, None),
        (resource.RLIMIT_CORE, 0, None),
    ]
    out = []
    for which, soft, hard in wanted:
        hard = soft if hard is None else hard
        _, current_hard = resource.getrlimit(which)
        if current_hard != resource.RLIM_INFINITY:
            soft = min(soft, current_hard)
            hard = min(hard, current_hard)
        out.append((which, soft, hard))
    return out


# -- output and logs ---------------------------------------------------------------


class LogCapture:
    """Drains a pipe, keeping the head and the tail of what came through."""

    def __init__(self, limit: int = MAX_LOG_CHARS - LOG_NOTE_RESERVE, head: int = LOG_HEAD_BYTES) -> None:
        self.head_limit = min(head, limit)
        self.tail_limit = max(0, limit - self.head_limit)
        self.head = bytearray()
        self.tail: collections.deque = collections.deque()
        self.tail_len = 0
        self.total = 0

    def feed(self, chunk: bytes) -> None:
        self.total += len(chunk)
        if len(self.head) < self.head_limit:
            take = self.head_limit - len(self.head)
            self.head += chunk[:take]
            chunk = chunk[take:]
        if not chunk or not self.tail_limit:
            return
        self.tail.append(chunk)
        self.tail_len += len(chunk)
        while self.tail and self.tail_len - len(self.tail[0]) >= self.tail_limit:
            self.tail_len -= len(self.tail.popleft())

    def drain(self, fd: int) -> None:
        while True:
            try:
                chunk = os.read(fd, 65536)
            except InterruptedError:
                continue
            except OSError:
                return
            if not chunk:
                return
            self.feed(chunk)

    def text(self) -> str:
        tail = b"".join(self.tail)
        if len(tail) > self.tail_limit:
            tail = tail[len(tail) - self.tail_limit:]
        omitted = self.total - len(self.head) - len(tail)
        parts = [bytes(self.head).decode("utf-8", "replace")]
        if omitted > 0:
            parts.append("\n[Runner] ... %d Bytes der Ausgabe ausgelassen ...\n" % omitted)
        parts.append(tail.decode("utf-8", "replace"))
        return clean_text("".join(parts))


def clean_text(text: str) -> str:
    """No NUL, no lone surrogates: PostgreSQL TEXT and JSONB accept neither."""
    if "\x00" in text:
        text = text.replace("\x00", "\ufffd")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        text = text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    return text


def clean_json(value: Any, depth: int = 0) -> Any:
    """Every string (keys included) through clean_text; non-finite floats to None."""
    if depth > MAX_OUTPUT_DEPTH:
        raise ValueError("output nested deeper than %d levels" % MAX_OUTPUT_DEPTH)
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {clean_text(str(k)): clean_json(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [clean_json(v, depth + 1) for v in value]
    return value


def _reject_constant(name: str) -> Any:
    raise ValueError("%s is not valid JSON" % name)


def read_output(dir_fd: int) -> Tuple[Optional[dict], Optional[str], str]:
    """(envelope, error message, note) from output.json in the job directory.

    Opened with O_NOFOLLOW|O_NONBLOCK relative to the job directory's own
    descriptor: a symlink, a FIFO or a directory planted there by the job
    is refused, never followed or waited on.
    """
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    try:
        fd = os.open("output.json", flags, dir_fd=dir_fd)
    except FileNotFoundError:
        return None, None, ""
    except OSError as exc:
        return None, MSG_OUTPUT_INVALID, "[Runner] output.json ist keine lesbare Datei (%s)." % (
            errno.errorcode.get(exc.errno, exc.errno))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return None, MSG_OUTPUT_INVALID, "[Runner] output.json ist keine gew\u00f6hnliche Datei."
        if info.st_size > MAX_OUTPUT_BYTES:
            return None, MSG_OUTPUT_TOO_LARGE, "[Runner] output.json hat %d Bytes." % info.st_size
        chunks = []
        size = 0
        while size <= MAX_OUTPUT_BYTES:
            chunk = os.read(fd, min(MiB, MAX_OUTPUT_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        if size > MAX_OUTPUT_BYTES:
            return None, MSG_OUTPUT_TOO_LARGE, "[Runner] output.json ist gr\u00f6sser als 8 MB."
    except OSError as exc:
        return None, MSG_OUTPUT_INVALID, "[Runner] output.json konnte nicht gelesen werden (%s)." % (
            errno.errorcode.get(exc.errno, exc.errno))
    finally:
        os.close(fd)
    try:
        envelope = json.loads(b"".join(chunks).decode("utf-8"), parse_constant=_reject_constant)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None, MSG_OUTPUT_INVALID, "[Runner] output.json ist kein g\u00fcltiges JSON."
    if not isinstance(envelope, dict) or not isinstance(envelope.get("ok"), bool):
        return None, MSG_OUTPUT_INVALID, "[Runner] output.json hat nicht die erwartete Form."
    if envelope["ok"]:
        if not isinstance(envelope.get("result"), dict):
            return None, MSG_NOT_DICT, ""
        try:
            return {"ok": True, "result": clean_json(envelope["result"])}, None, ""
        except (ValueError, RecursionError):
            return None, MSG_OUTPUT_INVALID, "[Runner] Die Ausgabe ist zu tief verschachtelt."
    message = HARNESS_ERRORS.get(str(envelope.get("error_code") or ""))
    if message is None:
        return None, MSG_OUTPUT_INVALID, "[Runner] output.json nennt keinen bekannten Fehler."
    return {"ok": False}, message, ""


def _make_accessible(root: str) -> None:
    """Give the owner rwx on every directory below ``root`` (never via symlinks).

    A job may have taken the permissions off its own directories; rmtree
    could then not enter them.
    """
    try:
        os.chmod(root, 0o700)
    except OSError:
        return
    for current, dirs, _files in os.walk(root, topdown=True):
        for name in dirs:
            path = os.path.join(current, name)
            try:
                if stat.S_ISDIR(os.lstat(path).st_mode):
                    os.chmod(path, 0o700)
            except OSError:
                pass


def remove_tree(path: str) -> None:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(info.st_mode):
        os.unlink(path)
        return
    _make_accessible(path)
    shutil.rmtree(path, ignore_errors=True)
    if os.path.lexists(path):
        logger.warning("Could not remove the job directory %s completely.", path)


# -- the runner ----------------------------------------------------------------


def _failed(message: str, logs: str = "", duration_ms: int = 0) -> Dict[str, Any]:
    return {"ok": False, "output": None, "error": message, "logs": logs, "duration_ms": duration_ms}


PY_VERSION_PROBE = r"""
import json, sys
try:
    from importlib import metadata
except ImportError:
    metadata = None
packages = {}
for name in ("numpy", "scipy", "pandas", "statsmodels"):
    try:
        packages[name] = metadata.version(name)
    except Exception:
        pass
print(json.dumps({"version": "%d.%d.%d" % sys.version_info[:3], "packages": packages}))
"""


def claim_process() -> None:
    """Process-wide settings the runner needs before it starts any job.

    As a child subreaper, every orphan a job leaves behind is reparented to
    this process, so the sweep finds it among its descendants however it
    detached itself. Non-dumpable, the server cannot be ptraced or read
    through /proc/<pid>/mem by the jobs, which run under the same uid.
    """
    if not _prctl(PR_SET_CHILD_SUBREAPER, 1):
        logger.warning("Could not become a child subreaper; detached job processes "
                       "may escape the sweep.")
    _prctl(PR_SET_DUMPABLE, 0)


class Runner:
    """Runs jobs. One per process, after claim_process()."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.pid = os.getpid()
        self.sid = os.getsid(0)
        self.uid = os.getuid()
        self._slots = threading.BoundedSemaphore(config.max_concurrent)
        # Bodies in memory at once: the running jobs plus two waiting.
        self._intake = threading.BoundedSemaphore(config.max_concurrent + 2)
        self._busy = 0
        self._busy_lock = threading.Lock()
        # Held while spawning a job, sweeping strays and reaping orphans, so a
        # sweep never sees a job's first process before it is registered and
        # the reaper never takes a child that Popen still has to wait for.
        self._spawn_lock = threading.Lock()
        self._child_pids: Set[int] = set()
        self._running_sids: Set[int] = set()
        self.languages: Dict[str, str] = {}
        self.interpreters: Dict[str, str] = {}
        os.makedirs(config.job_root, exist_ok=True)
        self.startup_free = self.free_bytes()
        self._remove_leftover_job_dirs()

    # -- languages -------------------------------------------------------------

    def detect_languages(self) -> None:
        home = tempfile.mkdtemp(prefix=JOB_DIR_PREFIX + "probe-", dir=self.config.job_root)
        try:
            self._detect_languages(self._probe_env(home))
        finally:
            remove_tree(home)

    def _detect_languages(self, env: Dict[str, str]) -> None:
        python = self.config.python
        if python and os.path.exists(python) and os.path.exists(PY_HARNESS):
            try:
                out = subprocess.run(
                    [python, "-I", "-c", PY_VERSION_PROBE], env=env,
                    stdin=subprocess.DEVNULL, capture_output=True, timeout=60, check=True,
                ).stdout.decode("utf-8", "replace")
                info = json.loads(out.strip().splitlines()[-1])
                packages = ", ".join("%s %s" % kv for kv in sorted(info.get("packages", {}).items()))
                self.languages["python"] = info["version"] + (" (%s)" % packages if packages else "")
                self.interpreters["python"] = python
            except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError) as exc:
                logger.error("Python evaluators unavailable: %s cannot be run (%s).", python, exc)
        else:
            logger.error("Python evaluators unavailable: no interpreter at %r.", python)
        julia = self.config.julia
        if julia and os.path.exists(julia) and os.path.exists(JL_HARNESS):
            try:
                out = subprocess.run(
                    [julia, "--startup-file=no", "--version"], env=env,
                    stdin=subprocess.DEVNULL, capture_output=True, timeout=60, check=True,
                ).stdout.decode("utf-8", "replace")
                match = re.search(r"(\d+\.\d+\.\d+\S*)", out)
                if not match:
                    raise ValueError("unexpected answer %r" % out[:100])
                version = match.group(1)
                packages = self._julia_packages(version)
                self.languages["julia"] = version + (" (%s)" % packages if packages else "")
                self.interpreters["julia"] = julia
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                logger.error("Julia evaluators unavailable: %s cannot be run (%s).", julia, exc)
        else:
            logger.info("Julia evaluators unavailable: no julia binary%s.",
                        " at %r" % julia if julia else " on PATH")

    def _julia_packages(self, version: str) -> str:
        """Versions of the image's packages, read from the depot's manifest."""
        major_minor = ".".join(version.split(".")[:2])
        manifest = os.path.join(self.config.julia_depot, "environments", "v" + major_minor,
                                "Manifest.toml")
        try:
            import tomllib

            with open(manifest, "rb") as handle:
                deps = tomllib.load(handle).get("deps", {})
        except (OSError, ValueError, ImportError):
            return ""
        wanted = ("JSON3", "Distributions", "HypothesisTests", "StatsBase", "DataFrames")
        found = []
        for name in wanted:
            entries = deps.get(name) or []
            if entries and isinstance(entries[0], dict) and entries[0].get("version"):
                found.append("%s %s" % (name, entries[0]["version"]))
        return ", ".join(found)

    def _probe_env(self, home: str) -> Dict[str, str]:
        return {"PATH": self.config.path, "HOME": home, "LANG": "C.UTF-8",
                "JULIA_DEPOT_PATH": os.path.join(home, "depot") + ":" + self.config.julia_depot + ":"}

    # -- state -------------------------------------------------------------------

    @property
    def busy(self) -> int:
        with self._busy_lock:
            return self._busy

    def free_bytes(self) -> int:
        try:
            info = os.statvfs(self.config.job_root)
        except OSError:
            return 0
        return int(info.f_bavail) * int(info.f_frsize)

    def health(self) -> Dict[str, Any]:
        ok = bool(self.interpreters) and self.free_bytes() >= self.config.min_free_bytes
        return {"ok": ok, "languages": dict(self.languages), "busy": self.busy,
                "max_concurrent": self.config.max_concurrent, "version": VERSION}

    def _remove_leftover_job_dirs(self) -> None:
        try:
            names = os.listdir(self.config.job_root)
        except OSError:
            return
        for name in names:
            if not name.startswith(JOB_DIR_PREFIX):
                continue
            path = os.path.join(self.config.job_root, name)
            try:
                if os.lstat(path).st_uid == self.uid:
                    remove_tree(path)
            except OSError:
                pass

    # -- process hygiene --------------------------------------------------------

    def sweep(self, keep_sids: Optional[Iterable[int]] = None) -> int:
        """Kill every descendant of this server outside the allowed sessions.

        Allowed are this server's session and every still running job's
        (``keep_sids`` overrides the latter, e.g. with () at shutdown).
        """
        with self._spawn_lock:
            allowed = {self.sid} | set(self._running_sids if keep_sids is None else keep_sids)
            table = process_table()
            victims = [
                table[pid] for pid in descendants(table, self.pid)
                if table[pid].uid == self.uid and table[pid].sid not in allowed
                and table[pid].state not in ("Z", "X")
            ]
            killed = [info.pid for info in victims if kill_verified(info)]
        if killed:
            logger.warning("Killed %d process(es) a job left behind.", len(killed))
            self._await_reaped(killed)
        return len(killed)

    def _await_reaped(self, pids: List[int], timeout: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        pending = set(pids)
        while pending and time.monotonic() < deadline:
            self.reap_orphans()
            pending = {pid for pid in pending if read_proc(pid) is not None}
            if pending:
                time.sleep(0.02)

    def reap_orphans(self) -> None:
        """Collect exit statuses of orphans that were reparented to us."""
        with self._spawn_lock:
            table = process_table()
            for info in table.values():
                if info.ppid == self.pid and info.state == "Z" and info.pid not in self._child_pids:
                    try:
                        os.waitpid(info.pid, os.WNOHANG)
                    except ChildProcessError:
                        pass

    def kill_all(self) -> None:
        with self._spawn_lock:
            pgids = list(self._running_sids)
        for pgid in pgids:
            _killpg(pgid)
        self.sweep(keep_sids=())

    # -- jobs ----------------------------------------------------------------------

    def intake(self, timeout: float) -> bool:
        return self._intake.acquire(timeout=timeout)

    def release_intake(self) -> None:
        self._intake.release()

    def run_job(self, language: str, code: str, data: dict, timeout_seconds: int,
                deadline: Optional[float] = None) -> Dict[str, Any]:
        """Run one job. Raises Busy when no slot frees before ``deadline``
        (monotonic; default: the slot wait from now) or the job space is short."""
        interpreter = self.interpreters.get(language)
        if not interpreter:
            return _failed(MSG_LANGUAGE_MISSING.format(name=LANGUAGE_NAMES.get(language, language)))
        if deadline is None:
            deadline = time.monotonic() + self.config.slot_wait_seconds
        if not self._slots.acquire(timeout=max(0.0, deadline - time.monotonic())):
            raise Busy(MSG_BUSY)
        try:
            if self.free_bytes() < self.config.min_free_bytes:
                raise Busy(MSG_LOW_SPACE)
            with self._busy_lock:
                self._busy += 1
            try:
                return self._execute(language, interpreter, code, data, timeout_seconds)
            finally:
                with self._busy_lock:
                    self._busy -= 1
        finally:
            self._slots.release()

    def _execute(self, language: str, interpreter: str, code: str, data: dict,
                 timeout_seconds: int) -> Dict[str, Any]:
        try:
            job_dir = tempfile.mkdtemp(prefix=JOB_DIR_PREFIX, dir=self.config.job_root)
        except OSError as exc:
            logger.error("Could not create a job directory: %s", exc)
            return _failed(MSG_START_FAILED)
        # Every later access goes through this descriptor: the job owns its
        # directory and could rename it and put something else at the path.
        try:
            dir_fd = os.open(job_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError as exc:
            logger.error("Could not open the job directory: %s", exc)
            remove_tree(job_dir)
            return _failed(MSG_START_FAILED)
        job_ino = os.fstat(dir_fd).st_ino
        try:
            try:
                self._write_job_files(dir_fd, code, data)
            except (OSError, ValueError, TypeError, RecursionError) as exc:
                logger.error("Could not prepare a job directory: %s", exc)
                return _failed(MSG_START_FAILED)
            return self._spawn_and_collect(language, interpreter, job_dir, dir_fd, timeout_seconds)
        finally:
            os.close(dir_fd)
            try:
                if os.lstat(job_dir).st_ino == job_ino:
                    remove_tree(job_dir)
                else:
                    logger.warning("A job replaced its own directory %s.", job_dir)
            except FileNotFoundError:
                pass
            except OSError as exc:
                logger.warning("Could not remove %s: %s", job_dir, exc)

    @staticmethod
    def _write_job_files(dir_fd: int, code: str, data: dict) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = os.open("code", flags, 0o600, dir_fd=dir_fd)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(code)
        fd = os.open("input.json", flags, 0o600, dir_fd=dir_fd)
        # ASCII escapes: the input may carry any string the database holds,
        # lone surrogates included, and both harnesses read \u escapes.
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            json.dump(data, handle, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        os.mkdir("depot", 0o700, dir_fd=dir_fd)
        os.mkdir("tmp", 0o700, dir_fd=dir_fd)

    def job_env(self, job_dir: str) -> Dict[str, str]:
        """The whole environment of a job: fixed values, nothing inherited but PATH."""
        return {
            "PATH": self.config.path,
            "HOME": job_dir,
            "TMPDIR": os.path.join(job_dir, "tmp"),
            "LANG": "C.UTF-8",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
            "JULIA_NUM_THREADS": "1",
            # The job's own depot first (anything Julia writes lands there and
            # goes with the job), then the image's read-only depot with the
            # packages, then Julia's bundled depots (the trailing ':').
            "JULIA_DEPOT_PATH": os.path.join(job_dir, "depot") + ":" + self.config.julia_depot + ":",
            "JULIA_LOAD_PATH": "@:@v#.#:@stdlib",
        }

    def job_argv(self, language: str, interpreter: str, job_dir: str) -> List[str]:
        if language == "python":
            return [interpreter, "-I", PY_HARNESS, job_dir]
        return [interpreter, "--startup-file=no", "--history-file=no",
                "--heap-size-hint=" + JULIA_HEAP_SIZE_HINT, JL_HARNESS, job_dir]

    def _spawn_and_collect(self, language: str, interpreter: str, job_dir: str, dir_fd: int,
                           timeout_seconds: int) -> Dict[str, Any]:
        limits = job_limits(language, timeout_seconds)
        capture = LogCapture()
        notes: List[str] = []
        started = time.monotonic()
        with self._spawn_lock:
            try:
                proc = subprocess.Popen(
                    self.job_argv(language, interpreter, job_dir),
                    cwd=job_dir, env=self.job_env(job_dir),
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    start_new_session=True, close_fds=True,
                    preexec_fn=lambda: _child_setup(limits),
                )
            except (OSError, subprocess.SubprocessError) as exc:
                logger.error("Could not start a %s job: %s", language, exc)
                return _failed(MSG_START_FAILED)
            self._child_pids.add(proc.pid)
            self._running_sids.add(proc.pid)
        reader = threading.Thread(target=capture.drain, args=(proc.stdout.fileno(),),
                                  name="job-log-%d" % proc.pid, daemon=True)
        reader.start()
        timed_out = False
        try:
            try:
                proc.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                # The group first (its children), then the process itself,
                # in case the user code moved it to another group.
                _killpg(proc.pid)
                proc.kill()
                proc.wait()
        finally:
            duration_ms = int((time.monotonic() - started) * 1000)
            with self._spawn_lock:
                self._child_pids.discard(proc.pid)
                self._running_sids.discard(proc.pid)
            # Whatever the job left running -- in its own group, in another
            # group of its session, or double-forked into a new session --
            # dies here, before its output is read.
            self.sweep()
            reader.join(timeout=5)
            if reader.is_alive():
                logger.warning("The log pipe of job %s stayed open.", proc.pid)
            else:
                proc.stdout.close()

        envelope, output_error, output_note = read_output(dir_fd)
        if output_note:
            notes.append(output_note)
        returncode = proc.returncode
        if timed_out:
            notes.append("[Runner] Zeitlimit von %d s erreicht; der Auswerter wurde beendet."
                         % timeout_seconds)
            error: Optional[str] = MSG_TIMEOUT
            envelope = None
        elif envelope is not None and envelope["ok"]:
            error = None
            if returncode != 0:
                notes.append("[Runner] Der Prozess endete mit Code %s nach dem Ergebnis." % returncode)
        elif output_error is not None:
            error = output_error
        elif returncode is not None and returncode < 0:
            sig = -returncode
            notes.append("[Runner] Prozess durch Signal %s beendet." % _signal_name(sig))
            error = MSG_LIMIT if sig in LIMIT_SIGNALS else MSG_CRASHED
        else:
            notes.append("[Runner] Prozess ohne Ergebnis beendet (Code %s)." % returncode)
            error = MSG_NO_OUTPUT

        logs = capture.text()
        if notes:
            logs = (logs + ("\n" if logs and not logs.endswith("\n") else "") + "\n".join(notes))
        logs = logs[-MAX_LOG_CHARS:] if len(logs) > MAX_LOG_CHARS else logs
        ok = error is None
        logger.info("%s job finished in %d ms: %s", language, duration_ms,
                    "ok" if ok else "failed (%s)" % ("timeout" if timed_out else "code %s" % returncode))
        return {
            "ok": ok,
            "output": envelope["result"] if ok else None,
            "error": error,
            "logs": logs,
            "duration_ms": duration_ms,
        }


def _signal_name(sig: int) -> str:
    try:
        return signal.Signals(sig).name
    except ValueError:
        return str(sig)


def _killpg(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


# -- HTTP ----------------------------------------------------------------------


def parse_run_request(body: bytes, max_seconds: int) -> Tuple[str, str, dict, int]:
    """Validate a POST /v1/run body. Raises BadRequest."""
    try:
        payload = json.loads(body.decode("utf-8"), parse_constant=_reject_constant)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise BadRequest("body is not valid JSON") from None
    if not isinstance(payload, dict):
        raise BadRequest("body is not an object")
    language = payload.get("language")
    if language not in LANGUAGES:
        raise BadRequest("unknown language")
    code = payload.get("code")
    if not isinstance(code, str) or len(code) > MAX_CODE_CHARS:
        raise BadRequest("code missing or longer than %d characters" % MAX_CODE_CHARS)
    try:
        code.encode("utf-8")
    except UnicodeEncodeError:
        raise BadRequest("code is not valid Unicode") from None
    data = payload.get("data")
    if not isinstance(data, dict):
        raise BadRequest("data is not an object")
    timeout = payload.get("timeout_seconds")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) \
            or (isinstance(timeout, float) and not math.isfinite(timeout)) or timeout < 1:
        raise BadRequest("timeout_seconds must be a number >= 1")
    # A limit above the runner's own cap is lowered to it, not refused: the
    # Platform's timeout setting and this cap are configured separately.
    seconds = max_seconds if timeout >= max_seconds else int(math.ceil(timeout))
    return language, code, data, max(1, seconds)


class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = "experiments-runner/" + VERSION
    sys_version = ""
    protocol_version = "HTTP/1.0"
    timeout = SOCKET_TIMEOUT_SECONDS

    @property
    def runner(self) -> Runner:
        return self.server.runner  # type: ignore[attr-defined]

    def address_string(self) -> str:
        if isinstance(self.client_address, tuple) and self.client_address:
            return str(self.client_address[0])
        return "unix"

    def log_request(self, code: Any = "-", size: Any = "-") -> None:
        path = getattr(self, "path", "")
        if path == "/health" and str(code) == "200":
            return
        logger.info("%s %s -> %s", getattr(self, "command", None), path, code)

    def log_error(self, format: str, *args: Any) -> None:  # noqa: A002 - base class signature
        logger.warning("HTTP: " + format, *args)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        logger.debug("HTTP: " + format, *args)

    def _send(self, status: int, payload: Dict[str, Any]) -> None:
        # UTF-8, not ASCII escapes: an 8 MB output of umlauts would otherwise
        # triple in size on the way to the Platform, whose client refuses
        # answers above 12 MB. Every string was cleaned of lone surrogates.
        try:
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except UnicodeEncodeError:
            body = json.dumps(payload, ensure_ascii=True, allow_nan=False).encode("ascii")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass  # the client went away; nothing to tell it

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        if self.path == "/health":
            self._send(200, self.runner.health())
        else:
            self._send(404, {"ok": False, "error": MSG_NOT_FOUND})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/run":
            self._send(404, {"ok": False, "error": MSG_NOT_FOUND})
            return
        try:
            self._run()
        except Exception:  # noqa: BLE001 - never let a handler thread die silently
            logger.exception("Unexpected error while handling a job.")
            self._send(500, {"ok": False, "error": MSG_INTERNAL})

    def _run(self) -> None:
        if self.headers.get("Transfer-Encoding"):
            self._send(400, {"ok": False, "error": MSG_BAD_REQUEST})
            return
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            self._send(411, {"ok": False, "error": MSG_BAD_REQUEST})
            return
        try:
            length = int(raw_length)
        except ValueError:
            length = -1
        if length < 0:
            self._send(400, {"ok": False, "error": MSG_BAD_REQUEST})
            return
        if length > MAX_BODY_BYTES:
            self.close_connection = True
            self._send(413, {"ok": False, "error": MSG_BODY_TOO_LARGE})
            return
        runner = self.runner
        # One deadline for both waits: a request gets its 503 when no slot
        # frees within the slot wait, however it queued.
        deadline = time.monotonic() + runner.config.slot_wait_seconds
        if not runner.intake(runner.config.slot_wait_seconds):
            self._send(503, {"ok": False, "error": MSG_BUSY})
            return
        try:
            body = self._read_body(length)
            if body is None:
                self._send(400, {"ok": False, "error": MSG_BAD_REQUEST})
                return
            try:
                language, code, data, timeout = parse_run_request(body, runner.config.max_seconds)
            except BadRequest as exc:
                logger.info("Refused a job: %s", exc)
                self._send(400, {"ok": False, "error": MSG_BAD_REQUEST})
                return
            del body
            try:
                result = runner.run_job(language, code, data, timeout, deadline=deadline)
            except Busy as exc:
                self._send(503, {"ok": False, "error": str(exc)})
                return
            self._send(200, result)
        finally:
            runner.release_intake()

    def _read_body(self, length: int) -> Optional[bytes]:
        chunks = []
        remaining = length
        try:
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, MiB))
                if not chunk:
                    return None
                chunks.append(chunk)
                remaining -= len(chunk)
        except OSError:
            return None
        return b"".join(chunks)


class _ServerMixin:
    """Bounded threads per connection; the runner attached to the server."""

    daemon_threads = True
    block_on_close = False
    request_queue_size = 64
    runner: Runner

    def _init_limits(self) -> None:
        self._connections = threading.BoundedSemaphore(MAX_CONNECTIONS)

    def process_request(self, request, client_address):  # type: ignore[override]
        if not self._connections.acquire(blocking=False):
            logger.warning("Too many connections; one refused.")
            self.shutdown_request(request)  # type: ignore[attr-defined]
            return
        try:
            super().process_request(request, client_address)  # type: ignore[misc]
        except BaseException:
            self._connections.release()
            raise

    def process_request_thread(self, request, client_address):  # type: ignore[override]
        try:
            super().process_request_thread(request, client_address)  # type: ignore[misc]
        finally:
            self._connections.release()


class UnixServer(_ServerMixin, socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    def __init__(self, path: str, runner: Runner) -> None:
        self.runner = runner
        self._init_limits()
        self.socket_id: Optional[Tuple[int, int]] = None
        super().__init__(path, _Handler)

    def server_bind(self) -> None:
        path = self.server_address
        directory = os.path.dirname(path)
        if not os.path.isdir(directory):
            raise RuntimeError("socket directory %s does not exist" % directory)
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            pass
        else:
            # A socket (or a link) left by the previous run; anything else at
            # that path is not ours to delete.
            if stat.S_ISSOCK(info.st_mode) or stat.S_ISLNK(info.st_mode):
                os.unlink(path)
            else:
                raise RuntimeError("%s exists and is not a socket" % path)
        previous = os.umask(0o117)
        try:
            self.socket.bind(path)
        finally:
            os.umask(previous)
        os.chmod(path, 0o660)
        info = os.lstat(path)
        self.socket_id = (info.st_dev, info.st_ino)

    def socket_intact(self) -> bool:
        try:
            info = os.lstat(self.server_address)
        except OSError:
            return False
        return stat.S_ISSOCK(info.st_mode) and (info.st_dev, info.st_ino) == self.socket_id

    def remove_socket(self) -> None:
        if self.socket_intact():
            try:
                os.unlink(self.server_address)
            except OSError:
                pass


class TCPServer(_ServerMixin, socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True

    def __init__(self, address: Tuple[str, int], runner: Runner) -> None:
        self.runner = runner
        self._init_limits()
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        super().__init__(address, _Handler)

    def socket_intact(self) -> bool:
        return True

    def remove_socket(self) -> None:
        pass


def make_server(config: Config, runner: Runner):
    kind, address = parse_listen(config.listen)
    if kind == "unix":
        return UnixServer(address, runner)
    logger.warning("Listening on TCP %s:%s WITHOUT authentication. Development only: "
                   "anyone who reaches this port can run code here.", *address)
    return TCPServer(address, runner)


class Watchdog(threading.Thread):
    """Exits the process when its socket goes, reaps orphans, heals a full /tmp.

    A job runs under the same uid as this server and could unlink or replace
    the socket file. The Platform must then not keep talking to whatever
    listens there now: the server exits and Docker restarts the container,
    which also ends every process in it.
    """

    def __init__(self, server: Any, runner: Runner) -> None:
        super().__init__(name="watchdog", daemon=True)
        self.server = server
        self.runner = runner
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        ticks = 0
        while not self._stop_event.wait(WATCH_INTERVAL_SECONDS):
            ticks += 1
            try:
                if not self.server.socket_intact():
                    die(self.runner, "the socket file was removed or replaced")
                self.runner.reap_orphans()
                if ticks % SPACE_CHECK_EVERY == 0:
                    self._check_space()
            except Exception:  # noqa: BLE001 - the watchdog must not die
                logger.exception("Watchdog error.")

    def _check_space(self) -> None:
        config = self.runner.config
        if (self.runner.busy == 0 and self.runner.startup_free >= config.min_free_bytes
                and self.runner.free_bytes() < config.min_free_bytes):
            # Only jobs write to the job space, and none is running: what
            # fills it is left over from one. A restart gives a fresh tmpfs.
            die(self.runner, "the job space stays full while no job runs")


def die(runner: Runner, reason: str) -> None:
    logger.error("Exiting: %s.", reason)
    try:
        runner.kill_all()
    finally:
        logging.shutdown()
        os._exit(3)


def serve(config: Config) -> int:
    claim_process()
    runner = Runner(config)
    runner.detect_languages()
    try:
        server = make_server(config, runner)
    except (OSError, RuntimeError, ValueError) as exc:
        logger.error("Cannot listen on %s: %s", config.listen, exc)
        return 1
    watchdog = Watchdog(server, runner)
    watchdog.start()
    stopping = threading.Event()

    def _on_signal(signum: int, _frame: Any) -> None:
        if not stopping.is_set():
            stopping.set()
            logger.info("Signal %s: shutting down.", _signal_name(signum))
            threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    logger.info("experiments-runner %s listening on %s; languages: %s; %d job(s) at once, "
                "at most %d s each.", VERSION, config.listen,
                ", ".join("%s %s" % kv for kv in sorted(runner.languages.items())) or "none",
                config.max_concurrent, config.max_seconds)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        watchdog.stop()
        runner.kill_all()
        server.remove_socket()
        server.server_close()
    return 0


# -- command line --------------------------------------------------------------


def healthcheck(config: Config) -> int:
    """Docker healthcheck: 0 when the server answers /health with ok true."""
    try:
        kind, address = parse_listen(config.listen)
        if kind == "unix":
            conn: http.client.HTTPConnection = _UnixConnection(address, timeout=5)
        else:
            host = "127.0.0.1" if address[0] in ("0.0.0.0", "") else address[0]
            conn = http.client.HTTPConnection(host, address[1], timeout=5)
        try:
            conn.request("GET", "/health")
            response = conn.getresponse()
            payload = json.loads(response.read(65536).decode("utf-8"))
        finally:
            conn.close()
    except (OSError, ValueError, http.client.HTTPException) as exc:
        print("unhealthy: %s" % exc)
        return 1
    if response.status == 200 and isinstance(payload, dict) and payload.get("ok") is True:
        print("healthy: busy %s of %s" % (payload.get("busy"), payload.get("max_concurrent")))
        return 0
    print("unhealthy: HTTP %s %s" % (response.status, json.dumps(payload)[:300]))
    return 1


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self._path)
        except BaseException:
            sock.close()
            raise
        self.sock = sock


SELF_TEST_CODE = {
    "python": (
        "import numpy, pandas, scipy.stats, statsmodels.api\n"
        "\n"
        "def evaluate(data):\n"
        "    return {'p': float(scipy.stats.norm.cdf(0.0)), 'n': int(numpy.sum(data['x']))}\n"
    ),
    # "recompiled": Julia wrote package caches into the job's own depot,
    # i.e. the ones precompiled into the image were not usable -- every job
    # would then spend minutes compiling.
    "julia": (
        "using JSON3, Distributions, HypothesisTests, StatsBase, DataFrames\n"
        "\n"
        "evaluate(data) = Dict(\"p\" => cdf(Normal(), 0.0), \"n\" => sum(data[\"x\"]),\n"
        "                      \"recompiled\" => isdir(joinpath(DEPOT_PATH[1], \"compiled\")))\n"
    ),
}


def self_test(config: Config) -> int:
    """Run one evaluator per language through the real job path (image smoke test)."""
    claim_process()
    runner = Runner(config)
    runner.detect_languages()
    failures = 0
    for language in LANGUAGES:
        if language not in runner.interpreters:
            print("FAIL %s: not available" % language)
            failures += 1
            continue
        result = runner.run_job(language, SELF_TEST_CODE[language], {"x": [1, 2, 3]}, 300)
        output = result.get("output") or {}
        if output.get("recompiled"):
            failures += 1
            print("FAIL %s: packages were compiled again at run time; the precompiled "
                  "caches in %s are not used" % (language, config.julia_depot))
        elif result.get("ok") and output.get("p") == 0.5 and output.get("n") == 6:
            print("OK   %s %s (%d ms)" % (language, runner.languages[language], result["duration_ms"]))
        else:
            failures += 1
            print("FAIL %s: %s\n%s" % (language, result.get("error"), result.get("logs", "")[-3000:]))
    return 1 if failures else 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = Config.from_env()
    if args == ["--healthcheck"]:
        return healthcheck(config)
    if args == ["--self-test"]:
        return self_test(config)
    if args:
        print("usage: runner.py [--healthcheck | --self-test]", file=sys.stderr)
        return 2
    return serve(config)


if __name__ == "__main__":
    sys.exit(main())
