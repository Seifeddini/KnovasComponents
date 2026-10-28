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
            from graph_views import fixes

            body = process_views.trace(context, d, _arg("process"), instance, show_meta=_flag("meta"))
            proc = d.process(_arg("process")) or {}
            # Fixes and edits need accounts (who proposes, node_grants) and a KnowledgeBase repository.
            body["changeable"] = bool(gate is not None and change_store is not None and change_repo
                                      and change_repo() is not None and proc.get("change_classes"))
            fixes.annotate_trace(d, _arg("process"), body, _pending_for(instance) if body["changeable"] else [])
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

    # -- change sets (Step 7): fixes and edits become ONE pull request on KnowledgeBase, never brain writes --

    from graph_views import change_requests as cr
    from graph_views import change_set as cs
    from graph_views import fixes

    def change_ready(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            if gate is None or change_store is None or node_grants is None:
                return jsonify({"success": False,
                                "error": "Änderungen brauchen Benutzerkonten (identity.enabled)."}), 409
            repo = change_repo() if change_repo else None
            if repo is None:
                return jsonify({"success": False, "error": "Kein Zugang zum KnowledgeBase-Repository konfiguriert "
                                                           "(views.change_requests)."}), 409
            try:
                return view(repo, *args, **kwargs)
            except cr.ChangeConflict as exc:
                return jsonify({"success": False, "error": str(exc), "conflict": True, "diff": exc.diff}), 409
            except cr.ChangeError as exc:
                return jsonify({"success": False, "error": str(exc)}), exc.status
            except process_views.NotPlaceable as exc:
                return jsonify({"success": False, "error": str(exc)}), 404
            except Exception as exc:  # GitHub failures carry status, never the token
                from graph_views.github_repo import GitHubError

                if isinstance(exc, GitHubError):
                    logger.warning("views: %s", exc)
                    return jsonify({"success": False, "error": str(exc)}), 502
                raise
        return wrapped

    def _actor() -> str:
        user = gate.current_user()
        return str(getattr(user, "display_name", "") or getattr(user, "email", "") or "unknown")

    def _open_set(repo, create: bool = False):
        store = change_store()
        user = gate.current_user()
        found = store.open_for(user.id)
        if found is None and create:
            found = store.create(user.id, change_base, cs.check_sha(repo.branch_sha(change_base)))
        return store, found

    def _set_body(found, repo) -> dict:
        if found is None:
            return {"success": True, "change_set": None, "count": 0, "can_write": hasattr(repo, "create_pull")}
        return {"success": True, "change_set": found, "count": len(found["ops"]), "can_write": hasattr(repo, "create_pull")}

    @bp.route("/api/views/changeset")
    @guarded
    @change_ready
    def changeset_get(repo):
        _, found = _open_set(repo)
        return jsonify(_set_body(found, repo))

    def _op_from(body: dict, context, d) -> dict:
        """One operation from a request: a fix (edit-map id from the artefact) or a plan field."""
        node = str(body.get("node") or "")
        process = str(body.get("process") or "")
        kind = str(body.get("kind") or "")
        context.index.ensure([node])
        if node not in context.index.nodes:
            raise cr.ChangeError("Instanz nicht gefunden oder nicht sichtbar.")
        edit_id = str(body.get("edit_id") or "")
        suggestion = None
        if body.get("where") is not None and body.get("position") is not None:
            edit_id = fixes.edit_id_at(d, process, context.index.type_of(node), str(body["where"]), int(body["position"]))
        target = str(body.get("target") or "")
        if not target and body.get("target_node"):
            try:
                target = fixes.target_key(context, str(body["target_node"]))
            except ValueError as exc:
                raise cr.ChangeError(str(exc)) from exc
        if kind in ("add", "replace") and body.get("where") is not None:
            key = cr.plan_of(context, node)["key"]
            chosen = str(body.get("new_target") or target) if kind == "replace" else target
            suggestion = fixes.suggestion_for(d, process, key, str(body["where"]), int(body["position"]), chosen)
        return cs.new_op(context, d, gate.current_user(), node_grants(), process=process, node=node, kind=kind,
                         edit_id=edit_id, target=target, new_target=str(body.get("new_target") or ""),
                         field=str(body.get("field") or ""), value=body.get("value"),
                         reason=str(body.get("reason") or ""), suggestion=suggestion)

    @bp.route("/api/views/changeset/ops", methods=["POST"])
    @guarded
    @change_ready
    def changeset_add(repo):
        """Add one operation, or several (``ops``: the gaps worklist's "Vorschläge übernehmen"). Each is
        checked on its own; refused ones come back with the reason and the others are kept."""
        context, d = _process_context()
        body = request.get_json(silent=True) or {}
        items = body.get("ops") if isinstance(body.get("ops"), list) else [body]
        store, found = _open_set(repo, create=True)
        added, refused = [], []
        for i, item in enumerate(items[:200]):
            try:
                op = _op_from(item, context, d)
            except cr.ChangeError as exc:
                refused.append({"index": i, "error": str(exc), "status": exc.status})
                continue
            if any(cs.same_change(op, o) for o in found["ops"]):
                refused.append({"index": i, "error": "Diese Änderung ist schon im Änderungssatz.", "status": 409})
                continue
            found["ops"].append(op)
            added.append(op)
        if added:
            found = store.save(found)
        if refused and not added and len(items) == 1:
            return jsonify({"success": False, "error": refused[0]["error"]}), refused[0]["status"]
        return jsonify({**_set_body(found, repo), "added": added, "refused": refused}), (201 if added else 200)

    @bp.route("/api/views/changeset/fields", methods=["POST"])
    @guarded
    @change_ready
    def changeset_fields(repo):
        """The plan form ("Bearbeiten"): every changed field becomes operations in the change set."""
        context, d = _process_context()
        body = request.get_json(silent=True) or {}
        node, process = str(body.get("node") or ""), str(body.get("process") or "")
        store, found = _open_set(repo, create=True)
        current, _ = cr.read_plan(repo, found["base"]["commit"], cr.plan_of(context, node))
        specs = cr.field_ops(current, body.get("values") or {})
        if not specs:
            return jsonify({"success": False, "error": "Keine Änderung gegenüber dem Plan."}), 400
        ops = [_op_from({**s, "node": node, "process": process}, context, d) for s in specs]
        found["ops"] += [o for o in ops if not any(cs.same_change(o, x) for x in found["ops"])]
        found = store.save(found)
        return jsonify({**_set_body(found, repo), "added": ops}), 201

    @bp.route("/api/views/changeset/ops/<op_id>", methods=["DELETE"])
    @guarded
    @change_ready
    def changeset_drop(repo, op_id: str):
        store, found = _open_set(repo)
        if found is None:
            return jsonify({"success": False, "error": "Kein offener Änderungssatz."}), 404
        found["ops"] = [o for o in found["ops"] if o["id"] != op_id]
        return jsonify(_set_body(store.save(found), repo))

    @bp.route("/api/views/changeset/discard", methods=["POST"])
    @guarded
    @change_ready
    def changeset_discard(repo):
        store, found = _open_set(repo)
        if found is not None:
            store.discard(found["id"])
        return jsonify(_set_body(None, repo))

    def _reasons(found) -> dict:
        body = request.get_json(silent=True) or {}
        reasons = {**(found.get("reasons") or {}), **{str(k): " ".join(str(v or "").split())
                                                      for k, v in (body.get("reasons") or {}).items()}}
        return reasons

    def _review(repo, found, reasons):
        context, d = _process_context()
        review = cs.build(context, d, repo, {**found, "reasons": reasons}, actor=_actor(), reasons=reasons)
        user, grants = gate.current_user(), node_grants()
        for op in review["ops"]:
            op["permitted"] = cr.may_propose(op["class"], user, op["instance"], grants)
        review["permitted"] = all(op["permitted"] for op in review["ops"])
        return review

    @bp.route("/api/views/changeset/review", methods=["POST"])
    @guarded
    @change_ready
    def changeset_review(repo):
        store, found = _open_set(repo)
        if found is None or not found["ops"]:
            return jsonify({"success": False, "error": "Der Änderungssatz ist leer."}), 400
        reasons = _reasons(found)
        found = store.save({**found, "reasons": reasons})
        review = _review(repo, found, reasons)
        return jsonify({"success": True, **cs.public(review), "reasons": reasons,
                        "can_write": hasattr(repo, "create_pull")})

    @bp.route("/api/views/changeset/rebase", methods=["POST"])
    @guarded
    @change_ready
    def changeset_rebase(repo):
        store, found = _open_set(repo)
        if found is None:
            return jsonify({"success": False, "error": "Kein offener Änderungssatz."}), 404
        found = store.save(cs.rebase(repo, found))
        review = _review(repo, found, found.get("reasons") or {})
        return jsonify({"success": True, **cs.public(review), "reasons": found.get("reasons") or {},
                        "can_write": hasattr(repo, "create_pull")})

    @bp.route("/api/views/changeset/submit", methods=["POST"])
    @guarded
    @change_ready
    def changeset_submit(repo):
        store, found = _open_set(repo)
        if found is None or not found["ops"]:
            return jsonify({"success": False, "error": "Der Änderungssatz ist leer."}), 400
        reasons = _reasons(found)
        review = _review(repo, found, reasons)
        if not review["permitted"]:
            return jsonify({"success": False, "error": "Nicht jede Änderung ist erlaubt: inhaltliche Änderungen nur für "
                                                       "Verantwortliche (node_grants owner) und Administratoren."}), 403
        pr = cs.submit(review, repo, actor=_actor(), reasons=reasons)
        store.submitted(found["id"], pr_number=pr.get("number"), pr_url=pr.get("url"), branch=pr["branch"],
                        reasons=reasons)
        from identity import audit

        audit.record(gate.connection(), action="plan.changeset.submitted", actor=gate.current_user(),
                     target_type="change_set", target_id=found["id"],
                     detail={"pr_number": pr.get("number"), "pr_url": pr.get("url"), "branch": pr["branch"],
                             "files": [f["path"] for f in review["files"]],
                             "classes": sorted({o["class"] for o in review["ops"] if o["ok"]}),
                             "instances": [{"key": i["key"], "class": i["class"],
                                            "rollback": i["rollback"], "decision": i.get("decision")}
                                           for i in review["instances"]],
                             "operations": [cs.describe(o) for o in review["ops"] if o["ok"]]})
        return jsonify({"success": True, "pr": pr}), 201

    @bp.route("/api/views/change")
    @guarded
    @change_ready
    def change_form(repo):
        """The plan's fields for "Bearbeiten", read at the change set's base (or master's head)."""
        context, d = _process_context()
        _, found = _open_set(repo)
        commit = found["base"]["commit"] if found else repo.branch_sha(change_base)
        body = cr.form(context, d, _arg("process"), _arg("node"), repo, commit)
        return jsonify(context.envelope(**body, can_write=hasattr(repo, "create_pull")))

    @bp.route("/api/views/pick")
    @guarded
    @change_ready
    def pick(repo):
        context, d = _process_context()
        return jsonify(fixes.picker(context, d, service.schemas, process=_arg("process"), node=_arg("node"),
                                    where=_arg("where"), position=_int("position", -1), q=_arg("q"),
                                    key_index_max=service.settings.key_index_max))

    @bp.route("/api/views/gaps")
    @guarded
    def gaps():
        context, d = _process_context()
        return jsonify(fixes.gaps(context, d, process=_arg("process"), step=_arg("step"),
                                  type_name=_arg("type"), owner=_arg("owner")))

    def _pending_for(node: str) -> list:
        if gate is None or change_store is None:
            return []
        from identity.change_sets import refresh

        store = change_store()
        rows = store.pending_for(node)
        if not rows:
            return []
        context = ctx()

        def applied(row) -> bool:
            # A merged set is applied once the tenant shows every link change it made.
            context.index.ensure(list({op["instance"] for op in row["ops"]}))
            return all(fixes.op_applied(context, op) for op in row["ops"])

        return refresh(rows, store, change_repo() if change_repo else None, applied)

    @bp.route("/api/views/changeset/pending")
    @guarded
    def changeset_pending():
        return jsonify({"success": True, "pending": _pending_for(_arg("node"))})

    bp.guarded = guarded
    bp.is_admin = is_admin
    bp.view_context = ctx
    return bp
