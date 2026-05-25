"""Environment configuration with fail-fast validation on boot."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Optional


_REQUIRED_VARS = (
    "KNOVAS_INTERNAL_API_URL",
    "RC_INSTANCE_TOKEN",
    "RC_CLIENT_ID",
    "RC_WATCH_ROOTS",
    "SEMANTIX_SECURE_BASE_URL",
    "SEMANTIX_CLIENT_CERT_PATH",
    "SEMANTIX_CLIENT_KEY_PATH",
    "SEMANTIX_CA_CERT_PATH",
)


@dataclass(frozen=True)
class AppConfig:
    knovas_internal_api_url: str
    rc_instance_token: str
    rc_client_id: str
    rc_watch_roots: tuple[str, ...]
    semantix_secure_base_url: str
    semantix_client_cert_path: str
    semantix_client_key_path: str
    semantix_ca_cert_path: str
    rc_api_port: int
    rc_rate_limit_enabled: bool
    rc_rate_limit_ip_max_tokens: int
    rc_rate_limit_ip_refill_per_sec: float
    rc_rate_limit_handled_max_tokens: int
    rc_rate_limit_handled_refill_per_sec: float
    knovas_verify_cache_ttl_seconds: int
    knovas_verify_timeout_seconds: int
    rc_sync_config_path: str
    rc_sync_config_api_enabled: bool
    rc_sync_auto_start_continuous: bool
    rc_sync_auto_start_requires_saved_body: bool
    rc_timezone: str
    rc_sync_state_path: str
    rc_sync_default_mode: str
    rc_sync_default_window_start: str
    rc_sync_default_window_end: str
    rc_sync_default_max_ingestion_requests_per_minute: int
    rc_sync_default_burst: int
    rc_sync_default_scan_interval_seconds: int
    rc_internal_local_bypass: bool
    testing: bool


_config: Optional[AppConfig] = None


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None or not raw.strip():
        return default
    return float(raw)


def reset_config() -> None:
    global _config
    _config = None


def load_config(*, validate: bool = True, force_reload: bool = False) -> AppConfig:
    global _config
    if _config is not None and not force_reload:
        return _config

    required = _required_env_keys()
    missing = [k for k in required if not (os.environ.get(k) or "").strip()]
    if validate and missing and not _env_bool("RC_SKIP_CONFIG_VALIDATION", False):
        print("Missing required environment variables:", ", ".join(missing), file=sys.stderr)
        sys.exit(1)

    roots_raw = (os.environ.get("RC_WATCH_ROOTS") or "").strip()
    roots = tuple(r.strip() for r in roots_raw.split(",") if r.strip())

    ttl = min(_env_int("KNOVAS_VERIFY_CACHE_TTL_SECONDS", 45), 60)

    _config = AppConfig(
        knovas_internal_api_url=(os.environ.get("KNOVAS_INTERNAL_API_URL") or "").rstrip("/"),
        rc_instance_token=(os.environ.get("RC_INSTANCE_TOKEN") or "").strip(),
        rc_client_id=(os.environ.get("RC_CLIENT_ID") or "").strip(),
        rc_watch_roots=roots,
        semantix_secure_base_url=(os.environ.get("SEMANTIX_SECURE_BASE_URL") or "").rstrip("/"),
        semantix_client_cert_path=os.environ.get("SEMANTIX_CLIENT_CERT_PATH", ""),
        semantix_client_key_path=os.environ.get("SEMANTIX_CLIENT_KEY_PATH", ""),
        semantix_ca_cert_path=os.environ.get("SEMANTIX_CA_CERT_PATH", ""),
        rc_api_port=_env_int("RC_API_PORT", 5001),
        rc_rate_limit_enabled=_env_bool("RC_RATE_LIMIT_ENABLED", True),
        rc_rate_limit_ip_max_tokens=_env_int("RC_RATE_LIMIT_IP_MAX_TOKENS", 30),
        rc_rate_limit_ip_refill_per_sec=_env_float("RC_RATE_LIMIT_IP_REFILL_PER_SEC", 0.5),
        rc_rate_limit_handled_max_tokens=_env_int("RC_RATE_LIMIT_HANDLED_MAX_TOKENS", 10),
        rc_rate_limit_handled_refill_per_sec=_env_float(
            "RC_RATE_LIMIT_HANDLED_REFILL_PER_SEC", 0.2
        ),
        knovas_verify_cache_ttl_seconds=ttl,
        knovas_verify_timeout_seconds=_env_int("RC_VERIFY_TIMEOUT_SECONDS", 10),
        rc_sync_config_path=os.environ.get(
            "RC_SYNC_CONFIG_PATH", "config/remote_controller_sync.json"
        ),
        rc_sync_config_api_enabled=_env_bool("RC_SYNC_CONFIG_API_ENABLED", False),
        rc_sync_auto_start_continuous=_env_bool("RC_SYNC_AUTO_START_CONTINUOUS", False),
        rc_sync_auto_start_requires_saved_body=_env_bool(
            "RC_SYNC_AUTO_START_REQUIRES_SAVED_BODY", True
        ),
        rc_timezone=(os.environ.get("RC_TIMEZONE") or "").strip(),
        rc_sync_state_path=os.environ.get("RC_SYNC_STATE_PATH", ".rc-sync-state.json"),
        rc_sync_default_mode=os.environ.get("RC_SYNC_DEFAULT_MODE", "continuous"),
        rc_sync_default_window_start=os.environ.get("RC_SYNC_DEFAULT_WINDOW_START", "08:00"),
        rc_sync_default_window_end=os.environ.get("RC_SYNC_DEFAULT_WINDOW_END", "20:00"),
        rc_sync_default_max_ingestion_requests_per_minute=_env_int(
            "RC_SYNC_DEFAULT_MAX_INGESTION_REQUESTS_PER_MINUTE", 30
        ),
        rc_sync_default_burst=_env_int("RC_SYNC_DEFAULT_BURST", 5),
        rc_sync_default_scan_interval_seconds=_env_int(
            "RC_SYNC_DEFAULT_SCAN_INTERVAL_SECONDS", 60
        ),
        rc_internal_local_bypass=_internal_local_bypass_enabled(),
        testing=_env_bool("TESTING", False),
    )
    return _config


def _internal_local_bypass_enabled() -> bool:
    return _env_bool("RC_INTERNAL_LOCAL_BYPASS", False) or _env_bool(
        "RC_DISCOVER_LOCAL_BYPASS", False
    )


def _required_env_keys() -> tuple[str, ...]:
    if _internal_local_bypass_enabled():
        return tuple(k for k in _REQUIRED_VARS if k != "RC_INSTANCE_TOKEN")
    return _REQUIRED_VARS


def get_config() -> AppConfig:
    return load_config(validate=False)
