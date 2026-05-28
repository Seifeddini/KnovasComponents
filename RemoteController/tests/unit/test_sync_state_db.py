import json

from sync.sync_state import SyncStateStore
from sync.sync_state_db import SyncStateDatabase, json_state_path_to_db, normalize_json_documents


def test_normalize_legacy_files():
    raw = {
        "files": {
            "a.md|2026-01-01T00:00:00Z|3": {
                "transmission_key_id": "k",
            }
        }
    }
    docs = normalize_json_documents(raw)
    assert "a.md" in docs
    assert docs["a.md"]["size_bytes"] == 3


def test_sqlite_migration_from_json(tmp_path):
    json_path = tmp_path / "state.json"
    json_path.write_text(
        json.dumps(
            {
                "documents": {
                    "x.md": {
                        "mtime_iso": "2026-01-01T00:00:00Z",
                        "size_bytes": 9,
                        "transmission_key_id": "tid",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    db_path = json_state_path_to_db(json_path)
    db = SyncStateDatabase(db_path, json_path=json_path)
    fps = db.load_fingerprints()
    db.close()
    assert fps["x.md"] == ("2026-01-01T00:00:00Z", 9)
    assert json_path.with_suffix(".json.migrated").exists() or not json_path.exists()


def test_load_fingerprints_bulk(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    monkeypatch.setenv("RC_SYNC_STATE_PATH", str(state_path))
    from config import load_config, reset_config

    reset_config()
    load_config(validate=False, force_reload=True)
    store = SyncStateStore(str(state_path))
    store.record_upload("one.md", "2026-01-01T00:00:00Z", 1, "k1")
    store.record_upload("two.md", "2026-01-02T00:00:00Z", 2, "k2")
    fps = store.load_fingerprints()
    assert len(fps) == 2
    assert store.document_status("one.md", "2026-01-01T00:00:00Z", 1, fingerprints=fps) == "synced"
