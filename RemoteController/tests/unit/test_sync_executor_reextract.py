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

import dataclasses
import json
import logging
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


def _requeue(rc) -> int:
    store = rc.state()
    try:
        return store.requeue_reextract(current_extraction_stamp())
    finally:
        store.close()


def _forget_hash(rc, *rels: str) -> None:
    """As for a row uploaded before this release: no stamp, no hash."""
    store = rc.state()
    try:
        for rel in rels:
            store.set_extraction(rel, None, None)
    finally:
        store.close()


def _newer_extractor_reads(rc, suffix: str) -> None:
    """A newer extractor reads the same file into a different text."""
    from sync import knovas_uploader

    real = knovas_uploader.extract_document_guarded

    def newer(path, **kwargs):
        doc = real(path, **kwargs)
        return dataclasses.replace(doc, text=doc.text + suffix, sentences=None,
                                   sections=None, pages=None)

    rc.monkeypatch.setattr("sync.knovas_uploader.extract_document_guarded", newer)


class TestReextraction:
    def test_unchanged_text_is_not_uploaded_again(self, rc):
        rc.write("a.txt")
        rc.run(rc.body())
        sha = rc.extraction("a.txt").text_sha256
        rc.upgrade()
        assert rc.outdated() == 1
        assert _requeue(rc) == 1
        result = rc.run(rc.body())
        assert rc.server.inits == [], "nothing sent: no billing"
        assert (result.reextract_unchanged, result.reextract_uploaded, result.files_uploaded) == (1, 0, 0)
        assert result.transmissions == []
        assert rc.extraction("a.txt") == (current_extraction_stamp(), sha, None, 0)
        assert rc.outdated() == 0
        assert rc.run(rc.body()).reextract_reached == 0, "done: it never comes back"

    def test_changed_text_is_uploaded_in_place(self, rc):
        rc.write("a.txt")
        rc.run(rc.body())
        old = rc.extraction("a.txt").text_sha256
        rc.upgrade()
        _requeue(rc)
        _newer_extractor_reads(rc, " samt Tabelle")
        result = rc.run(rc.body())
        assert rc.server.rels() == ["a.txt"]
        assert rc.server.inits[0]["identifier"] == "rc-sync/a.txt", "same identifier: in place"
        assert result.reextract_uploaded == 1 and result.files_uploaded == 1
        state = rc.extraction("a.txt")
        assert state.stamp == current_extraction_stamp() and state.text_sha256 not in (None, old)
        assert rc.outdated() == 0 and rc.run(rc.body()).files_uploaded == 0

    def test_a_row_from_before_hashes_is_uploaded_once(self, rc):
        rc.write("a.txt")
        rc.run(rc.body())
        _forget_hash(rc, "a.txt")
        _requeue(rc)
        assert rc.run(rc.body()).reextract_uploaded == 1
        rc.upgrade()
        _requeue(rc)
        assert rc.run(rc.body()).reextract_unchanged == 1, "later upgrades send only what changed"

    def test_an_unchanged_re_extraction_follows_the_new_partial_note(self, rc):
        """A born-digital PDF an older release recorded partial: the newer
        extraction is complete and the text the same -- not re-sent, and
        off the backfill list."""
        rc.write("a.txt")
        rc.run(rc.body())
        store = rc.state()
        try:
            store.record_partial("a.txt", FIXED_MTIME_ISO, len(TEXT), "tk-1",
                                 {"reason": "ocr_backend_none"})
        finally:
            store.close()
        rc.upgrade()
        _requeue(rc)
        assert rc.run(rc.body()).reextract_unchanged == 1
        store = rc.state()
        try:
            assert store.partial_paths() == []
        finally:
            store.close()


