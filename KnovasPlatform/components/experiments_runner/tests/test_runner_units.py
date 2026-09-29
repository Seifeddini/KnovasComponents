"""In-process tests of the runner's building blocks (no server, no jobs)."""

from __future__ import annotations

import json
import os
import resource
import subprocess
import sys
import threading
import time

import pytest

import runner as R


# -- configuration ----------------------------------------------------------------


def test_parse_listen_accepts_unix_in_both_spellings_and_tcp():
    assert R.parse_listen("unix:/run/experiments-runner/runner.sock") == (
        "unix", "/run/experiments-runner/runner.sock")
    # The Platform's EXPERIMENTS_RUNNER_URL spelling works here too.
    assert R.parse_listen("unix:///run/x.sock") == ("unix", "/run/x.sock")
    assert R.parse_listen("tcp:0.0.0.0:8090") == ("tcp", ("0.0.0.0", 8090))
    assert R.parse_listen("tcp:[::1]:8090") == ("tcp", ("::1", 8090))


@pytest.mark.parametrize("spec", [
    "", "unix:relative.sock", "unix:/" + "a" * 200, "unix:/x\x00y", "tcp:host", "tcp::80",
    "tcp:host:0", "tcp:host:70000", "tcp:host:http", "http://localhost:8090",
])
def test_parse_listen_refuses_malformed_specs(spec):
    with pytest.raises(ValueError):
        R.parse_listen(spec)


def test_config_defaults_and_clamping(tmp_path):
    config = R.Config.from_env({"PATH": "/nowhere"})
    assert config.listen == "unix:/run/experiments-runner/runner.sock"
    assert config.max_concurrent == 2
    assert config.max_seconds == 600
    assert config.job_root == "/tmp"
    assert config.python == sys.executable
    assert config.julia == ""                     # nothing on that PATH
    assert config.julia_depot == "/opt/julia-depot"
    assert config.min_free_bytes == 256 * R.MiB
    assert config.slot_wait_seconds == 10.0

    config = R.Config.from_env({
        "PATH": "/nowhere", "RUNNER_MAX_CONCURRENT": "99", "RUNNER_MAX_SECONDS": "1",
        "RUNNER_MIN_FREE_MB": "not-a-number", "RUNNER_TMP_DIR": str(tmp_path),
        "RUNNER_JULIA": "/opt/julia/bin/julia",
    })
    assert config.max_concurrent == 16
    assert config.max_seconds == 5
    assert config.min_free_bytes == 256 * R.MiB
    assert config.job_root == str(tmp_path)
    assert config.julia == "/opt/julia/bin/julia"


def test_config_finds_julia_on_path(tmp_path):
    fake = tmp_path / "julia"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    assert R.Config.from_env({"PATH": str(tmp_path)}).julia == str(fake)


# -- requests -------------------------------------------------------------------------


def _body(**payload):
    base = {"language": "python", "code": "def evaluate(d): return {}", "data": {},
            "timeout_seconds": 30}
    base.update(payload)
    return json.dumps(base).encode()


def test_parse_run_request_accepts_the_client_payload():
    language, code, data, timeout = R.parse_run_request(_body(data={"a": [1, 2]}), 600)
    assert (language, data, timeout) == ("python", {"a": [1, 2]}, 30)
    assert code.startswith("def evaluate")


def test_parse_run_request_caps_and_rounds_the_timeout():
    assert R.parse_run_request(_body(timeout_seconds=5000), 600)[3] == 600
    assert R.parse_run_request(_body(timeout_seconds=10 ** 400), 600)[3] == 600
    assert R.parse_run_request(_body(timeout_seconds=2.2), 600)[3] == 3


@pytest.mark.parametrize("body", [
    b"not json",
    b"\xff\xfe",
    b"[]",
    _body(language="ruby"),
    _body(language=None),
    _body(code=None),
    _body(code=123),
    _body(code="x" * (R.MAX_CODE_CHARS + 1)),
    _body(data=[1]),
    _body(data=None),
    _body(timeout_seconds=0),
    _body(timeout_seconds=-5),
    _body(timeout_seconds="30"),
    _body(timeout_seconds=True),
    _body(timeout_seconds=None),
    b'{"language": "python", "code": "", "data": {"x": NaN}, "timeout_seconds": 5}',
    b'{"language": "python", "code": "", "data": {}, "timeout_seconds": Infinity}',
    b'{"language": "python", "code": "\\ud800", "data": {}, "timeout_seconds": 5}',
    b"[" * 100000 + b"]" * 100000,
])
def test_parse_run_request_refuses_bad_bodies(body):
    with pytest.raises(R.BadRequest):
        R.parse_run_request(body, 600)


