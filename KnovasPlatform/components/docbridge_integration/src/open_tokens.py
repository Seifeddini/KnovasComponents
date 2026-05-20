"""
Short-lived signed open tokens for companion UNC redeem (single-process replay cache).
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional, Set

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired


class OpenTokenManager:
    """
    Mints signed tokens binding doc_id + relative autodoc path.
    Redeem validates signature, TTL, optional single-use (in-process jti store).

    Note: jti replay prevention is per-process only. For multiple Gunicorn workers,
    use sticky sessions to the same worker or replace with shared store (Redis).
    """

    def __init__(self, secret: str, salt: str = "docbridge-open-unc-v1", max_age_seconds: int = 120):
        self._serializer = URLSafeTimedSerializer(secret, salt=salt)
        self._max_age = max_age_seconds
        self._lock = threading.Lock()
        self._used_jti: Dict[str, float] = {}
        self._used_jti_ttl = float(max_age_seconds)

    def mint(self, rel_path: str, doc_id: str) -> str:
        import secrets

        jti = secrets.token_urlsafe(16)
        payload: Dict[str, Any] = {"rel": rel_path, "doc": doc_id, "jti": jti}
        return self._serializer.dumps(payload)

    def verify_and_consume(self, token: str, consume: bool = True) -> Optional[Dict[str, str]]:
        """
        Returns {'rel': str, 'doc': str} on success, None on failure.
        If consume=True, records jti to reject immediate replay (same process).
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

        now = time.monotonic()
        with self._lock:
            self._prune_locked(now)
            if jti in self._used_jti:
                return None
            if consume:
                self._used_jti[jti] = now

        return {"rel": rel, "doc": doc}

    def _prune_locked(self, now: float) -> None:
        cutoff = now - self._used_jti_ttl
        dead = [k for k, t in self._used_jti.items() if t < cutoff]
        for k in dead:
            del self._used_jti[k]
