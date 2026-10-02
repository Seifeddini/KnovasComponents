from flask import Blueprint, jsonify, request

from auth.knovas_verify_client import require_operator_or_tenant_admin, require_same_origin
from auth.rc_rate_limit import require_rc_handled_rate_limit, require_rc_ip_rate_limit
from sync.sync_config import load_sync_config
from sync.sync_state import SyncStateStore
from sync.sync_state_db import REQUEUE_OUTCOMES
from sync.sync_scheduler import (
    RC_CAPABILITIES,
    SyncRunContext,
    doc_fields_status,
    get_scheduler_status,
    load_last_sync_body,
    request_cycle_now,
    requeue_doc_fields,
    save_last_sync_body,
    start_continuous,
    stop_continuous,
)
from util.schema import validate

sync_control_bp = Blueprint("sync_control", __name__)

_RC_DECORATORS = (
    require_rc_ip_rate_limit,
    require_same_origin,
    require_operator_or_tenant_admin,
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
    if not body:
        # No body, or an empty {} -- the Platform console starts with what it
        # stored via POST /sync/body; an employee may do the same after POST /sync.
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
    # What this RemoteController understands in a sync body (the Platform
    # refuses to push profile keys an older RC would answer 400 to), and the
    # Knovas document-fields state: keys, codes and counts, never values.
    status["capabilities"] = list(RC_CAPABILITIES)
    status["doc_fields"] = doc_fields_status()
    if request.args.get("live") == "1":
        body = load_last_sync_body()
        if body:
            if request.args.get("deep_scan") == "1":
                from sync.sync_executor import scan_document_inventory, _max_scan_entries_per_cycle

                from m365.source import M365Error

                sync_cfg = load_sync_config()
                max_scan = _max_scan_entries_per_cycle(sync_cfg) or 5000
                try:
                    status["document_sync"] = scan_document_inventory(
                        body, sync_config=sync_cfg, max_scan_entries=max_scan
                    ).as_dict()
                except M365Error as exc:
                    status["document_sync_error"] = str(exc)
                else:
                    if max_scan > 0:
                        status["document_sync"]["scan_capped_at"] = max_scan
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
                    "fields_changed": last.get("fields_changed"),
                    "live_tracked_paths": tracked,
                    "deep_scan_required_for_full_inventory": True,
                }
    return jsonify(status), 200


@sync_control_bp.route("/sync/doc-fields/requeue", methods=["POST"])
@_apply_decorators
def sync_doc_fields_requeue():
    """Re-send documents whose fields the server did not take.

    ``{"outcome": "not_accepted" | "refused" | "reupload_failed" | "all"}``:
    their stored digest is cleared, so the next cycles find them
    ``fields_changed`` and re-upload them within RC_FIELDS_REUPLOAD_PER_CYCLE.
    Answers ``{"requeued": n}``. Each re-upload is a full, billed upload.
    """
    body = request.get_json(silent=True) if request.is_json else None
    outcome = body.get("outcome") if isinstance(body, dict) else None
    if outcome not in REQUEUE_OUTCOMES:
        return jsonify({
            "error": "outcome must be one of: " + ", ".join(REQUEUE_OUTCOMES),
            "status": "error",
        }), 400
    requeued = requeue_doc_fields(outcome)
    if requeued:
        # A running worker picks them up now rather than after its idle wait.
        request_cycle_now()
    return jsonify({"requeued": requeued}), 200
