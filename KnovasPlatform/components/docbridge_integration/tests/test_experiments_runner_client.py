"""RunnerClient against small fake runners: over a unix socket and over TCP.

What matters: an unreachable or busy runner is ``Unavailable`` (nothing ran,
try later); a job that ran too long is an ``ok: False`` result (it did run);
whatever the runner answers is bounded and never raises out of health().
"""

import http.server
import json
import os
import socketserver
import tempfile
import threading
import time

import pytest

from experiments.errors import Unavailable
from experiments.runner_client import RunnerClient


class FakeRunner:
    """Scripted behaviour shared by the unix and TCP servers."""

    def __init__(self):
        self.requests = []
        self.health = {"ok": True, "languages": {"python": "3.11.9", "julia": "1.11.9"},
                       "busy": 1, "max_concurrent": 2}
        self.health_status = 200
        self.run_status = 200
        self.run_body = None  # bytes; default: an ok result echoing the input
        self.delay = 0.0


def make_handler(fake):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # unix sockets have no client address
            pass

        def _send(self, status, body):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            fake.requests.append(("GET", self.path, None))
            if self.path != "/health":
                return self._send(404, b"{}")
            self._send(fake.health_status, json.dumps(fake.health).encode())

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            fake.requests.append(("POST", self.path, body))
            if fake.delay:
                time.sleep(fake.delay)
            if fake.run_body is not None:
                return self._send(fake.run_status, fake.run_body)
            answer = {"ok": True, "output": {"verdict": "better", "echo": body["data"]},
                      "error": None, "logs": "hello from evaluate\n", "duration_ms": 12}
            self._send(fake.run_status, json.dumps(answer).encode())

    return Handler


class _UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


@pytest.fixture
def fake():
    return FakeRunner()


@pytest.fixture
def unix_runner(fake):
    tmp = tempfile.mkdtemp(prefix="kxr")  # short: AF_UNIX paths are limited to ~108 bytes
    path = os.path.join(tmp, "runner.sock")
    server = _UnixServer(path, make_handler(fake))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"unix://{path}"
    server.shutdown()
    server.server_close()
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    os.rmdir(tmp)


@pytest.fixture
def tcp_runner(fake):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), make_handler(fake))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture(params=["unix", "tcp"])
def runner_url(request, unix_runner, tcp_runner):
    return unix_runner if request.param == "unix" else tcp_runner


# -- health -------------------------------------------------------------------------


def test_health_reports_languages_and_busy(runner_url):
    health = RunnerClient(runner_url).health()
    assert health == {"configured": True, "ok": True,
                      "languages": {"python": "3.11.9", "julia": "1.11.9"}, "busy": 1}


def test_health_is_cached_for_thirty_seconds(runner_url, fake, monkeypatch):
    import experiments.runner_client as rc

    now = [1000.0]
    monkeypatch.setattr(rc.time, "monotonic", lambda: now[0])
    client = RunnerClient(runner_url)
    client.health()
    client.health()
    assert len(fake.requests) == 1
    now[0] += 31
    fake.health["ok"] = False
    assert client.health()["ok"] is False
    assert len(fake.requests) == 2


def test_health_never_raises(runner_url, fake):
    fake.health_status = 500
    assert RunnerClient(runner_url).health()["ok"] is False
    fake.health_status = 200
    fake.health = ["not", "an", "object"]
    assert RunnerClient(runner_url).health()["ok"] is False


def test_health_of_a_missing_runner(tmp_path):
    for url in (f"unix://{tmp_path}/nothing.sock", "http://127.0.0.1:9", "ftp://x", "",
                "unix://relative.sock", "http://"):
        health = RunnerClient(url).health()
        assert health == {"configured": True, "ok": False, "languages": {}, "busy": 0}


def test_health_sanitises_odd_fields(runner_url, fake):
    fake.health = {"ok": "yes", "languages": {"python": "x" * 1000}, "busy": -3}
    health = RunnerClient(runner_url).health()
    assert health["ok"] is False  # only a real true counts
    assert len(health["languages"]["python"]) == 200
    assert health["busy"] == 0


# -- run ----------------------------------------------------------------------------


def test_run_posts_the_job_and_returns_the_result(runner_url, fake):
    client = RunnerClient(runner_url)
    result = client.run(language="python", code="def evaluate(data):\n    return {}\n",
                        data={"metric": {"key": "ctr"}, "rows": [1, 2]}, timeout_seconds=20)
    assert result == {"ok": True, "output": {"verdict": "better",
                                             "echo": {"metric": {"key": "ctr"}, "rows": [1, 2]}},
                      "error": None, "logs": "hello from evaluate\n", "duration_ms": 12}
    method, path, body = fake.requests[-1]
    assert (method, path) == ("POST", "/v1/run")
    assert body == {"language": "python", "code": "def evaluate(data):\n    return {}\n",
                    "data": {"metric": {"key": "ctr"}, "rows": [1, 2]}, "timeout_seconds": 20}


def test_run_passes_unicode_through(runner_url, fake):
    RunnerClient(runner_url).run(language="julia", code="# Gr\u00fcsse\n",
                                 data={"title": "Zufriedenheit \u2013 \u00dcbersicht"},
                                 timeout_seconds=5)
    assert fake.requests[-1][2]["data"]["title"] == "Zufriedenheit \u2013 \u00dcbersicht"


def test_busy_runner_is_unavailable(runner_url, fake):
    fake.run_status = 503
    fake.run_body = b'{"error": "busy"}'
    with pytest.raises(Unavailable) as exc:
        RunnerClient(runner_url).run(language="python", code="", data={}, timeout_seconds=5)
    assert exc.value.message == "Die Rechenumgebung ist nicht erreichbar."


