"""On-disk OCR cache for the extraction child (GI-EXTRACT-04).

OCR is the expensive step of a scanned PDF: a page costs one to two seconds
of CPU, a re-sync of a synced folder or the shadow run's second rendering
would pay it again. ``knovas-extract`` 0.4 takes an injectable cache object
(``ocr=OcrOptions(cache=...)``) keyed per page image (plan decision D5: a
sha256 over the undecoded image stream plus the rot/dpi/lang/psm/engine
fingerprint, so an engine or language change never serves stale text); the
library never writes files itself. This module is the RC's implementation.

The cache is a copy of document text at rest, which is why it is handled like
the sync state and not like a scratch file:

* one SQLite file beside ``RC_SYNC_STATE_PATH`` (``.rc-ocr-cache.db``),
  created with mode ``0600``;
* size-capped (``RC_OCR_CACHE_MAX_MB``, default 512 MiB) with least-recently-
  used eviction; ``0`` disables it — no file is created and every ``get``
  misses;
* a ``documents`` index table records which document put an entry, so
  ``purge_document(relative_path)`` runs when a document is deleted or
  unsynced (``SyncStateStore.remove_tracked``) and ``purge_all`` on a reset;
* opened lazily and never shared across a fork: the parent process keeps no
  connection open, the forked extraction child opens its own, and a
  connection inherited by accident is dropped on the first use in the new
  process.

The object is duck-typed — ``get(key) -> str | None`` and ``put(key, value)``
— rather than implementing the library's protocol class, so the same source
runs against today's release, which has no such protocol.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_OCR_CACHE_MAX_MB = 512
CACHE_FILENAME = ".rc-ocr-cache.db"
_EVICT_BATCH = 64

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    key TEXT PRIMARY KEY NOT NULL,
    value TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    created_at REAL NOT NULL,
    last_used_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS entries_last_used ON entries(last_used_at);
CREATE TABLE IF NOT EXISTS documents (
    key TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    PRIMARY KEY (key, relative_path)
);
CREATE INDEX IF NOT EXISTS documents_path ON documents(relative_path);
"""


def ocr_cache_max_mb() -> int:
    """Cap in MiB from ``RC_OCR_CACHE_MAX_MB``; ``0`` disables the cache."""
    raw = (os.environ.get("RC_OCR_CACHE_MAX_MB") or "").strip()
    if not raw:
        return DEFAULT_OCR_CACHE_MAX_MB
    try:
        return max(0, int(raw))
    except ValueError:
        logger.warning("Invalid RC_OCR_CACHE_MAX_MB=%r; using default %d", raw, DEFAULT_OCR_CACHE_MAX_MB)
        return DEFAULT_OCR_CACHE_MAX_MB


def ocr_cache_path_for_state(state_path: Path) -> Path:
    """The cache file lives in the directory of ``RC_SYNC_STATE_PATH``."""
    parent = state_path.parent if state_path.name else Path(".")
    return parent / CACHE_FILENAME


class MemoryOcrCache:
    """Process-local, per-document cache: what the shadow run's second
    rendering hits when the disk cache is disabled (plan decision D13)."""

    def __init__(self) -> None:
        self._entries: dict[str, str] = {}
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[str]:
        value = self._entries.get(str(key))
        if value is None:
            self.misses += 1
            return None
        self.hits += 1
        return value

    def put(self, key: str, value: str) -> None:
        if isinstance(value, str):
            self._entries[str(key)] = value

    def close(self) -> None:
        self._entries.clear()


