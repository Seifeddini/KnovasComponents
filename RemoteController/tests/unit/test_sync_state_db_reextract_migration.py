"""The re-extraction columns of the sync state DB (spec L6).

An existing state file gains ``extraction_stamp``, ``text_sha256``,
``resend_reason`` and ``resend_attempts`` in place. Every row keeps its
fingerprint and its fields columns, and every row synced before counts as
produced by an older extraction (stamp NULL) -- nothing is queued by the
upgrade itself.
"""
from __future__ import annotations

import sqlite3

import pytest

from sync.doc_fields_payload import FieldsRecord
from sync.sync_state import SyncStateStore
from sync.sync_state_db import RESEND_REEXTRACT, ExtractionState, FieldsState, SyncStateDatabase

TABLES = """
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
PRE_FIELDS = """
CREATE TABLE documents (
    relative_path TEXT PRIMARY KEY NOT NULL,
    mtime_iso TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    last_uploaded_at TEXT,
    transmission_key_id TEXT
);
""" + TABLES
DOC_FIELDS = """
CREATE TABLE documents (
    relative_path TEXT PRIMARY KEY NOT NULL,
    mtime_iso TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    last_uploaded_at TEXT,
    transmission_key_id TEXT,
    fields_digest TEXT,
    fields_sent INTEGER NOT NULL DEFAULT 0,
    fields_outcome TEXT,
    fields_warning_codes TEXT,
    fields_attempts INTEGER NOT NULL DEFAULT 0
);
""" + TABLES
NEW_COLUMNS = {"extraction_stamp", "text_sha256", "resend_reason", "resend_attempts"}
TS = "2026-10-01T00:00:00Z"
STAMP = "0123456789abcdef"
OLDER = "fedcba9876543210"
SHA = "a" * 64


def _old_db(path, schema: str) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(schema)
    for rel, size, key in (("Mandate/a.pdf", 10, "tk-old"), ("leer.txt", 0, "skip:unconvertible")):
        conn.execute(
            "INSERT INTO documents (relative_path, mtime_iso, size_bytes, last_uploaded_at, "
            "transmission_key_id) VALUES (?, ?, ?, ?, ?)",
            (rel, TS, size, TS, key),
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
    @pytest.mark.parametrize("schema", [PRE_FIELDS, DOC_FIELDS], ids=["pre-fields", "doc-fields"])
    def test_an_older_file_gains_the_columns_and_every_row_is_outdated(self, tmp_path, schema):
        db_path = tmp_path / "state.db"
        _old_db(db_path, schema)
        db = SyncStateDatabase(db_path)
        try:
            assert db.load_fingerprints() == {"Mandate/a.pdf": (TS, 10), "leer.txt": (TS, 0)}
            assert db.extraction_state("Mandate/a.pdf") == ExtractionState(None, None, None, 0)
            assert db.count_extraction_outdated(STAMP) == 2
            assert db.count_reextract_queued() == 0, "the upgrade queues nothing by itself"
            assert db.load_reextract_queue() == {}
        finally:
            db.close()
        assert NEW_COLUMNS <= _columns(db_path)

    def test_the_fields_columns_survive(self, tmp_path):
        db_path = tmp_path / "state.db"
        _old_db(db_path, DOC_FIELDS)
        conn = sqlite3.connect(str(db_path))
        conn.execute("UPDATE documents SET fields_digest = 'd1', fields_sent = 1, "
                     "fields_outcome = 'staged' WHERE relative_path = 'Mandate/a.pdf'")
        conn.commit()
        conn.close()
        db = SyncStateDatabase(db_path)
        try:
            assert db.fields_state("Mandate/a.pdf") == FieldsState("d1", True, "staged", 0)
        finally:
            db.close()

    def test_opening_twice_and_side_by_side_is_harmless(self, tmp_path):
        db_path = tmp_path / "state.db"
        _old_db(db_path, PRE_FIELDS)
        first, second = SyncStateDatabase(db_path), SyncStateDatabase(db_path)
        try:
            first.load_fingerprints()
            second.load_fingerprints()
        finally:
            first.close()
            second.close()
        again = SyncStateDatabase(db_path)
        try:
            assert again.count_tracked() == 2
        finally:
            again.close()

    def test_an_older_connector_writing_the_file_leaves_the_row_outdated(self, tmp_path):
        """Downgrade: the old INSERT OR REPLACE names only its own columns,
        so the stamp falls back to NULL -- outdated, never wrongly current,
        and not queued."""
        db_path = tmp_path / "state.db"
        db = SyncStateDatabase(db_path)
        try:
            db.record_upload("a.pdf", TS, 1, "tk")
            assert db.set_extraction("a.pdf", STAMP, SHA) is True
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
            assert db.extraction_state("a.pdf") == ExtractionState(None, None, None, 0)
            assert db.count_extraction_outdated(STAMP) == 1
        finally:
            db.close()


class TestStampAndQueue:
    def test_set_extraction_stores_both_and_leaves_the_queue(self, store):
        store.record_upload("a.pdf", TS, 1, "tk")
        assert store.requeue_reextract(STAMP) == 1
        assert store.set_extraction("a.pdf", STAMP, SHA) is True
        assert store.extraction_state("a.pdf") == ExtractionState(STAMP, SHA, None, 0)
        assert store.count_extraction_outdated(STAMP) == 0
        assert store.set_extraction("missing.pdf", STAMP, SHA) is False
        assert store.count_tracked_paths() == 1, "never creates a row"

    def test_requeue_marks_outdated_rows_once(self, store):
        for rel in ("a.pdf", "b.docx", "c.txt"):
            store.record_upload(rel, TS, 1, "tk")
        store.set_extraction("c.txt", STAMP, SHA)
        store.set_extraction("b.docx", OLDER, SHA)
        assert store.count_extraction_outdated(STAMP) == 2
        assert store.requeue_reextract(STAMP) == 2
        assert store.requeue_reextract(STAMP) == 0, "already waiting: not counted twice"
        assert store.count_reextract_queued() == 2
        assert store.load_reextract_queue() == {"a.pdf": None, "b.docx": SHA}
        assert store.extraction_state("b.docx").resend_reason == RESEND_REEXTRACT

    def test_set_extraction_stamp_keeps_the_hash(self, store):
        store.record_upload("a.pdf", TS, 1, "tk")
        store.set_extraction("a.pdf", OLDER, SHA)
        store.requeue_reextract(STAMP)
        assert store.set_extraction_stamp("a.pdf", STAMP) is True
        assert store.extraction_state("a.pdf") == ExtractionState(STAMP, SHA, None, 0)

    def test_unchanged_moves_the_stamp_and_follows_the_new_partial_note(self, store):
        store.record_partial("scan.pdf", TS, 1, "tk", {"reason": "ocr_backend_none"})
        store.set_extraction("scan.pdf", OLDER, SHA)
        store.requeue_reextract(STAMP)
        store.record_reextract_unchanged("scan.pdf", STAMP, None)
        assert store.extraction_state("scan.pdf") == ExtractionState(STAMP, SHA, None, 0)
        assert store.partial_paths() == [], "the newer extraction is complete"
        assert store.load_fingerprints()["scan.pdf"] == (TS, 1)
        store.set_extraction("scan.pdf", OLDER, SHA)
        store.requeue_reextract(STAMP)
        store.record_reextract_unchanged("scan.pdf", STAMP, {"ocr_pages_skipped": 3})
        assert store.partial_note("scan.pdf") == {"ocr_pages_skipped": 3}

    def test_failures_leave_the_queue_after_the_cap_and_stay_outdated(self, store):
        store.record_upload("a.pdf", TS, 1, "tk")
        store.requeue_reextract(STAMP)
        assert store.count_reextract_failure("a.pdf", 3) is False
        assert store.count_reextract_failure("a.pdf", 3) is False
        assert store.extraction_state("a.pdf").resend_attempts == 2
        assert store.count_reextract_failure("a.pdf", 3) is True
        assert store.extraction_state("a.pdf") == ExtractionState(None, None, None, 0)
        assert store.count_extraction_outdated(STAMP) == 1
        assert store.count_reextract_failure("a.pdf", 3) is False, "not queued: nothing counted"
        assert store.requeue_reextract(STAMP) == 1, "a new request queues it again"

    def test_fingerprint_writes_keep_the_columns(self, store):
        store.record_upload("a.pdf", TS, 1, "tk1", fields=FieldsRecord("d", "staged", sent=True))
        store.set_extraction("a.pdf", STAMP, SHA)
        store.record_upload("a.pdf", "2026-10-02T00:00:00Z", 2, "tk2")
        store.record_skip("a.pdf", TS, 1, reason="unconvertible")
        assert store.extraction_state("a.pdf") == ExtractionState(STAMP, SHA, None, 0)

    def test_remove_and_reset_forget_the_columns(self, store):
        store.record_upload("a.pdf", TS, 1, "tk")
        store.record_upload("b.pdf", TS, 1, "tk")
        store.requeue_reextract(STAMP)
        store.remove_tracked("a.pdf")
        assert store.extraction_state("a.pdf") is None
        assert store.count_reextract_queued() == 1
        store.reset_all()
        assert store.count_extraction_outdated(STAMP) == 0
        assert store.count_reextract_queued() == 0
