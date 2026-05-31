from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from sync.sync_config import effective_filters
from sync.sync_executor import (
    _collect_files,
    _iter_candidate_files,
    _should_skip_failed_upload,
    is_within_max_document_age,
    plan_sync_cycle,
    run_sync_work,
    scan_document_inventory,
)
from sync.knovas_uploader import UploadResult
from sync.sync_state import SyncStateStore


NOW = datetime(2026, 5, 23, 12, 0, 0, tzinfo=timezone.utc)


def _mtime_iso(days_ago: int) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


def test_is_within_max_document_age():
    assert is_within_max_document_age(_mtime_iso(1), 86400 * 30, now=NOW)
    assert not is_within_max_document_age(_mtime_iso(40), 86400 * 30, now=NOW)


def test_effective_filters_body_overrides_scheduler():
    body = {"filters": {"max_document_age_seconds": 100}}
    cfg = {"max_document_age_seconds": 9999}
    assert effective_filters(body, cfg)["max_document_age_seconds"] == 100


def test_effective_filters_scheduler_default():
    body = {"filters": {}}
    cfg = {"max_document_age_seconds": 9999}
    assert effective_filters(body, cfg)["max_document_age_seconds"] == 9999


def test_scan_excluded_max_age(tmp_watch_root):
    root = tmp_watch_root
    old = root / "old.md"
    old.write_text("old", encoding="utf-8")
    old_mtime = (NOW - timedelta(days=60)).timestamp()
    import os

    os.utime(old, (old_mtime, old_mtime))

    (root / "fresh.md").write_text("new", encoding="utf-8")

    body = {
        "mode": "incremental",
        "sources": [{"path": str(root), "recursive": True}],
        "filters": {"max_document_age_seconds": 86400 * 30},
        "ingestion": {"identifier_prefix": "rc"},
    }
    summary = scan_document_inventory(body, now=NOW)
    assert summary.total == 3
    assert summary.excluded_max_age == 1
    assert summary.pending >= 1


def test_scan_synced_old_file_stays_synced(tmp_watch_root, tmp_path, monkeypatch):
    root = tmp_watch_root
    old = root / "legacy.md"
    old.write_text("legacy", encoding="utf-8")
    old_mtime = (NOW - timedelta(days=90)).timestamp()
    import os

    os.utime(old, (old_mtime, old_mtime))
    stat = old.stat()
    mtime_iso = (
        datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )

    state_path = tmp_path / "state.json"
    monkeypatch.setenv("RC_SYNC_STATE_PATH", str(state_path))
    from config import load_config, reset_config

    reset_config()
    load_config(validate=False, force_reload=True)

    store = SyncStateStore(str(state_path))
    store.record_upload("legacy.md", mtime_iso, stat.st_size, "key-1")

    body = {
        "mode": "incremental",
        "sources": [{"path": str(root), "recursive": True}],
        "filters": {"max_document_age_seconds": 86400 * 30},
        "ingestion": {"identifier_prefix": "rc"},
    }
    summary = scan_document_inventory(body, now=NOW)
    assert summary.synced >= 1
    assert summary.excluded_max_age == 0


def test_collect_files_skips_excluded_max_age(tmp_watch_root):
    root = tmp_watch_root
    old = root / "old.md"
    old.write_text("old", encoding="utf-8")
    old_mtime = (NOW - timedelta(days=60)).timestamp()
    import os

    os.utime(old, (old_mtime, old_mtime))

    body = {
        "mode": "incremental",
        "sources": [{"path": str(root), "recursive": True}],
        "filters": {"max_document_age_seconds": 86400 * 30, "include_globs": ["old.md"]},
        "ingestion": {"identifier_prefix": "rc"},
    }
    files = _collect_files(body, should_stop=lambda: False, now=NOW)
    assert files == []


def test_plan_sync_cycle_single_walk(tmp_watch_root, tmp_path, monkeypatch):
    root = tmp_watch_root
    (root / "a.md").write_text("a", encoding="utf-8")
    body = {
        "mode": "incremental",
        "sources": [{"path": str(root), "recursive": True}],
        "ingestion": {"identifier_prefix": "rc"},
    }
    walk_calls = {"n": 0}
    original = _iter_candidate_files

    def counting_iter(*args, **kwargs):
        walk_calls["n"] += 1
        yield from original(*args, **kwargs)

    monkeypatch.setattr("sync.sync_executor._iter_candidate_files", counting_iter)
    state_path = tmp_path / "state.json"
    monkeypatch.setenv("RC_SYNC_STATE_PATH", str(state_path))
    from config import load_config, reset_config

    reset_config()
    load_config(validate=False, force_reload=True)
    from sync.sync_state import SyncStateStore

    store = SyncStateStore(str(state_path))
    try:
        plan_sync_cycle(body, store, now=NOW)
    finally:
        store.close()
    assert walk_calls["n"] == 1


def test_scan_uses_scheduler_default_when_body_omits_age(tmp_watch_root):
    root = tmp_watch_root
    old = root / "stale.md"
    old.write_text("old", encoding="utf-8")
    old_mtime = (NOW - timedelta(days=60)).timestamp()
    import os

    os.utime(old, (old_mtime, old_mtime))

    body = {
        "mode": "incremental",
        "sources": [{"path": str(root), "recursive": True}],
        "filters": {"include_globs": ["stale.md"]},
        "ingestion": {"identifier_prefix": "rc"},
    }
    sync_cfg = {"max_document_age_seconds": 86400 * 30}
    summary = scan_document_inventory(body, sync_config=sync_cfg, now=NOW)
    assert summary.excluded_max_age == 1


def test_should_skip_failed_upload():
    assert _should_skip_failed_upload(
        UploadResult("a.docx", None, 0, "error", 0, error="File is not a zip file"),
        "incremental",
    )
    assert not _should_skip_failed_upload(
        UploadResult("a.pdf", "key", 2, "error", 3, error="part 1 failed: 503"),
        "incremental",
    )


def test_run_sync_work_skips_bad_docx_without_crashing(tmp_watch_root, tmp_path, monkeypatch):
    root = tmp_watch_root
    (root / "good.md").write_text("ok", encoding="utf-8")
    (root / "bad.docx").write_bytes(b"not-a-docx")

    state_path = tmp_path / "state.json"
    monkeypatch.setenv("RC_SYNC_STATE_PATH", str(state_path))
    from config import load_config, reset_config

    reset_config()
    load_config(validate=False, force_reload=True)

    body = {
        "mode": "incremental",
        "sources": [{"path": str(root), "recursive": True}],
        "filters": {"include_globs": ["good.md", "bad.docx"]},
        "ingestion": {"identifier_prefix": "rc"},
    }

    from sync.knovas_uploader import SemantixUploader

    uploader = SemantixUploader()
    with patch.object(uploader, "_request") as req:
        ok = MagicMock()
        ok.status_code = 200
        ok.content = b'{"key": "tx-1"}'
        ok.json.return_value = {"key": "tx-1"}
        req.return_value = ok
        result = run_sync_work(body, uploader)

    assert result.files_uploaded == 1
    assert len(result.errors) == 1
    assert result.errors[0]["path"] == "bad.docx"

    store = SyncStateStore(str(state_path))
    try:
        bad = root / "bad.docx"
        stat = bad.stat()
        mtime_iso = (
            datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        assert store.should_skip("bad.docx", mtime_iso, stat.st_size)
    finally:
        store.close()