def test_code_at_the_limit_is_accepted():
    code = "#" * R.MAX_CODE_CHARS
    assert R.parse_run_request(_body(code=code), 600)[1] == code


# -- logs ---------------------------------------------------------------------------------


def test_log_capture_keeps_head_and_tail_and_says_what_it_left_out():
    cap = R.LogCapture(limit=100, head=20)
    cap.feed(b"H" * 20)
    for _ in range(1000):
        cap.feed(b"m" * 97)
    cap.feed(b"END")
    text = cap.text()
    assert text.startswith("H" * 20)
    assert text.endswith("END")
    assert "Bytes der Ausgabe ausgelassen" in text
    # Memory stays bounded while draining a flood.
    assert cap.tail_len < 100 + 97 + 3


def test_log_capture_without_overflow_is_verbatim():
    cap = R.LogCapture(limit=100, head=20)
    cap.feed("Gr\u00fcsse\n".encode())
    assert cap.text() == "Gr\u00fcsse\n"


def test_log_capture_drains_a_pipe_until_eof():
    read_fd, write_fd = os.pipe()

    def write_all():
        with os.fdopen(write_fd, "wb") as handle:
            handle.write(b"x" * 200000)

    writer = threading.Thread(target=write_all)
    writer.start()
    cap = R.LogCapture()
    cap.drain(read_fd)
    writer.join()
    os.close(read_fd)
    assert cap.total == 200000
    assert len(cap.text()) <= R.MAX_LOG_CHARS


def test_clean_text_removes_what_postgres_refuses():
    assert R.clean_text("a\x00b") == "a\ufffdb"
    assert R.clean_text("x\ud800y") == "x\ufffdy"
    assert R.clean_text("Gr\u00fcsse") == "Gr\u00fcsse"


def test_clean_json_cleans_keys_values_and_non_finite_numbers():
    value = {"k\x00": ["\ud800", float("nan"), float("inf"), 1.5, 2, True, None, {"a": "b"}]}
    assert R.clean_json(value) == {"k\ufffd": ["\ufffd", None, None, 1.5, 2, True, None, {"a": "b"}]}


def test_clean_json_refuses_deep_nesting():
    deep = current = {}
    for _ in range(R.MAX_OUTPUT_DEPTH + 5):
        current["x"] = {}
        current = current["x"]
    with pytest.raises(ValueError):
        R.clean_json(deep)


# -- output ---------------------------------------------------------------------------------


@pytest.fixture
def job_dir(tmp_path):
    path = tmp_path / "job"
    path.mkdir()
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    yield path, fd
    os.close(fd)


def test_read_output_without_a_file(job_dir):
    _, fd = job_dir
    assert R.read_output(fd) == (None, None, "")


def test_read_output_accepts_a_valid_envelope(job_dir):
    path, fd = job_dir
    (path / "output.json").write_text('{"ok": true, "result": {"verdict": "better", "s": "a\\u0000"}}')
    envelope, error, note = R.read_output(fd)
    assert envelope == {"ok": True, "result": {"verdict": "better", "s": "a\ufffd"}}
    assert error is None and note == ""


@pytest.mark.parametrize("code, message", sorted(R.HARNESS_ERRORS.items()))
def test_read_output_maps_harness_errors(job_dir, code, message):
    path, fd = job_dir
    (path / "output.json").write_text(json.dumps({"ok": False, "error_code": code}))
    assert R.read_output(fd)[:2] == ({"ok": False}, message)


