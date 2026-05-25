from flask import Blueprint, jsonify, request

from auth.knovas_verify_client import require_internal_access
from auth.rc_rate_limit import require_rc_handled_rate_limit, require_rc_ip_rate_limit
from sync.sync_config import load_sync_config
from sync.sync_executor import SyncRunResult
from sync.sync_scheduler import (
    SyncRunContext,
    run_one_time,
    save_last_sync_body,
    start_continuous,
)
from util.schema import validate

sync_bp = Blueprint("sync", __name__)

_RC_DECORATORS = (
    require_rc_ip_rate_limit,
    require_internal_access,
    require_rc_handled_rate_limit,
)


def _apply_decorators(func):
    for dec in reversed(_RC_DECORATORS):
        func = dec(func)
    return func


def _build_sync_response(scheduler_status: str, result) -> dict:
    status = "completed" if scheduler_status == "completed" else scheduler_status
    response: dict = {
        "status": status,
        "scheduler_status": scheduler_status,
        "files_scanned": result.files_scanned,
        "files_uploaded": result.files_uploaded,
        "files_skipped": result.files_skipped,
        "ingestion_requests_sent": result.ingestion_requests_sent,
        "paused_reason": result.paused_reason,
        "transmissions": result.transmissions,
        "errors": result.errors,
    }
    if result.document_sync is not None:
        response["document_sync"] = result.document_sync.as_dict()
    return response


@sync_bp.route("/sync", methods=["POST"])
@_apply_decorators
def sync():
    if not request.is_json:
        return jsonify({"error": "Request body must be JSON", "status": "error"}), 400
    body = request.get_json(silent=True)
    if body is None or not isinstance(body, dict):
        return jsonify({"error": "JSON object required", "status": "error"}), 400

    errors = validate(body, "sync_request.schema.json")
    if errors:
        return jsonify({"error": errors[0], "status": "error"}), 400

    save_last_sync_body(body)
    sync_cfg = load_sync_config()

    if sync_cfg.get("mode") == "continuous":
        status = start_continuous(SyncRunContext(sync_body=body, sync_config=sync_cfg))
        empty = SyncRunResult()
        response = _build_sync_response(status, empty)
        return jsonify(response), 200

    scheduler_status, result = run_one_time(SyncRunContext(sync_body=body, sync_config=sync_cfg))
    response = _build_sync_response(scheduler_status, result)
    val_errors = validate(response, "sync_response.schema.json")
    if val_errors:
        return jsonify({"error": "Internal schema validation failed", "status": "error"}), 500
    return jsonify(response), 200
