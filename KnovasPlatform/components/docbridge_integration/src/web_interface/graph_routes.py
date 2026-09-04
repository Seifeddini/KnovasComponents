"""The /api/graph/* namespace: schema-driven node types, nodes and facts.

A separate module rather than more routes in app.py, which is already ~1900
lines. Follows the blueprint-factory shape admin.py established: the app's own
helpers are passed in, so this module imports nothing from app.py and can be
tested against a minimal Flask app.

Authorisation is on the route, never on whether the UI draws the control.

Plan: docs/superpowers/plans/2026-09-02-typed-node-workbench-components.md
"""
from __future__ import annotations

import functools
import logging

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

_GENERIC_ERROR = "Ein Fehler ist aufgetreten."
_FIXTURE_MODE_ERROR = "Wissensnetz-Modus erforderlich"


def create_graph_blueprint(gate, grant_store, source, *, graph_mode):
    """Build the blueprint.

    Args:
        gate: the IdentityGate; ``gate.current_user()`` or None.
        grant_store: zero-arg callable -> NodeGrantStore over THIS request's
            connection (``gate.connection()`` is request-scoped).
        source: a callable returning the Knovas client for this request.
        graph_mode: a callable returning True when ONTOLOGY_SOURCE=graph.
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
                return jsonify({"success": False,
                                "error": "Nur fuer Administratoren."}), 403
            return view(*args, **kwargs)
        return wrapped

    def require_node_write(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            user = gate.current_user() if gate else None
            if user is None:
                return jsonify({"success": False, "error": "Nicht angemeldet."}), 401
            node_id = kwargs.get("node_id")
            if not grant_store().may_write(node_id, user):
                return jsonify({"success": False,
                                "error": "Keine Bearbeitungsrechte fuer diesen Knoten."}), 403
            return view(*args, **kwargs)
        return wrapped

    bp.require_graph_mode = require_graph_mode      # exported for D1-D3
    bp.require_user = require_user
    bp.require_admin = require_admin
    bp.require_node_write = require_node_write

    def _fail(exc, message):
        logger.error(message, exc_info=True)
        return jsonify({"success": False, "error": _GENERIC_ERROR}), 500

    @bp.route("/node-types", methods=["GET"])
    @require_graph_mode
    @require_user
    def list_node_types():
        try:
            return jsonify({"success": True,
                            "node_types": source().graph_node_types()})
        except Exception as exc:                     # noqa: BLE001
            return _fail(exc, "Graph node-type list failed")

    @bp.route("/node-types", methods=["POST"])
    @require_graph_mode
    @require_admin
    def create_node_type():
        payload = request.get_json(silent=True) or {}
        name = " ".join(str(payload.get("name") or "").split())
        if not name:
            return jsonify({"success": False, "error": "Name fehlt."}), 400
        try:
            created = source().graph_create_node_type(name)
        except Exception as exc:                     # noqa: BLE001
            return _fail(exc, "Graph node-type create failed")
        if created is None:
            return jsonify({"success": False, "error": "Typ nicht anlegbar."}), 400
        node_type = created.get("node_type", created) if isinstance(created, dict) else created
        return jsonify({"success": True, "node_type": node_type}), 201

    @bp.route("/node-types/<type_id>/schema", methods=["GET"])
    @require_graph_mode
    @require_user
    def read_schema(type_id):
        try:
            attributes = list(source().graph_schema(type_id) or [])
        except Exception as exc:                     # noqa: BLE001
            return _fail(exc, "Graph schema read failed")
        # The API orders by (sort_order, name); re-sorting here keeps the
        # contract explicit for a client that must render fields in order.
        attributes.sort(key=lambda a: (a.get("sort_order", 0), a.get("name", "")))
        return jsonify({"success": True, "attributes": attributes})

    @bp.route("/node-types/<type_id>/schema", methods=["POST"])
    @require_graph_mode
    @require_admin
    def create_attribute(type_id):
        from graph_model import DATATYPES

        payload = request.get_json(silent=True) or {}
        name = " ".join(str(payload.get("name") or "").split())
        datatype = str(payload.get("datatype") or "").strip()
        enum_values = payload.get("enum_values")
        if not name:
            return jsonify({"success": False, "error": "Name fehlt."}), 400
        if datatype not in DATATYPES:
            return jsonify({"success": False,
                            "error": "Unbekannter Datentyp."}), 400
        if datatype == "enum" and not isinstance(enum_values, list):
            return jsonify({"success": False,
                            "error": "Auswahlfeld braucht Werte."}), 400
        try:
            created = source().graph_create_schema_attribute(
                type_id, name, datatype=datatype,
                required=bool(payload.get("required", False)),
                description=payload.get("description"),
                sort_order=int(payload.get("sort_order", 0)),
                enum_values=enum_values,
                target_node_type_id=payload.get("target_node_type_id"))
        except Exception as exc:                     # noqa: BLE001
            return _fail(exc, "Graph attribute create failed")
        if created is None:
            return jsonify({"success": False, "error": "Typ nicht gefunden."}), 404
        return jsonify({"success": True,
                        "attribute": created.get("attribute", created)}), 201

    @bp.route("/node-types/<type_id>/schema/<attribute_id>", methods=["PATCH"])
    @require_graph_mode
    @require_admin
    def update_attribute(type_id, attribute_id):
        payload = request.get_json(silent=True) or {}
        fields = {k: payload[k] for k in
                  ("name", "description", "required", "sort_order", "enum_values",
                   "target_node_type_id") if k in payload}
        if not fields:
            return jsonify({"success": False, "error": "Keine Aenderung."}), 400
        try:
            updated = source().graph_update_schema_attribute(
                type_id, attribute_id, **fields)
        except Exception as exc:                     # noqa: BLE001
            return _fail(exc, "Graph attribute update failed")
        if updated is None:
            return jsonify({"success": False, "error": "Attribut nicht gefunden."}), 404
        return jsonify({"success": True,
                        "attribute": updated.get("attribute", updated)})

    @bp.route("/node-types/<type_id>/schema/<attribute_id>", methods=["DELETE"])
    @require_graph_mode
    @require_admin
    def deprecate_attribute(type_id, attribute_id):
        """Deprecation, not deletion: existing facts keep their attribute_id.
        The response says `deprecated` so the UI cannot accidentally word it
        as a delete."""
        try:
            result = source().graph_deprecate_schema_attribute(type_id, attribute_id)
        except Exception as exc:                     # noqa: BLE001
            return _fail(exc, "Graph attribute deprecate failed")
        if result is None:
            return jsonify({"success": False, "error": "Attribut nicht gefunden."}), 404
        return jsonify({"success": True, "deprecated": True})

    return bp