class TestQueueOrderAndBounds:
    def test_partial_first_then_pdf_docx_mail_and_the_rest(self, rc):
        from sync.sync_executor import plan_sync_cycle

        names = ["z.txt", "c.msg", "b.eml", "d.docx", "e.pdf", "f.pdf", "notiz.md"]
        store = rc.state()
        try:
            for name in names:
                rc.write(name)
                store.record_upload(name, FIXED_MTIME_ISO, len(TEXT), "tk-old")
            store.record_partial("f.pdf", FIXED_MTIME_ISO, len(TEXT), "tk-old",
                                 {"ocr_pages_skipped": 1})
            assert store.requeue_reextract(current_extraction_stamp()) == 7
            plan = plan_sync_cycle(rc.body(), store)
        finally:
            store.close()
        order = [item[1] for item in plan.reextract_queue]
        assert order[:3] == ["f.pdf", "e.pdf", "d.docx"]
        assert set(order[3:5]) == {"b.eml", "c.msg"} and set(order[5:]) == {"notiz.md", "z.txt"}
        assert plan.reextract_reached == 7 and plan.upload_queue == []

    def test_the_bound_per_cycle(self, rc):
        rc.env(RC_REEXTRACT_PER_CYCLE="2")
        for i in range(5):
            rc.write(f"r{i}.txt")
        rc.run(rc.body())
        rc.upgrade()
        assert _requeue(rc) == 5
        cycles = [rc.run(rc.body()).reextract_unchanged for _ in range(4)]
        assert cycles == [2, 2, 1, 0]
        assert rc.outdated() == 0

    def test_on_top_of_new_work_and_within_max_files_per_cycle(self, rc):
        for i in range(3):
            rc.write(f"r{i}.txt")
        rc.run(rc.body())
        rc.upgrade()
        _requeue(rc)
        rc.write("Neu1.txt")
        rc.write("Neu2.txt")
        result = rc.run(rc.body(), sync_config={"max_files_per_cycle": 3})
        assert sorted(rc.server.rels()) == ["Neu1.txt", "Neu2.txt"], "new work first"
        assert result.files_uploaded == 2 and result.reextract_unchanged == 1
        assert result.reextract_reached == 3

    def test_processing_keeps_the_priority_not_small_first(self, rc):
        big = "Rechnung " * 600
        rc.write("gross.txt", text=big)
        rc.write("klein.txt")
        rc.run(rc.body())
        _forget_hash(rc, "gross.txt", "klein.txt")
        store = rc.state()
        try:
            store.record_partial("gross.txt", FIXED_MTIME_ISO, len(big), "tk-1",
                                 {"ocr_pages_skipped": 1})
            store.requeue_reextract(current_extraction_stamp())
        finally:
            store.close()
        rc.run(rc.body())
        assert rc.server.rels() == ["gross.txt", "klein.txt"]

    def test_a_fields_change_wins_and_its_upload_clears_the_mark(self, rc):
        rc.write("a.txt")
        rc.run(rc.body())
        rc.upgrade()
        _requeue(rc)
        result = rc.run(rc.body(fields={"doc_type": "invoice"}))
        assert result.document_sync.fields_changed == 1 and result.reextract_reached == 0
        assert rc.server.rels() == ["a.txt"]
        assert rc.server.inits[0]["fields"] == {"doc_type": "invoice"}
        assert rc.outdated() == 0 and rc.extraction("a.txt").resend_reason is None

    def test_a_sequential_subfolder_waits_for_its_re_extractions(self, rc, monkeypatch):
        from sync.subfolder_queue import SubfolderQueue

        calls = []
        original = SubfolderQueue.maybe_advance

        def spy(self, source_root, **kwargs):
            calls.append(kwargs)
            return original(self, source_root, **kwargs)

        monkeypatch.setattr(SubfolderQueue, "maybe_advance", spy)
        rc.write("A/r.txt")
        rc.write("B/r.txt")
        cfg = {"sequential_subfolders": True}
        first = rc.run(rc.body(), sync_config=cfg)
        assert first.subfolder_progress["current_subfolder"] == "A"
        rc.upgrade()
        assert _requeue(rc) == 1
        result = rc.run(rc.body(), sync_config=cfg)
        assert result.reextract_reached == 1 and calls[-1]["modified"] == 1
        assert result.subfolder_progress["current_subfolder"] == "A", "waits for the re-extraction"
        done = rc.run(rc.body(), sync_config=cfg)
        assert done.subfolder_progress["current_subfolder"] == "B"


