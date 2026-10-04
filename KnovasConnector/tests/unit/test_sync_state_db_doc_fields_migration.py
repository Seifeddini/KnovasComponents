"""The document-fields columns of the sync state DB (spec 3.7).

An existing state file gains the columns in place, every row keeps its
fingerprint, and nothing reads as "fields changed" afterwards (a NULL digest
equals ""). ``record_upload`` became an UPSERT: a write without a
``FieldsRecord`` leaves the fields columns alone, where ``INSERT OR REPLACE``
used to reset every column it did not name.
"""
from __future__ import annotations

import sqlite3

import pytest

from sync.doc_fields_payload import FieldsRecord, reupload_failed_record
from sync.sync_state import SyncStateStore
from sync.sync_state_db import REQUEUE_DIGEST, FieldsState, SyncStateDatabase

OLD_SCHEMA = """
CREATE TABLE documents (
    relative_path TEXT PRIMARY KEY NOT NULL,
    mtime_iso TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    last_uploaded_at TEXT,
    transmission_key_id TEXT
);
CREATE TABLE partial_documents (
    relative_path TEXT PRIMARY KEY NOT NULL,
    note_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE TABLE extract_retries (
    relative_path TEXT PRIMARY KEY NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    updated_at TEXT
);
"""
NEW_COLUMNS = {"fields_digest", "fields_sent", "fields_outcome", "fields_warning_codes", "fields_attempts"}
TS = "2026-10-01T00:00:00Z"


def _old_db(path):
    conn = sqlite3.connect(str(path))
    conn.executescript(OLD_SCHEMA)
    conn.execute(
        "INSERT INTO documents VALUES (?, ?, ?, ?, ?)",
        ("Mandate/a.pdf", TS, 10, TS, "tk-old"),
    )
    conn.commit()
    conn.close()


def _columns(path) -> set[str]:
    conn = sqlite3.connect(str(path))
    try:
        return {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
    finally:
        conn.close()


@pytest.fixture
def store(tmp_path):
    s = SyncStateStore(str(tmp_path / "state.json"))
    yield s
    s.close()


class TestMigration:
    def test_old_file_gains_the_columns_and_keeps_its_rows(self, tmp_path):
        db_path = tmp_path / "state.db"
        _old_db(db_path)
        db = SyncStateDatabase(db_path)
        try:
            assert db.load_fingerprints() == {"Mandate/a.pdf": (TS, 10)}
            assert db.fields_state("Mandate/a.pdf") == FieldsState(None, False, None, 0)
        finally:
            db.close()
        assert NEW_COLUMNS <= _columns(db_path)

    def test_opening_twice_and_side_by_side_is_harmless(self, tmp_path):
        db_path = tmp_path / "state.db"
        _old_db(db_path)
        first, second = SyncStateDatabase(db_path), SyncStateDatabase(db_path)
        try:
            first.load_fingerprints()
            second.load_fingerprints()
        finally:
            first.close()
            second.close()
        again = SyncStateDatabase(db_path)
        try:
            assert again.count_tracked() == 1
        finally:
            again.close()

    def test_a_fresh_file_has_the_columns(self, tmp_path):
        db = SyncStateDatabase(tmp_path / "fresh.db")
        try:
            db.count_tracked()
        finally:
            db.close()
        assert NEW_COLUMNS <= _columns(tmp_path / "fresh.db")

    def test_an_older_rc_writing_the_migrated_file_still_works(self, tmp_path):
        """Downgrade: the old INSERT OR REPLACE names only its own columns;
        the fields columns fall back to their defaults ("nothing known")."""
        db_path = tmp_path / "state.db"
        db = SyncStateDatabase(db_path)
        try:
            db.record_upload("a.pdf", TS, 1, "tk", fields=FieldsRecord("d1", "staged", sent=True))
        finally:
            db.close()
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT OR REPLACE INTO documents "
            "(relative_path, mtime_iso, size_bytes, last_uploaded_at, transmission_key_id) "
            "VALUES (?, ?, ?, ?, ?)",
            ("a.pdf", TS, 2, TS, "tk2"),
        )
        conn.commit()
        conn.close()
        db = SyncStateDatabase(db_path)
        try:
            assert db.fields_state("a.pdf") == FieldsState(None, False, None, 0)
        finally:
            db.close()


