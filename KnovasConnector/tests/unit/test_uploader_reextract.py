"""The uploader's side of re-extraction (spec L6).

Every upload reports the extraction stamp and the sha256 of what it
carried. Given the hash of the last upload (``unchanged_text_sha256``) it
re-extracts, compares, and sends nothing -- no init, no part, no billing --
when they match. The HTTP layer is ``sync.knovas_uploader.requests.request``
answered by a scripted server; extraction runs in-process.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

import pytest
import requests

from sync.doc_fields_payload import spec_from_source
from sync.extraction_stamp import current_extraction_stamp
from sync.knovas_uploader import SemantixUploader

INIT = "/secured/init_document_transmission"
PART = "/secured/transmit_document_part"
BODY = {"ingestion": {"identifier_prefix": "rc-sync"}}
REL = "Rechnung_17.txt"


def _response(status: int, body: Any = None) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    resp._content = b"" if body is None else json.dumps(body).encode("utf-8")
    resp.headers["Content-Type"] = "application/json"
    return resp


class Server:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Optional[dict]]] = []

    @property
    def inits(self) -> list[dict]:
        return [body for _, path, body in self.calls if path == INIT]

    def __call__(self, method: str, url: str, json: Optional[dict] = None, **_: Any):
        path = "/" + url.split("/", 3)[3]
        self.calls.append((method, path, json))
        if path == INIT:
            return _response(201, {"status": "success", "transmission_key_id": "tk-1"})
        if path == PART:
            return _response(200, {"status": "success", "transmission_complete": True})
        return _response(404, {"status": "error", "error_code": "HTTP_404"})


@pytest.fixture
def env(tmp_path, monkeypatch):
    """In-process extraction, no sidecar, a generous ingest limiter, no
    backoff sleeps, and the scripted server."""
    from config import load_config, reset_config
    from sync.ingest_rate_limit import configure

    monkeypatch.setenv("RC_EXTRACT_TIMEOUT_SECONDS", "0")
    monkeypatch.delenv("RC_DOC_FIELDS", raising=False)
    reset_config()
    load_config(validate=False, force_reload=True)
    configure(60000, 1000)
    monkeypatch.setattr("sync.knovas_uploader.write_context_sidecar", lambda *a, **k: None)
    monkeypatch.setattr("sync.knovas_uploader.time.sleep", lambda s: None)
    server = Server()
    monkeypatch.setattr("sync.knovas_uploader.requests.request", server)
    path = tmp_path / REL
    path.write_text("Rechnung fuer Beratung", encoding="utf-8")
    yield server, path
    reset_config()


def test_an_upload_reports_its_stamp_and_the_hash_of_what_it_carried(env):
    server, path = env
    up = SemantixUploader().upload_file(path, REL, BODY)
    assert up.status == "ok" and len(server.inits) == 1
    assert up.extraction_stamp == current_extraction_stamp()
    assert re.fullmatch(r"[0-9a-f]{64}", up.text_sha256)


def test_the_same_hash_sends_nothing(env):
    server, path = env
    first = SemantixUploader().upload_file(path, REL, BODY)
    server.calls.clear()
    again = SemantixUploader().upload_file(path, REL, BODY,
                                           unchanged_text_sha256=first.text_sha256)
    assert server.calls == [], "no init, no part: nothing is billed"
    assert again.status == "unchanged"
    assert (again.transmission_key_id, again.ingestion_requests, again.error) == (None, 0, None)
    assert (again.text_sha256, again.extraction_stamp, again.parts) == (
        first.text_sha256, first.extraction_stamp, first.parts)


def test_another_hash_uploads_as_always(env):
    server, path = env
    up = SemantixUploader().upload_file(path, REL, BODY, unchanged_text_sha256="0" * 64)
    assert up.status == "ok" and len(server.inits) == 1 and up.transmission_key_id == "tk-1"


def test_field_values_and_the_description_are_part_of_it(env):
    server, path = env
    invoice = spec_from_source({"path": "/data", "fields": {"doc_type": "invoice"}})
    contract = spec_from_source({"path": "/data", "fields": {"doc_type": "contract"}})
    first = SemantixUploader().upload_file(path, REL, BODY, source=invoice)
    other_fields = SemantixUploader().upload_file(path, REL, BODY, source=contract,
                                                  unchanged_text_sha256=first.text_sha256)
    assert other_fields.status == "ok"
    described = {"ingestion": {"identifier_prefix": "rc-sync", "description": "Mandat 17"}}
    other_description = SemantixUploader().upload_file(path, REL, described, source=invoice,
                                                       unchanged_text_sha256=first.text_sha256)
    assert other_description.status == "ok"
    same = SemantixUploader().upload_file(path, REL, BODY, source=invoice,
                                          unchanged_text_sha256=first.text_sha256)
    assert same.status == "unchanged"


def _extraction_misses(monkeypatch, note) -> None:
    monkeypatch.setattr("sync.knovas_uploader.partial_note_for", lambda doc, expect_ocr: dict(note))


def test_an_extraction_missing_more_ocr_pages_than_knovas_holds_is_kept(env, monkeypatch):
    """``ocr_pages_missing_at_knovas``: OCR pages the text Knovas holds
    lacks (0: complete). An extraction missing more -- skipped plus failed
    -- is not sent: no init, no part, nothing billed."""
    server, path = env
    _extraction_misses(monkeypatch, {"ocr_pages_skipped": 2, "ocr_pages_failed": 1, "ocr_pages": 5})
    kept = SemantixUploader().upload_file(path, REL, BODY, ocr_pages_missing_at_knovas=2)
    assert server.calls == [] and kept.status == "kept"
    assert (kept.transmission_key_id, kept.ingestion_requests, kept.error) == (None, 0, None)
    assert kept.extraction_stamp == current_extraction_stamp()
    assert kept.text_sha256 is None, "not what Knovas holds: never stored"
    assert SemantixUploader().upload_file(path, REL, BODY, ocr_pages_missing_at_knovas=3).status == "ok"
    assert SemantixUploader().upload_file(path, REL, BODY).status == "ok", "no baseline: sent as always"


def test_a_complete_extraction_is_never_kept(env):
    server, path = env
    up = SemantixUploader().upload_file(path, REL, BODY, ocr_pages_missing_at_knovas=0)
    assert up.status == "ok" and len(server.inits) == 1


def test_the_same_text_missing_more_pages_is_kept_not_unchanged(env, monkeypatch):
    """A page OCR'd blank before and skipped now: the same upload, but its
    note would put a complete document on the backfill list."""
    server, path = env
    first = SemantixUploader().upload_file(path, REL, BODY)
    server.calls.clear()
    _extraction_misses(monkeypatch, {"ocr_pages_skipped": 1, "ocr_pages": 1})
    again = SemantixUploader().upload_file(path, REL, BODY, unchanged_text_sha256=first.text_sha256,
                                           ocr_pages_missing_at_knovas=0)
    assert again.status == "kept" and server.calls == []