@pytest.mark.parametrize("content, message", [
    ("{broken", R.MSG_OUTPUT_INVALID),
    ('{"ok": true, "result": NaN}', R.MSG_OUTPUT_INVALID),
    ("[1, 2]", R.MSG_OUTPUT_INVALID),
    ('{"ok": "yes"}', R.MSG_OUTPUT_INVALID),
    ('{"ok": false, "error_code": "made-up"}', R.MSG_OUTPUT_INVALID),
    ('{"ok": true, "result": [1]}', R.MSG_NOT_DICT),
    ('{"ok": true}', R.MSG_NOT_DICT),
    ('{"ok": true, "result": ' + '{"a": ' * 200 + "1" + "}" * 200 + "}", R.MSG_OUTPUT_INVALID),
])
def test_read_output_refuses_malformed_envelopes(job_dir, content, message):
    path, fd = job_dir
    (path / "output.json").write_text(content)
    envelope, error, _note = R.read_output(fd)
    assert envelope is None and error == message


def test_read_output_does_not_follow_a_symlink(job_dir, tmp_path):
    path, fd = job_dir
    secret = tmp_path / "secret.json"
    secret.write_text('{"ok": true, "result": {"secret": 1}}')
    os.symlink(secret, path / "output.json")
    envelope, error, note = R.read_output(fd)
    assert envelope is None and error == R.MSG_OUTPUT_INVALID
    assert "ELOOP" in note


def test_read_output_does_not_block_on_a_fifo(job_dir):
    path, fd = job_dir
    os.mkfifo(path / "output.json")
    started = time.monotonic()
    envelope, error, _ = R.read_output(fd)
    assert time.monotonic() - started < 2
    assert envelope is None and error == R.MSG_OUTPUT_INVALID


def test_read_output_refuses_a_directory(job_dir):
    path, fd = job_dir
    (path / "output.json").mkdir()
    assert R.read_output(fd)[1] == R.MSG_OUTPUT_INVALID


def test_read_output_refuses_more_than_8_mb(job_dir):
    path, fd = job_dir
    with open(path / "output.json", "wb") as handle:
        handle.truncate(R.MAX_OUTPUT_BYTES + 1)
    assert R.read_output(fd)[1] == R.MSG_OUTPUT_TOO_LARGE


def test_read_output_uses_the_directory_descriptor_not_the_path(job_dir, tmp_path):
    # A job may rename its directory and put another in its place; the
    # runner keeps reading from the directory it created.
    path, fd = job_dir
    (path / "output.json").write_text('{"ok": true, "result": {"mine": 1}}')
    os.rename(path, tmp_path / "moved")
    path.mkdir()
    (path / "output.json").write_text('{"ok": true, "result": {"planted": 1}}')
    assert R.read_output(fd)[0] == {"ok": True, "result": {"mine": 1}}


def test_remove_tree_copes_with_directories_without_permissions(tmp_path):
    root = tmp_path / "xrun-test"
    (root / "a" / "b").mkdir(parents=True)
    (root / "a" / "b" / "f").write_text("x")
    os.symlink("/etc", root / "link")
    (root / "a" / "b").chmod(0)
    (root / "a").chmod(0)
    R.remove_tree(str(root))
    assert not root.exists()
    assert os.path.isdir("/etc")


# -- jobs and processes ----------------------------------------------------------------------


