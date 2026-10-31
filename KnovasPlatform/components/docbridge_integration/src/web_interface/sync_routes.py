"""The sync page ("Sync von GitHub", WP KC-0.2) and its JSON, for tenant admins only.

GET /sync draws the page; GET /api/sync answers the settings (the token only as set or not set), the last
successful sync, the latest runs and the history; POST /api/sync/check, /probe and /apply start the steps
(probe and apply run in the background: 202 with the run's id; GET /api/sync/runs/<id> follows it). Every POST
passes the app's X-CSRF-Token gate.
"""
from __future__ import annotations

import functools
import logging
from typing import Any

from flask import Blueprint, jsonify, render_template

from brain_sync import Busy, SyncError, SyncService

logger = logging.getLogger(__name__)


def create_sync_blueprint(service: SyncService, *, gate: Any, page_context=None) -> Blueprint:
    bp = Blueprint("brain_sync", __name__)

    def user():
        return gate.current_user() if gate is not None else None

    def is_admin() -> bool:
        if gate is None:
            return True                       # one shared login: the deployment's operator
        u = user()
        return u is not None and "admin" in (getattr(u, "roles", ()) or ())

    def admin_only(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            if gate is not None and user() is None:
                return jsonify({"success": False, "error": "Nicht angemeldet."}), 401
            if not is_admin():
                return jsonify({"success": False, "error": "Nur für Administratoren."}), 403
            try:
                return view(*args, **kwargs)
            except Busy as exc:
                return jsonify({"success": False, "error": str(exc), "busy": True}), 409
            except SyncError as exc:
                return jsonify({"success": False, "error": str(exc)}), exc.status
        return wrapped

    def state():
        token_set = False
        try:
            token_set = service.token_set()
        except Exception as exc:  # noqa: BLE001
            logger.warning("brain sync: token state unknown (%s)", type(exc).__name__)
        try:
            version = service.runner.version()
        except ImportError:
            version = None
        history = service.history()
        return {"success": True, "settings": {**service.settings(), "token": "gesetzt" if token_set else "nicht gesetzt"},
                "current": service.current(), "history": history,
                "running": next((r for r in history if r["status"] == "running"), None), "package": version}

    @bp.route("/sync")
    def sync_page():
        if gate is not None and user() is None:
            return jsonify({"success": False, "error": "Nicht angemeldet."}), 401
        if not is_admin():
            return jsonify({"success": False, "error": "Nur für Administratoren."}), 403
        extra = page_context() if page_context else {}
        return render_template("sync.html", **extra)

    @bp.route("/api/sync", methods=["GET"])
    @admin_only
    def sync_state():
        return jsonify(state())

    @bp.route("/api/sync/check", methods=["POST"])
    @admin_only
    def sync_check():
        return jsonify({"success": True, **service.check(user())})

    @bp.route("/api/sync/probe", methods=["POST"])
    @admin_only
    def sync_probe():
        return jsonify({"success": True, "run_id": service.probe(user())}), 202

    @bp.route("/api/sync/apply", methods=["POST"])
    @admin_only
    def sync_apply():
        return jsonify({"success": True, "run_id": service.apply(user())}), 202

    @bp.route("/api/sync/runs/<run_id>", methods=["GET"])
    @admin_only
    def sync_run(run_id: str):
        import uuid

        try:
            uuid.UUID(run_id)
        except ValueError:
            return jsonify({"success": False, "error": "Lauf nicht gefunden."}), 404
        found = service.run(run_id)
        if found is None or found["tenant"] != service.tenant():
            return jsonify({"success": False, "error": "Lauf nicht gefunden."}), 404
        return jsonify({"success": True, "run": found})

    return bp
