"""PMVP-20 context, verification fix 1: ASSISTANT_CONTEXT_SECONDS is a wall-clock limit for all reads of a question.

The verifier's repro: a local Core that sends its body in 64-byte pieces every 0.5 s kept one question busy for 47.6 s
against a budget of 8, because each read's time left was a socket timeout, which restarts with every packet. Here a
real HTTP server on 127.0.0.1 trickles the body, or the headers, and the client gives up at the deadline whatever the
Core does (``KnovasAPIClient._get_by_deadline``); the remaining hits keep their snippets.
"""
from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from assistant import context as ctx
from assistant.core import make_read_text
from assistant.service import passages_from_rows
from knovas_client import DOCUMENT_TEXT_MAX_BODY_BYTES, KnovasAPIClient

DOCS = [f"{i:08d}-0000-4000-8000-00000000000{i}" for i in range(1, 4)]
SNIPPET = "Die Kuendigungsfrist betraegt drei Monate zum Ende eines Kalendermonats."
TEXT = "Vorher steht etwas. " + SNIPPET + " Nachher steht mehr Text in diesem Dokument."


def _body(text=TEXT):
    return json.dumps({"status": "success", "message": "Document text", "text": text, "offset": 0,
                       "next_offset": None, "total_chars": len(text), "truncated": False}).encode("utf-8")


class _Core(BaseHTTPRequestHandler):
    """``mode``: ``fast`` (the whole answer at once), ``body`` (headers at once, then the body in 64-byte pieces every
    0.5 s), ``headers`` (one byte of the status line and headers every 0.3 s), ``large`` (a body past the limit)."""

    mode = "fast"
    seen: list = []

    def log_message(self, *args):  # the server's own access log: not needed
        pass

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's name
        type(self).seen.append(self.path)
        if not re.fullmatch(r"/secured/document/[0-9a-f-]{36}/text\?.*", self.path):
            self.send_error(404)
            return
        body = _body() if self.mode != "large" else b'{"text": "' + b"x" * (DOCUMENT_TEXT_MAX_BODY_BYTES + 10) + b'"}'
        head = (f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n"
                f"X-Request-ID: req-1\r\n\r\n").encode("ascii")
        try:
            if self.mode == "headers":
                for i in range(len(head)):
                    self.wfile.write(head[i:i + 1])
                    self.wfile.flush()
                    time.sleep(0.3)
                self.wfile.write(body)
            elif self.mode == "body":
                self.wfile.write(head)
                for i in range(0, len(body), 64):
                    self.wfile.write(body[i:i + 64])
                    self.wfile.flush()
                    time.sleep(0.5)
            else:
                self.wfile.write(head + body)
            self.wfile.flush()
        except OSError:  # the client gave up and closed the connection
            pass
        self.close_connection = True


@pytest.fixture
def core():
    _Core.mode, _Core.seen = "fast", []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Core)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


class _Config:
    def __init__(self, values):
        self._v = dict(values)

    def get(self, key, default=None):
        return self._v.get(key, default)

    def get_bool(self, key, default=False):
        return bool(self._v.get(key, default))

    def get_int(self, key, default=0):
        return int(self._v.get(key, default))


def _client(server):
    """The real client, its secured routes pointed at the local server over plain HTTP (no certificate)."""
    client = KnovasAPIClient(config_loader=_Config({
        "api.base_url": "https://core.test", "api.customer_id": "tenant-a", "api.cert_path": "client.crt",
        "api.key_path": "client.key", "api.ca_cert_path": "ca.crt", "api.cert_auto_renew_enabled": False,
        "api.rate_limit.requests_per_second": 0, "api.http_read_timeout": 30}))
    client.base_url = f"http://127.0.0.1:{server.server_address[1]}"
    client._session = requests.Session()
    return client


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    for name in ("ASSISTANT_CONTEXT_DOCS", "ASSISTANT_CONTEXT_SECONDS", "ASSISTANT_PASSAGE_CHARS"):
        monkeypatch.delenv(name, raising=False)


def test_a_fast_answer_is_read_through_the_stream(core):
    part = _client(core).document_text(DOCS[0], deadline=time.monotonic() + 5)
    assert part["text"] == TEXT and part["next_offset"] is None


@pytest.mark.parametrize("mode", ["body", "headers"])
def test_a_trickling_answer_is_given_up_at_the_deadline(core, mode):
    _Core.mode = mode
    started = time.monotonic()
    with pytest.raises(requests.exceptions.ReadTimeout) as caught:
        _client(core).document_text(DOCS[0], deadline=started + 1.0)
    took = time.monotonic() - started
    assert 0.9 <= took < 1.5, took
    assert DOCS[0] not in str(caught.value) and "/secured/document/{id}/text" in str(caught.value)


def test_without_a_deadline_the_read_timeout_is_the_wall_clock_limit(core):
    _Core.mode = "body"
    started = time.monotonic()
    with pytest.raises(requests.exceptions.ReadTimeout):
        _client(core).document_text(DOCS[0], timeout=1.0)
    assert time.monotonic() - started < 1.5


def test_an_answer_past_the_size_limit_is_not_a_part(core):
    _Core.mode = "large"
    with pytest.raises(ValueError):
        _client(core).document_text(DOCS[0], deadline=time.monotonic() + 10)


def test_the_reader_reports_the_trickle_as_a_timeout(core):
    from assistant.errors import CoreFailed

    _Core.mode = "body"
    with pytest.raises(CoreFailed) as caught:
        make_read_text(_client(core))(DOCS[0], 0, 0.8)
    assert caught.value.reason == "timeout"


@pytest.mark.parametrize("seconds", ["1", "2"])
def test_a_trickling_core_holds_a_question_no_longer_than_its_budget(core, monkeypatch, seconds):
    """The verifier's repro through the whole widening: three hits, each document trickled."""
    _Core.mode = "body"
    monkeypatch.setenv("ASSISTANT_CONTEXT_SECONDS", seconds)
    rows = [{"doc_id": f"Mandate/doc-{i}.pdf", "snippet": SNIPPET, "document_uuid": d} for i, d in enumerate(DOCS)]
    started = time.monotonic()
    out = ctx.widen_passages(passages_from_rows(rows), make_read_text(_client(core)))
    took = time.monotonic() - started
    assert took < float(seconds) + 0.5, took
    assert [p.text for p in out] == [SNIPPET] * 3
    assert len(_Core.seen) == 1          # the first read used the whole budget; the others were not started


def test_within_the_budget_the_hits_are_widened(core, monkeypatch):
    monkeypatch.setenv("ASSISTANT_CONTEXT_SECONDS", "2")
    rows = [{"doc_id": f"Mandate/doc-{i}.pdf", "snippet": SNIPPET, "document_uuid": d} for i, d in enumerate(DOCS)]
    out = ctx.widen_passages(passages_from_rows(rows), make_read_text(_client(core)))
    assert [p.text for p in out] == [TEXT] * 3 and len(_Core.seen) == 3
