"""POST /remote_controller/verify_operator with short TTL cache."""
from __future__ import annotations

import hashlib
import ipaddress
import threading
import time
from functools import wraps
from typing import Optional
from urllib.parse import urlparse

import requests
from flask import g, jsonify, request

from auth.jwt_identity import employee_id_from_jwt_token
from config import get_config

_cache: dict[tuple[str, str], tuple[float, str]] = {}
_cache_lock = threading.Lock()


def _token_fingerprint(jwt_token: str) -> str:
    """SHA-256 of the exact token bytes: only a byte-identical, already
    Knovas-verified token can reuse a cache entry."""
    return hashlib.sha256(jwt_token.encode()).hexdigest()


def _cache_get(key: tuple[str, str], ttl: float) -> Optional[str]:
    now = time.monotonic()
    with _cache_lock:
        entry = _cache.get(key)
        if not entry:
            return None
        expires, client_id = entry
        if now >= expires:
            del _cache[key]
            return None
        return client_id


def _cache_set(key: tuple[str, str], client_id: str, ttl: float) -> None:
    with _cache_lock:
        _cache[key] = (time.monotonic() + ttl, client_id)


class KnovasVerifyClient:
    def __init__(self):
        cfg = get_config()
        self._base_url = cfg.knovas_internal_api_url
        self._instance_token = cfg.rc_instance_token
        self._timeout = cfg.knovas_verify_timeout_seconds
        self._ttl = float(cfg.knovas_verify_cache_ttl_seconds)

    def verify_operator(self, jwt_token: str, employee_id: str) -> tuple[bool, Optional[str], Optional[tuple]]:
        if not self._instance_token:
            return (
                False,
                None,
                ({"error": "RC instance token is not configured", "status": "error"}, 500),
            )

        cache_key = (employee_id, _token_fingerprint(jwt_token))
        cached = _cache_get(cache_key, self._ttl)
        if cached:
            return True, cached, None

        url = f"{self._base_url}/remote_controller/verify_operator"
        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "X-RC-Instance-Token": self._instance_token,
            "Content-Type": "application/json",
        }
        # SECURITY CONTRACT (verify Knovas-side): `employee_id` here is derived
        # from the UNVERIFIED token payload and is sent only as a hint. The Knovas
        # `/remote_controller/verify_operator` endpoint MUST authorize off the
        # cryptographically-verified token subject, NOT this body field — otherwise
        # a valid low-privilege token could claim a higher-privileged employee_id
        # (CWE-639). Do not let RC's cache/hint become the authorization source.
        payload = {"employee_id": employee_id}

        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=self._timeout)
        except requests.RequestException:
            return (
                False,
                None,
                (
                    {"error": "Remote operator verification unavailable", "status": "error"},
                    503,
                ),
            )

        if resp.status_code == 200:
            data = resp.json() if resp.content else {}
            if isinstance(data, dict) and data.get("authorized"):
                client_id = str(data.get("client_id") or "")
                if client_id:
                    _cache_set(cache_key, client_id, self._ttl)
                    return True, client_id, None
            return (
                False,
                None,
                ({"error": "Operator not authorized", "status": "error"}, 403),
            )

        try:
            body = resp.json()
        except ValueError:
            body = {"error": resp.text or "Verification failed", "status": "error"}

        if resp.status_code == 429:
            return False, None, (body, 429)
        if resp.status_code in (401, 403):
            return False, None, (body, resp.status_code)
        return (
            False,
            None,
            (body if isinstance(body, dict) else {"error": "Verification failed"}, resp.status_code),
        )


_verify_client: Optional[KnovasVerifyClient] = None


def get_verify_client() -> KnovasVerifyClient:
    global _verify_client
    if _verify_client is None:
        _verify_client = KnovasVerifyClient()
    return _verify_client


def internal_local_bypass_enabled() -> bool:
    return get_config().rc_internal_local_bypass


def _is_loopback_addr(addr: Optional[str]) -> bool:
    """True only for 127.0.0.0/8 or ::1 — used to keep 'local bypass' local
    even though gunicorn binds 0.0.0.0."""
    if not addr:
        return False
    try:
        return ipaddress.ip_address(addr.strip()).is_loopback
    except ValueError:
        return False


_STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _origin_matches_host() -> bool:
    """Cross-origin / DNS-rebind guard: an Origin/Referer, when present, must
    resolve to the same host the request was addressed to."""
    source = request.headers.get("Origin") or request.headers.get("Referer")
    if not source:
        return True
    try:
        source_host = urlparse(source).netloc
    except ValueError:
        return False
    return bool(source_host) and source_host == request.host


def require_same_origin(func):
    """Reject cross-origin state-changing requests and require JSON bodies on
    state-changing methods (CSRF / DNS-rebind defense for localhost routes)."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        if not _origin_matches_host():
            return jsonify({"error": "Cross-origin request rejected", "status": "error"}), 403
        if request.method in _STATE_CHANGING_METHODS and not request.is_json:
            return jsonify({"error": "Request body must be JSON", "status": "error"}), 400
        return func(*args, **kwargs)

    return wrapper


def _apply_internal_local_context() -> None:
    """Internal LAN: skip Knovas verify_operator (no RC_INSTANCE_TOKEN or JWT)."""
    cfg = get_config()
    g.rc_client_id = cfg.rc_client_id
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        jwt_token = auth[7:].strip()
        if jwt_token:
            employee_id = employee_id_from_jwt_token(jwt_token)
            if employee_id:
                g.rc_employee_id = employee_id


def require_internal_access(func):
    """Production: full Knovas verify. Internal bypass: no instance token or JWT required."""
    verified = require_knovas_verify(func)

    @wraps(func)
    def wrapper(*args, **kwargs):
        if internal_local_bypass_enabled():
            if not _is_loopback_addr(request.remote_addr):
                return (
                    jsonify(
                        {
                            "error": "Local bypass is permitted only from loopback",
                            "status": "error",
                        }
                    ),
                    403,
                )
            _apply_internal_local_context()
            return func(*args, **kwargs)
        return verified(*args, **kwargs)

    return wrapper


# Backward-compatible alias
require_discover_access = require_internal_access


def require_knovas_verify(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return jsonify({"error": "Authorization Bearer token required", "status": "error"}), 401
        jwt_token = auth[7:].strip()
        if not jwt_token:
            return jsonify({"error": "Authorization Bearer token required", "status": "error"}), 401

        employee_id = employee_id_from_jwt_token(jwt_token)
        if not employee_id:
            return (
                jsonify(
                    {
                        "error": "Bearer token must contain a valid operator UUID claim",
                        "status": "error",
                    }
                ),
                401,
            )

        ok, client_id, err = get_verify_client().verify_operator(jwt_token, employee_id)
        if not ok:
            body, status = err or ({"error": "Not authorized", "status": "error"}, 403)
            return jsonify(body), status
        g.rc_employee_id = employee_id
        g.rc_client_id = client_id
        return func(*args, **kwargs)

    return wrapper
