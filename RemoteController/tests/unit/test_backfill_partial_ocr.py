"""scripts/backfill_partial_ocr.py: the nightly pass over partial documents."""
from __future__ import annotations

import importlib.util
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

    base = dict(dry_run=False, limit=0, timeout=1800, max_ocr_pages=5000, ocr_time_budget=1800, verbose=False)
    base.update(overrides)
    return argparse.Namespace(**base)


def test_env_for_budget_trip_and_retries_exhausted(backfill):
    args = _args(backfill)
    env = backfill._env_for({"ocr_pages_skipped": 12, "ocr_pages": 40}, args)
    assert env["RC_OCR_MAX_PAGES"] == "5000"
    assert env["RC_OCR_TIME_BUDGET_SECONDS"] == "1800"
    assert env["RC_EXTRACT_TIMEOUT_SECONDS"] == "1800"
    assert "RC_PDF_OCR_ENABLED" not in env
    exhausted = backfill._env_for({"reason": "extract_retries_exhausted"}, args)
    assert exhausted["RC_PDF_OCR_ENABLED"] == "false", "the hung page is skipped: text pages land"


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
