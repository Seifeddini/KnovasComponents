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
    return bp
