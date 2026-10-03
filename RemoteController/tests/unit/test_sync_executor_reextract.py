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
import importlib.util
import io
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional
from unittest.mock import patch

import pytest
import requests

from sync.extraction_stamp import current_extraction_stamp
from sync.sync_state import SyncStateStore

BACKFILL = Path(__file__).resolve().parents[2] / "scripts" / "backfill_partial_ocr.py"
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
        self.snippets: list[str] = []
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
        if path == PART:
            self.snippets.append((json or {}).get("snippet", ""))
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
        self.server.snippets.clear()
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


def _cycle_budget_trips(rc, note: dict, text: str = "Rechnung") -> None:
    """The cycle's OCR budget (RC_OCR_MAX_PAGES, RC_OCR_TIME_BUDGET_SECONDS)
    trips on a long scan the backfill completed with its larger one: the
    extraction returns fewer pages and a partial note."""
    from sync import knovas_uploader

    real = knovas_uploader.extract_document_guarded

    def cut(path, **kwargs):
        return dataclasses.replace(real(path, **kwargs), text=text, sentences=None,
                                   sections=None, pages=None)

    rc.monkeypatch.setattr("sync.knovas_uploader.extract_document_guarded", cut)
    rc.monkeypatch.setattr("sync.knovas_uploader.partial_note_for",
                           lambda doc, expect_ocr: dict(note))


def _kept(rc) -> int:
    store = rc.state()
    try:
        return store.count_reextract_kept()
    finally:
        store.close()


def _scanned_pdf(pages: int) -> bytes:
    """Image-only pages, each with its own mark: every page needs OCR."""
    fitz = pytest.importorskip("fitz")
    image = pytest.importorskip("PIL.Image")
    doc = fitz.open()
    for index in range(pages):
        page = doc.new_page(width=595, height=842)
        img = image.new("L", (600, 850), color=255)
        for x in range(10 + index * 7, 60 + index * 7):
            for y in range(10, 40):
                img.putpixel((x, y), 0)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        page.insert_image(page.rect, stream=buf.getvalue())
    out = doc.tobytes()
    doc.close()
    return out


class _PageEngine:
    """An OCR engine (``OcrOptions.backend``) that reads a page as its number."""

    name = "fake"
    version = "1"
    needs_image = False

    def recognize(self, page_image):
        return f"Seite {page_image.page_index + 1} erkannter Text."


def _backfill_script():
    spec = importlib.util.spec_from_file_location("backfill_partial_ocr", BACKFILL)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


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

    def test_an_unchanged_re_extraction_keeps_the_backfills_mark(self, rc):
        """A scan the backfill could not improve (a page that always fails)
        is noted backfill_unchanged, and later runs skip it. An unchanged
        re-extraction sent nothing -- Knovas holds what it held -- so the
        mark stays and the next backfill run does not bill it again."""
        note = {"ocr_pages_failed": 1, "ocr_pages": 2, "text_pages": 1, "ocr_backend": "tesserocr"}
        rc.monkeypatch.setattr("sync.knovas_uploader.partial_note_for",
                               lambda doc, expect_ocr: dict(note))
        rc.write("scan.txt")
        assert rc.run(rc.body()).files_partial == 1
        store = rc.state()
        try:
            store.record_partial("scan.txt", FIXED_MTIME_ISO, len(TEXT), "tk-1",
                                 {**note, "backfill_unchanged": 1})
        finally:
            store.close()
        rc.upgrade()
        _requeue(rc)
        assert rc.run(rc.body()).reextract_unchanged == 1 and rc.server.inits == []
        store = rc.state()
        try:
            assert store.partial_note("scan.txt") == {**note, "backfill_unchanged": 1}
        finally:
            store.close()
        with patch("sync.sync_scheduler.load_last_sync_body", return_value=rc.body()):
            assert _backfill_script().main(["--timeout", "0"]) == 0
        assert rc.server.inits == [], "the backfill does not send it again"


