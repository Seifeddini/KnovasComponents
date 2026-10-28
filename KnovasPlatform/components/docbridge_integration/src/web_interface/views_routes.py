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
                           view_store: Optional[Callable[[], Any]] = None,
                           view_prefs: Any = None,
                           change_repo: Optional[Callable[[], Any]] = None,
                           change_store: Optional[Callable[[], Any]] = None,
                           node_grants: Optional[Callable[[], Any]] = None,
                           change_base: str = "master") -> Blueprint:
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
        from graph_views import discovery

        return jsonify(discovery.search(ctx(), service.schemas, str(request.args.get("q", "") or ""),
                                        show_meta=_flag("meta"), key_index_max=service.settings.key_index_max))

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
        if _arg("sections") == "context":
            return _placeable(lambda: jsonify(process_views.context_only(
                context, d, _arg("process"), instance, show_meta=_flag("meta"))))
        def traced():
            body = process_views.trace(context, d, _arg("process"), instance, show_meta=_flag("meta"))
            classes = cr.change_classes(d, _arg("process"))
            body["changeable"] = bool(classes and classes.get("instance_type") == body["instance"]["type"]
                                      and gate is not None and change_store is not None)
            return jsonify(body)

        return _placeable(traced)

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

    @bp.route("/api/views/saved/defaults", methods=["POST"])
    @saved
    def saved_defaults(store, user):
        if not is_admin():
            return jsonify({"success": False, "error": "Nur für Administratoren."}), 403
        from graph_views import default_views

        which = str(_body().get("set") or "knovas")
        if which not in default_views.SETS:
            return jsonify({"success": False, "error": f"Unbekannter Satz {which}."}), 400
        return jsonify({"success": True, **default_views.seed(store, user, which)})

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

    # -- discovery (Step 6b): start tiles, suggestions, lists, locating a node ---

    from graph_views import discovery

    def _prefs_user():
        if gate is None or view_prefs is None:
            return None
        return gate.current_user()

    @bp.route("/api/views/start")
    @guarded
    def views_start():
        context, d = _process_context()
        return jsonify(discovery.start(context, d, show_meta=_flag("meta")))

    @bp.route("/api/views/suggestions")
    @guarded
    def views_suggestions():
        context, d = _process_context()
        user = _prefs_user()
        recent = view_prefs.get(gate.connection(), user)["recent"] if user is not None else []
        return jsonify(discovery.suggestions(context, d, service.schemas, user=user, recent=recent))

    @bp.route("/api/views/list")
    @guarded
    def views_list():
        context, d = _process_context()
        if not _arg("type"):
            return jsonify({"success": False, "error": "Typ angeben."}), 400
        if _arg("type") not in context.index.by_type:
            return jsonify({"success": False, "error": "Typ unbekannt oder nicht sichtbar."}), 404
        return jsonify(discovery.list_view(
            context, d, service.schemas, _arg("type"), q=_arg("q"), sort=_arg("sort") or "name",
            desc=_flag("desc"), page=_int("page", 0) or 0, owner=_arg("owner"), status=_arg("status"),
            step=_arg("step"), show_meta=_flag("meta"), full_max=service.settings.key_index_max))

    @bp.route("/api/views/locate/<node_id>")
    @guarded
    def views_locate(node_id: str):
        """Where a node can be shown: its process (Ablauf) and its place on a spine (Baum)."""
        context, d = _process_context()
        context.index.ensure([node_id])
        if node_id not in context.index.nodes:
            return jsonify({"success": False, "error": "Knoten nicht gefunden oder nicht sichtbar."}), 404
        keys = discovery.key_index(context, service.schemas, [context.index.type_of(node_id)],
                                   service.settings.key_index_max)
        return jsonify(context.envelope(node=discovery.header(context, keys, node_id),
                                        process=discovery.process_of(context, d, node_id),
                                        tree=discovery.tree_path(context, d, node_id)))

    @bp.route("/api/views/selection", methods=["GET"])
    @guarded
    def views_selection_get():
        user = _prefs_user()
        return jsonify({"success": True, **(view_prefs.get(gate.connection(), user) if user is not None
                                            else {"selection": None, "kind": None, "recent": []})})

    @bp.route("/api/views/selection", methods=["PUT"])
    @guarded
    def views_selection_put():
        user = _prefs_user()
        if user is None:
            return jsonify({"success": True, "stored": False})
        body = request.get_json(silent=True) or {}
        try:
            value = view_prefs.remember(gate.connection(), user, str(body.get("node") or ""), str(body.get("kind") or ""))
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        return jsonify({"success": True, "stored": True, **value})

    # -- change requests (Step 7): pull requests on the plan file, never brain writes --

    from graph_views import change_requests as cr

    def change_ready(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            if gate is None or change_store is None or node_grants is None:
                return jsonify({"success": False,
                                "error": "Änderungsanträge brauchen Benutzerkonten (identity.enabled)."}), 409
            repo = change_repo() if change_repo else None
            if repo is None:
                return jsonify({"success": False, "error": "Kein Zugang zum KnowledgeBase-Repository konfiguriert "
                                                           "(views.change_requests)."}), 409
            try:
                return view(repo, *args, **kwargs)
            except cr.ChangeConflict as exc:
                return jsonify({"success": False, "error": str(exc), "conflict": True, "diff": exc.diff,
                                "current": exc.current, "base": exc.base}), 409
            except cr.ChangeError as exc:
                return jsonify({"success": False, "error": str(exc)}), exc.status
            except Exception as exc:  # GitHub failures carry status, never the token
                from graph_views.github_repo import GitHubError

                if isinstance(exc, GitHubError):
                    logger.warning("views: %s", exc)
                    return jsonify({"success": False, "error": str(exc)}), 502
                raise
        return wrapped

    def _permissions(node_id: str) -> dict:
        user = gate.current_user()
        grants = node_grants()
        return {c: cr.may_propose(c, user, node_id, grants) for c in ("minor", "substantive")}

    def _actor() -> str:
        user = gate.current_user()
        return str(getattr(user, "display_name", "") or getattr(user, "email", "") or "unknown")

    @bp.route("/api/views/change")
    @guarded
    @change_ready
    def change_form(repo):
        context, d = _process_context()
        node = _arg("node")
        body = cr.form(context, d, _arg("process"), node, repo, change_base)
        body["permissions"] = _permissions(node)
        body["can_write"] = hasattr(repo, "create_pull")
        return jsonify(context.envelope(**body))

    def _prepare(repo):
        context, d = _process_context()
        body = request.get_json(silent=True) or {}
        node = str(body.get("node") or "")
        prepared = cr.prepare(context, d, str(body.get("process") or ""), node, repo, change_base,
                              body.get("values") or {}, base_shas=body.get("base") or {},
                              why=str(body.get("why") or ""), actor=_actor())
        return context, node, prepared

    @bp.route("/api/views/change/preview", methods=["POST"])
    @guarded
    @change_ready
    def change_preview(repo):
        context, node, p = _prepare(repo)
        return jsonify(context.envelope(**{k: v for k, v in p.items() if k != "files"},
                                        files=sorted(p["files"]), permitted=_permissions(node)[p["class"]],
                                        can_write=hasattr(repo, "create_pull")))

    @bp.route("/api/views/change", methods=["POST"])
    @guarded
    @change_ready
    def change_submit(repo):
        context, node, p = _prepare(repo)
        user = gate.current_user()
        if not cr.may_propose(p["class"], user, node, node_grants()):
            who = "Nur die Plan-Verantwortlichen (node_grants owner) und Administratoren" if p["class"] == "substantive"                 else "Nur wer den Plan bearbeiten darf (owner, editor) und Administratoren"
            return jsonify({"success": False, "error": f"{who} können diese Änderung beantragen."}), 403
        why = str((request.get_json(silent=True) or {}).get("why") or "")
        pr = cr.submit(p, repo, change_base, actor=_actor(), why=why)
        store = change_store()
        row = store.record(node_id=node, process_key=str((request.get_json(silent=True) or {}).get("process") or ""),
                           instance_key=p["plan"], plan_path=p["path"], change_class=p["class"], fields=p["fields"],
                           expected=p["expected"], pr_number=pr.get("number"), pr_url=pr.get("url"),
                           branch=pr["branch"], user=user)
        from identity import audit

        audit.record(gate.connection(), action="plan.change.proposed", actor=user, target_type="feature_plan",
                     target_id=p["plan"], detail={"node_id": node, "fields": p["fields"], "class": p["class"],
                                                  "new_status": p["new_status"], "pr_number": pr.get("number"),
                                                  "pr_url": pr.get("url"), "files": sorted(p["files"])})
        return jsonify({"success": True, "pr": pr, "request": row}), 201

    @bp.route("/api/views/change/pending")
    @guarded
    def change_pending():
        if gate is None or change_store is None:
            return jsonify({"success": True, "pending": []})
        node = _arg("node")
        context = ctx()
        from graph_views.facts import fact_values, read_facts
        from identity.change_requests import refresh

        store = change_store()
        rows = store.unfinished(node)
        if rows:
            facts = fact_values(read_facts(context.state, context.reader, [node]).get(node))
            rows = refresh(rows, store, change_repo() if change_repo else None, facts)
        return jsonify({"success": True, "pending": rows})

    bp.guarded = guarded
    bp.is_admin = is_admin
    bp.view_context = ctx
    return bp
