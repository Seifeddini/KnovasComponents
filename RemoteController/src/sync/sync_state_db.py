"""SQLite-backed sync state with one-time migration from legacy JSON."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Collection, NamedTuple, Optional

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    relative_path TEXT PRIMARY KEY NOT NULL,
    mtime_iso TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    last_uploaded_at TEXT,
    transmission_key_id TEXT
);
CREATE TABLE IF NOT EXISTS partial_documents (
    relative_path TEXT PRIMARY KEY NOT NULL,
    note_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS extract_retries (
    relative_path TEXT PRIMARY KEY NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    updated_at TEXT
);
"""

_MAX_LAST_ERROR_CHARS = 500

#: Knovas document-fields columns of ``documents`` (spec 3.7), added in place
#: to older files. Additive only: an older RemoteController reading the same
#: file never names them, and its ``INSERT OR REPLACE`` resets them to these
#: defaults, which the newer one reads as "nothing known".
_FIELDS_COLUMNS = (
    ("fields_digest", "TEXT"),
    ("fields_sent", "INTEGER NOT NULL DEFAULT 0"),
    ("fields_outcome", "TEXT"),
    ("fields_warning_codes", "TEXT"),
    ("fields_attempts", "INTEGER NOT NULL DEFAULT 0"),
)

#: Re-extraction columns of ``documents`` (spec L6), added in place like the
#: fields columns. ``extraction_stamp`` NULL means synced before stamps
#: existed, i.e. by an older extraction. ``resend_reason`` is the side-queue
#: marker (``RESEND_REEXTRACT``), or ``RESEND_KEPT`` after a re-extraction
#: that was not sent; ``resend_attempts`` counts failed re-extractions. An
#: older RemoteController's ``INSERT OR REPLACE`` resets them to NULL / 0:
#: outdated and not queued, the safe direction.
_EXTRACTION_COLUMNS = (
    ("extraction_stamp", "TEXT"),
    ("text_sha256", "TEXT"),
    ("resend_reason", "TEXT"),
    ("resend_attempts", "INTEGER NOT NULL DEFAULT 0"),
)

#: ``resend_reason`` of a row ``POST /sync/reextract/requeue`` queued.
RESEND_REEXTRACT = "reextract"
#: ``resend_reason`` of a row whose re-extraction missed more OCR pages than
#: the text Knovas holds and was not sent: Knovas keeps the text of an older
#: extraction. Counted, never processed; the next upload clears it, and a
#: request after the stamp changed queues the row again.
RESEND_KEPT = "kept"

#: Stored instead of a digest when a document must come back for its fields
#: although its governing digest may be "" (a clear the server never saw, a
#: transient refusal). It never equals a sha256 or "", so the row counts as
#: ``fields_changed`` on the next cycle.
REQUEUE_DIGEST = "requeue"

#: Requeue selectors (``POST /sync/doc-fields/requeue``) and the stored
#: outcomes each one matches.
REQUEUE_OUTCOMES = ("not_accepted", "refused", "reupload_failed", "all")
_REQUEUE_WHERE = {
    "not_accepted": "fields_outcome = 'not_accepted'",
    "refused": "fields_outcome LIKE 'refused:%'",
    "reupload_failed": "fields_outcome LIKE 'reupload_failed:%'",
}
_REQUEUE_WHERE["all"] = "(" + " OR ".join(_REQUEUE_WHERE.values()) + ")"


class FieldsState(NamedTuple):
    """The fields columns of one ``documents`` row."""

    digest: Optional[str]
    sent: bool
    outcome: Optional[str]
    attempts: int


class ExtractionState(NamedTuple):
    """The re-extraction columns of one ``documents`` row."""

    stamp: Optional[str]
    text_sha256: Optional[str]
    resend_reason: Optional[str]
    resend_attempts: int


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def json_state_path_to_db(path: Path) -> Path:
    """Derive SQLite path from RC_SYNC_STATE_PATH (e.g. .rc-sync-state.json -> .rc-sync-state.db)."""
    if path.suffix.lower() == ".json":
        return path.with_suffix(".db")
    return path.with_name(path.name + ".db")


