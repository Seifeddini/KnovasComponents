import json

from sync.sync_state import SyncStateStore


def _store_at(tmp_path, monkeypatch) -> tuple[SyncStateStore, object]:
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
    summary = store.summarize(
        [
            ("synced.md", "2026-01-01T00:00:00Z", 1),
            ("changed.md", "2026-01-02T00:00:00Z", 2),
            ("pending.md", "2026-01-03T00:00:00Z", 3),
        ]
    )
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


def test_atomic_write(tmp_path, monkeypatch):
    store, state_path = _store_at(tmp_path, monkeypatch)
    store.record_upload("b.md", "2026-01-01T00:00:00Z", 5, "key-2")
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert "documents" in data
    assert data["documents"]["b.md"]["transmission_key_id"] == "key-2"