class TestUpsertKeepsFields:
    def test_record_without_fields_leaves_the_columns(self, store):
        store.record_upload("a.pdf", TS, 1, "tk1", fields=FieldsRecord("d1", "staged", sent=True,
                                                                       warning_codes=("ambiguous_date",)))
        store.record_upload("a.pdf", "2026-10-02T00:00:00Z", 2, "tk2")
        assert store.fields_state("a.pdf") == FieldsState("d1", True, "staged", 0)
        assert store.load_fingerprints()["a.pdf"] == ("2026-10-02T00:00:00Z", 2)

    def test_skip_and_partial_pass_the_record_through(self, store):
        store.record_skip("bad.pdf", TS, 1, reason="unconvertible", fields=FieldsRecord("d", "none"))
        store.record_partial("scan.pdf", TS, 1, "tk", {"ocr_pages_skipped": 1},
                             fields=FieldsRecord("e", "none"))
        assert store.fields_state("bad.pdf").digest == "d"
        assert store.fields_state("scan.pdf").digest == "e"
        store.record_skip("bad.pdf", TS, 1, reason="unconvertible")
        assert store.fields_state("bad.pdf").digest == "d"

    def test_none_digest_and_sent_keep_the_stored_values(self, store):
        store.record_upload("a.pdf", TS, 1, "tk", fields=FieldsRecord("d1", "staged", sent=True))
        store.record_upload("a.pdf", TS, 1, "tk", fields=FieldsRecord(None, "refused:doc_fields_unavailable",
                                                                      count_attempt=True))
        assert store.fields_state("a.pdf") == FieldsState("d1", True, "refused:doc_fields_unavailable", 1)
        store.record_upload("a.pdf", TS, 1, "tk", fields=FieldsRecord(None, "refused:doc_fields_unavailable",
                                                                      count_attempt=True))
        assert store.fields_state("a.pdf").attempts == 2
        store.record_upload("a.pdf", TS, 1, "tk", fields=FieldsRecord("d2", "cleared", sent=False))
        assert store.fields_state("a.pdf") == FieldsState("d2", False, "cleared", 0)

    def test_update_fields_touches_existing_rows_only(self, store):
        assert store.update_fields("missing.pdf", FieldsRecord("d", "none")) is None
        assert store.count_tracked_paths() == 0
        store.record_upload("a.pdf", TS, 1, "tk")
        assert store.update_fields("a.pdf", FieldsRecord("d", "none")) == 0
        assert store.load_fingerprints()["a.pdf"] == (TS, 1)
        assert store.increment_fields_attempts("a.pdf") == 1
        assert store.increment_fields_attempts("missing.pdf") == 0

    def test_warning_codes_are_stored_as_a_json_list(self, tmp_path):
        store = SyncStateStore(str(tmp_path / "state.json"))
        try:
            store.record_upload("a.pdf", TS, 1, "tk", fields=FieldsRecord(
                "d", "staged", sent=True, warning_codes=("ambiguous_date", "unresolved_entity")))
        finally:
            store.close()
        conn = sqlite3.connect(str(tmp_path / "state.db"))
        try:
            row = conn.execute("SELECT fields_warning_codes FROM documents").fetchone()
        finally:
            conn.close()
        assert row[0] == '["ambiguous_date", "unresolved_entity"]'


class TestRequeue:
    def _seed(self, store):
        store.record_upload("na-sent.pdf", TS, 1, "tk", fields=FieldsRecord("d", "staged", sent=True))
        store.update_fields("na-sent.pdf", FieldsRecord("d", "not_accepted"))
        store.record_upload("na.pdf", TS, 1, "tk", fields=FieldsRecord("d", "not_accepted"))
        store.record_upload("ref.pdf", TS, 1, "tk", fields=FieldsRecord("d", "refused:unknown_field"))
        store.record_upload("fail.pdf", TS, 1, "tk", fields=FieldsRecord("d", "staged", sent=True))
        store.update_fields("fail.pdf", reupload_failed_record("d", "init_403"))
        store.increment_fields_attempts("fail.pdf")
        store.record_upload("ok.pdf", TS, 1, "tk", fields=FieldsRecord("d", "staged", sent=True))

    def test_not_accepted_rows_lose_their_digest(self, store):
        self._seed(store)
        assert store.count_fields_requeue_candidates("not_accepted") == 2
        assert store.requeue_fields("not_accepted") == 2
        # staged before: a clear must be possible, so never NULL (NULL == "")
        assert store.fields_state("na-sent.pdf").digest == REQUEUE_DIGEST
        assert store.fields_state("na.pdf").digest is None
        assert store.fields_state("ok.pdf").digest == "d"
        assert store.requeue_fields("not_accepted") == 0, "already waiting: not counted twice"

    def test_refused_and_reupload_failed_and_all(self, store):
        self._seed(store)
        assert store.requeue_fields("refused") == 1
        assert store.fields_state("ref.pdf").digest is None
        assert store.requeue_fields("reupload_failed") == 1
        failed = store.fields_state("fail.pdf")
        assert failed.digest == REQUEUE_DIGEST and failed.attempts == 0
        assert store.requeue_fields("all") == 2  # the two not_accepted rows
        assert store.fields_state("ok.pdf").digest == "d"

    def test_unknown_selector_is_refused(self, store):
        with pytest.raises(ValueError):
            store.requeue_fields("staged")

    def test_counts_for_the_status(self, store):
        self._seed(store)
        counts = store.fields_counts()
        assert counts["with_fields"] == 3
        assert counts["not_accepted"] == 2
        assert counts["refused"] == 1
        assert counts["reupload_failed"] == 1
        assert counts["accepted"] == 1
        assert counts["requeued"] == 0
        store.requeue_fields("all")
        assert store.fields_counts()["requeued"] == 4

    def test_remove_and_reset_forget_the_columns(self, store):
        self._seed(store)
        store.remove_tracked("ok.pdf")
        assert store.fields_state("ok.pdf") is None
        store.reset_all()
        assert store.fields_counts()["with_fields"] == 0
