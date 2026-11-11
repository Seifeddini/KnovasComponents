"""The settings page's editable section: read, save and test the registered settings (WP KC-0.1).

Admins only, as the admin console: anyone else gets 403 here and does not see the section. Every
POST passes the app's X-CSRF-Token gate. A secret never appears in a response: the page shows
whether it is set, and a Test proves it works.
"""
from __future__ import annotations

import functools
import logging
from typing import Any, Callable, Dict, List

from flask import Blueprint, jsonify, request

import platform_settings as ps

logger = logging.getLogger(__name__)

#: The Platform's built-in roles, when the role table cannot be read.
BUILTIN_ROLES = ("admin", "approver", "ingestion_manager", "member")
BUNDLE_KEY = "views.config_source"
TOKEN_KEY = "views.change_requests.github_token"


def create_settings_blueprint(resolver: ps.Resolver, *, gate: Any,
                              roles: Callable[[], List[str]],
                              profiles: Callable[[], List[Dict[str, Any]]],
                              bundle_github: Callable[[str], Any],
                              repository: Callable[[], str],
                              make_repo: Callable[[str, str], Any]) -> Blueprint:
    """Build the blueprint.

    Args:
        resolver: the installation's platform_settings.Resolver.
        gate: the IdentityGate, or None when identity is off (read-only then: nothing can be saved).
        roles: the Platform's role keys.
        profiles: the profiles of the tenant's bundle, [{id, audience}], for the role picker.
        bundle_github: repository name -> GitHub client, for testing a github: bundle source.
        repository: the knowledge-base repository the token is for.
        make_repo: (repository, token) -> GitHub client, for testing a token.
    """
    bp = Blueprint("platform_settings", __name__)

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
            return view(*args, **kwargs)
        return wrapped

    def body() -> Dict[str, Any]:
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}

    def answer() -> Dict[str, Any]:
        try:
            found = profiles()
            profiles_error = None
        except Exception as exc:  # noqa: BLE001 - the picker falls back to free text
            from graph_views.bundle import BundleError

            # A refused bundle names its reason (no customer data); anything else only its type.
            found, profiles_error = [], str(exc) if isinstance(exc, (ps.SettingError, BundleError)) else type(exc).__name__
        return {"success": True, "settings": resolver.entries(), "can_edit": gate is not None and is_admin(),
                "roles": list(roles()), "profiles": found, "profiles_error": profiles_error}

    @bp.route("/api/settings/platform", methods=["GET"])
    @admin_only
    def read():
        return jsonify(answer())

    @bp.route("/api/settings/platform", methods=["POST"])
    @admin_only
    def save():
        if gate is None:
            return jsonify({"success": False, "error": "Einstellungen speichern braucht Benutzerkonten (identity.enabled)."}), 409
        values = body().get("values")
        if not isinstance(values, dict):
            return jsonify({"success": False, "error": "values fehlt."}), 400
        try:
            changed = resolver.save(user(), values, roles=roles())
        except ps.SettingError as exc:
            return jsonify({"success": False, "error": str(exc)}), exc.status
        return jsonify({**answer(), "changed": changed})

    @bp.route("/api/settings/platform/test/<key>", methods=["POST"])
    @admin_only
    def test(key: str):
        value = body().get("value")
        if key == BUNDLE_KEY:
            spec = str(value).strip() if value not in (None, "") else str(resolver.value(BUNDLE_KEY) or "")
            if not spec:
                return jsonify({"ok": False, "error": "Keine Quelle angegeben."})
            return jsonify(ps.test_bundle_source(spec, bundle_github))
        if key == TOKEN_KEY:
            token = str(value).strip() if value not in (None, "") else resolver.secret(TOKEN_KEY)
            return jsonify(ps.test_repository_access(repository(), token, make_repo))
        return jsonify({"ok": False, "error": f"Für {key} gibt es keinen Test."}), 404

    return bp