def test_unreachable_runner_is_unavailable(tmp_path):
    for url in (f"unix://{tmp_path}/gone.sock", "http://127.0.0.1:9", "gopher://x"):
        with pytest.raises(Unavailable):
            RunnerClient(url).run(language="python", code="", data={}, timeout_seconds=5)


def test_timeout_after_acceptance_is_a_failed_result(runner_url, fake):
    client = RunnerClient(runner_url)
    client.read_grace_seconds = 0.0
    fake.delay = 1.6
    started = time.monotonic()
    result = client.run(language="python", code="", data={}, timeout_seconds=1)
    assert time.monotonic() - started < 1.5
    assert result["ok"] is False
    assert result["error"] == "Zeitlimit \u00fcberschritten."
    assert result["output"] is None


def test_the_read_timeout_is_the_job_limit_plus_grace(runner_url, fake):
    client = RunnerClient(runner_url)
    client.read_grace_seconds = 1.0
    fake.delay = 1.3  # longer than the job limit, shorter than limit + grace
    assert client.run(language="python", code="", data={}, timeout_seconds=1)["ok"] is True


@pytest.mark.parametrize("status, body, message", [
    (200, b"not json", "Die Rechenumgebung hat eine ung\u00fcltige Antwort geliefert."),
    (200, b'["a list"]', "Die Rechenumgebung hat eine ung\u00fcltige Antwort geliefert."),
    (200, b'{"output": {}}', "Die Rechenumgebung hat eine ung\u00fcltige Antwort geliefert."),
    (400, b'{"error": "code too long"}', "Die Rechenumgebung hat die Anfrage abgelehnt."),
    (500, b"Traceback: secret", "Die Rechenumgebung hat mit einem Fehler geantwortet (HTTP 500)."),
])
def test_bad_answers_become_fixed_german_errors(runner_url, fake, status, body, message):
    fake.run_status, fake.run_body = status, body
    result = RunnerClient(runner_url).run(language="python", code="", data={}, timeout_seconds=5)
    assert result["ok"] is False and result["error"] == message
    assert "secret" not in json.dumps(result)


def test_runner_failure_result_is_passed_on_bounded(runner_url, fake):
    fake.run_body = json.dumps({
        "ok": False, "output": None,
        "error": "Der Auswerter ist mit einem Fehler abgebrochen." + "x" * 1000,
        "logs": "Traceback ...\n" + "y" * 100_000, "duration_ms": 40,
    }).encode()
    result = RunnerClient(runner_url).run(language="python", code="", data={}, timeout_seconds=5)
    assert result["ok"] is False
    assert result["error"].startswith("Der Auswerter ist mit einem Fehler abgebrochen.")
    assert len(result["error"]) == 500
    assert len(result["logs"]) == 64 * 1024
    assert result["duration_ms"] == 40


def test_non_finite_numbers_in_the_answer_become_null(runner_url, fake):
    fake.run_body = (b'{"ok": true, "output": {"p": NaN, "xs": [1.5, Infinity]}, '
                     b'"logs": "", "duration_ms": "fast"}')
    result = RunnerClient(runner_url).run(language="python", code="", data={}, timeout_seconds=5)
    assert result["ok"] is True
    assert result["output"] == {"p": None, "xs": [1.5, None]}
    assert isinstance(result["duration_ms"], int)


def test_oversized_answer_is_refused(runner_url, fake, monkeypatch):
    import experiments.runner_client as rc

    monkeypatch.setattr(rc, "MAX_RESPONSE_BYTES", 1000)
    fake.run_body = json.dumps({"ok": True, "output": {"blob": "z" * 5000}}).encode()
    result = RunnerClient(runner_url).run(language="python", code="", data={}, timeout_seconds=5)
    assert result["ok"] is False
    assert result["error"] == "Die Rechenumgebung hat eine ung\u00fcltige Antwort geliefert."


def test_input_is_checked_before_sending(runner_url, fake, monkeypatch):
    import experiments.runner_client as rc

    client = RunnerClient(runner_url)
    assert client.run(language="cobol", code="", data={}, timeout_seconds=5)["ok"] is False
    assert client.run(language="python", code="x" * 200_001, data={},
                      timeout_seconds=5)["error"] == "Der Code ist zu lang."
    assert client.run(language="python", code="", data={"v": float("nan")},
                      timeout_seconds=5)["error"] == "Die Eingabedaten enthalten ung\u00fcltige Werte."
    monkeypatch.setattr(rc, "MAX_REQUEST_BYTES", 100)
    assert client.run(language="python", code="", data={"big": "b" * 200},
                      timeout_seconds=5)["error"] == \
        "Die Eingabedaten sind zu gross f\u00fcr die Rechenumgebung."
    assert fake.requests == []


def test_unavailable_run_invalidates_the_health_cache(runner_url, fake):
    client = RunnerClient(runner_url)
    assert client.health()["ok"] is True
    fake.run_status, fake.run_body = 503, b"{}"
    with pytest.raises(Unavailable):
        client.run(language="python", code="", data={}, timeout_seconds=5)
    fake.health["ok"] = False
    assert client.health()["ok"] is False  # probed again, not served from cache


def test_concurrent_runs_are_independent(runner_url, fake):
    client = RunnerClient(runner_url)
    results = []

    def go(i):
        results.append(client.run(language="python", code="", data={"i": i},
                                  timeout_seconds=5)["output"]["echo"]["i"])

    threads = [threading.Thread(target=go, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sorted(results) == list(range(6))
