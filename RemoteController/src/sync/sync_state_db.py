"""SQLite-backed sync state with one-time migration from legacy JSON."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    relative_path TEXT PRIMARY KEY NOT NULL,
    mtime_iso TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    last_uploaded_at TEXT,
    transmission_key_id TEXT
);
"""


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
            self._maybe_migrate_from_json()
        return self._conn

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
    ) -> None:
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        conn = self._connect()
        conn.execute(
            """
            INSERT OR REPLACE INTO documents
            (relative_path, mtime_iso, size_bytes, last_uploaded_at, transmission_key_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (relative_path, mtime_iso, size_bytes, now, transmission_key_id),
        )
        conn.commit()
        if fingerprints is not None:
            fingerprints[relative_path] = (mtime_iso, size_bytes)

    def list_tracked_paths(self) -> list[str]:
        conn = self._connect()
        cur = conn.execute("SELECT relative_path FROM documents ORDER BY relative_path")
        return [row[0] for row in cur]
