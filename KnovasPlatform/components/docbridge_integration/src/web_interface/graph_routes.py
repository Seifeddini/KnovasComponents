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

    @bp.route("/nodes", methods=["GET"])
    @require_graph_mode
    @require_user
    def list_nodes():
        try:
            nodes = source().graph_nodes(
                node_type_id=request.args.get("type") or None,
                q=request.args.get("q") or None)
        except Exception as exc:                     # noqa: BLE001
            return _fail(exc, "Graph node list failed")
        # Deliberately NOT filtered by node_grants: read visibility is the
        # backend ACL's answer and it has already been applied.
        return jsonify({"success": True, "nodes": nodes})

    @bp.route("/nodes", methods=["POST"])
    @require_graph_mode
    @require_user
    def create_node():
        payload = request.get_json(silent=True) or {}
        name = " ".join(str(payload.get("name") or "").split())
        if not name:
            return jsonify({"success": False, "error": "Name fehlt."}), 400
        try:
            created = source().graph_create_node(
                name, node_type_id=payload.get("node_type_id") or None)
        except Exception as exc:                     # noqa: BLE001
            return _fail(exc, "Graph node create failed")
        if created is None:
            return jsonify({"success": False, "error": "Knoten nicht anlegbar."}), 400
        node = created.get("node", created)
        # CreateMechanism: the creator becomes the owner in the same request.
        grant_store().set_owner(str(node["id"]), gate.current_user().id)
        return jsonify({"success": True, "node": node}), 201

    @bp.route("/nodes/<node_id>", methods=["GET"])
    @require_graph_mode
    @require_user
    def node_detail(node_id):
        from graph_workbench import compose_node

        try:
            payload = compose_node(source(), grant_store(), node_id)
        except Exception as exc:                     # noqa: BLE001
            return _fail(exc, "Graph node detail failed")
        if payload is None:
            return jsonify({"success": False, "error": "Knoten nicht gefunden."}), 404
        payload["success"] = True
        payload["may_write"] = grant_store().may_write(node_id, gate.current_user())
        return jsonify(payload)

    @bp.route("/nodes/<node_id>", methods=["PATCH"])
    @require_graph_mode
    @require_node_write
    def update_node(node_id):
        payload = request.get_json(silent=True) or {}
        fields = {k: payload[k] for k in
                  ("name", "description", "node_type_id", "required_groups")
                  if k in payload}
        if not fields:
            return jsonify({"success": False, "error": "Keine Aenderung."}), 400
        try:
            updated = source().graph_update_node(node_id, **fields)
        except Exception as exc:                     # noqa: BLE001
            return _fail(exc, "Graph node update failed")
        if updated is None:
            return jsonify({"success": False, "error": "Knoten nicht gefunden."}), 404
        return jsonify({"success": True, "node": updated.get("node", updated)})

    def _attribute(type_id, attribute_id):
        """One attribute definition, including deprecated ones.

        A fact may target a deprecated attribute -- deprecation keeps facts --
        so a lookup that hid them would make editing an existing value fail.
        """
        for attribute in source().graph_schema(type_id, include_deprecated=True):
            if str(attribute.get("id")) == str(attribute_id):
                return attribute
        return None

    def _encoded(node_id, payload):
        """(value, attribute_id, label, error_response)."""
        from graph_model import FactValueError, encode

        attribute_id = payload.get("attribute_id")
        label = " ".join(str(payload.get("label") or "").split())
        raw = payload.get("value")
        if not attribute_id:
            if not label:
                return None, None, None, (jsonify(
                    {"success": False,
                     "error": "Ein freies Feld braucht eine Bezeichnung."}), 400)
            return raw, None, label, None

        detail = source().graph_node(node_id) or {}
        type_id = (detail.get("node", detail) or {}).get("node_type_id")
        attribute = _attribute(type_id, attribute_id) if type_id else None
        if attribute is None:
            return None, None, None, (jsonify(
                {"success": False, "error": "Attribut nicht gefunden."}), 404)
        try:
            value = encode(attribute.get("datatype", "text"), raw,
                           enum_values=attribute.get("enum_values"))
        except FactValueError as exc:
            # The codec's message is written for a user and names the field
            # rule; replacing it with a generic error would waste it.
            return None, None, None, (jsonify({"success": False,
                                               "error": str(exc)}), 400)
        return value, str(attribute_id), None, None

    @bp.route("/nodes/<node_id>/facts", methods=["POST"])
    @require_graph_mode
    @require_node_write
    def create_fact(node_id):
        payload = request.get_json(silent=True) or {}
        value, attribute_id, label, error = _encoded(node_id, payload)
        if error:
            return error
        try:
            created = source().graph_create_fact(
                node_id, value, attribute_id=attribute_id, label=label)
        except Exception as exc:                     # noqa: BLE001
            return _fail(exc, "Graph fact create failed")
        if created is None:
            return jsonify({"success": False, "error": "Knoten nicht gefunden."}), 404
        return jsonify({"success": True, "fact": created.get("fact", created)}), 201

    @bp.route("/facts/<fact_id>", methods=["PATCH", "DELETE"])
    @require_graph_mode
    @require_user
    def mutate_fact(fact_id):
        payload = request.get_json(silent=True) or {}
        node_id = payload.get("node_id") or request.args.get("node_id")
        # The write gate is per node and a fact does not carry its node in the
        # URL. No node id means nothing to authorise against; defaulting to
        # allow would be the bug.
        if not node_id or not grant_store().may_write(node_id, gate.current_user()):
            return jsonify({"success": False,
                            "error": "Keine Bearbeitungsrechte fuer diesen Knoten."}), 403
        try:
            if request.method == "DELETE":
                result = source().graph_delete_fact(fact_id)
            else:
                value, attribute_id, label, error = _encoded(node_id, payload)
                if error:
                    return error
                result = source().graph_update_fact(fact_id, value=value)
        except Exception as exc:                     # noqa: BLE001
            return _fail(exc, "Graph fact mutation failed")
        if result is None:
            return jsonify({"success": False, "error": "Fakt nicht gefunden."}), 404
        return jsonify({"success": True, "fact": result.get("fact", result)})

    def _person(user_id):
        user = gate.users().get(user_id)
        if user is None:
            return {"id": str(user_id), "email": None,
                    "display_name": "Unbekanntes Konto"}
        return {"id": str(user.id), "email": user.email,
                "display_name": getattr(user, "display_name", None) or user.email}

    @bp.route("/nodes/<node_id>/grants", methods=["GET"])
    @require_graph_mode
    @require_user
    def read_grants(node_id):
        current = grant_store().for_node(node_id)
        return jsonify({
            "success": True,
            "owner": _person(current["owner"]) if current["owner"] else None,
            "editors": [_person(uid) for uid in current["editors"]],
        })

    def _may_grant(node_id):
        user = gate.current_user()
        if user is None:
            return False
        if "admin" in user.roles:
            return True
        # mayGrant (node_grants.als): the owner or an admin, never an editor.
        return grant_store().for_node(node_id)["owner"] == str(user.id)

    @bp.route("/nodes/<node_id>/grants", methods=["POST"])
    @require_graph_mode
    @require_user
    def add_grant(node_id):
        if not _may_grant(node_id):
            # An editor may edit, not delegate. Otherwise one grant silently
            # becomes the right to hand out every further grant.
            return jsonify({"success": False,
                            "error": "Nur Eigentuemer oder Administrator."}), 403
        payload = request.get_json(silent=True) or {}
        user_id = str(payload.get("user_id") or "")
        email = " ".join(str(payload.get("email") or "").split())
        if not user_id and email:
            found = gate.users().get_by_email(email)
            user_id = str(found.id) if found else ""
        if not user_id or gate.users().get(user_id) is None:
            return jsonify({"success": False, "error": "Konto nicht gefunden."}), 404
        grant_store().grant_editor(node_id, user_id, granted_by=gate.current_user().id)
        return jsonify({"success": True, "editor": _person(user_id)}), 201

    @bp.route("/nodes/<node_id>/grants/<user_id>", methods=["DELETE"])
    @require_graph_mode
    @require_user
    def remove_grant(node_id, user_id):
        from identity.node_grants import OwnerRevokeError

        if not _may_grant(node_id):
            return jsonify({"success": False,
                            "error": "Nur Eigentuemer oder Administrator."}), 403
        try:
            grant_store().revoke(node_id, user_id)
        except OwnerRevokeError as exc:
            return jsonify({"success": False, "error": str(exc)}), 409
        return jsonify({"success": True})

    return bp
