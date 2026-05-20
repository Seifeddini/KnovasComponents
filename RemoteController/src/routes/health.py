import os
from pathlib import Path

from flask import Blueprint, jsonify

from config import get_config
from sync.sync_scheduler import get_scheduler_status

health_bp = Blueprint("health", __name__)


@health_bp.route("/health", methods=["GET"])
def health():
    cfg = get_config()

    roots_detail = []
    all_roots_ok = bool(cfg.rc_watch_roots)
    for r in cfg.rc_watch_roots:
        exists = Path(r).exists()
        readable = exists and os.access(r, os.R_OK)
        roots_detail.append({"path": r, "exists": exists, "readable": readable})
        if not (exists and readable):
            all_roots_ok = False

    try:
        sched = get_scheduler_status()
        scheduler_ok = True
        scheduler_state = sched.get("scheduler_state", "unknown")
    except Exception:
        scheduler_ok = False
        scheduler_state = "error"

    config_ok = bool(
        cfg.knovas_internal_api_url
        and cfg.rc_instance_token
        and cfg.semantix_secure_base_url
    )

    checks = {
        "config": "ok" if config_ok else "degraded",
        "watch_roots": "ok" if all_roots_ok else "degraded",
        "watch_roots_detail": roots_detail,
        "scheduler": "ok" if scheduler_ok else "error",
        "scheduler_state": scheduler_state,
    }

    healthy = config_ok and all_roots_ok and scheduler_ok
    return jsonify({
        "status": "ok" if healthy else "degraded",
        "service": "remote-controller",
        "checks": checks,
    }), (200 if healthy else 503)