class TestFailures:
    def test_a_failure_keeps_the_row_outdated_and_leaves_the_queue_after_three(self, rc):
        rc.write("a.txt")
        rc.run(rc.body())
        _forget_hash(rc, "a.txt")
        _requeue(rc)
        rc.server.fail_init = 500
        for attempt in (1, 2, 3):
            result = rc.run(rc.body())
            assert (result.reextract_failed, result.files_retry) == (1, 1), attempt
            assert rc.outdated() == 1
        assert rc.extraction("a.txt").resend_reason is None, "left the queue after 3 attempts"
        assert rc.run(rc.body()).reextract_reached == 0 and rc.server.inits == [], "no loop"
        store = rc.state()
        try:
            assert store.retry_count("a.txt") == 0 and store.partial_paths() == [], \
                "never an extraction retry, never partial: Knovas holds the last upload"
            assert store.load_fingerprints()["a.txt"] == (FIXED_MTIME_ISO, len(TEXT))
        finally:
            store.close()
        rc.server.fail_init = None
        assert _requeue(rc) == 1, "a new request queues it again"
        assert rc.run(rc.body()).reextract_uploaded == 1 and rc.outdated() == 0

    def test_an_unconvertible_file_takes_the_current_stamp_and_leaves_the_queue(self, rc):
        rc.write("leer.txt", text="")
        rc.run(rc.body())
        rc.upgrade()
        assert rc.outdated() == 1 and _requeue(rc) == 1
        result = rc.run(rc.body())
        assert rc.server.inits == [] and result.files_retry == 0
        state = rc.extraction("leer.txt")
        assert state.stamp == current_extraction_stamp() and state.resend_reason is None
        assert rc.outdated() == 0


class TestConfiguration:
    @pytest.fixture(autouse=True)
    def _fresh_config_afterwards(self):
        from config import reset_config

        yield
        reset_config()

    @staticmethod
    def _load(monkeypatch, raw: Optional[str]):
        from config import load_config, reset_config

        monkeypatch.delenv("RC_REEXTRACT_PER_CYCLE", raising=False)
        if raw is not None:
            monkeypatch.setenv("RC_REEXTRACT_PER_CYCLE", raw)
        reset_config()
        return load_config(validate=False, force_reload=True)

    @pytest.mark.parametrize("raw,expected", [(None, 100), ("250", 250), ("0", 1),
                                              ("99999", 10000), ("viele", 100)])
    def test_the_bound_is_clamped_at_runtime(self, monkeypatch, raw, expected):
        from config import reextract_per_cycle

        self._load(monkeypatch, raw)
        assert reextract_per_cycle() == expected

    def test_boot_refuses_what_it_would_have_to_guess(self, monkeypatch):
        from config import _reextract_config_problems, load_config

        self._load(monkeypatch, "0")
        assert _reextract_config_problems() == ["RC_REEXTRACT_PER_CYCLE must be between 1 and 10000"]
        self._load(monkeypatch, "viele")
        assert _reextract_config_problems() == ["RC_REEXTRACT_PER_CYCLE must be an integer"]
        with pytest.raises(SystemExit):
            load_config(validate=True, force_reload=True)
        self._load(monkeypatch, "500")
        assert _reextract_config_problems() == []


def test_a_re_extraction_logs_counts_only(rc, caplog):
    secret = "Muster AG Geheimakte"
    rc.write(f"{secret}.txt", text=f"{secret}: Honorarnote")
    rc.run(rc.body())
    rc.upgrade()
    caplog.clear()
    caplog.set_level(logging.INFO)
    assert _requeue(rc) == 1
    result = rc.run(rc.body())
    assert result.reextract_unchanged == 1
    assert "reextract uploaded=0 unchanged=1 failed=0 reached=1" in caplog.text
    assert secret not in caplog.text and "Honorarnote" not in caplog.text
