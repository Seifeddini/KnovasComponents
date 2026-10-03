"""Re-extraction through the sync cycle (spec L6).

Every upload records the extraction stamp and the sha256 of what it
carried. A row ``POST /sync/reextract/requeue`` queued is re-extracted on a
later cycle -- partial first, then .pdf, .docx, mail, the rest; at most
RC_REEXTRACT_PER_CYCLE, after new, modified and fields work -- and uploaded
in place only when its upload would change. Whole cycles with the real
uploader against a scripted Secure API; extraction runs in-process on small
text files.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

import pytest
import requests

from sync.extraction_stamp import current_extraction_stamp
from sync.sync_state import SyncStateStore

INIT = "/secured/init_document_transmission"
PART = "/secured/transmit_document_part"
DELETE = "/secured/delete_information_object"
FIXED_MTIME = 1_700_000_000
FIXED_MTIME_ISO = "2023-11-14T22:13:20Z"
TEXT = "Rechnung fuer Beratung"


def _response(status: int, body: Any = None) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    resp._content = b"" if body is None else json.dumps(body).encode("utf-8")
    resp.headers["Content-Type"] = "application/json"
    return resp


class Server:
    """A scripted Secure API. ``fail_init``: answer every init with that status."""

    def __init__(self) -> None:
        self.inits: list[dict] = []
        self.fail_init: Optional[int] = None

    def rels(self) -> list[str]:
        return [body["path"] for body in self.inits]

    def __call__(self, method: str, url: str, json: Optional[dict] = None, **_: Any):
        path = "/" + url.split("/", 3)[3]
        if path == INIT:
            self.inits.append(json or {})
            if self.fail_init is not None:
                return _response(self.fail_init, {"status": "error"})
            return _response(201, {"status": "success",
                                   "transmission_key_id": f"tk-{len(self.inits)}"})
        if path in (PART, DELETE):
            return _response(200, {"status": "success", "transmission_complete": True})
        return _response(404, {"status": "error", "error_code": "HTTP_404"})


class Harness:
    def __init__(self, root: Path, state_path: Path, monkeypatch) -> None:
        self.root = root
        self.state_path = state_path
        self.monkeypatch = monkeypatch
        self.server = Server()

    def write(self, rel: str, text: str = TEXT) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        os.utime(path, (FIXED_MTIME, FIXED_MTIME))
        return path

    def body(self, mode: str = "incremental", **source: Any) -> dict:
        return {
            "mode": mode,
            "sources": [{"path": str(self.root), "recursive": True, **source}],
            "filters": {"include_globs": ["*.txt", "*.md", "*.pdf", "*.docx", "*.eml", "*.msg"]},
            "ingestion": {"identifier_prefix": "rc-sync"},
        }

    def run(self, body: dict, *, sync_config: Optional[dict] = None):
        from sync.knovas_uploader import SemantixUploader
        from sync.sync_executor import run_sync_work

        self.monkeypatch.setattr("sync.knovas_uploader.requests.request", self.server)
        self.server.inits.clear()
        return run_sync_work(body, SemantixUploader(), sync_config=sync_config)

    def state(self) -> SyncStateStore:
        return SyncStateStore(str(self.state_path))

    def extraction(self, rel: str):
        store = self.state()
        try:
            return store.extraction_state(rel)
        finally:
            store.close()

    def outdated(self) -> int:
        store = self.state()
        try:
            return store.count_extraction_outdated(current_extraction_stamp())
        finally:
            store.close()

    def upgrade(self) -> None:
        """A newer knovas-extract: every stamp so far is an older extraction."""
        self.monkeypatch.setattr("sync.extraction_stamp._knovas_extract_version",
                                 lambda: "99.0.0")

    def env(self, **values: str) -> None:
        from config import load_config, reset_config

        for key, value in values.items():
            self.monkeypatch.setenv(key, value)
        reset_config()
        load_config(validate=False, force_reload=True)


@pytest.fixture
def rc(tmp_path, monkeypatch):
    from config import load_config, reset_config
    from sync.ingest_rate_limit import configure

    root = tmp_path / "share"
    root.mkdir()
    state_path = tmp_path / "state" / ".rc-sync-state.json"
    monkeypatch.setenv("RC_WATCH_ROOTS", str(root))
    monkeypatch.setenv("RC_SYNC_STATE_PATH", str(state_path))
    monkeypatch.setenv("RC_EXTRACT_TIMEOUT_SECONDS", "0")
    monkeypatch.setenv("SEMANTIX_CERT_AUTO_RENEW_ENABLED", "false")
    monkeypatch.delenv("M365_FOLDER_URL", raising=False)
    for key in ("RC_DOC_FIELDS", "RC_FIELDS_REUPLOAD_PER_CYCLE", "RC_REEXTRACT_PER_CYCLE",
                "RC_UPLOAD_ORDER", "RC_PDF_TEXT_MODE"):
        monkeypatch.delenv(key, raising=False)
    reset_config()
    load_config(validate=False, force_reload=True)
    configure(60000, 1000)
    monkeypatch.setattr("sync.knovas_uploader.write_context_sidecar", lambda *a, **k: None)
    monkeypatch.setattr("sync.knovas_uploader.time.sleep", lambda s: None)
    yield Harness(root, state_path, monkeypatch)
    reset_config()


class TestEveryUploadIsStamped:
    def test_an_upload_stores_the_stamp_and_the_hash(self, rc):
        rc.write("a.txt")
        assert rc.run(rc.body()).files_uploaded == 1
        state = rc.extraction("a.txt")
        assert state.stamp == current_extraction_stamp()
        assert state.text_sha256 is not None and len(state.text_sha256) == 64
        assert state.resend_reason is None and rc.outdated() == 0

    def test_rows_synced_before_this_release_are_outdated_and_nothing_is_resent(self, rc):
        rc.write("a.txt")
        store = rc.state()
        try:
            store.record_upload("a.txt", FIXED_MTIME_ISO, len(TEXT), "tk-old")
        finally:
            store.close()
        assert rc.outdated() == 1
        assert rc.run(rc.body()).files_uploaded == 0, "outdated is no reason to upload by itself"

    def test_a_partial_upload_is_stamped_too(self, rc, monkeypatch):
        monkeypatch.setattr("sync.knovas_uploader.partial_note_for",
                            lambda doc, expect_ocr: {"ocr_pages_skipped": 2, "ocr_pages": 5})
        rc.write("scan.txt")
        assert rc.run(rc.body()).files_partial == 1
        assert rc.extraction("scan.txt").stamp == current_extraction_stamp()

    def test_a_new_unconvertible_file_carries_the_current_stamp(self, rc):
        rc.write("leer.txt", text="")
        rc.run(rc.body())
        assert rc.server.inits == []
        assert rc.extraction("leer.txt").stamp == current_extraction_stamp()
        assert rc.outdated() == 0, "the current extractor's verdict, not an older extraction"

    def test_a_changed_setting_makes_every_row_outdated(self, rc):
        rc.write("a.txt")
        rc.write("b.txt")
        rc.run(rc.body())
        assert rc.outdated() == 0
        rc.env(RC_PDF_TEXT_MODE="plain")
        assert rc.outdated() == 2

    def test_full_mode_stamps_existing_rows_only(self, rc):
        rc.write("a.txt")
        rc.run(rc.body())
        store = rc.state()
        try:
            store.set_extraction("a.txt", None, None)
        finally:
            store.close()
        rc.write("neu.txt")
        assert rc.run(rc.body(mode="full")).files_uploaded == 2
        assert rc.extraction("a.txt").stamp == current_extraction_stamp()
        assert rc.extraction("neu.txt") is None, "full mode creates no rows"
