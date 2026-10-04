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


def test_record_skip_marks_synced(tmp_path, monkeypatch):
    store, _ = _store_at(tmp_path, monkeypatch)
    store.record_skip("scan.pdf", "2026-01-01T00:00:00Z", 100, reason="unconvertible")
    assert store.document_status("scan.pdf", "2026-01-01T00:00:00Z", 100) == "synced"
    assert store.should_skip("scan.pdf", "2026-01-01T00:00:00Z", 100)


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


# --- partial documents and extraction retries (GI-EXTRACT-02) ----------------


def test_record_partial_counts_as_synced_but_stays_listed(tmp_path, monkeypatch):
    store, _ = _store_at(tmp_path, monkeypatch)
    note = {"ocr_pages_skipped": 12, "ocr_pages": 40}
    store.record_partial("scan.pdf", "2026-01-01T00:00:00Z", 100, "tk-1", note)
    assert store.status_for("scan.pdf", "2026-01-01T00:00:00Z", 100) == "synced"
    assert store.status_for("scan.pdf", "2026-01-02T00:00:00Z", 100) == "modified"
    assert store.partial_paths() == ["scan.pdf"]
    assert store.partial_note("scan.pdf") == note
    assert store.count_partial_paths() == 1
    # a clean upload clears the note
    store.record_upload("scan.pdf", "2026-01-02T00:00:00Z", 100, "tk-2")
    assert store.partial_paths() == []
    assert store.partial_note("scan.pdf") is None


def test_partial_note_survives_reopen(tmp_path, monkeypatch):
    store, state_path = _store_at(tmp_path, monkeypatch)
    store.record_partial("a.pdf", "2026-01-01T00:00:00Z", 1, "tk", {"reason": "extract_retries_exhausted"})
    store.close()
    again = SyncStateStore(str(state_path))
    assert again.partial_note("a.pdf") == {"reason": "extract_retries_exhausted"}
    again.close()


def test_retry_counter_increments_and_clears(tmp_path, monkeypatch):
    store, _ = _store_at(tmp_path, monkeypatch)
    assert store.retry_count("x.pdf") == 0
    assert store.increment_retry_count("x.pdf", error="extractor died (exit -9)") == 1
    assert store.increment_retry_count("x.pdf") == 2
    assert store.retry_count("x.pdf") == 2
    assert store.status_for("x.pdf", "2026-01-01T00:00:00Z", 1) == "pending", "a retry records no fingerprint"
    store.record_upload("x.pdf", "2026-01-01T00:00:00Z", 1, "tk")
    assert store.retry_count("x.pdf") == 0
    store.increment_retry_count("y.pdf")
    store.record_partial("y.pdf", "2026-01-01T00:00:00Z", 1, "partial", {"reason": "extract_retries_exhausted"})
    assert store.retry_count("y.pdf") == 0, "recording partial resets the counter"


def test_remove_tracked_clears_partial_note_retries_and_ocr_cache(tmp_path, monkeypatch):
    from sync.ocr_cache import OcrDiskCache

    store, state_path = _store_at(tmp_path, monkeypatch)
    monkeypatch.setenv("RC_OCR_CACHE_MAX_MB", "8")
    cache = OcrDiskCache.beside_state_path(state_path)
    cache.for_document("gone.pdf").put("k1", "ocr text")
    cache.for_document("stay.pdf").put("k2", "other text")
    cache.close()
    store.record_partial("gone.pdf", "2026-01-01T00:00:00Z", 1, "tk", {"ocr_pages_skipped": 1})
    store.increment_retry_count("gone.pdf")
    store.remove_tracked("gone.pdf")
    assert store.partial_paths() == []
    assert store.retry_count("gone.pdf") == 0
    probe = OcrDiskCache.beside_state_path(state_path)
    assert probe.get("k1") is None, "the removed document's OCR entries are purged"
    assert probe.get("k2") == "other text"
    probe.close()


def test_reset_all_drops_everything_and_the_cache_file(tmp_path, monkeypatch):
    from sync.ocr_cache import OcrDiskCache

    store, state_path = _store_at(tmp_path, monkeypatch)
    monkeypatch.setenv("RC_OCR_CACHE_MAX_MB", "8")
    cache = OcrDiskCache.beside_state_path(state_path)
    cache.for_document("a.pdf").put("k", "v")
    cache.close()
    assert cache.path.exists()
    store.record_upload("a.pdf", "2026-01-01T00:00:00Z", 1, "tk")
    store.record_partial("b.pdf", "2026-01-01T00:00:00Z", 1, "tk", {"ocr_pages_skipped": 1})
    store.reset_all()
    assert store.count_tracked_paths() == 0 and store.partial_paths() == []
    assert not cache.path.exists()
