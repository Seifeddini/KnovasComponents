"""GI-RC-02 rate limit decorators for Remote Controller API."""
from __future__ import annotations

from functools import wraps
from typing import Callable

from flask import g, jsonify, request

from config import get_config
from util.rate_limiter import RateLimiter, TokenBucketStrategy

_ip_limiter: RateLimiter | None = None
_handled_limiter: RateLimiter | None = None


def _client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _get_ip_limiter() -> RateLimiter:
    global _ip_limiter
    if _ip_limiter is None:
        cfg = get_config()
        _ip_limiter = RateLimiter(
            TokenBucketStrategy(
                max_tokens=cfg.rc_rate_limit_ip_max_tokens,
                refill_rate=cfg.rc_rate_limit_ip_refill_per_sec,
                fail_open=True,
            )
        )
    return _ip_limiter


def _get_handled_limiter() -> RateLimiter:
    global _handled_limiter
    if _handled_limiter is None:
        cfg = get_config()
        _handled_limiter = RateLimiter(
            TokenBucketStrategy(
                max_tokens=cfg.rc_rate_limit_handled_max_tokens,
                refill_rate=cfg.rc_rate_limit_handled_refill_per_sec,
                fail_open=True,
            )
        )
    return _handled_limiter


def require_rc_ip_rate_limit(func: Callable) -> Callable:
    @wraps(func)
    def wrapper(*args, **kwargs):
        cfg = get_config()
        if cfg.rc_rate_limit_enabled:
            ident = f"rc-ip:{_client_ip()}"
            if not _get_ip_limiter().is_allowed(ident):
                return jsonify({"error": "Rate limit exceeded", "status": "error"}), 429
        return func(*args, **kwargs)

    return wrapper


def require_rc_handled_rate_limit(func: Callable) -> Callable:
    @wraps(func)
    def wrapper(*args, **kwargs):
        cfg = get_config()
        if cfg.rc_rate_limit_enabled:
            emp = getattr(g, "rc_employee_id", None) or _client_ip()
            ident = f"rc-handled:{emp}"
            if not _get_handled_limiter().is_allowed(ident):
                return jsonify({"error": "Rate limit exceeded", "status": "error"}), 429
        return func(*args, **kwargs)

    return wrapper
