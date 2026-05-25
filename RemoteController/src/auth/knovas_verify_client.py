"""POST /remote_controller/verify_operator with short TTL cache."""
from __future__ import annotations

import base64
import json
import threading
import time
from functools import wraps
from typing import Any, Optional

import requests
from flask import g, jsonify, request

from auth.jwt_identity import employee_id_from_jwt_token
from config import get_config

_cache: dict[tuple[str, str], tuple[float, str]] = {}
_cache_lock = threading.Lock()


def _extract_jti(jwt_token: str) -> str:
    try:
        parts = jwt_token.split(".")
        if len(parts) < 2:
            return ""
        payload = parts[1]
        padding = "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload + padding))
        return str(data.get("jti") or "")
    except Exception:
        return ""


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

        jti = _extract_jti(jwt_token)
        cache_key = (employee_id, jti)
        cached = _cache_get(cache_key, self._ttl)
        if cached:
            return True, cached, None

        url = f"{self._base_url}/remote_controller/verify_operator"
        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "X-RC-Instance-Token": self._instance_token,
            "Content-Type": "application/json",
        }
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


def discover_local_bypass_enabled() -> bool:
    return get_config().rc_discover_local_bypass


def _apply_local_discover_context() -> None:
    """Local /discover only: skip Knovas verify_operator (no RC_INSTANCE_TOKEN)."""
    cfg = get_config()
    g.rc_client_id = cfg.rc_client_id
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        jwt_token = auth[7:].strip()
        if jwt_token:
            employee_id = employee_id_from_jwt_token(jwt_token)
            if employee_id:
                g.rc_employee_id = employee_id


def require_discover_access(func):
    """Production: full Knovas verify. Local bypass: no instance token or JWT required."""
    verified = require_knovas_verify(func)

    @wraps(func)
    def wrapper(*args, **kwargs):
        if discover_local_bypass_enabled():
            _apply_local_discover_context()
            return func(*args, **kwargs)
        return verified(*args, **kwargs)

    return wrapper


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
