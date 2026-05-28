from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from sync.sync_config import effective_filters
from sync.sync_executor import (
    _collect_files,
    _iter_candidate_files,
    is_within_max_document_age,
    plan_sync_cycle,
    scan_document_inventory,
)
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
