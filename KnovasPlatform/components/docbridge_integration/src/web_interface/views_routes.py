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
                           page_context: Optional[Callable[[], dict]] = None,
                           view_store: Optional[Callable[[], Any]] = None) -> Blueprint:
    """Build the blueprint.

    Args:
        gate: the IdentityGate, or None when identity is off (the app's own
            login gate then guards every path, as for the rest of the UI).
        service: the ViewService holding the per-subject caches.
        graph_mode: True when ONTOLOGY_SOURCE=graph.
        page_context: extra template variables for the page (csrf, brand, ...).
        view_store: zero-arg callable -> SavedViewStore over this request's
            connection; None when identity is off (no saved views then).
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

    def is_admin() -> bool:
        # Identity off: the one shared login is the operator of this deployment.
        if gate is None:
            return True
        user = gate.current_user()
        return user is not None and "admin" in user.roles

    @bp.route("/api/views/definitions")
    @guarded
    def definitions():
        context = ctx()
        d = service.definitions(context, refresh=_flag("refresh"))
        body = context.envelope(
            source=d.source, processes=d.processes, spines=d.spines, rules=d.rules,
            pinned={name: {k: v for k, v in pin.items() if k in ("commit", "note")}
                    for name, pin in (d.pinned.get("files") or {}).items()})
        if is_admin():
            # Validation errors, gaps and differences are for the people who can
            # fix the ontology; everyone else sees the definitions themselves.
            body.update(errors=d.errors, gaps=d.gaps, differences=d.differences)
        return jsonify(body)

    from graph_views import process_views

    def _arg(name: str) -> str:
        return str(request.args.get(name, "") or "").strip()

    def _process_context():
        context = ctx()
        return context, service.definitions(context)

    def _placeable(fn):
        try:
            return fn()
        except process_views.NotPlaceable as exc:
            return jsonify({"success": False, "error": str(exc)}), 404

    @bp.route("/api/views/instances")
    @guarded
    def process_instances():
        context, d = _process_context()
        rows = process_views.search_instances(context, d, _arg("process"), _arg("q"))
        return jsonify(context.envelope(results=rows))

    @bp.route("/api/views/trace")
    @guarded
    def process_trace():
        context, d = _process_context()
        instance = _arg("instance")
        if not _arg("process") or not instance:
            return jsonify({"success": False, "error": "Prozess und Instanz angeben."}), 400
        return _placeable(lambda: jsonify(process_views.trace(
            context, d, _arg("process"), instance, show_meta=_flag("meta"))))

    @bp.route("/api/views/trace/items")
    @guarded
    def process_trace_items():
        context, d = _process_context()
        return _placeable(lambda: jsonify(process_views.trace_items(
            context, d, _arg("process"), _arg("instance"), _arg("where"),
            _int("position", -1), _int("page", 0) or 0)))

    @bp.route("/api/views/board")
    @guarded
    def process_board():
        context, d = _process_context()
        if not _arg("process"):
            return jsonify({"success": False, "error": "Prozess angeben."}), 400
        return _placeable(lambda: jsonify(process_views.board(
            context, d, _arg("process"), owner=_arg("owner"), capability=_arg("capability"),
            status=_arg("status"))))

    from graph_views import spine_views

    def _drawable(fn):
        try:
            return fn()
        except spine_views.NotDrawable as exc:
            return jsonify({"success": False, "error": str(exc)}), 404

    @bp.route("/api/views/tree")
    @guarded
    def spine_tree():
        context, d = _process_context()
        if not _arg("spine"):
            return jsonify({"success": False, "error": "Achse angeben."}), 400
        return _drawable(lambda: jsonify(spine_views.tree(
            context, d, _arg("spine"), parent=_arg("parent"), level=_arg("level"), branch=_arg("branch"))))

    @bp.route("/api/views/matrix")
    @guarded
    def spine_matrix():
        context, d = _process_context()
        if not (_arg("spine") and _arg("rows") and _arg("cols")):
            return jsonify({"success": False, "error": "Achse, Zeilen- und Spaltenebene angeben."}), 400
        return _drawable(lambda: jsonify(spine_views.matrix(
            context, d, _arg("spine"), _arg("rows"), _arg("cols"), branch=_arg("branch"))))

    # -- saved views (Platform configuration, never brain nodes) -------------

    def saved(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            if gate is None or view_store is None:
                return jsonify({"success": False,
                                "error": "Gespeicherte Ansichten brauchen Benutzerkonten (identity.enabled)."}), 409
            user = gate.current_user()
            if user is None:
                return jsonify({"success": False, "error": "Nicht angemeldet."}), 401
            from identity.saved_views import SavedViewError

            try:
                return view(view_store(), user, *args, **kwargs)
            except SavedViewError as exc:
                return jsonify({"success": False, "error": str(exc)}), exc.status
        return wrapped

    def _body():
        body = request.get_json(silent=True)
        return body if isinstance(body, dict) else {}

    @bp.route("/api/views/saved", methods=["GET"])
    @saved
    def saved_list(store, user):
        return jsonify({"success": True, "views": store.list_for(user), "is_admin": is_admin()})

    @bp.route("/api/views/saved", methods=["POST"])
    @saved
    def saved_create(store, user):
        body = _body()
        view = store.create(user, body, audience=str(body.get("audience") or "personal"))
        return jsonify({"success": True, "view": view}), 201

    @bp.route("/api/views/saved/<view_id>", methods=["GET"])
    @saved
    def saved_get(store, user, view_id):
        return jsonify({"success": True, "view": store.get(view_id, user)})

    @bp.route("/api/views/saved/<view_id>", methods=["PATCH"])
    @saved
    def saved_update(store, user, view_id):
        return jsonify({"success": True, "view": store.update(view_id, user, _body())})

    @bp.route("/api/views/saved/<view_id>", methods=["DELETE"])
    @saved
    def saved_delete(store, user, view_id):
        store.delete(view_id, user)
        return jsonify({"success": True})

    @bp.route("/api/views/saved/<view_id>/share", methods=["POST"])
    @saved
    def saved_share(store, user, view_id):
        view = store.share(view_id, user, name=str(_body().get("name") or "") or None)
        return jsonify({"success": True, "view": view}), 201

    @bp.route("/api/views/saved/audit", methods=["GET"])
    @saved
    def saved_audit(store, user):
        if not is_admin():
            return jsonify({"success": False, "error": "Nur für Administratoren."}), 403
        from identity import audit

        rows = []
        for action in ("created", "updated", "deleted", "shared"):
            rows += audit.recent(gate.connection(), action=f"view.shared.{action}", limit=50)
        rows.sort(key=lambda r: str(r.get("occurred_at") or ""), reverse=True)
        for r in rows:
            when = r.get("occurred_at")
            r["occurred_at"] = when.isoformat(timespec="seconds") if hasattr(when, "isoformat") else when
            r["actor_user_id"] = str(r["actor_user_id"]) if r.get("actor_user_id") else None
        return jsonify({"success": True, "entries": rows[:50]})

    bp.guarded = guarded
    bp.is_admin = is_admin
    bp.view_context = ctx
    return bp
