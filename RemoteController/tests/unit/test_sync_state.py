import json
from pathlib import Path

from sync.sync_state import SyncStateStore
from sync.sync_state_db import json_state_path_to_db


def _store_at(tmp_path, monkeypatch) -> tuple[SyncStateStore, Path]:
    state_path = tmp_path / "state.json"
    monkeypatch.setenv("RC_SYNC_STATE_PATH", str(state_path))
    from config import load_config, reset_config

    reset_config()
    load_config(validate=False, force_reload=True)
    return SyncStateStore(str(state_path)), state_path


def test_skip_unchanged(tmp_path, monkeypatch):
    store, _ = _store_at(tmp_path, monkeypatch)
    store.record_upload("a.md", "2026-01-01T00:00:00Z", 10, "key-1")
    assert store.document_status("a.md", "2026-01-01T00:00:00Z", 10) == "synced"
    assert store.should_skip("a.md", "2026-01-01T00:00:00Z", 10)
    assert store.document_status("a.md", "2026-01-02T00:00:00Z", 10) == "modified"
    assert not store.should_skip("a.md", "2026-01-02T00:00:00Z", 10)


def test_pending_never_uploaded(tmp_path, monkeypatch):
    store, _ = _store_at(tmp_path, monkeypatch)
    assert store.document_status("new.md", "2026-01-01T00:00:00Z", 5) == "pending"


def test_summarize_counts(tmp_path, monkeypatch):
    store, _ = _store_at(tmp_path, monkeypatch)
    store.record_upload("synced.md", "2026-01-01T00:00:00Z", 1, "k1")
    store.record_upload("changed.md", "2026-01-01T00:00:00Z", 2, "k2")
    summary = store.summarize(
        [
            ("synced.md", "2026-01-01T00:00:00Z", 1),
            ("changed.md", "2026-01-02T00:00:00Z", 2),
            ("pending.md", "2026-01-03T00:00:00Z", 3),
        ]
    )
    assert summary.total == 3
    assert summary.synced == 1
    assert summary.modified == 1
    assert summary.pending == 1


def test_migrate_legacy_files_key(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    monkeypatch.setenv("RC_SYNC_STATE_PATH", str(state_path))
    state_path.write_text(
        json.dumps(
            {
                "files": {
                    "legacy.md|2026-01-01T00:00:00Z|7": {
                        "last_uploaded_at": "2026-01-01T12:00:00Z",
                        "transmission_key_id": "legacy-key",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    from config import load_config, reset_config

    reset_config()
    load_config(validate=False, force_reload=True)
    store = SyncStateStore(str(state_path))
    assert store.document_status("legacy.md", "2026-01-01T00:00:00Z", 7) == "synced"
    assert store.document_status("legacy.md", "2026-01-02T00:00:00Z", 7) == "modified"
    db_path = json_state_path_to_db(state_path)
    assert db_path.exists()


def test_atomic_write(tmp_path, monkeypatch):
    store, state_path = _store_at(tmp_path, monkeypatch)
    store.record_upload("b.md", "2026-01-01T00:00:00Z", 5, "key-2")
    assert store.document_status("b.md", "2026-01-01T00:00:00Z", 5) == "synced"
    assert store.count_tracked_paths() == 1
    db_path = json_state_path_to_db(state_path)
    assert db_path.exists()
