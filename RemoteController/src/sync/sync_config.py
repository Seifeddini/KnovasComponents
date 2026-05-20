"""Load, validate, and persist remote_controller_sync.json."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

from config import get_config
from util.schema import validate

logger = logging.getLogger(__name__)

SCHEMA_FILE = "remote_controller_sync_config.schema.json"
FORBIDDEN_KEYS = {
    "rc_instance_token",
    "semantix_client_cert_path",
    "semantix_client_key_path",
    "semantix_ca_cert_path",
}


def seed_from_env() -> dict[str, Any]:
    cfg = get_config()
    doc: dict[str, Any] = {
        "schema_version": 1,
        "enabled": True,
        "mode": cfg.rc_sync_default_mode,
        "window": {
            "start_local": cfg.rc_sync_default_window_start,
            "end_local": cfg.rc_sync_default_window_end,
        },
        "rate_limit": {
            "max_ingestion_requests_per_minute": cfg.rc_sync_default_max_ingestion_requests_per_minute,
            "burst": cfg.rc_sync_default_burst,
        },
        "pause_policy": "finish_current_unit_then_pause",
    }
    if cfg.rc_sync_default_mode == "continuous":
        doc["scan_interval_seconds"] = cfg.rc_sync_default_scan_interval_seconds
    return doc


def _extra_validate(doc: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if doc.get("mode") == "continuous":
        interval = doc.get("scan_interval_seconds")
        if interval is None or int(interval) < 5:
            errors.append("scan_interval_seconds must be >= 5 for continuous mode")
    rl = doc.get("rate_limit") or {}
    if int(rl.get("max_ingestion_requests_per_minute", 0)) < 1:
        errors.append("max_ingestion_requests_per_minute must be >= 1")
    if int(rl.get("burst", 0)) < 1:
        errors.append("burst must be >= 1")
    return errors


def validate_sync_config(doc: dict[str, Any]) -> list[str]:
    for key in doc:
        if key.lower() in FORBIDDEN_KEYS:
            return [f"Forbidden config key: {key}"]
    errors = validate(doc, SCHEMA_FILE)
    errors.extend(_extra_validate(doc))
    return errors


def load_sync_config(path: Optional[str] = None) -> dict[str, Any]:
    cfg = get_config()
    p = Path(path or cfg.rc_sync_config_path)
    if not p.exists():
        doc = seed_from_env()
        save_sync_config(doc, path=str(p))
        return doc
    doc = json.loads(p.read_text(encoding="utf-8"))
    errors = validate_sync_config(doc)
    if errors:
        raise ValueError(f"Invalid sync config: {'; '.join(errors)}")
    return doc


def save_sync_config(doc: dict[str, Any], path: Optional[str] = None) -> None:
    errors = validate_sync_config(doc)
    if errors:
        raise ValueError(f"Invalid sync config: {'; '.join(errors)}")
    p = Path(path or get_config().rc_sync_config_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2)
        os.replace(tmp, p)
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def config_snapshot_hash(doc: dict[str, Any]) -> str:
    payload = json.dumps(doc, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
