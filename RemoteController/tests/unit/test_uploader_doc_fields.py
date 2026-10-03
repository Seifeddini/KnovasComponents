"""Init ``fields``, the echo and the fallback in the uploader (spec 3.6).

The init body without a fields configuration must stay exactly what it was
before document fields existed (the backward-compatibility rule, modelled on
``tests/test_sync_access_groups.py``). With one, ``fields`` is the last key,
``{}`` is sent only to clear values staged before, a refusal the server
blames on the fields is re-posted once without them (D5), and a 503
``doc_fields_*`` comes back at once instead of after five backoff rounds.

The HTTP layer is ``sync.knovas_uploader.requests.request`` answered by a
scripted server; extraction runs in-process on small text files.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Optional

import pytest
import requests

from sync.doc_fields_payload import SourceSpec, spec_from_source
from sync.knovas_uploader import MAX_TITLE_CHARS, SemantixUploader

INIT = "/secured/init_document_transmission"
PART = "/secured/transmit_document_part"
BODY = {"ingestion": {"identifier_prefix": "rc-sync"}}
ECHO = {
    "staged": 2,
    "mapped_keys": {"mandant": "mandant", "doc_type": "doc_type"},
    "unknown_keys": [],
    "warnings": [{"key": "mandant", "path": "fields.mandant", "code": "unresolved_entity"}],
}


def _response(status: int, body: Any = None) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    resp._content = b"" if body is None else json.dumps(body).encode("utf-8")
    resp.headers["Content-Type"] = "application/json"
    return resp


def _created(**extra: Any) -> requests.Response:
    return _response(201, {"status": "success", "message": "Transmission initialized",
                           "transmission_key_id": "tk-1", **extra})


class Server:
    """Answers the uploader's calls; ``init`` decides each init answer."""

    def __init__(self, init: Callable[[dict, int], requests.Response]):
        self.init = init
        self.calls: list[tuple[str, str, Optional[dict]]] = []

    @property
    def inits(self) -> list[dict]:
        return [body for _, path, body in self.calls if path == INIT]

    def __call__(self, method: str, url: str, json: Optional[dict] = None, **_: Any):
        path = "/" + url.split("/", 3)[3]
        self.calls.append((method, path, json))
        if path == INIT:
            return self.init(json or {}, len(self.inits))
        if path == PART:
            return _response(200, {"status": "success", "transmission_complete": True})
        return _response(404, {"status": "error", "error_code": "HTTP_404"})


@pytest.fixture
def env(tmp_path, monkeypatch):
    """In-process extraction, no sidecar, a generous ingest limiter and no
    backoff sleeps."""
    from config import load_config, reset_config
    from sync.ingest_rate_limit import configure

    monkeypatch.setenv("RC_EXTRACT_TIMEOUT_SECONDS", "0")
    monkeypatch.delenv("RC_DOC_FIELDS", raising=False)
    reset_config()
    load_config(validate=False, force_reload=True)
    configure(60000, 1000)
    monkeypatch.setattr("sync.knovas_uploader.write_context_sidecar", lambda *a, **k: None)
    monkeypatch.setattr("sync.knovas_uploader.time.sleep", lambda s: None)
    yield tmp_path
    reset_config()


def _serve(monkeypatch, init) -> Server:
    server = Server(init)
    monkeypatch.setattr("sync.knovas_uploader.requests.request", server)
    return server


def _doc(tmp_path: Path, name: str = "Rechnung_17.txt") -> Path:
    path = tmp_path / name
    path.write_text("Rechnung fuer Beratung", encoding="utf-8")
    return path


def _today(rel: str, *, title: str, access_groups=None) -> dict:
    """The init body as it was before document fields existed."""
    body = {"identifier": f"rc-sync/{rel}", "part_count": 1, "title": title, "path": rel}
    if access_groups:
        body["access_groups"] = list(access_groups)
    return body


MANDATE = {"path": "/data/Mandate", "fields": {"doc_type": "invoice"},
           "field_templates": ["{mandant}/{period}/**"]}
REL = "Muster AG/GJ 2024/Rechnung_17.txt"


