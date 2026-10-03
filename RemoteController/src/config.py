"""Environment configuration with fail-fast validation on boot."""
from __future__ import annotations

import ipaddress
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
    rc_local_bypass_trusted_networks: tuple
    rc_platform_broker_pubkey_path: str
    testing: bool
    # Knovas document fields (spec 3.9). RC_DOC_FIELDS can only switch the
    # feature off: whether the server takes the values is read from its echo.
    rc_doc_fields: bool = True
    rc_fields_reupload_per_cycle: int = 100
    rc_fields_reupload_max_attempts: int = 3


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


def _parse_cidrs(key: str) -> tuple:
    """Parse a comma-separated CIDR list into ip_network objects; invalid
    entries are ignored."""
    nets = []
    for part in (os.environ.get(key) or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            continue
    return tuple(nets)


#: RC_DOC_FIELDS spellings. Anything else is refused at boot; at runtime an
#: unreadable value counts as off, the only direction the switch may take.
_DOC_FIELDS_ON = ("on", "true", "1", "yes")
_DOC_FIELDS_OFF = ("off", "false", "0", "no")
FIELDS_REUPLOAD_PER_CYCLE_DEFAULT = 100
FIELDS_REUPLOAD_PER_CYCLE_RANGE = (1, 10000)
FIELDS_REUPLOAD_MAX_ATTEMPTS_DEFAULT = 3
FIELDS_REUPLOAD_MAX_ATTEMPTS_RANGE = (1, 100)


def _doc_fields_switch() -> bool:
    raw = (os.environ.get("RC_DOC_FIELDS") or "").strip().lower()
    if not raw:
        return True
    return raw in _DOC_FIELDS_ON


def _bounded_int(key: str, default: int, bounds: tuple[int, int]) -> int:
    """An integer setting clamped into ``bounds``; unreadable -> default."""
    raw = (os.environ.get(key) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return min(max(value, bounds[0]), bounds[1])


def _doc_fields_config_problems() -> list[str]:
    """Refuse a document-fields setting at boot rather than guess at it."""
    problems: list[str] = []
    raw = (os.environ.get("RC_DOC_FIELDS") or "").strip().lower()
    if raw and raw not in _DOC_FIELDS_ON + _DOC_FIELDS_OFF:
        problems.append("RC_DOC_FIELDS must be on or off")
    for key, bounds in (
        ("RC_FIELDS_REUPLOAD_PER_CYCLE", FIELDS_REUPLOAD_PER_CYCLE_RANGE),
        ("RC_FIELDS_REUPLOAD_MAX_ATTEMPTS", FIELDS_REUPLOAD_MAX_ATTEMPTS_RANGE),
    ):
        raw = (os.environ.get(key) or "").strip()
        if not raw:
            continue
        try:
            value = int(raw)
        except ValueError:
            problems.append(f"{key} must be an integer")
            continue
        if not bounds[0] <= value <= bounds[1]:
            problems.append(f"{key} must be between {bounds[0]} and {bounds[1]}")
    return problems


def reset_config() -> None:
    global _config
    _config = None


def load_config(*, validate: bool = True, force_reload: bool = False) -> AppConfig:
    global _config
    if _config is not None and not force_reload:
        return _config

    missing = _missing_required_env()
    if validate and missing:
        skip_requested = _env_bool("RC_SKIP_CONFIG_VALIDATION", False)
        # The skip is a test-only convenience; outside TESTING it must never
        # suppress missing-secret errors on the production boot path.
        if skip_requested and not _env_bool("TESTING", False):
            print(
                "WARNING: RC_SKIP_CONFIG_VALIDATION is set but TESTING is not; "
                "ignoring it and enforcing config validation.",
                file=sys.stderr,
            )
        if not (skip_requested and _env_bool("TESTING", False)):
            print(
                "Missing required environment variables:",
                ", ".join(missing),
                file=sys.stderr,
            )
            sys.exit(1)

    if validate:
        m365_problems = _m365_config_problems()
        if m365_problems:
            print("Microsoft 365 document source misconfigured:", file=sys.stderr)
            for problem in m365_problems:
                print(f"  - {problem}", file=sys.stderr)
            sys.exit(1)
        doc_fields_problems = _doc_fields_config_problems()
        if doc_fields_problems:
            print("Document fields misconfigured:", file=sys.stderr)
            for problem in doc_fields_problems:
                print(f"  - {problem}", file=sys.stderr)
            sys.exit(1)

    roots_raw = (os.environ.get("RC_WATCH_ROOTS") or "").strip()
    roots = tuple(r.strip() for r in roots_raw.split(",") if r.strip())

    ttl = min(_env_int("KNOVAS_VERIFY_CACHE_TTL_SECONDS", 45), 60)

    _config = AppConfig(
        knovas_internal_api_url=(os.environ.get("KNOVAS_INTERNAL_API_URL") or "").rstrip("/"),
        rc_instance_token=(os.environ.get("RC_INSTANCE_TOKEN") or "").strip(),
        rc_client_id=(os.environ.get("RC_CLIENT_ID") or "").strip(),
        rc_watch_roots=roots,
        semantix_secure_base_url=_semantix_secure_base_url(),
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
        rc_sync_default_window_start=os.environ.get("RC_SYNC_DEFAULT_WINDOW_START", "00:00"),
        rc_sync_default_window_end=os.environ.get("RC_SYNC_DEFAULT_WINDOW_END", "23:59"),
        rc_sync_default_max_ingestion_requests_per_minute=_env_int(
            "RC_SYNC_DEFAULT_MAX_INGESTION_REQUESTS_PER_MINUTE", 30
        ),
        rc_sync_default_burst=_env_int("RC_SYNC_DEFAULT_BURST", 5),
        rc_sync_default_scan_interval_seconds=_env_int(
            "RC_SYNC_DEFAULT_SCAN_INTERVAL_SECONDS", 60
        ),
        rc_internal_local_bypass=_internal_local_bypass_enabled(),
        rc_local_bypass_trusted_networks=_parse_cidrs("RC_LOCAL_BYPASS_TRUSTED_CIDRS"),
        rc_platform_broker_pubkey_path=(os.environ.get("RC_PLATFORM_BROKER_PUBKEY_PATH") or "").strip(),
        testing=_env_bool("TESTING", False),
        rc_doc_fields=_doc_fields_switch(),
        rc_fields_reupload_per_cycle=_bounded_int(
            "RC_FIELDS_REUPLOAD_PER_CYCLE",
            FIELDS_REUPLOAD_PER_CYCLE_DEFAULT,
            FIELDS_REUPLOAD_PER_CYCLE_RANGE,
        ),
        rc_fields_reupload_max_attempts=_bounded_int(
            "RC_FIELDS_REUPLOAD_MAX_ATTEMPTS",
            FIELDS_REUPLOAD_MAX_ATTEMPTS_DEFAULT,
            FIELDS_REUPLOAD_MAX_ATTEMPTS_RANGE,
        ),
    )
    return _config


def _m365_config_problems() -> list[str]:
    """Fail at boot, with the reason, rather than on every sync cycle."""
    folder_url = (os.environ.get("M365_FOLDER_URL") or "").strip()
    if not folder_url:
        return []
    from m365.location import LocationError, default_tenant_for_host, parse_folder_url

    problems: list[str] = []
    hostname = ""
    try:
        hostname = parse_folder_url(folder_url).hostname
    except LocationError as exc:
        problems.append(f"M365_FOLDER_URL: {exc}")
    if not (os.environ.get("M365_CLIENT_ID") or "").strip():
        problems.append("M365_CLIENT_ID is not set")
    if not (os.environ.get("M365_CLIENT_SECRET") or "").strip():
        problems.append("M365_CLIENT_SECRET is not set")
    if hostname and not (
        (os.environ.get("M365_TENANT_ID") or "").strip() or default_tenant_for_host(hostname)
    ):
        problems.append("M365_TENANT_ID is not set and cannot be derived from the address")
    return problems


def _internal_local_bypass_enabled() -> bool:
    return _env_bool("RC_INTERNAL_LOCAL_BYPASS", False) or _env_bool(
        "RC_DISCOVER_LOCAL_BYPASS", False
    )


def _semantix_secure_base_url() -> str:
    return (
        (os.environ.get("SEMANTIX_SECURE_BASE_URL") or os.environ.get("KNOVAS_API_URL") or "")
    ).rstrip("/")


def _required_env_keys() -> tuple[str, ...]:
    skip: set[str] = set()
    if _internal_local_bypass_enabled():
        skip.update({"RC_INSTANCE_TOKEN", "KNOVAS_INTERNAL_API_URL"})
    return tuple(k for k in _REQUIRED_VARS if k not in skip)


def _missing_required_env() -> list[str]:
    required = _required_env_keys()
    missing = [k for k in required if k != "SEMANTIX_SECURE_BASE_URL" and not (os.environ.get(k) or "").strip()]
    if "SEMANTIX_SECURE_BASE_URL" in required and not _semantix_secure_base_url():
        missing.append("SEMANTIX_SECURE_BASE_URL")
    return missing


def get_config() -> AppConfig:
    return load_config(validate=False)


def doc_fields_enabled() -> bool:
    """``RC_DOC_FIELDS`` (default on). Off: no ``fields`` key is ever sent,
    no config digest is computed and the fields columns stay untouched."""
    return bool(get_config().rc_doc_fields)


def fields_reupload_per_cycle() -> int:
    """``RC_FIELDS_REUPLOAD_PER_CYCLE`` (default 100, 1-10000): documents
    re-sent per cycle because only their fields configuration changed."""
    return int(get_config().rc_fields_reupload_per_cycle)


def fields_reupload_max_attempts() -> int:
    """``RC_FIELDS_REUPLOAD_MAX_ATTEMPTS`` (default 3): failed re-uploads
    of one document before it leaves the queue as ``reupload_failed``."""
    return int(get_config().rc_fields_reupload_max_attempts)
