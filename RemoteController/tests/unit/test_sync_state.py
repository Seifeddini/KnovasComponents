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
    assert store.should_skip("a.md", "2026-01-01T00:00:00Z", 10)
    assert not store.should_skip("a.md", "2026-01-02T00:00:00Z", 10)


def test_atomic_write(tmp_path, monkeypatch):
    store, state_path = _store_at(tmp_path, monkeypatch)
    store.record_upload("b.md", "2026-01-01T00:00:00Z", 5, "key-2")
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert "files" in data