class TestBackwardCompatibleBody:
    def test_no_source_means_todays_body_byte_for_byte(self, env, monkeypatch):
        server = _serve(monkeypatch, lambda body, n: _created())
        result = SemantixUploader().upload_file(_doc(env), "note.txt", BODY)
        assert result.status == "ok" and result.fields is None
        assert json.dumps(server.inits[0]) == json.dumps(_today("note.txt", title="Rechnung_17.txt"))

    def test_source_without_fields_sends_no_fields_key(self, env, monkeypatch):
        server = _serve(monkeypatch, lambda body, n: _created())
        spec = spec_from_source({"path": "/data", "access_groups": ["litigation"]})
        result = SemantixUploader().upload_file(_doc(env), "note.txt", BODY, source=spec)
        assert json.dumps(server.inits[0]) == json.dumps(
            _today("note.txt", title="Rechnung_17.txt", access_groups=["litigation"])
        )
        assert result.fields is not None and result.fields.outcome == "none"
        assert not result.fields.fields_sent

    def test_kill_switch_off_never_sends_fields(self, env, monkeypatch):
        from config import load_config, reset_config

        monkeypatch.setenv("RC_DOC_FIELDS", "off")
        reset_config()
        load_config(validate=False, force_reload=True)
        server = _serve(monkeypatch, lambda body, n: _created(fields=ECHO))
        result = SemantixUploader().upload_file(
            _doc(env), REL, BODY, source=spec_from_source(MANDATE), previous_fields_sent=True
        )
        assert "fields" not in server.inits[0]
        assert result.fields is None and result.status == "ok"

    def test_metadata_only_source_and_a_pdf_like_file_send_nothing(self, env, monkeypatch):
        server = _serve(monkeypatch, lambda body, n: _created())
        spec = spec_from_source({"path": "/data", "metadata_fields": ["email_date", "email_doc_type"]})
        result = SemantixUploader().upload_file(_doc(env), "note.txt", BODY, source=spec)
        assert "fields" not in server.inits[0]
        assert result.fields.outcome == "none" and result.fields.digest != ""


class TestFieldsOnTheWire:
    def test_values_go_last_and_source_groups_win(self, env, monkeypatch):
        server = _serve(monkeypatch, lambda body, n: _created(fields=ECHO))
        spec = spec_from_source({**MANDATE, "access_groups": ["mandate"]})
        result = SemantixUploader().upload_file(
            _doc(env), REL, BODY, access_groups=("ignored",), source=spec
        )
        body = server.inits[0]
        assert list(body) == ["identifier", "part_count", "title", "path", "access_groups", "fields"]
        assert body["access_groups"] == ["mandate"]
        assert body["fields"] == {"doc_type": "invoice", "mandant": "Muster AG", "period": "GJ 2024"}
        assert "fields_mode" not in body and "fields_strict" not in body
        assert result.fields.outcome == "staged" and result.fields.staged == 2
        assert result.fields.warning_codes == Counter({"unresolved_entity": 1})
        assert result.fields.digest

    def test_no_echo_is_not_accepted_never_staged(self, env, monkeypatch):
        _serve(monkeypatch, lambda body, n: _created())
        result = SemantixUploader().upload_file(_doc(env), REL, BODY, source=spec_from_source(MANDATE))
        assert result.status == "ok"
        assert result.fields.outcome == "not_accepted"

    def test_empty_object_only_to_clear_values_staged_before(self, env, monkeypatch):
        server = _serve(monkeypatch, lambda body, n: _created(fields={"staged": 0, "mapped_keys": {},
                                                                       "unknown_keys": [], "warnings": []}))
        empty = spec_from_source({"path": "/data", "field_templates": ["Archiv/**"]})
        first = SemantixUploader().upload_file(_doc(env), REL, BODY, source=empty)
        assert "fields" not in server.inits[0] and first.fields.outcome == "none"
        cleared = SemantixUploader().upload_file(
            _doc(env), REL, BODY, source=empty, previous_fields_sent=True
        )
        assert server.inits[1]["fields"] == {}
        assert cleared.fields.outcome == "cleared"

    def test_a_clear_without_echo_is_not_accepted(self, env, monkeypatch):
        _serve(monkeypatch, lambda body, n: _created())
        result = SemantixUploader().upload_file(
            _doc(env), REL, BODY, source=SourceSpec(), previous_fields_sent=True
        )
        assert result.fields.outcome == "not_accepted"

    def test_title_is_capped_at_500(self, env, monkeypatch):
        from sync.document_text import ExtractedDocument

        server = _serve(monkeypatch, lambda body, n: _created())
        SemantixUploader().upload_file(_doc(env, "x.txt"), "x.txt", BODY)
        monkeypatch.setattr(
            "sync.knovas_uploader.extract_document_guarded",
            lambda path, document_key=None: ExtractedDocument(
                text="Rechnung", sentences=None, title="T" * 600
            ),
        )
        SemantixUploader().upload_file(_doc(env, "y.txt"), "y.txt", BODY)
        assert server.inits[0]["title"] == "x.txt", "a short title is unchanged"
        assert server.inits[1]["title"] == "T" * MAX_TITLE_CHARS

    def test_client_side_drops_are_counted(self, env, monkeypatch):
        server = _serve(monkeypatch, lambda body, n: _created(fields=ECHO))
        spec = SourceSpec(fields={"doc_type": "invoice", "notes": "x" * 300})
        result = SemantixUploader().upload_file(_doc(env), "note.txt", BODY, source=spec)
        assert server.inits[0]["fields"] == {"doc_type": "invoice"}
        assert result.fields_dropped == {"value_too_long": 1}


