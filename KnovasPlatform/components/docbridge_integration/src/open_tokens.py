"""
Short-lived signed open tokens for companion UNC redeem.

Single-use (``jti``) replay prevention is backed by a shared, process-safe
SQLite store so it holds across the default multiple Gunicorn workers (each a
separate process). When no usable store path is available the manager degrades
to an in-process cache (single-process single-use only).
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import Any, Dict, Optional

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired


class OpenTokenManager:
    """
    Mints signed tokens binding doc_id + relative autodoc path.
    Redeem validates signature, TTL, and single-use across workers.

    The single-use ``jti`` is recorded in a SQLite table with a UNIQUE primary
    key; a concurrent second redeem (same or different worker) hits an
    IntegrityError and is rejected as a replay. TTL-expired rows are pruned.
    """

    def __init__(
        self,
        secret: str,
        salt: str = "docbridge-open-unc-v1",
        max_age_seconds: int = 120,
        store_path: Optional[str] = None,
    ):
        self._serializer = URLSafeTimedSerializer(secret, salt=salt)
        self._max_age = max_age_seconds
        self._lock = threading.Lock()
        self._used_jti_ttl = float(max_age_seconds)
        self._store_path: Optional[str] = store_path
        self._fallback_used: Dict[str, float] = {}
        self._backend = "memory"
        self._init_store()

    # -- store setup -------------------------------------------------------
    @staticmethod
    def _looks_unusable(path: str) -> bool:
        """
        A POSIX-absolute path on Windows (e.g. ``/app/data/...``) is a container
        path; don't create stray drive-root directories on a dev Windows host —
        degrade to the in-process cache instead.
        """
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
                    "CREATE TABLE IF NOT EXISTS used_jti "
                    "(jti TEXT PRIMARY KEY, ts REAL NOT NULL)"
                )
                conn.commit()
            finally:
                conn.close()
            self._backend = "sqlite"
        except (sqlite3.Error, OSError):
            # Cannot use the shared store (unwritable path): degrade gracefully.
            self._store_path = None
            self._backend = "memory"

    # -- public API --------------------------------------------------------
    def mint(self, rel_path: str, doc_id: str) -> str:
        import secrets

        jti = secrets.token_urlsafe(16)
        payload: Dict[str, Any] = {"rel": rel_path, "doc": doc_id, "jti": jti}
        return self._serializer.dumps(payload)

    def verify_and_consume(self, token: str, consume: bool = True) -> Optional[Dict[str, str]]:
        """
        Returns {'rel': str, 'doc': str} on success, None on failure.
        If consume=True, records jti to reject replay (across all workers sharing
        the store).
        """
        try:
            data = self._serializer.loads(token, max_age=self._max_age)
        except (BadSignature, SignatureExpired):
            return None

        rel = (data.get("rel") or "").strip()
        doc = (data.get("doc") or "").strip()
        jti = (data.get("jti") or "").strip()
        if not rel or not doc or not jti:
            return None

        if consume:
            if not self._consume_jti(jti):
                return None
        elif self._jti_seen(jti):
            return None

        return {"rel": rel, "doc": doc}

    # -- jti store ---------------------------------------------------------
    def _consume_jti(self, jti: str) -> bool:
        """Record jti as used. Returns True if newly recorded, False on replay."""
        now = time.time()
        if self._store_path is None:
            with self._lock:
                self._prune_memory_locked(now)
                if jti in self._fallback_used:
                    return False
                self._fallback_used[jti] = now
                return True
        with self._lock:
            conn = self._connect()
            try:
                self._prune_sql(conn, now)
                try:
                    conn.execute(
                        "INSERT INTO used_jti (jti, ts) VALUES (?, ?)", (jti, now)
                    )
                    conn.commit()
                    return True
                except sqlite3.IntegrityError:
                    return False
            finally:
                conn.close()

    def _jti_seen(self, jti: str) -> bool:
        now = time.time()
        if self._store_path is None:
            with self._lock:
                self._prune_memory_locked(now)
                return jti in self._fallback_used
        with self._lock:
            conn = self._connect()
            try:
                self._prune_sql(conn, now)
                row = conn.execute(
                    "SELECT 1 FROM used_jti WHERE jti = ?", (jti,)
                ).fetchone()
                return row is not None
            finally:
                conn.close()

    def _prune_memory_locked(self, now: float) -> None:
        cutoff = now - self._used_jti_ttl
        dead = [k for k, t in self._fallback_used.items() if t < cutoff]
        for k in dead:
            del self._fallback_used[k]

    def _prune_sql(self, conn: sqlite3.Connection, now: float) -> None:
        cutoff = now - self._used_jti_ttl
        conn.execute("DELETE FROM used_jti WHERE ts < ?", (cutoff,))
        conn.commit()