class TestKeptWhenTheCycleWouldSendLess:
    """A re-extraction never replaces Knovas's text with one that misses more
    OCR pages. A large scan the backfill completed (5000 pages / 1800 s)
    comes back partial under the cycle's budget (500 pages / 240 s): sent,
    it would be billed, the pages past the budget would leave the index and
    the search-context sidecar, and the next backfill would bill it again."""

    def test_a_complete_text_is_not_replaced_by_a_partial_one(self, rc, caplog):
        rc.write("akte.txt")
        rc.run(rc.body())
        sha = rc.extraction("akte.txt").text_sha256
        rc.upgrade()
        assert _requeue(rc) == 1
        sidecars: list = []
        rc.monkeypatch.setattr("sync.knovas_uploader.write_context_sidecar",
                               lambda *args, **kwargs: sidecars.append(args[1]))
        _cycle_budget_trips(rc, {"ocr_pages_skipped": 400, "ocr_pages": 500, "ocr_backend": "tesserocr"})
        caplog.clear()
        caplog.set_level(logging.INFO)
        result = rc.run(rc.body())
        assert rc.server.inits == [], "nothing sent: no billing"
        assert sidecars == [], "the sidecar keeps the text Knovas holds"
        assert (result.reextract_kept, result.reextract_uploaded, result.reextract_unchanged,
                result.files_uploaded, result.files_partial) == (1, 0, 0, 0, 0)
        assert result.transmissions == [] and result.errors == []
        assert "reextract uploaded=0 unchanged=0 kept=1 failed=0 reached=1" in caplog.text
        assert "akte" not in caplog.text
        state = rc.extraction("akte.txt")
        assert (state.stamp, state.text_sha256) == (current_extraction_stamp(), sha), \
            "the stamp moves on, the hash stays: Knovas keeps its text"
        store = rc.state()
        try:
            assert store.partial_paths() == [], "Knovas's text is complete: nothing to backfill"
        finally:
            store.close()
        assert _kept(rc) == 1 and rc.outdated() == 0
        assert _requeue(rc) == 0, "a later request does not re-read it for nothing"
        assert rc.run(rc.body()).reextract_reached == 0, "it left the queue"

    @pytest.mark.parametrize("stored, again, sent", [
        ({"ocr_pages_skipped": 1, "ocr_pages": 9}, {"ocr_pages_skipped": 3, "ocr_pages": 7}, False),
        ({"ocr_pages_failed": 1, "ocr_pages": 9, "backfill_unchanged": 1},
         {"ocr_pages_skipped": 2, "ocr_pages": 8}, False),
        ({"ocr_pages_skipped": 1, "ocr_pages": 9}, {"ocr_pages_failed": 1, "ocr_pages": 9}, True),
        ({"ocr_pages_skipped": 3, "ocr_pages": 7}, {"ocr_pages_skipped": 1, "ocr_pages": 9}, True),
        # Retries ran out: Knovas holds no text of this version to compare with.
        ({"reason": "extract_retries_exhausted"}, {"ocr_pages_skipped": 3, "ocr_pages": 7}, True),
    ], ids=["more-missing", "more-than-the-backfill-left", "as-many", "fewer", "retries-exhausted"])
    def test_a_partial_text_is_replaced_only_by_one_missing_no_more(self, rc, stored, again, sent):
        rc.write("scan.txt")
        rc.run(rc.body())
        store = rc.state()
        try:
            store.record_partial("scan.txt", FIXED_MTIME_ISO, len(TEXT), "tk-1", stored)
        finally:
            store.close()
        rc.upgrade()
        _requeue(rc)
        _cycle_budget_trips(rc, again)
        result = rc.run(rc.body())
        assert (len(rc.server.inits), result.reextract_uploaded, result.reextract_kept) == \
            ((1, 1, 0) if sent else (0, 0, 1))
        store = rc.state()
        try:
            # Kept: the note still says what Knovas holds, so the document
            # stays on the backfill list -- its larger budget brings it to
            # the new extraction.
            assert store.partial_note("scan.txt") == (again if sent else stored)
        finally:
            store.close()
        assert rc.outdated() == 0 and _kept(rc) == (0 if sent else 1)

    def test_the_next_extractor_queues_a_kept_document_again(self, rc):
        from sync import knovas_uploader

        real_extract = knovas_uploader.extract_document_guarded
        real_note = knovas_uploader.partial_note_for
        rc.write("akte.txt")
        rc.run(rc.body())
        rc.upgrade()
        _requeue(rc)
        _cycle_budget_trips(rc, {"ocr_pages_skipped": 400, "ocr_pages": 500})
        assert rc.run(rc.body()).reextract_kept == 1
        rc.monkeypatch.setattr("sync.extraction_stamp._knovas_extract_version", lambda: "100.0.0")
        rc.monkeypatch.setattr("sync.knovas_uploader.extract_document_guarded", real_extract)
        rc.monkeypatch.setattr("sync.knovas_uploader.partial_note_for", real_note)
        assert rc.outdated() == 1 and _requeue(rc) == 1
        result = rc.run(rc.body())
        assert result.reextract_unchanged == 1 and rc.server.inits == []
        assert _kept(rc) == 0 and rc.outdated() == 0

    def test_a_scan_the_backfill_completed_end_to_end(self, rc, monkeypatch):
        """The real PDF extraction with an OCR engine that reads each page
        as its number: the cycle OCRs 3 of 6 pages (its budget), the backfill
        script completes the scan, and after an extractor upgrade the
        re-extraction -- 3 pages again -- leaves Knovas's text alone."""
        import sync.document_text as document_text

        real_build = document_text.build_ocr_options
        monkeypatch.setattr(document_text, "build_ocr_options",
                            lambda options: real_build({**options, "backend": _PageEngine()}))
        monkeypatch.setenv("RC_OCR_CACHE_MAX_MB", "0")
        monkeypatch.setenv("RC_OCR_MAX_PAGES", "3")  # the cycle's 500, scaled down
        (rc.root / "akte.pdf").write_bytes(_scanned_pdf(6))
        assert rc.run(rc.body()).files_partial == 1
        assert not any("Seite 6" in snippet for snippet in rc.server.snippets)

        backfill = _backfill_script()
        rc.server.snippets.clear()
        with patch("sync.sync_scheduler.load_last_sync_body", return_value=rc.body()):
            assert backfill.main(["--timeout", "0", "--max-ocr-pages", "100"]) == 0
        assert any("Seite 6" in snippet for snippet in rc.server.snippets), "complete at Knovas"
        store = rc.state()
        try:
            assert store.partial_paths() == []
        finally:
            store.close()
        sha = rc.extraction("akte.pdf").text_sha256

        rc.upgrade()
        assert _requeue(rc) == 1
        result = rc.run(rc.body())
        assert rc.server.inits == [] and result.reextract_kept == 1
        assert rc.extraction("akte.pdf").text_sha256 == sha
        store = rc.state()
        try:
            assert store.partial_paths() == [], "nothing for the backfill to send (and bill) again"
        finally:
            store.close()

    def test_a_fields_re_send_is_not_held_back(self, rc):
        """A fields re-send exists to deliver values that only an upload
        carries, so it is sent even when it misses more pages; the partial
        note puts the document on the backfill list, which restores it."""
        rc.write("akte.txt")
        rc.run(rc.body())
        _cycle_budget_trips(rc, {"ocr_pages_skipped": 400, "ocr_pages": 500})
        result = rc.run(rc.body(fields={"doc_type": "court_file"}))
        assert rc.server.rels() == ["akte.txt"] and result.files_partial == 1
        assert _kept(rc) == 0


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
    assert "reextract uploaded=0 unchanged=1 kept=0 failed=0 reached=1" in caplog.text
    assert secret not in caplog.text and "Honorarnote" not in caplog.text
