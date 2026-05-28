from flask import Blueprint, jsonify, request

from auth.knovas_verify_client import require_internal_access
from auth.rc_rate_limit import require_rc_handled_rate_limit, require_rc_ip_rate_limit
from sync.sync_config import load_sync_config
from sync.sync_state import SyncStateStore
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
    require_internal_access,
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
    status = get_scheduler_status()
    if request.args.get("live") == "1":
        body = load_last_sync_body()
        if body:
            if request.args.get("deep_scan") == "1":
                from sync.sync_executor import scan_document_inventory

                status["document_sync"] = scan_document_inventory(
                    body, sync_config=load_sync_config()
                ).as_dict()
            else:
                store = SyncStateStore()
                try:
                    tracked = store.count_tracked_paths()
                finally:
                    store.close()
                last = status.get("document_sync") or {}
                status["document_sync"] = {
                    "total": last.get("total"),
                    "synced": tracked,
                    "pending": last.get("pending"),
                    "modified": last.get("modified"),
                    "excluded_max_age": last.get("excluded_max_age"),
                    "live_tracked_paths": tracked,
                    "deep_scan_required_for_full_inventory": True,
                }
    return jsonify(status), 200