def test_job_env_holds_only_the_fixed_variables(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_TOKEN", "do-not-leak")
    runner = R.Runner(R.Config.from_env({"PATH": "/usr/bin", "RUNNER_TMP_DIR": str(tmp_path),
                                         "RUNNER_JULIA_DEPOT": "/opt/julia-depot"}))
    env = runner.job_env("/tmp/xrun-abc")
    assert env == {
        "PATH": "/usr/bin",
        "HOME": "/tmp/xrun-abc",
        "TMPDIR": "/tmp/xrun-abc/tmp",
        "LANG": "C.UTF-8",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "JULIA_NUM_THREADS": "1",
        "JULIA_DEPOT_PATH": "/tmp/xrun-abc/depot:/opt/julia-depot:",
        "JULIA_LOAD_PATH": "@:@v#.#:@stdlib",
    }


def test_job_argv_per_language(tmp_path):
    runner = R.Runner(R.Config.from_env({"PATH": "/usr/bin", "RUNNER_TMP_DIR": str(tmp_path)}))
    assert runner.job_argv("python", "/opt/venv/bin/python3", "/tmp/j") == [
        "/opt/venv/bin/python3", "-I", R.PY_HARNESS, "/tmp/j"]
    assert runner.job_argv("julia", "/usr/local/julia/bin/julia", "/tmp/j") == [
        "/usr/local/julia/bin/julia", "--startup-file=no", "--history-file=no",
        "--heap-size-hint=1G", R.JL_HARNESS, "/tmp/j"]


def test_runner_removes_job_dirs_left_by_a_previous_run(tmp_path):
    (tmp_path / "xrun-old" / "sub").mkdir(parents=True)
    (tmp_path / "unrelated").mkdir()
    R.Runner(R.Config.from_env({"PATH": "/usr/bin", "RUNNER_TMP_DIR": str(tmp_path)}))
    assert not (tmp_path / "xrun-old").exists()
    assert (tmp_path / "unrelated").exists()


def test_job_limits_follow_the_contract():
    limits = {which: (soft, hard) for which, soft, hard in R.job_limits("python", 60)}
    assert limits[resource.RLIMIT_CPU] == (65, 66)
    assert limits[resource.RLIMIT_AS][0] == 1536 * R.MiB
    assert limits[resource.RLIMIT_FSIZE][0] == 64 * R.MiB
    assert limits[resource.RLIMIT_NOFILE][0] == 256
    assert limits[resource.RLIMIT_NPROC][0] == 128
    assert limits[resource.RLIMIT_CORE] == (0, 0)
    julia = {which: (soft, hard) for which, soft, hard in R.job_limits("julia", 10)}
    assert julia[resource.RLIMIT_AS][0] == 6 * 1024 * R.MiB
    assert julia[resource.RLIMIT_CPU] == (15, 16)


def test_job_limits_never_exceed_the_servers_own_hard_limits(monkeypatch):
    real = resource.getrlimit

    def fake(which):
        if which == resource.RLIMIT_NOFILE:
            return (64, 100)
        return real(which)

    monkeypatch.setattr(R.resource, "getrlimit", fake)
    limits = {which: (soft, hard) for which, soft, hard in R.job_limits("python", 5)}
    assert limits[resource.RLIMIT_NOFILE] == (100, 100)


def test_descendants_walks_the_whole_tree():
    def info(pid, ppid):
        return R.ProcInfo(pid=pid, ppid=ppid, sid=pid, state="S", starttime=1, uid=0)

    table = {p.pid: p for p in (info(10, 1), info(11, 10), info(12, 11), info(13, 1), info(14, 12))}
    assert R.descendants(table, 10) == {11, 12, 14}
    assert R.descendants(table, 13) == set()


def test_read_proc_describes_this_process():
    me = R.read_proc(os.getpid())
    assert me.pid == os.getpid()
    assert me.ppid == os.getppid()
    assert me.sid == os.getsid(0)
    assert me.uid == os.getuid()
    assert R.read_proc(2 ** 22 + 12345) is None


def test_kill_verified_refuses_a_reused_pid_and_kills_the_real_one():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        info = None
        for _ in range(100):
            info = R.read_proc(proc.pid)
            if info is not None:
                break
            time.sleep(0.01)
        impostor = R.ProcInfo(pid=info.pid, ppid=info.ppid, sid=info.sid, state=info.state,
                              starttime=info.starttime + 1, uid=info.uid)
        assert R.kill_verified(impostor) is False
        assert proc.poll() is None
        assert R.kill_verified(info) is True
        assert proc.wait(timeout=5) == -9
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_messages_are_german_and_fixed():
    for message in (R.MSG_EXCEPTION, R.MSG_TIMEOUT, R.MSG_NOT_DICT, R.MSG_OUTPUT_TOO_LARGE,
                    R.MSG_OUTPUT_INVALID, R.MSG_NO_OUTPUT, R.MSG_LIMIT, R.MSG_BUSY, R.MSG_LOW_SPACE):
        assert message.endswith(".")
        assert len(message) <= 500
    assert R.MSG_EXCEPTION == "Der Auswerter ist mit einem Fehler abgebrochen."
    assert R.MSG_TIMEOUT == "Zeitlimit \u00fcberschritten."


def test_sources_are_ascii():
    tests_dir = os.path.dirname(os.path.abspath(__file__))
    names = [os.path.join(R.HERE, n) for n in ("runner.py", "harness.py", "harness.jl")]
    names += [os.path.join(tests_dir, n) for n in os.listdir(tests_dir) if n.endswith(".py")]
    for name in names:
        with open(name, "rb") as handle:
            assert all(byte < 128 for byte in handle.read()), name