def normalize_json_documents(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    documents = data.get("documents")
    if not isinstance(documents, dict):
        documents = {}
    legacy = data.get("files")
    if isinstance(legacy, dict) and legacy:
        for key, meta in legacy.items():
            if not isinstance(meta, dict):
                continue
            parts = key.split("|", 2)
            if len(parts) != 3:
                continue
            rel_path, mtime_iso, size_str = parts
            try:
                size_bytes = int(size_str)
            except ValueError:
                continue
            documents[rel_path] = {
                "mtime_iso": mtime_iso,
                "size_bytes": size_bytes,
                "last_uploaded_at": meta.get("last_uploaded_at"),
                "transmission_key_id": meta.get("transmission_key_id"),
            }
    return documents if isinstance(documents, dict) else {}


class SyncStateDatabase:
    def __init__(self, db_path: Path, *, json_path: Optional[Path] = None):
        self._db_path = db_path
        self._json_path = json_path or (
            db_path.with_suffix(".json") if db_path.suffix.lower() == ".db" else None
        )
        self._conn: Optional[sqlite3.Connection] = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._db_path), timeout=30.0)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(_SCHEMA)
            self._ensure_columns()
            self._maybe_migrate_from_json()
        return self._conn

    def _ensure_columns(self) -> None:
        """Add the fields and the re-extraction columns to an older file."""
        conn = self._conn
        assert conn is not None
        present = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
        for name, decl in _FIELDS_COLUMNS + _EXTRACTION_COLUMNS:
            if name in present:
                continue
            try:
                conn.execute(f"ALTER TABLE documents ADD COLUMN {name} {decl}")
            except sqlite3.OperationalError as exc:
                # Another process added it between the PRAGMA and the ALTER.
                if "duplicate column" not in str(exc).lower():
                    raise
        conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _maybe_migrate_from_json(self) -> None:
        conn = self._conn
        assert conn is not None
        row = conn.execute("SELECT COUNT(*) FROM documents").fetchone()
        if row and row[0] > 0:
            return
        json_path = self._json_path
        if json_path is None or not json_path.exists():
            return
        try:
            raw = json.loads(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Skipping JSON migration (unreadable): %s", type(exc).__name__)
            return
        if not isinstance(raw, dict):
            return
        documents = normalize_json_documents(raw)
        if not documents:
            return
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        rows = []
        for rel, entry in documents.items():
            if not isinstance(entry, dict):
                continue
            rows.append(
                (
                    rel,
                    str(entry.get("mtime_iso") or ""),
                    int(entry.get("size_bytes") or 0),
                    entry.get("last_uploaded_at") or now,
                    entry.get("transmission_key_id"),
                )
            )
        conn.executemany(
            """
            INSERT OR REPLACE INTO documents
            (relative_path, mtime_iso, size_bytes, last_uploaded_at, transmission_key_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        backup = json_path.with_suffix(json_path.suffix + ".migrated")
        try:
            os.replace(json_path, backup)
            logger.info("Migrated %d paths from JSON to SQLite; renamed JSON to %s", len(rows), backup.name)
        except OSError:
            logger.info("Migrated %d paths from JSON to SQLite", len(rows))

    def load_fingerprints(self) -> dict[str, tuple[str, int]]:
        conn = self._connect()
        cur = conn.execute(
            "SELECT relative_path, mtime_iso, size_bytes FROM documents"
        )
        return {row[0]: (row[1], int(row[2])) for row in cur}

    def count_tracked(self) -> int:
        conn = self._connect()
        row = conn.execute("SELECT COUNT(*) FROM documents").fetchone()
        return int(row[0]) if row else 0

    def lookup_fingerprint(
        self, fingerprints: dict[str, tuple[str, int]], relative_path: str
    ) -> Optional[tuple[str, int]]:
        if relative_path in fingerprints:
            return fingerprints[relative_path]
        conn = self._connect()
        row = conn.execute(
            "SELECT mtime_iso, size_bytes FROM documents WHERE relative_path = ?",
            (relative_path,),
        ).fetchone()
        if not row:
            return None
        fp = (row[0], int(row[1]))
        fingerprints[relative_path] = fp
        return fp

    def record_upload(
        self,
        relative_path: str,
        mtime_iso: str,
        size_bytes: int,
        transmission_key_id: str,
        *,
        fingerprints: Optional[dict[str, tuple[str, int]]] = None,
        fields: Any = None,
    ) -> None:
        """Store the fingerprint of an upload.

        An UPSERT naming its columns: the fields columns survive a write that
        does not carry ``fields`` (a ``FieldsRecord``), where the former
        ``INSERT OR REPLACE`` reset every column it did not name.
        """
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO documents
            (relative_path, mtime_iso, size_bytes, last_uploaded_at, transmission_key_id)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(relative_path) DO UPDATE SET
                mtime_iso = excluded.mtime_iso,
                size_bytes = excluded.size_bytes,
                last_uploaded_at = excluded.last_uploaded_at,
                transmission_key_id = excluded.transmission_key_id
            """,
            (relative_path, mtime_iso, size_bytes, now, transmission_key_id),
        )
        if fields is not None:
            self._write_fields(conn, relative_path, fields)
        conn.commit()
        if fingerprints is not None:
            fingerprints[relative_path] = (mtime_iso, size_bytes)

    # ----- Knovas document fields (spec 3.7) ----------------------------------

    @staticmethod
    def _write_fields(conn: sqlite3.Connection, relative_path: str, record: Any) -> None:
        """Write a ``FieldsRecord`` into an existing row (no-op without one).

        ``digest`` / ``sent`` None keep the stored value; ``count_attempt``
        adds one to ``fields_attempts``, anything else resets it to 0.
        """
        sets = [
            "fields_outcome = ?",
            "fields_warning_codes = ?",
            "fields_attempts = CASE WHEN ? THEN fields_attempts + 1 ELSE 0 END",
        ]
        params: list[Any] = [
            str(record.outcome),
            json.dumps(list(record.warning_codes or ())),
            1 if record.count_attempt else 0,
        ]
        if record.digest is not None:
            sets.append("fields_digest = ?")
            params.append(str(record.digest))
        if record.sent is not None:
            sets.append("fields_sent = ?")
            params.append(1 if record.sent else 0)
        params.append(relative_path)
        conn.execute(f"UPDATE documents SET {', '.join(sets)} WHERE relative_path = ?", params)

    def update_fields(self, relative_path: str, record: Any) -> Optional[int]:
        """Write the fields columns of an existing row only (full mode, a
        re-upload that left the queue); returns ``fields_attempts`` after the
        write, or None when the path is not tracked."""
        conn = self._connect()
        self._write_fields(conn, relative_path, record)
        conn.commit()
        return self._attempts(conn, relative_path)

    def increment_fields_attempts(self, relative_path: str) -> int:
        """One more failed re-upload of a document whose fields changed."""
        conn = self._connect()
        conn.execute(
            "UPDATE documents SET fields_attempts = fields_attempts + 1 WHERE relative_path = ?",
            (relative_path,),
        )
        conn.commit()
        return self._attempts(conn, relative_path) or 0

    @staticmethod
    def _attempts(conn: sqlite3.Connection, relative_path: str) -> Optional[int]:
        row = conn.execute(
            "SELECT fields_attempts FROM documents WHERE relative_path = ?", (relative_path,)
        ).fetchone()
        return int(row[0] or 0) if row else None

    def load_fields_states(self) -> dict[str, FieldsState]:
        """Every row's fields columns, once per scan cycle."""
        conn = self._connect()
        cur = conn.execute(
            "SELECT relative_path, fields_digest, fields_sent, fields_outcome, fields_attempts "
            "FROM documents"
        )
        return {
            row[0]: FieldsState(row[1], bool(row[2]), row[3], int(row[4] or 0)) for row in cur
        }

    def fields_state(self, relative_path: str) -> Optional[FieldsState]:
        conn = self._connect()
        row = conn.execute(
            "SELECT fields_digest, fields_sent, fields_outcome, fields_attempts "
            "FROM documents WHERE relative_path = ?",
            (relative_path,),
        ).fetchone()
        if not row:
            return None
        return FieldsState(row[0], bool(row[1]), row[2], int(row[3] or 0))

    def requeue_fields(self, outcome: str, paths: Optional[Collection[str]] = None) -> int:
        """Queue documents for a fields re-upload; returns how many.

        The digest is cleared, so the next cycle finds the document
        ``fields_changed`` and re-sends it within the per-cycle bound. A
        document whose values were staged before gets ``REQUEUE_DIGEST``
        instead of NULL: NULL equals "" and would never re-send a clear.
        Rows already waiting are not counted twice.

        ``paths`` limits the requeue to documents a scan reaches (the
        cycle's scanned paths): a row the scan never visits again -- a
        completed sequential subfolder, a removed file kept tracked by
        ``delete_on_remove: false`` -- would otherwise wait for good. The
        extraction retry counter of a queued row is cleared: a re-upload
        that leaves the queue starts with a clean slate.
        """
        conn = self._connect()
        selected = self._requeue_candidates(conn, outcome, paths)
        if not selected:
            return 0
        where = _REQUEUE_WHERE[outcome]
        cur = conn.executemany(
            f"""
            UPDATE documents SET
                fields_digest = CASE WHEN fields_sent = 1 THEN ? ELSE NULL END,
                fields_attempts = 0
            WHERE relative_path = ? AND {where}
              AND fields_digest IS NOT NULL AND fields_digest != ?
            """,
            [(REQUEUE_DIGEST, path, REQUEUE_DIGEST) for path in selected],
        )
        count = int(cur.rowcount or 0)
        conn.executemany(
            "DELETE FROM extract_retries WHERE relative_path = ?", [(path,) for path in selected]
        )
        conn.commit()
        return count

    def count_fields_requeue_candidates(
        self, outcome: str, paths: Optional[Collection[str]] = None
    ) -> int:
        """Rows ``requeue_fields(outcome, paths)`` would queue now."""
        return len(self._requeue_candidates(self._connect(), outcome, paths))

    def fields_requeue_candidate_paths(self, outcome: str = "all") -> list[str]:
        """Paths ``requeue_fields(outcome)`` would queue now (kept in memory
        only, to scope the requeue endpoint to what the last scan reached)."""
        return self._requeue_candidates(self._connect(), outcome, None)

    @staticmethod
    def _requeue_candidates(
        conn: sqlite3.Connection, outcome: str, paths: Optional[Collection[str]]
    ) -> list[str]:
        where = _REQUEUE_WHERE.get(outcome)
        if where is None:
            raise ValueError("unknown requeue outcome")
        cur = conn.execute(
            f"SELECT relative_path FROM documents WHERE {where} "
            "AND fields_digest IS NOT NULL AND fields_digest != ?",
            (REQUEUE_DIGEST,),
        )
        found = [row[0] for row in cur]
        if paths is None:
            return found
        return [path for path in found if path in paths]

    def fields_counts(self) -> dict[str, int]:
        """Document counts for ``/sync/status`` (no paths, no values)."""
        conn = self._connect()
        row = conn.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN fields_sent = 1 THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN fields_outcome LIKE 'refused:%' THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN fields_outcome = 'not_accepted' THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN fields_outcome LIKE 'reupload_failed:%' THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN fields_outcome IN ('staged', 'cleared') THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN fields_outcome IS NOT NULL
                    AND (fields_digest IS NULL OR fields_digest = ?) THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN fields_outcome = 'not_accepted'
                    AND (fields_digest IS NULL OR fields_digest = ?) THEN 1 ELSE 0 END), 0)
            FROM documents
            """,
            (REQUEUE_DIGEST, REQUEUE_DIGEST),
        ).fetchone()
        keys = ("with_fields", "refused", "not_accepted", "reupload_failed", "accepted", "requeued",
                "not_accepted_requeued")
        return {key: int(value or 0) for key, value in zip(keys, row or ())}

    # ----- re-extraction (spec L6) ---------------------------------------------

    def extraction_state(self, relative_path: str) -> Optional[ExtractionState]:
        conn = self._connect()
        row = conn.execute(
            "SELECT extraction_stamp, text_sha256, resend_reason, resend_attempts "
            "FROM documents WHERE relative_path = ?",
            (relative_path,),
        ).fetchone()
        if not row:
            return None
        return ExtractionState(row[0], row[1], row[2], int(row[3] or 0))

    def set_extraction(
        self, relative_path: str, stamp: Optional[str], text_sha: Optional[str]
    ) -> bool:
        """After an upload: the stamp of the extraction that produced it and
        the hash of what it carried; the row leaves the re-extraction queue.
        Existing rows only; False when the path is not tracked."""
        conn = self._connect()
        cur = conn.execute(
            "UPDATE documents SET extraction_stamp = ?, text_sha256 = ?, "
            "resend_reason = NULL, resend_attempts = 0 WHERE relative_path = ?",
            (stamp, text_sha, relative_path),
        )
        conn.commit()
        return bool(cur.rowcount)

    def set_extraction_stamp(self, relative_path: str, stamp: str) -> bool:
        """The current extraction read the file and nothing was uploaded --
        the upload would not change, or the file is unconvertible: the stamp
        moves on, the stored hash stays, the row leaves the queue."""
        conn = self._connect()
        cur = conn.execute(
            "UPDATE documents SET extraction_stamp = ?, resend_reason = NULL, "
            "resend_attempts = 0 WHERE relative_path = ?",
            (stamp, relative_path),
        )
        conn.commit()
        return bool(cur.rowcount)

    def set_extraction_kept(self, relative_path: str, stamp: str) -> bool:
        """The current extraction would have sent less than Knovas holds,
        so nothing was sent: the stamp moves on and the row leaves the queue
        marked ``RESEND_KEPT``; the stored hash stays."""
        conn = self._connect()
        cur = conn.execute(
            "UPDATE documents SET extraction_stamp = ?, resend_reason = ?, "
            "resend_attempts = 0 WHERE relative_path = ?",
            (stamp, RESEND_KEPT, relative_path),
        )
        conn.commit()
        return bool(cur.rowcount)

    def count_reextract_kept(self) -> int:
        conn = self._connect()
        row = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE resend_reason = ?", (RESEND_KEPT,)
        ).fetchone()
        return int(row[0]) if row else 0

    def count_extraction_outdated(self, stamp: str) -> int:
        """Tracked rows an older extraction produced: another stamp, or none
        (synced before stamps existed). Counts only, for ``/sync/status``."""
        conn = self._connect()
        row = conn.execute(
            "SELECT COUNT(*) FROM documents "
            "WHERE extraction_stamp IS NULL OR extraction_stamp != ?",
            (stamp,),
        ).fetchone()
        return int(row[0]) if row else 0

    def requeue_reextract(self, stamp: str) -> int:
        """Queue every outdated row for re-extraction (``resend_reason``);
        returns how many were newly queued -- rows already waiting are not
        counted twice. A kept row is outdated again only once the stamp
        changed, and is then queued like any other."""
        conn = self._connect()
        cur = conn.execute(
            "UPDATE documents SET resend_reason = ?, resend_attempts = 0 "
            "WHERE (extraction_stamp IS NULL OR extraction_stamp != ?) "
            "AND (resend_reason IS NULL OR resend_reason = ?)",
            (RESEND_REEXTRACT, stamp, RESEND_KEPT),
        )
        conn.commit()
        return int(cur.rowcount or 0)

    def count_reextract_queued(self) -> int:
        conn = self._connect()
        row = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE resend_reason = ?", (RESEND_REEXTRACT,)
        ).fetchone()
        return int(row[0]) if row else 0

    def load_reextract_queue(self) -> dict[str, Optional[str]]:
        """Queued rows and their stored ``text_sha256`` (None: uploaded
        before hashes existed), read once per scan cycle."""
        conn = self._connect()
        cur = conn.execute(
            "SELECT relative_path, text_sha256 FROM documents WHERE resend_reason = ?",
            (RESEND_REEXTRACT,),
        )
        return {row[0]: row[1] for row in cur}

    def count_reextract_failure(self, relative_path: str, max_attempts: int) -> bool:
        """One more failed re-extraction of a queued row. At ``max_attempts``
        the row leaves the queue -- still outdated, so the next request
        queues it again. True when it left."""
        conn = self._connect()
        conn.execute(
            "UPDATE documents SET resend_attempts = resend_attempts + 1 "
            "WHERE relative_path = ? AND resend_reason = ?",
            (relative_path, RESEND_REEXTRACT),
        )
        row = conn.execute(
            "SELECT resend_attempts FROM documents WHERE relative_path = ? AND resend_reason = ?",
            (relative_path, RESEND_REEXTRACT),
        ).fetchone()
        left = row is not None and int(row[0] or 0) >= max_attempts
        if left:
            conn.execute(
                "UPDATE documents SET resend_reason = NULL, resend_attempts = 0 "
                "WHERE relative_path = ?",
                (relative_path,),
            )
        conn.commit()
        return left

    def list_tracked_paths(self) -> list[str]:
        conn = self._connect()
        cur = conn.execute("SELECT relative_path FROM documents ORDER BY relative_path")
        return [row[0] for row in cur]

    def remove_tracked(self, relative_path: str, *, fingerprints: Optional[dict[str, tuple[str, int]]] = None) -> None:
        conn = self._connect()
        conn.execute("DELETE FROM documents WHERE relative_path = ?", (relative_path,))
        conn.execute("DELETE FROM partial_documents WHERE relative_path = ?", (relative_path,))
        conn.execute("DELETE FROM extract_retries WHERE relative_path = ?", (relative_path,))
        conn.commit()
        if fingerprints is not None:
            fingerprints.pop(relative_path, None)

    def remove_all(self) -> None:
        conn = self._connect()
        conn.execute("DELETE FROM documents")
        conn.execute("DELETE FROM partial_documents")
        conn.execute("DELETE FROM extract_retries")
        conn.commit()

    # ----- partial documents (GI-EXTRACT-02) ---------------------------------

    def set_partial(self, relative_path: str, note: dict[str, Any]) -> None:
        conn = self._connect()
        conn.execute(
            "INSERT OR REPLACE INTO partial_documents (relative_path, note_json, recorded_at) VALUES (?, ?, ?)",
            (relative_path, json.dumps(note, ensure_ascii=False, sort_keys=True), _now_iso()),
        )
        conn.commit()

    def clear_partial(self, relative_path: str) -> None:
        conn = self._connect()
        conn.execute("DELETE FROM partial_documents WHERE relative_path = ?", (relative_path,))
        conn.commit()

    def get_partial(self, relative_path: str) -> Optional[dict[str, Any]]:
        conn = self._connect()
        row = conn.execute(
            "SELECT note_json FROM partial_documents WHERE relative_path = ?", (relative_path,)
        ).fetchone()
        if not row:
            return None
        try:
            note = json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            return {}
        return note if isinstance(note, dict) else {}

    def list_partial_paths(self) -> list[str]:
        conn = self._connect()
        cur = conn.execute("SELECT relative_path FROM partial_documents ORDER BY relative_path")
        return [row[0] for row in cur]

    def count_partial(self) -> int:
        conn = self._connect()
        row = conn.execute("SELECT COUNT(*) FROM partial_documents").fetchone()
        return int(row[0]) if row else 0

    # ----- extraction retries ----------------------------------------------

    def get_retry_count(self, relative_path: str) -> int:
        conn = self._connect()
        row = conn.execute(
            "SELECT retry_count FROM extract_retries WHERE relative_path = ?", (relative_path,)
        ).fetchone()
        return int(row[0]) if row else 0

    def increment_retry(self, relative_path: str, *, error: Optional[str] = None) -> int:
        conn = self._connect()
        current = self.get_retry_count(relative_path)
        count = current + 1
        conn.execute(
            "INSERT OR REPLACE INTO extract_retries (relative_path, retry_count, last_error, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (relative_path, count, (error or "")[:_MAX_LAST_ERROR_CHARS] or None, _now_iso()),
        )
        conn.commit()
        return count

    def clear_retries(self, relative_path: str) -> None:
        conn = self._connect()
        conn.execute("DELETE FROM extract_retries WHERE relative_path = ?", (relative_path,))
        conn.commit()
