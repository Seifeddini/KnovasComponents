"""The /views page ("Ansichten") and its JSON under /api/views/*.

Blueprint-factory shape of graph_routes: the app passes its collaborators in,
so this module imports nothing from app.py and is tested against a bare Flask
app. Every route is read-only on the brain.

Plan: docs/superpowers/plans/2026-10-27-process-and-spine-views.md
"""
from __future__ import annotations

import functools
import logging
from typing import Any, Callable, Optional

from flask import Blueprint, jsonify, render_template, request

from graph_views import service as views
from graph_views.reader import ReadFailed

logger = logging.getLogger(__name__)

_FIXTURE_MODE_ERROR = "Wissensnetz-Modus erforderlich"


def _flag(name: str) -> bool:
    return str(request.args.get(name, "") or "").strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: Optional[int] = None) -> Optional[int]:
    raw = str(request.args.get(name, "") or "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def create_views_blueprint(gate: Any, service: views.ViewService, *,
                           graph_mode: Callable[[], bool],
                           page_context: Optional[Callable[[], dict]] = None) -> Blueprint:
    """Build the blueprint.

    Args:
        gate: the IdentityGate, or None when identity is off (the app's own
            login gate then guards every path, as for the rest of the UI).
        service: the ViewService holding the per-subject caches.
        graph_mode: True when ONTOLOGY_SOURCE=graph.
        page_context: extra template variables for the page (csrf, brand, ...).
    """
    bp = Blueprint("views_ui", __name__)

    def guarded(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            if gate is not None and gate.current_user() is None:
                return jsonify({"success": False, "error": "Nicht angemeldet."}), 401
            if not graph_mode():
                return jsonify({"success": False, "error": _FIXTURE_MODE_ERROR}), 409
            try:
                return view(*args, **kwargs)
            except PermissionError:
                return jsonify({"success": False, "error": "Nicht angemeldet."}), 401
            except ReadFailed as exc:
                logger.warning("views: brain read failed: %s", exc)
                return jsonify({"success": False,
                                "error": "Der Wissensgraph antwortet gerade nicht. Bitte erneut versuchen."}), 502
        return wrapped

    def ctx():
        return service.context(refresh=_flag("refresh"))

    @bp.route("/views")
    def views_page():
        extra = page_context() if page_context else {}
        return render_template("views.html", graph_mode=graph_mode(), **extra)

    @bp.route("/api/views/graph")
    @guarded
    def graph():
        node = str(request.args.get("node", "") or "").strip()
        if not node:
            return jsonify({"success": False, "error": "Knoten fehlt."}), 400
        body = views.ego_graph(ctx(), node, show_meta=_flag("meta"), threshold=_int("n"))
        return jsonify(body), (200 if body.get("success") else 404)

    @bp.route("/api/views/graph/group")
    @guarded
    def graph_group():
        node = str(request.args.get("node", "") or "").strip()
        relation = str(request.args.get("relation", "") or "").strip()
        direction = str(request.args.get("direction", "") or "").strip()
        type_name = str(request.args.get("type", "") or "").strip()
        if not node or not relation or direction not in ("in", "out") or not type_name:
            return jsonify({"success": False, "error": "Gruppe unvollständig."}), 400
        return jsonify(views.group_members(ctx(), node, relation, direction, type_name,
                                           _int("page", 0) or 0, show_meta=_flag("meta")))

    @bp.route("/api/views/node/<node_id>")
    @guarded
    def node_dossier(node_id: str):
        body = views.dossier(ctx(), node_id, show_meta=_flag("meta"))
        return jsonify(body), (200 if body.get("success") else 404)

    @bp.route("/api/views/search")
    @guarded
    def search():
        return jsonify(views.search(ctx(), str(request.args.get("q", "") or ""),
                                    show_meta=_flag("meta")))

    bp.guarded = guarded
    bp.view_context = ctx
    return bp