class OcrDiskCache:
    """SQLite-backed LRU cache of OCR output, keyed per page image."""

    def __init__(self, path: Path, max_bytes: int) -> None:
        self._path = Path(path)
        self._max_bytes = max(0, int(max_bytes))
        self._conn: Optional[sqlite3.Connection] = None
        self._pid: Optional[int] = None
        self.hits = 0
        self.misses = 0

    # ----- construction -------------------------------------------------

    @classmethod
    def beside_state_path(cls, state_path: Path, max_mb: Optional[int] = None) -> "OcrDiskCache":
        cap = ocr_cache_max_mb() if max_mb is None else max(0, int(max_mb))
        return cls(ocr_cache_path_for_state(Path(state_path)), cap * 1024 * 1024)

    @classmethod
    def from_env(cls) -> "OcrDiskCache":
        from config import get_config

        return cls.beside_state_path(Path(get_config().rc_sync_state_path))

    @property
    def path(self) -> Path:
        return self._path

    @property
    def enabled(self) -> bool:
        return self._max_bytes > 0

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    @property
    def connection_pid(self) -> Optional[int]:
        """The process that owns the open connection, or None when closed."""
        return self._pid if self._conn is not None else None

    def for_document(self, relative_path: str):
        """A view whose ``put`` indexes entries under ``relative_path``."""
        return DocumentOcrCache(self, relative_path)

    # ----- connection ----------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        pid = os.getpid()
        if self._conn is not None and self._pid != pid:
            # Inherited across fork(): SQLite forbids using a connection in
            # the child. Drop it without touching it and open our own.
            self._conn = None
        if self._conn is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            if not self._path.exists():
                fd = os.open(self._path, os.O_CREAT | os.O_WRONLY, 0o600)
                os.close(fd)
            try:
                os.chmod(self._path, 0o600)
            except OSError:
                pass
            conn = sqlite3.connect(str(self._path), timeout=30.0)
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(_SCHEMA)
            self._conn = conn
            self._pid = pid
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            if self._pid == os.getpid():
                self._conn.close()
            self._conn = None
            self._pid = None

    # ----- cache protocol (duck-typed) -----------------------------------

    def get(self, key: str) -> Optional[str]:
        if not self.enabled:
            self.misses += 1
            return None
        try:
            conn = self._connect()
            row = conn.execute("SELECT value FROM entries WHERE key = ?", (str(key),)).fetchone()
            if row is None:
                self.misses += 1
                return None
            conn.execute("UPDATE entries SET last_used_at = ? WHERE key = ?", (time.time(), str(key)))
            conn.commit()
        except sqlite3.Error as exc:
            logger.warning("OCR cache read failed: %s", type(exc).__name__)
            self.misses += 1
            return None
        self.hits += 1
        return str(row[0])

    def put(self, key: str, value: str, *, relative_path: Optional[str] = None) -> None:
        if not self.enabled or not isinstance(value, str):
            return
        size = len(value.encode("utf-8"))
        if size > self._max_bytes:
            return
        now = time.time()
        try:
            conn = self._connect()
            conn.execute(
                "INSERT OR REPLACE INTO entries (key, value, size_bytes, created_at, last_used_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (str(key), value, size, now, now),
            )
            if relative_path:
                conn.execute(
                    "INSERT OR IGNORE INTO documents (key, relative_path) VALUES (?, ?)",
                    (str(key), str(relative_path)),
                )
            self._evict_locked(conn)
            conn.commit()
        except sqlite3.Error as exc:
            logger.warning("OCR cache write failed: %s", type(exc).__name__)

    # ----- housekeeping --------------------------------------------------

    def _evict_locked(self, conn: sqlite3.Connection) -> None:
        total = self._total_bytes(conn)
        while total > self._max_bytes:
            rows = conn.execute(
                "SELECT key, size_bytes FROM entries ORDER BY last_used_at ASC, key ASC LIMIT ?",
                (_EVICT_BATCH,),
            ).fetchall()
            if not rows:
                break
            for key, size in rows:
                conn.execute("DELETE FROM entries WHERE key = ?", (key,))
                conn.execute("DELETE FROM documents WHERE key = ?", (key,))
                total -= int(size)
                if total <= self._max_bytes:
                    break

    @staticmethod
    def _total_bytes(conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM entries").fetchone()
        return int(row[0]) if row else 0

    def purge_document(self, relative_path: str) -> int:
        """Remove the entries only this document put; returns how many.

        An entry a second tracked document also produced (the same scan
        filed twice) stays for that document and is removed with it.
        """
        if not relative_path or not self._path.exists():
            return 0
        if not self.enabled:
            return 0
        try:
            conn = self._connect()
            rows = conn.execute(
                "SELECT key FROM documents WHERE relative_path = ?", (str(relative_path),)
            ).fetchall()
            removed = 0
            for (key,) in rows:
                others = conn.execute(
                    "SELECT 1 FROM documents WHERE key = ? AND relative_path != ? LIMIT 1",
                    (key, str(relative_path)),
                ).fetchone()
                if others is None:
                    conn.execute("DELETE FROM entries WHERE key = ?", (key,))
                    removed += 1
            conn.execute("DELETE FROM documents WHERE relative_path = ?", (str(relative_path),))
            conn.commit()
            return removed
        except sqlite3.Error as exc:
            logger.warning("OCR cache purge failed: %s", type(exc).__name__)
            return 0
        finally:
            self.close()

    def purge_all(self) -> None:
        """Drop every entry and the file itself (reset / cache disabled)."""
        self.close()
        for suffix in ("", "-journal", "-wal", "-shm"):
            candidate = Path(str(self._path) + suffix)
            try:
                candidate.unlink()
            except FileNotFoundError:
                continue
            except OSError as exc:
                logger.warning("OCR cache file not removed: %s", type(exc).__name__)

    def stats(self) -> dict[str, int]:
        out = {"hits": self.hits, "misses": self.misses, "entries": 0, "bytes": 0, "max_bytes": self._max_bytes}
        if not self.enabled or not self._path.exists():
            return out
        try:
            conn = self._connect()
            row = conn.execute("SELECT COUNT(*), COALESCE(SUM(size_bytes), 0) FROM entries").fetchone()
            if row:
                out["entries"], out["bytes"] = int(row[0]), int(row[1])
        except sqlite3.Error:
            pass
        return out


class DocumentOcrCache:
    """The object handed to the library for ONE document: same store, every
    ``put`` indexed under the document so it can be purged later."""

    def __init__(self, cache: OcrDiskCache, relative_path: str) -> None:
        self._cache = cache
        self._relative_path = str(relative_path or "")

    @property
    def hits(self) -> int:
        return self._cache.hits

    @property
    def misses(self) -> int:
        return self._cache.misses

    def get(self, key: str) -> Optional[str]:
        return self._cache.get(key)

    def put(self, key: str, value: str) -> None:
        self._cache.put(key, value, relative_path=self._relative_path or None)

    def close(self) -> None:
        self._cache.close()


def ocr_cache_for_document(relative_path: Optional[str]):
    """The cache object for one document's extraction (called in the child).

    The disk cache when it is enabled, otherwise a per-document in-memory
    cache so the shadow run still OCRs each page once.
    """
    try:
        disk = OcrDiskCache.from_env()
    except Exception as exc:  # noqa: BLE001 - config missing in odd contexts
        logger.debug("OCR disk cache unavailable: %s", type(exc).__name__)
        return MemoryOcrCache()
    if not disk.enabled:
        return MemoryOcrCache()
    return disk.for_document(relative_path or "")
