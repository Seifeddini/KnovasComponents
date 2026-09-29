import os
from pathlib import Path

from flask import Blueprint, jsonify

from config import get_config
from m365.source import M365Error, active_m365_source, m365_configured
from sync.sync_scheduler import get_scheduler_status

health_bp = Blueprint("health", __name__)


@health_bp.route("/health", methods=["GET"])
def health():
    cfg = get_config()

    roots_detail = []
    all_roots_ok = bool(cfg.rc_watch_roots)
    m365_check = None
    if m365_configured():
        # The watch root only names the Microsoft 365 folder; nothing is read
        # from it, so an empty or absent directory there is not a fault.
        all_roots_ok = True
        try:
            source = active_m365_source()
            m365_check = source.status() if source is not None else None
        except M365Error:
            m365_check = {"configured": True, "last_error": "configuration"}
    for r in cfg.rc_watch_roots if m365_check is None else ():
        exists = Path(r).exists()
        readable = exists and os.access(r, os.R_OK)
        # Deliberately omit the absolute path: /health is unauthenticated and
        # must not leak host filesystem layout. exists/readable are enough for
        # liveness/readiness probes.
        roots_detail.append({"exists": exists, "readable": readable})
        if not (exists and readable):
            all_roots_ok = False

    try:
        sched = get_scheduler_status()
        scheduler_ok = True
        scheduler_state = sched.get("scheduler_state", "unknown")
    except Exception:
        scheduler_ok = False
        scheduler_state = "error"

    config_ok = bool(cfg.semantix_secure_base_url) and (
        cfg.rc_internal_local_bypass
        or (cfg.knovas_internal_api_url and cfg.rc_instance_token)
    )

    checks = {
        "config": "ok" if config_ok else "degraded",
        "watch_roots": "ok" if all_roots_ok else "degraded",
        "watch_roots_detail": roots_detail,
        "scheduler": "ok" if scheduler_ok else "error",
        "scheduler_state": scheduler_state,
    }

    m365_ok = True
    if m365_check is not None:
        # No error text here: /health is unauthenticated, and Graph messages
        # name sites and folders. ./scripts/doctor.sh prints the reason.
        m365_ok = not m365_check.get("last_error")
        checks["source"] = "m365"
        checks["m365"] = "ok" if m365_ok else "degraded"
        checks["m365_detail"] = {
            "kind": m365_check.get("kind"),
            "last_ok_at": m365_check.get("last_ok_at"),
        }

    healthy = config_ok and all_roots_ok and scheduler_ok and m365_ok
    return jsonify({
        "status": "ok" if healthy else "degraded",
        "service": "remote-controller",
        "checks": checks,
    }), (200 if healthy else 503)
