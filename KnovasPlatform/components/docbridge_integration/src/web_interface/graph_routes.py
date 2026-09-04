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

from flask import Blueprint, jsonify

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
    return bp