REFUSALS = [
    (400, "invalid_fields", {"path": "fields"}, "invalid_fields", False),
    (400, "fields_too_large", {"path": "fields"}, "fields_too_large", False),
    (400, "ambiguous_field", {"path": "fields.partei", "candidates": ["party"]}, "ambiguous_field", False),
    (422, "unknown_field", {"path": "fields.mandat"}, "unknown_field", False),
    (422, "invalid_fields", {"errors": [{"path": "fields.period", "code": "ambiguous_date"}]},
     "invalid_fields", False),
    (400, "something_new", {"path": "fields.doc_type"}, "other", False),
    (503, "doc_fields_unavailable", {}, "doc_fields_unavailable", True),
    (503, "doc_fields_ingest_unavailable", {}, "doc_fields_ingest_unavailable", True),
    (401, "assertion_rejected", {}, "assertion_rejected", False),
]


class TestRefusalFallback:
    @pytest.mark.parametrize("status,code,extra,expected,transient", REFUSALS)
    def test_one_retry_without_fields_and_the_document_is_indexed(
        self, env, monkeypatch, status, code, extra, expected, transient
    ):
        def init(body, n):
            if "fields" in body:
                return _response(status, {"status": "error", "error_code": code, "error": "x", **extra})
            return _created()

        server = _serve(monkeypatch, init)
        spec = spec_from_source({**MANDATE, "access_groups": ["mandate"]})
        result = SemantixUploader().upload_file(_doc(env), REL, BODY, source=spec)
        assert result.status == "ok" and result.transmission_key_id == "tk-1"
        assert len(server.inits) == 2, "exactly one retry"
        retry = server.inits[1]
        assert json.dumps(retry) == json.dumps(
            _today(REL, title="Rechnung_17.txt", access_groups=["mandate"])
        ), "the retry is today's body"
        assert result.fields.outcome == f"refused:{expected}"
        assert result.fields.transient is transient
        assert result.ingestion_requests == 3  # two inits, one part
        assert any(path == PART for _, path, _ in server.calls)

    def test_retry_that_fails_too_is_an_init_failure_not_a_refusal(self, env, monkeypatch):
        """BROKERED tenant: an access_groups body 401s on its own, so the
        refusal was not caused by the fields."""
        server = _serve(monkeypatch, lambda body, n: _response(
            401, {"status": "error", "error_code": "assertion_rejected", "error": "x"}))
        spec = spec_from_source({**MANDATE, "access_groups": ["mandate"]})
        result = SemantixUploader().upload_file(_doc(env), REL, BODY, source=spec)
        assert len(server.inits) == 2
        assert result.status == "error" and result.error == "init failed: 401"
        assert result.fields is None

    def test_an_error_the_fields_did_not_cause_is_not_retried(self, env, monkeypatch):
        server = _serve(monkeypatch, lambda body, n: _response(
            400, {"status": "error", "error": "title too long", "type": "validation_error"}))
        result = SemantixUploader().upload_file(_doc(env), REL, BODY, source=spec_from_source(MANDATE))
        assert len(server.inits) == 1
        assert result.status == "error" and result.error == "init failed: 400"
        assert result.fields is None

    def test_no_fallback_without_fields(self, env, monkeypatch):
        server = _serve(monkeypatch, lambda body, n: _response(
            422, {"status": "error", "error_code": "unknown_field", "path": "fields.x"}))
        result = SemantixUploader().upload_file(_doc(env), "note.txt", BODY)
        assert len(server.inits) == 1 and result.error == "init failed: 422"


class TestNoInCallBackoffForDocFields503:
    def test_doc_fields_503_returns_at_once(self, env, monkeypatch):
        def init(body, n):
            if "fields" in body:
                return _response(503, {"status": "error", "error_code": "doc_fields_unavailable"})
            return _created()

        server = _serve(monkeypatch, init)
        result = SemantixUploader().upload_file(_doc(env), REL, BODY, source=spec_from_source(MANDATE))
        assert [("fields" in b) for b in server.inits] == [True, False]
        assert result.fields.outcome == "refused:doc_fields_unavailable"

    def test_a_plain_503_with_fields_is_still_retried(self, env, monkeypatch):
        def init(body, n):
            if n < 3:
                return _response(503, {"status": "error", "error": "busy"})
            return _created(fields=ECHO)

        server = _serve(monkeypatch, init)
        result = SemantixUploader().upload_file(_doc(env), REL, BODY, source=spec_from_source(MANDATE))
        assert len(server.inits) == 3 and all("fields" in b for b in server.inits)
        assert result.fields.outcome == "staged"

    def test_without_fields_a_doc_fields_503_keeps_the_old_backoff(self, env, monkeypatch):
        server = _serve(monkeypatch, lambda body, n: _response(
            503, {"status": "error", "error_code": "doc_fields_unavailable"}))
        result = SemantixUploader().upload_file(_doc(env), "note.txt", BODY)
        assert len(server.inits) == 5 and result.error == "init failed: 503"
