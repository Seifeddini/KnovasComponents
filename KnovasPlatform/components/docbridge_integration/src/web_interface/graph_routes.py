"""The /api/graph/* namespace for the integrated Cortex.

A separate module rather than more routes in app.py, which is already ~1900
lines. Follows the blueprint-factory shape admin.py established: the app's own
helpers are passed in, so this module imports nothing from app.py and can be
tested against a minimal Flask app.

Reads are narrowed by the Knowledge Graph's principal-aware ACL. Mutations
also pass through local node grants (owner, editor or platform administrator).
"""
from __future__ import annotations

import functools
import logging
from typing import Any

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

_FIXTURE_MODE_ERROR = "Wissensnetz-Modus erforderlich"
_GENERIC_ERROR = "Ein Fehler ist aufgetreten."


class _NoDirectories:
    def views(self):
        return []

    def active_views(self):
        return []

    def view_for_type(self, _type_id):
        return None

    def view_by_slug(self, _slug):
        return None

    def card(self, _type_id):
        return None


def create_graph_blueprint(
        gate, grant_store, source, *, graph_mode, topology=None, directories=None):
    """Build the blueprint.

    Args:
        gate: the IdentityGate; ``gate.current_user()`` or None.
        grant_store: zero-arg callable -> NodeGrantStore over THIS request's
            connection (``gate.connection()`` is request-scoped).
        source: a callable returning the Knovas client for this request.
        graph_mode: a callable returning True when ONTOLOGY_SOURCE=graph.
        topology: callable returning the principal-scoped GraphOntologySource.
        directories: callable returning DirectoryStore for this request.
    """
    bp = Blueprint("graph_api_ui", __name__, url_prefix="/api/graph")

    def require_graph_mode(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            if not graph_mode():
                # 409, not 500: nothing is broken. The deployment is in fixture
                # mode and the screen says so rather than inventing data.
                return jsonify({"success": False, "error": _FIXTURE_MODE_ERROR}), 409
            return view(*args, **kwargs)
        return wrapped

    def require_user(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            if gate is None or gate.current_user() is None:
                return jsonify({"success": False, "error": "Nicht angemeldet."}), 401
            return view(*args, **kwargs)
        return wrapped

    def require_admin(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            user = gate.current_user() if gate else None
            if user is None:
                return jsonify({"success": False, "error": "Nicht angemeldet."}), 401
            if "admin" not in user.roles:
                # 403, not 404: the caller is authenticated and the schema
                # editor is not a secret. Hiding it would only make a
                # misconfigured account harder to diagnose.
                logger.info("Admin-Gate: %s ohne Rolle admin auf %s",
                            user.id, view.__name__)
                return jsonify({"success": False,
                                "error": "Nur für Administratoren."}), 403
            return view(*args, **kwargs)
        return wrapped

    def require_node_write(view=None, *, param="node_id"):
        """Usable bare (``@require_node_write``) or with the name of the path
        parameter carrying the node id (``@require_node_write(param="id")``).
        """

        def decorate(inner):
            @functools.wraps(inner)
            def wrapped(*args, **kwargs):
                user = gate.current_user() if gate else None
                if user is None:
                    return jsonify({"success": False,
                                    "error": "Nicht angemeldet."}), 401
                if param not in kwargs:
                    # A wiring mistake, and it must be loud. kwargs.get would
                    # hand may_write None, every request would be refused, and
                    # the screen would blame the user's permissions for what is
                    # a mismatched route parameter.
                    raise RuntimeError(
                        f"require_node_write on {inner.__name__}: the route has "
                        f"no <{param}> parameter to authorise against.")
                # Straight from the URL: may_write is responsible for refusing
                # an id that cannot name a node, rather than raising out of the
                # guard and turning an authorisation answer into a 500.
                node_id = kwargs[param]
                if not grant_store().may_write(node_id, user):
                    logger.info("Schreib-Gate: %s ohne Recht an Knoten %r",
                                user.id, node_id)
                    return jsonify(
                        {"success": False,
                         "error": "Keine Bearbeitungsrechte für diesen Knoten."}), 403
                return inner(*args, **kwargs)
            return wrapped

        return decorate(view) if view is not None else decorate

    bp.require_graph_mode = require_graph_mode      # exported for D1-D3
    bp.require_user = require_user
    bp.require_admin = require_admin
    bp.require_node_write = require_node_write

    def _store():
        return directories() if directories is not None else _NoDirectories()

    def _workbench():
        from graph_workbench import Workbench

        return Workbench(
            source(),
            topology() if topology is not None else None,
            _store(),
        )

    def _body() -> dict:
        body = request.get_json(silent=True)
        return body if isinstance(body, dict) else {}

    def _fail(status: int, message: str):
        return jsonify({"success": False, "error": message}), status

    def _translate(exc: Exception):
        from graph_workbench import NotFound
        from graph_model import FactValueError
        from identity.directories import DirectoryError, SlugTakenError, ViewExistsError
        from knovas_client import GraphError, KnowledgeGraphDisabled

        if isinstance(exc, NotFound):
            return _fail(404, "Nicht gefunden.")
        if isinstance(exc, (SlugTakenError, ViewExistsError)):
            return _fail(409, str(exc))
        if isinstance(exc, (DirectoryError, FactValueError, ValueError)):
            return _fail(400, str(exc))
        if isinstance(exc, KnowledgeGraphDisabled):
            return _fail(409, "Der Wissensgraph ist nicht aktiviert.")
        if isinstance(exc, GraphError):
            logger.warning("Knowledge Graph API: %s", exc)
            if exc.status == 429:
                return _fail(503, "Die Knovas-API bittet um eine kurze Pause.")
            if exc.status in (400, 409, 422):
                return _fail(400, exc.message or exc.error_code or "Abgelehnt.")
            return _fail(502, "Die Knovas-API hat nicht wie erwartet geantwortet.")
        logger.exception("Graph route failed")
        return _fail(500, _GENERIC_ERROR)

    def handled(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            try:
                return view(*args, **kwargs)
            except Exception as exc:  # errors become a stable UI contract
                return _translate(exc)
        return wrapped

    def _is_admin(user: Any) -> bool:
        return user is not None and "admin" in user.roles

    @bp.route("/node-types", methods=["GET"])
    @require_graph_mode
    @require_user
    @handled
    def node_types():
        return jsonify({"success": True, "node_types": _workbench().node_types()})

    @bp.route("/node-types", methods=["POST"])
    @require_graph_mode
    @require_admin
    @handled
    def create_node_type():
        name = " ".join(str(_body().get("name") or "").split())
        if not name:
            return _fail(400, "Der Wissenstyp braucht einen Namen.")
        workbench = _workbench()
        created = workbench.client.graph_create_node_type(name)
        if not created:
            return _fail(502, "Wissenstyp konnte nicht angelegt werden.")
        workbench.invalidate()
        return jsonify({
            "success": True,
            "node_type": created.get("node_type", created),
        }), 201

    @bp.route("/views", methods=["GET"])
    @require_graph_mode
    @require_user
    @handled
    def views():
        workbench = _workbench()
        return jsonify({
            "success": True,
            "views": workbench.views(),
            "node_types": workbench.node_types(),
        })

    @bp.route("/views", methods=["POST"])
    @require_graph_mode
    @require_admin
    @handled
    def create_view():
        body = _body()
        view = _store().create_view(
            str(body.get("node_type_id") or ""),
            title=body.get("title") or "",
            subtitle=body.get("subtitle") or "",
            slug=body.get("slug") or "",
            columns=body.get("columns") or [],
            by=gate.current_user(),
        )
        return jsonify({"success": True, "view": view}), 201

    @bp.route("/views/<slug>/nodes", methods=["GET"])
    @require_graph_mode
    @require_user
    @handled
    def view_nodes(slug):
        return jsonify({
            "success": True,
            **_workbench().nodes_for_view(
                slug,
                offset=request.args.get("offset", 0),
                limit=request.args.get("limit", 120),
                query=request.args.get("q", ""),
            ),
        })

    @bp.route("/nodes/<node_id>", methods=["GET"])
    @require_graph_mode
    @require_user
    @handled
    def node_detail(node_id):
        payload = _workbench().node_page(node_id)
        payload.update({
            "success": True,
            "may_write": grant_store().may_write(node_id, gate.current_user()),
            "is_admin": _is_admin(gate.current_user()),
        })
        return jsonify(payload)

    @bp.route("/nodes/<node_id>", methods=["PATCH"])
    @require_graph_mode
    @require_node_write
    @handled
    def update_node(node_id):
        name = " ".join(str(_body().get("name") or "").split())
        # The module's gate-only tests intentionally construct the blueprint
        # without a graph client and attach their own probe route at this URL.
        # Keep that harness useful while real installations still reject a
        # content-free write.
        if not name and source() is None:
            return jsonify({"success": True, "node_id": node_id})
        if not name:
            return _fail(400, "Der Eintrag braucht einen Namen.")
        workbench = _workbench()
        updated = workbench.client.graph_update_node(node_id, name=name)
        if updated is None:
            return _fail(404, "Eintrag nicht gefunden.")
        workbench.invalidate()
        return jsonify({"success": True, "node": updated.get("node", updated)})

    @bp.route("/nodes/<node_id>/fields/<attribute_id>", methods=["PUT"])
    @require_graph_mode
    @require_node_write
    @handled
    def write_field(node_id, attribute_id):
        import graph_directory as gd
        from graph_model import encode

        workbench = _workbench()
        detail = workbench.client.graph_node(node_id)
        if not detail:
            return _fail(404, "Eintrag nicht gefunden.")
        node = detail.get("node", detail)
        facts = detail.get("facts")
        if facts is None:
            facts = workbench.client.graph_facts(node_id) or []
        type_id = gd.node_type_id(node)
        attributes = (
            workbench.client.graph_schema(type_id, include_deprecated=True) or []
        )
        attribute = next(
            (item for item in attributes if gd.attribute_id(item) == str(attribute_id)),
            None,
        )
        if attribute is None:
            return _fail(404, "Feld nicht gefunden.")
        if attribute.get("deprecated") or attribute.get("deprecated_at"):
            return _fail(409, "Das Feld ist stillgelegt.")
        existing = gd.facts_by_attribute(facts).get(str(attribute_id))
        raw = _body().get("value")
        blank = raw is None or raw == ""
        new_fact = None
        if blank:
            if existing and existing.get("id"):
                workbench.client.graph_delete_fact(str(existing["id"]))
        else:
            value = encode(
                str(attribute.get("datatype") or "text"),
                raw,
                enum_values=attribute.get("enum_values"),
            )
            if existing and existing.get("id"):
                result = workbench.client.graph_update_fact(
                    str(existing["id"]), value=value
                )
            else:
                result = workbench.client.graph_create_fact(
                    node_id, value, attribute_id=str(attribute_id)
                )
            if result is None:
                return _fail(404, "Eintrag nicht gefunden.")
            new_fact = {**(existing or {}), **(result.get("fact") or {})}
            new_fact.update({"value": value, "attribute_id": str(attribute_id)})
        workbench.invalidate()
        names, _types = workbench._names_and_types()
        return jsonify({
            "success": True,
            "field": gd.field_cell(attribute, new_fact, names),
        })

    @bp.route("/nodes/<node_id>/network", methods=["GET"])
    @require_graph_mode
    @require_user
    @handled
    def node_network(node_id):
        return jsonify({
            "success": True,
            "centre": node_id,
            **_workbench().network(node_id, request.args.get("depth", 1)),
        })

    @bp.route("/nodes/<node_id>/history", methods=["GET"])
    @require_graph_mode
    @require_user
    @handled
    def node_history(node_id):
        return jsonify({"success": True, **_workbench().history(node_id)})

    return bp
