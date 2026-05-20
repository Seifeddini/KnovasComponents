from flask import Blueprint, jsonify, request

from auth.knovas_verify_client import require_knovas_verify
from auth.mtls import require_rc_mtls
from auth.rc_rate_limit import require_rc_handled_rate_limit, require_rc_ip_rate_limit
from sync.sync_config import load_sync_config
from sync.sync_scheduler import (
    SyncRunContext,
    get_scheduler_status,
    load_last_sync_body,
    save_last_sync_body,
    start_continuous,
    stop_continuous,
)
from util.schema import validate

sync_control_bp = Blueprint("sync_control", __name__)

_RC_DECORATORS = (
    require_rc_ip_rate_limit,
    require_rc_mtls,
    require_knovas_verify,
    require_rc_handled_rate_limit,
)


def _apply_decorators(func):
    for dec in reversed(_RC_DECORATORS):
        func = dec(func)
    return func


@sync_control_bp.route("/sync/start", methods=["POST"])
@_apply_decorators
def sync_start():
    body = request.get_json(silent=True) if request.is_json else None
    if body is None:
        body = load_last_sync_body()
    if not body:
        return jsonify({"error": "No sync body available", "status": "error"}), 400

    errors = validate(body, "sync_request.schema.json")
    if errors:
        return jsonify({"error": errors[0], "status": "error"}), 400

    save_last_sync_body(body)
    sync_cfg = load_sync_config()
    status = start_continuous(SyncRunContext(sync_body=body, sync_config=sync_cfg))
    return jsonify({"scheduler_status": status, "status": status}), 200


@sync_control_bp.route("/sync/stop", methods=["POST"])
@_apply_decorators
def sync_stop():
    status = stop_continuous()
    return jsonify({"scheduler_status": status, "status": status}), 200


@sync_control_bp.route("/sync/status", methods=["GET"])
@_apply_decorators
def sync_status():
    return jsonify(get_scheduler_status()), 200
