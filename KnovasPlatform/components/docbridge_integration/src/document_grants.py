"""Which documents a person may be handed the bytes of, recorded per search.

Why this exists
---------------
``/secured/query`` decides what a person may retrieve. The Platform's file
routes — preview, thumbnail, download, client-path — are the *other* way to a
document: they take a pointer and read the file off the Platform's own disk,
where nothing Knovas decided stands in the way. Someone who knows or guesses a
pointer reaches the bytes without ever having been allowed to find it.

The wall those routes were given asks the Secure API
(``GET /secured/document_readable``) whether the caller may read one pointer.
That endpoint does not exist and never has, so every call answered 404, the
guard failed closed exactly as designed, and every document became "Not found"
for every user while search kept working — the text under a result comes from
the context sidecars, not from the file, so the deployment reads as healthy
right up to the moment somebody clicks.

So the decision is made here instead, out of something the Platform already
holds: **the pointers retrieval handed this person**. A file route serves a
document only if that person's own search returned it, within the TTL. This is
a capability, not an ACL evaluation — deliberately, because evaluating the ACL
here would mean a second copy of a policy that could disagree with the
backend's, and a wall that disagrees is worse than no wall.

What it does and does not promise
---------------------------------
It promises: you cannot fetch a file your own retrieval never surfaced. That
closes the enumeration hole, which is what these routes actually expose.

It does not promise more than retrieval does. Whatever ``/secured/query``
filters out, this never grants; whatever it returns, this allows. So the
property tracks the backend automatically — if retrieval starts filtering per
person, this starts enforcing per person, with no change here.

Storage
-------
SQLite under the app data dir, for the same reason ``open_tokens`` uses one:
gunicorn runs several worker processes by default, and a grant written while
serving the search must be visible to the worker that serves the thumbnail.
An unusable path degrades to an in-process dict, which on a multi-worker
deployment means some requests miss — noisy, but it fails closed, never open.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from typing import Dict, Iterable, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 3600


def normalize_pointer(pointer: str) -> str:
    """One spelling for a pointer, so a grant and a check cannot miss.

    Callers legitimately spell the same document as ``a\\b.pdf`` or ``a/b.pdf``,
    and with or without a leading slash. Case is left alone: the pointer maps
    onto a path on a case-sensitive filesystem, so folding it would let one
    grant cover two different files.
    """
    return str(pointer or "").strip().replace("\\", "/").lstrip("/")


class DocumentGrantStore:
    """Records, per person, which pointers their retrieval returned."""

    def __init__(
        self,
        store_path: Optional[str] = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._ttl = max(60, int(ttl_seconds))
        self._store_path = store_path
        self._lock = threading.Lock()
        self._fallback: Dict[Tuple[str, str], float] = {}
        self.backend = "memory"
        self._init_store()

    # -- store setup -------------------------------------------------------
    @staticmethod
    def _looks_unusable(path: str) -> bool:
        """A POSIX-absolute path on Windows is a container path; don't create
        stray drive-root directories on a dev host (same rule as open_tokens)."""
        return os.name == "nt" and path.startswith("/")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._store_path, timeout=5.0)
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_store(self) -> None:
        path = self._store_path
        if not path or self._looks_unusable(path):
            self._store_path = None
            return
        parent = os.path.dirname(path)
        if parent:
            try:
                os.makedirs(parent, exist_ok=True)
            except OSError:
                pass
        try:
            conn = self._connect()
            try:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS document_grants "
                    "(subject TEXT NOT NULL, pointer TEXT NOT NULL, ts REAL NOT NULL, "
                    " PRIMARY KEY (subject, pointer))"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS document_grants_ts "
                    "ON document_grants (ts)"
                )
                conn.commit()
            finally:
                conn.close()
            self.backend = "sqlite"
        except (sqlite3.Error, OSError) as exc:
            logger.warning(
                "Document grant store unusable at %s (%s); falling back to an "
                "in-process cache. On a multi-worker deployment that refuses "
                "some legitimate previews.", path, exc,
            )
            self._store_path = None
            self.backend = "memory"

    # -- public API --------------------------------------------------------
    def grant(self, subject: str, pointers: Iterable[str]) -> int:
        """Record that retrieval returned these pointers to this person.

        Re-granting refreshes the timestamp, so a document stays reachable for
        as long as the person keeps finding it, and ages out when they stop.
        """
        subject = str(subject or "").strip()
        if not subject:
            return 0
        keys = {normalize_pointer(p) for p in pointers}
        keys.discard("")
        if not keys:
            return 0
        now = time.time()
        if self._store_path is None:
            with self._lock:
                self._prune_memory_locked(now)
                for key in keys:
                    self._fallback[(subject, key)] = now
            return len(keys)
        with self._lock:
            conn = None
            try:
                # Inside the try, not before it: opening is as likely to fail as
                # writing (a locked or vanished file), and an exception escaping
                # here would surface as a 500 on the search itself rather than
                # as a search that simply grants nothing.
                conn = self._connect()
                self._prune_sql(conn, now)
                conn.executemany(
                    "INSERT INTO document_grants (subject, pointer, ts) "
                    "VALUES (?, ?, ?) ON CONFLICT(subject, pointer) "
                    "DO UPDATE SET ts = excluded.ts",
                    [(subject, key, now) for key in sorted(keys)],
                )
                conn.commit()
            except (sqlite3.Error, OSError) as exc:
                logger.warning("Could not record document grants: %s", exc)
                return 0
            finally:
                if conn is not None:
                    conn.close()
        return len(keys)

    def granted(self, subject: str, *pointers: str) -> bool:
        """Whether this person may be handed this document.

        Several spellings may be offered for one document (the raw Knovas
        pointer, the path relative to the mount); any one of them matching a
        live grant is enough, because they name the same file.
        """
        subject = str(subject or "").strip()
        if not subject:
            return False
        keys = {normalize_pointer(p) for p in pointers}
        keys.discard("")
        if not keys:
            return False
        now = time.time()
        cutoff = now - self._ttl
        if self._store_path is None:
            with self._lock:
                self._prune_memory_locked(now)
                return any(
                    self._fallback.get((subject, key), 0.0) >= cutoff for key in keys
                )
        with self._lock:
            conn = None
            try:
                # Opening is inside the try for the same reason as in grant():
                # this answer gates bytes, so every failure has to become a
                # refusal, never an exception the caller turns into a 500.
                conn = self._connect()
                placeholders = ",".join("?" for _ in keys)
                row = conn.execute(
                    "SELECT 1 FROM document_grants WHERE subject = ? AND ts >= ? "
                    f"AND pointer IN ({placeholders}) LIMIT 1",
                    (subject, cutoff, *sorted(keys)),
                ).fetchone()
                return row is not None
            except (sqlite3.Error, OSError) as exc:
                # Fail closed: a store we cannot read is not permission to serve.
                logger.warning("Could not read document grants: %s", exc)
                return False
            finally:
                if conn is not None:
                    conn.close()

    # -- pruning -----------------------------------------------------------
    def _prune_memory_locked(self, now: float) -> None:
        cutoff = now - self._ttl
        for key in [k for k, ts in self._fallback.items() if ts < cutoff]:
            del self._fallback[key]

    def _prune_sql(self, conn: sqlite3.Connection, now: float) -> None:
        conn.execute("DELETE FROM document_grants WHERE ts < ?", (now - self._ttl,))
        conn.commit()
