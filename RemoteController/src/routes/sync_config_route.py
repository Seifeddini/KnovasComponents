from flask import Blueprint, jsonify, request

from auth.knovas_verify_client import require_knovas_verify
from auth.mtls import require_rc_mtls
from auth.rc_rate_limit import require_rc_handled_rate_limit, require_rc_ip_rate_limit
from config import get_config
from sync.sync_config import load_sync_config, save_sync_config, validate_sync_config

sync_config_bp = Blueprint("sync_config", __name__)

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


def _redact(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k not in ("rc_instance_token",)}


@sync_config_bp.route("/sync/config", methods=["GET", "POST"])
@_apply_decorators
def sync_config_handler():
    if not get_config().rc_sync_config_api_enabled:
        return jsonify({"error": "Sync config API is disabled", "status": "error"}), 404

    if request.method == "GET":
        doc = load_sync_config()
        return jsonify(_redact(doc)), 200

    if not request.is_json:
        return jsonify({"error": "Request body must be JSON", "status": "error"}), 400
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "JSON object required", "status": "error"}), 400

    errors = validate_sync_config(body)
    if errors:
        return jsonify({"error": errors[0], "status": "error"}), 400

    save_sync_config(body)
    return jsonify(_redact(body)), 200
