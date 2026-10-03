"""scripts/backfill_partial_ocr.py: the nightly pass over partial documents."""
from __future__ import annotations

import importlib.util
import logging
import os
import shutil
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from sync.knovas_uploader import UploadResult
from sync.sync_state import SyncStateStore

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "backfill_partial_ocr.py"


@pytest.fixture
def backfill():
    spec = importlib.util.spec_from_file_location("backfill_partial_ocr", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _args(backfill, **overrides):
    import argparse

    base = dict(dry_run=False, limit=0, timeout=1800, max_ocr_pages=5000, ocr_time_budget=1800,
                ocr_page_timeout=backfill.DEFAULT_OCR_PAGE_TIMEOUT_SECONDS, retry_unchanged=False, verbose=False)
    base.update(overrides)
    return argparse.Namespace(**base)


def test_env_for_budget_trip_and_retries_exhausted(backfill):
    args = _args(backfill)
    env = backfill._env_for({"ocr_pages_skipped": 12, "ocr_pages": 40}, args)
    assert env["RC_OCR_MAX_PAGES"] == "5000"
    assert env["RC_OCR_TIME_BUDGET_SECONDS"] == "1800"
    assert env["RC_EXTRACT_TIMEOUT_SECONDS"] == "1800"
    assert "RC_PDF_OCR_ENABLED" not in env
    assert "RC_OCR_PAGE_TIMEOUT_SECONDS" not in env, "no page failed: the configured page timeout"
    exhausted = backfill._env_for({"reason": "extract_retries_exhausted"}, args)
    assert exhausted["RC_PDF_OCR_ENABLED"] == "false", "the hung page is skipped: text pages land"


#: A note spec E1 records for a page that failed OCR (it raised, or ran past
#: RC_OCR_PAGE_TIMEOUT_SECONDS): an empty page.
FAILED_NOTE = {"ocr_pages_skipped": 0, "ocr_pages_failed": 1, "ocr_pages": 9, "text_pages": 2, "ocr_backend": "cli"}


def test_env_for_failed_pages_gives_them_a_longer_page_timeout(backfill):
    """A page that ran past the page timeout fails again under the same one."""
    from sync.document_text import DEFAULT_OCR_PAGE_TIMEOUT_SECONDS as cycle_page_timeout

    assert backfill.DEFAULT_OCR_PAGE_TIMEOUT_SECONDS > cycle_page_timeout
    env = backfill._env_for(FAILED_NOTE, _args(backfill))
    assert env["RC_OCR_PAGE_TIMEOUT_SECONDS"] == str(backfill.DEFAULT_OCR_PAGE_TIMEOUT_SECONDS)
    longer = backfill._env_for(FAILED_NOTE, _args(backfill, ocr_page_timeout=900))
    assert longer["RC_OCR_PAGE_TIMEOUT_SECONDS"] == "900"
    assert longer["RC_OCR_MAX_PAGES"] == "5000" and "RC_PDF_OCR_ENABLED" not in longer


def test_the_longer_page_timeout_leaves_the_attempt_its_ocr_budget(backfill, monkeypatch):
    """The child caps the OCR budget below the ceiling, keeping room for the
    pages still running when it trips: one page timeout per OCR worker
    before spec E2, one page timeout with it. Even with the most workers
    (8) the default must not starve the attempt -- a starved one would
    upload less text than the cycle did."""
    from sync.document_text import extract_timeout_seconds, ocr_options_kwargs

    for key, value in backfill._env_for(FAILED_NOTE, _args(backfill)).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("RC_OCR_WORKERS", "8")
    monkeypatch.delenv("RC_EXTRACT_TIMEOUT_PER_PAGE_SECONDS", raising=False)
    budget = ocr_options_kwargs(extract_timeout_seconds(40))["time_budget_seconds"]
    assert budget >= 600, f"ten minutes of OCR at least, got {budget} s"


@pytest.mark.parametrize("before, after, unchanged", [
    (FAILED_NOTE, dict(FAILED_NOTE), True),                         # the same page failed again
    (FAILED_NOTE, None, False),                                     # complete now
    ({**FAILED_NOTE, "ocr_pages_failed": 3}, FAILED_NOTE, False),   # fewer pages missing
    ({"ocr_pages_skipped": 12, "ocr_pages": 40},
     {"ocr_pages_skipped": 0, "ocr_pages_failed": 12, "ocr_pages": 40}, True),  # moved, not fewer
    ({"ocr_pages_skipped": 2, "ocr_pages": 3}, {**FAILED_NOTE, "ocr_pages_failed": 4}, True),  # worse
    ({"reason": "extract_retries_exhausted"}, {"ocr_pages_skipped": 4}, False),  # no counts before
])
def test_unchanged_means_still_partial_with_no_fewer_pages_missing(backfill, before, after, unchanged):
    assert backfill._unchanged(before, after) is unchanged


def _setup(tmp_path, monkeypatch):
    root = tmp_path / "share"
    (root / "Mandant").mkdir(parents=True)
    (root / "Mandant" / "scan.pdf").write_bytes(b"%PDF-1.4 stub")
    state_path = tmp_path / "state" / "state.json"
    state_path.parent.mkdir()
    monkeypatch.setenv("RC_WATCH_ROOTS", str(root))
    monkeypatch.setenv("RC_SYNC_STATE_PATH", str(state_path))
    monkeypatch.delenv("M365_FOLDER_URL", raising=False)
    from config import load_config, reset_config

    reset_config()
    load_config(validate=False, force_reload=True)
    state = SyncStateStore(str(state_path))
    state.record_partial("Mandant/scan.pdf", "2026-01-01T00:00:00Z", 13, "tk-1", {"ocr_pages_skipped": 2, "ocr_pages": 3})
    state.record_partial("Mandant/gone.pdf", "2026-01-01T00:00:00Z", 1, "tk-2", {"reason": "extract_retries_exhausted"})
    state.close()
    return root, state_path


def test_dry_run_uploads_nothing_and_keeps_the_notes(tmp_path, monkeypatch, backfill):
    root, state_path = _setup(tmp_path, monkeypatch)
    body = {"mode": "incremental", "sources": [{"path": str(root), "recursive": True}],
            "filters": {}, "ingestion": {"identifier_prefix": "rc"}}
    with patch("sync.sync_scheduler.load_last_sync_body", return_value=body), patch(
        "sync.knovas_uploader.SemantixUploader.upload_file"
    ) as upload:
        assert backfill.main(["--dry-run"]) == 0
    upload.assert_not_called()
    state = SyncStateStore(str(state_path))
    try:
        assert state.partial_paths() == ["Mandant/gone.pdf", "Mandant/scan.pdf"]
    finally:
        state.close()


def test_backfill_reuploads_and_clears_the_note_on_success(tmp_path, monkeypatch, backfill):
    root, state_path = _setup(tmp_path, monkeypatch)
    body = {"mode": "incremental", "sources": [{"path": str(root), "recursive": True, "access_groups": ["g1"]}],
            "filters": {}, "ingestion": {"identifier_prefix": "rc"}}
    seen: dict = {}

    def fake_upload(self, local_path, rel, sync_body, access_groups=()):
        import os

        seen.update(rel=rel, groups=access_groups, max_pages=os.environ.get("RC_OCR_MAX_PAGES"),
                    ocr=os.environ.get("RC_PDF_OCR_ENABLED"))
        return UploadResult(rel, "tk-new", 2, "ok", 3)

    with patch("sync.sync_scheduler.load_last_sync_body", return_value=body), patch(
        "sync.knovas_uploader.SemantixUploader.__init__", return_value=None
    ), patch("sync.knovas_uploader.SemantixUploader.upload_file", fake_upload):
        assert backfill.main([]) == 0
    assert seen["rel"] == "Mandant/scan.pdf" and seen["groups"] == ("g1",)
    assert seen["max_pages"] == "5000" and seen["ocr"] is None
    state = SyncStateStore(str(state_path))
    try:
        # scan.pdf is complete now; gone.pdf is no longer on the share and is left for the prune
        assert state.partial_paths() == ["Mandant/gone.pdf"]
        stat = (root / "Mandant" / "scan.pdf").stat()
        mtime_iso = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        assert state.status_for("Mandant/scan.pdf", mtime_iso, stat.st_size) == "synced"
    finally:
        state.close()


def test_backfill_failure_leaves_the_note(tmp_path, monkeypatch, backfill):
    root, state_path = _setup(tmp_path, monkeypatch)
    body = {"mode": "incremental", "sources": [{"path": str(root), "recursive": True}],
            "filters": {}, "ingestion": {"identifier_prefix": "rc"}}

    def fake_upload(self, local_path, rel, sync_body, access_groups=()):
        return UploadResult(rel, None, 0, "error", 0, error="extraction timeout after 1800s (child killed)")

    with patch("sync.sync_scheduler.load_last_sync_body", return_value=body), patch(
        "sync.knovas_uploader.SemantixUploader.__init__", return_value=None
    ), patch("sync.knovas_uploader.SemantixUploader.upload_file", fake_upload):
        assert backfill.main(["--limit", "5"]) == 3
    state = SyncStateStore(str(state_path))
    try:
        assert "Mandant/scan.pdf" in state.partial_paths()
        assert state.partial_note("Mandant/scan.pdf") == {"ocr_pages_skipped": 2, "ocr_pages": 3}
    finally:
        state.close()


def _body(root):
    return {"mode": "incremental", "sources": [{"path": str(root), "recursive": True}],
            "filters": {}, "ingestion": {"identifier_prefix": "rc"}}


def _uploading(body, fake_upload) -> ExitStack:
    stack = ExitStack()
    stack.enter_context(patch("sync.sync_scheduler.load_last_sync_body", return_value=body))
    stack.enter_context(patch("sync.knovas_uploader.SemantixUploader.__init__", return_value=None))
    stack.enter_context(patch("sync.knovas_uploader.SemantixUploader.upload_file", fake_upload))
    return stack


def test_a_document_the_backfill_cannot_complete_is_sent_once(tmp_path, monkeypatch, backfill, caplog):
    """A page that fails every time -- it raises, or runs past even the
    longer page timeout -- failed again on every run, and every run uploaded
    the document again, billed for the same text. One attempt that leaves it
    unchanged is noted; later runs skip it until --retry-unchanged."""
    root, state_path = _setup(tmp_path, monkeypatch)
    state = SyncStateStore(str(state_path))
    state.record_partial("Mandant/scan.pdf", "2026-01-01T00:00:00Z", 13, "tk-1", dict(FAILED_NOTE))
    state.close()
    sent: list = []

    def fake_upload(self, local_path, rel, sync_body, access_groups=()):
        import os

        sent.append((rel, os.environ.get("RC_OCR_PAGE_TIMEOUT_SECONDS")))
        return UploadResult(rel, "tk-new", 2, "ok", 3, partial=dict(FAILED_NOTE))

    with _uploading(_body(root), fake_upload):
        for _ in range(3):
            assert backfill.main([]) == 0
        assert sent == [("Mandant/scan.pdf", str(backfill.DEFAULT_OCR_PAGE_TIMEOUT_SECONDS))], \
            "one billed attempt, with the longer page timeout"
        state = SyncStateStore(str(state_path))
        try:
            assert "Mandant/scan.pdf" in state.partial_paths(), "still partial: the note stays"
            assert state.partial_note("Mandant/scan.pdf") == {**FAILED_NOTE, "backfill_unchanged": 1}
        finally:
            state.close()
        with caplog.at_level(logging.INFO, logger="backfill_partial_ocr"):
            assert backfill.main(["--dry-run"]) == 0
        assert "unchanged=1" in caplog.text
        assert backfill.main(["--retry-unchanged"]) == 0
    assert len(sent) == 2, "--retry-unchanged sends it again"
    state = SyncStateStore(str(state_path))
    try:
        assert state.partial_note("Mandant/scan.pdf")["backfill_unchanged"] == 2
    finally:
        state.close()


def test_fewer_pages_missing_is_progress_and_is_tried_again(tmp_path, monkeypatch, backfill):
    root, state_path = _setup(tmp_path, monkeypatch)
    state = SyncStateStore(str(state_path))
    state.record_partial(
        "Mandant/scan.pdf", "2026-01-01T00:00:00Z", 13, "tk-1", {**FAILED_NOTE, "ocr_pages_failed": 3}
    )
    state.close()
    results = iter([{**FAILED_NOTE, "ocr_pages_failed": 2}, {**FAILED_NOTE, "ocr_pages_failed": 2}])
    sent: list = []

    def fake_upload(self, local_path, rel, sync_body, access_groups=()):
        sent.append(rel)
        return UploadResult(rel, "tk-new", 2, "ok", 3, partial=next(results))

    with _uploading(_body(root), fake_upload):
        for _ in range(3):
            assert backfill.main([]) == 0
    assert len(sent) == 2, "3 -> 2 failed pages is progress, 2 -> 2 is not"
    state = SyncStateStore(str(state_path))
    try:
        assert state.partial_note("Mandant/scan.pdf") == {
            **FAILED_NOTE, "ocr_pages_failed": 2, "backfill_unchanged": 1,
        }
    finally:
        state.close()


def test_a_document_landed_with_ocr_off_records_the_connectors_stamp(tmp_path, monkeypatch, backfill):
    """Exhausted retries are backfilled with OCR off, and the stamp covers
    the OCR switch. That one-off override is no new extraction: the row
    records the stamp of the Connector's own settings. With the override's
    it would count as outdated, and every request would queue it to be read
    with OCR on again -- into the page that hung, at the full ceiling."""
    from sync.extraction_stamp import current_extraction_stamp

    root, state_path = _setup(tmp_path, monkeypatch)
    (root / "Mandant" / "gone.pdf").write_bytes(b"%PDF-1.4 stub")
    monkeypatch.delenv("RC_PDF_OCR_ENABLED", raising=False)
    connector_stamp = current_extraction_stamp()
    seen: dict = {}

    def fake_upload(self, local_path, rel, sync_body, access_groups=()):
        import os

        # As the uploader does: the stamp of the settings in force meanwhile.
        seen[rel] = os.environ.get("RC_PDF_OCR_ENABLED")
        return UploadResult(rel, "tk-new", 1, "ok", 2, text_sha256="a" * 64,
                            extraction_stamp=current_extraction_stamp())

    with _uploading(_body(root), fake_upload):
        assert backfill.main([]) == 0
    assert seen == {"Mandant/gone.pdf": "false", "Mandant/scan.pdf": None}
    state = SyncStateStore(str(state_path))
    try:
        assert state.extraction_state("Mandant/gone.pdf").stamp == connector_stamp
        assert state.count_extraction_outdated(connector_stamp) == 0
        assert state.requeue_reextract(connector_stamp) == 0, "not queued into the hung page"
    finally:
        state.close()


def _link_file(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("creating a symbolic link needs a privilege here")


def _link_folder(link: Path, target: Path) -> None:
    """A symbolic link to a folder; on Windows without the right to create
    one, a junction, which must not be followed either."""
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name != "nt":
            pytest.skip("no symbolic links here")
        import _winapi

        _winapi.CreateJunction(str(target), str(link))


def _nothing_uploaded(root, state_path, backfill) -> None:
    uploaded: list = []

    def fake_upload(self, local_path, rel, sync_body, access_groups=()):
        uploaded.append(rel)
        return UploadResult(rel, "tk-new", 1, "ok", 2)

    with _uploading(_body(root), fake_upload):
        assert backfill.main([]) == 0
    assert uploaded == [], "nothing is read through a link"
    state = SyncStateStore(str(state_path))
    try:
        assert state.partial_note("Mandant/scan.pdf") == {"ocr_pages_skipped": 2, "ocr_pages": 3}, \
            "the scan does not see it either: left for the prune"
    finally:
        state.close()


def test_a_link_in_place_of_a_partial_file_is_not_read(tmp_path, monkeypatch, backfill):
    """The scan never follows a symbolic link, and neither does the backfill:
    a partial document replaced by a link to any file the Connector can read
    must not be uploaded under its identifier and access groups."""
    root, state_path = _setup(tmp_path, monkeypatch)
    secret = tmp_path / "secret" / "client-key.pem"
    secret.parent.mkdir()
    secret.write_text("-----BEGIN PRIVATE KEY-----", encoding="utf-8")
    (root / "Mandant" / "scan.pdf").unlink()
    _link_file(root / "Mandant" / "scan.pdf", secret)
    _nothing_uploaded(root, state_path, backfill)


@pytest.mark.parametrize("within", [False, True], ids=["out-of-the-source", "within-the-source"])
def test_a_folder_link_on_the_way_is_not_followed(tmp_path, monkeypatch, backfill, within):
    """Out of the source: any folder the Connector can read. Within it:
    another client's folder, whose file Knovas's folder rule would show
    under this path. The scan descends into neither."""
    root, state_path = _setup(tmp_path, monkeypatch)
    target = (root / "Andere") if within else (tmp_path / "elsewhere")
    target.mkdir()
    (target / "scan.pdf").write_bytes(b"%PDF-1.4 not this document")
    shutil.rmtree(root / "Mandant")
    _link_folder(root / "Mandant", target)
    _nothing_uploaded(root, state_path, backfill)


def test_documents_left_unchanged_do_not_use_up_the_limit(tmp_path, monkeypatch, backfill):
    root, state_path = _setup(tmp_path, monkeypatch)
    (root / "Mandant" / "alt.pdf").write_bytes(b"%PDF-1.4 stub")
    state = SyncStateStore(str(state_path))
    state.record_partial(
        "Mandant/alt.pdf", "2026-01-01T00:00:00Z", 13, "tk-3", {**FAILED_NOTE, "backfill_unchanged": 1}
    )
    state.close()
    sent: list = []

    def fake_upload(self, local_path, rel, sync_body, access_groups=()):
        sent.append(rel)
        return UploadResult(rel, "tk-new", 2, "ok", 3)

    with _uploading(_body(root), fake_upload):
        assert backfill.main(["--limit", "2"]) == 0
    assert sent == ["Mandant/scan.pdf"], "alt.pdf waits for --retry-unchanged; gone.pdf is missing"


def test_old_backend_none_notes_are_cleared_without_an_upload(tmp_path, monkeypatch, backfill):
    """Spec E1: the rule before it recorded every born-digital PDF partial
    with {"reason": "ocr_backend_none"} (knovas-extract 0.4 says backend
    "none" when no page needed OCR). Their text is complete at Knovas: the
    note goes, nothing is uploaded -- each upload is billed."""
    root, state_path = _setup(tmp_path, monkeypatch)
    (root / "Mandant" / "digital.pdf").write_bytes(b"%PDF-1.4 stub")
    state = SyncStateStore(str(state_path))
    state.record_partial(
        "Mandant/digital.pdf", "2026-01-01T00:00:00Z", 13, "tk-3",
        {"reason": "ocr_backend_none", "ocr_pages": 0, "ocr_backend": "none", "text_pages": 4},
    )
    state.close()
    body = {"mode": "incremental", "sources": [{"path": str(root), "recursive": True}],
            "filters": {}, "ingestion": {"identifier_prefix": "rc"}}
    uploaded: list = []

    def fake_upload(self, local_path, rel, sync_body, access_groups=()):
        uploaded.append(rel)
        return UploadResult(rel, "tk-new", 2, "ok", 3)

    with patch("sync.sync_scheduler.load_last_sync_body", return_value=body), patch(
        "sync.knovas_uploader.SemantixUploader.__init__", return_value=None
    ), patch("sync.knovas_uploader.SemantixUploader.upload_file", fake_upload):
        assert backfill.main(["--dry-run"]) == 0
        state = SyncStateStore(str(state_path))
        try:
            assert "Mandant/digital.pdf" in state.partial_paths(), "a dry run changes nothing"
        finally:
            state.close()
        assert backfill.main([]) == 0
    assert uploaded == ["Mandant/scan.pdf"], "the born-digital PDF is not re-sent"
    state = SyncStateStore(str(state_path))
    try:
        assert state.partial_paths() == ["Mandant/gone.pdf"]
        assert state.status_for("Mandant/digital.pdf", "2026-01-01T00:00:00Z", 13) == "synced", \
            "its fingerprint stays: the next cycle does not upload it either"
    finally:
        state.close()
