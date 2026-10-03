"""scripts/doctor.sh: the document-fields lines, run against the mock.

doctor's Knovas section is Python inside a heredoc. These tests cut it out
and run it against the mock over plain HTTP (no certificates), so the state
it prints is the state the mock plays: a 401 counts as "filters on" only
with error_code assertion_rejected (any other 401 comes from the
certificate check, before any gate), and a server that reads the
GET doc-values pointer only from the URL (before S2) is named.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

MOCK_DIR = Path(__file__).resolve().parents[1]
DOCTOR = MOCK_DIR.parents[1] / "scripts" / "doctor.sh"
TESTING = MOCK_DIR / "testing.py"

if not TESTING.is_file() or not DOCTOR.is_file():  # pragma: no cover
    pytest.skip("mock or doctor.sh not in this checkout", allow_module_level=True)

_spec = importlib.util.spec_from_file_location("knovas_mock_testing_doctor", TESTING)
testing = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(testing)


def _knovas_section() -> str:
    text = DOCTOR.read_text(encoding="utf-8")
    for block in text.split("<<'PY'")[1:]:
        body = block.split("\n", 1)[1].split("\nPY\n", 1)[0]
        if "probe_doc_fields" in body:
            return body
    raise AssertionError("doctor.sh has no document-fields probe")


def _serve(app):
    from werkzeug.serving import make_server

    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _doctor(app) -> str:
    server = _serve(app)
    try:
        env = dict(os.environ, SEMANTIX_API_URL=f"http://127.0.0.1:{server.server_port}",
                   SEMANTIX_CLIENT_CERT="", SEMANTIX_CLIENT_KEY="", SEMANTIX_CA_CERT="",
                   CORTEX_ENABLED="false")
        done = subprocess.run([sys.executable, "-c", _knovas_section()], env=env,
                              capture_output=True, text=True, timeout=60)
        return done.stdout + done.stderr
    finally:
        server.shutdown()


def _lines(output: str) -> list[str]:
    return [line.strip() for line in output.splitlines() if "Dokumentfelder" in line]


def _refusing_certificate(environ, start_response):
    """Every route answers like the Secure API to a refused certificate."""
    body = json.dumps({"status": "error", "error": "Client certificate validation failed",
                       "error_code": "AUTH_FAILED"}).encode()
    start_response("401 UNAUTHORIZED", [("Content-Type", "application/json"),
                                         ("Content-Length", str(len(body)))])
    return [body]


def test_a_refused_certificate_is_no_brokered_tenant():
    lines = _lines(_doctor(_refusing_certificate))
    assert lines == ["WARN  Dokumentfelder: state unknown -- certificate or authentication "
                     "refused (HTTP 401, error_code AUTH_FAILED)"]


def test_an_assertion_refusal_is_filters_on_in_a_brokered_tenant():
    lines = _lines(_doctor(testing.load_mock_app(doc_fields="filters", brokered=True)))
    assert lines[0].startswith("OK  Dokumentfelder: Filter an (BROKERED-Mandant;")


def test_a_server_that_reads_the_pointer_from_the_body():
    lines = _lines(_doctor(testing.load_mock_app(doc_fields="values")))
    assert lines[0].startswith("info  Dokumentfelder: Werte, ohne Liste und Filter")
    assert lines[1] == ("OK  Dokumentfelder: the server reads the document pointer from the "
                        "body (HTTP 404, error_code NOT_FOUND)")


def test_a_server_before_s2_is_named():
    app = testing.load_mock_app(doc_fields="filters")
    testing.mock_state(app).pointer_in_body = False
    lines = _lines(_doctor(app))
    assert lines[0].startswith("OK  Dokumentfelder: Werte, Liste und Filter")
    assert lines[1].startswith("FAIL  Dokumentfelder: Knovas-Update noetig (HTTP 400, "
                               "error_code invalid_value)")


def test_feature_off_probes_nothing_more():
    app = testing.load_mock_app(doc_fields="off")
    lines = _lines(_doctor(app))
    assert len(lines) == 1 and lines[0].startswith("info  Dokumentfelder: aus")
    # Knovas 1.5.0 has document fields on for every account: "aus" is a
    # switch Knovas turned (or an older server), not a step still to come.
    assert "switched off for this tenant at Knovas" in lines[0]
    assert not [r for r in testing.mock_state(app).requests
                if r["method"] == "GET" and r["path"] == "/secured/graph/doc-values"]
