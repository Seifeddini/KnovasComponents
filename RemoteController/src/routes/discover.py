from flask import Blueprint, jsonify, request

from auth.knovas_verify_client import require_knovas_verify
from auth.rc_rate_limit import require_rc_handled_rate_limit, require_rc_ip_rate_limit
from discover.filesystem import discover_filesystem
from util.schema import validate

discover_bp = Blueprint("discover", __name__)

_RC_DECORATORS = (
    require_rc_ip_rate_limit,
    require_knovas_verify,
    require_rc_handled_rate_limit,
)


def _apply_decorators(func):
    for dec in reversed(_RC_DECORATORS):
        func = dec(func)
    return func


@discover_bp.route("/discover", methods=["GET"])
@_apply_decorators
def discover():
    max_depth = int(request.args.get("max_depth", 3))
    include_globs = request.args.getlist("include_globs") or None
    exclude_globs = request.args.getlist("exclude_globs") or None
    root = request.args.get("root")

    try:
        body = discover_filesystem(
            root_param=root,
            max_depth=max_depth,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
        )
    except PermissionError as exc:
        return jsonify({"error": str(exc), "status": "error"}), 403

    errors = validate(body, "discover_response.schema.json")
    if errors:
        return jsonify({"error": "Internal schema validation failed", "status": "error"}), 500
    return jsonify(body), 200
