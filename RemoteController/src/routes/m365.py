"""Microsoft 365 routes: the embeddable preview the Platform shows in its dialog."""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from auth.knovas_verify_client import require_operator_or_tenant_user, require_same_origin
from m365.source import M365Error, active_m365_source

logger = logging.getLogger(__name__)

m365_bp = Blueprint("m365", __name__)


@m365_bp.route("/m365/preview", methods=["POST"])
@require_same_origin
@require_operator_or_tenant_user
def m365_preview():
    """``{"doc_id": "<identifier>", "page": 3}`` -> a short-lived viewer URL.

    Deliberately not behind the handled-request limiter: the Platform calls
    this once per opened preview for every person of the firm, and paging
    through a result list is exactly that. Microsoft Graph throttles on its
    side; a 429 there comes back as a 503 here and the Platform falls back to
    the indexed text.
    """
    try:
        source = active_m365_source()
    except M365Error as exc:
        return jsonify({"error": f"Microsoft 365: {exc}", "status": "error"}), 503
    if source is None:
        return jsonify({"error": "No Microsoft 365 folder is configured", "status": "error"}), 404
    body = request.get_json(silent=True) or {}
    doc_id = str(body.get("doc_id") or "").strip()
    if not doc_id:
        return jsonify({"error": "doc_id is required", "status": "error"}), 400
    page = body.get("page")
    try:
        page_number = int(page) if page not in (None, "") else None
    except (TypeError, ValueError):
        page_number = None
    try:
        preview = source.preview(doc_id, page=page_number)
    except KeyError:
        return jsonify({"error": "Unknown document", "status": "error"}), 404
    except M365Error as exc:
        logger.warning("Microsoft 365 preview for %s failed: %s", doc_id, exc)
        return jsonify({"error": str(exc), "status": "error"}), 503
    return jsonify({"status": "ok", **preview}), 200
