"""Flask glue between the identity package and the search UI.

Kept out of ``app.py`` on purpose: that module is 2'600 lines and adding a
second authentication system inline would make both harder to read than either
is alone. Everything here is request plumbing — the decisions live in
``users.py`` and ``sessions.py``.

Connection strategy
-------------------
One connection per request, opened lazily the first time identity is touched
and closed at teardown. For this product that is the right trade: a 5–50 lawyer
firm produces a few requests a second at peak, a local PostgreSQL connect costs
single-digit milliseconds, and a search request costs hundreds. A pool
(``psycopg_pool``) is the upgrade path if a deployment ever proves otherwise;
it is not worth the dependency today.

Plan: docs/superpowers/plans/2026-08-14-section-b-buildout.md (KC-B1-6)
"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable

from flask import g, jsonify, redirect, request, session, url_for

from identity import db, sessions as sessions_mod, users as users_mod

logger = logging.getLogger(__name__)

SESSION_KEY = "sid"

#: Endpoints served without a signed-in user. Everything else needs one.
PUBLIC_ENDPOINTS = frozenset(
    {
        "static",
        "favicon",
        "login",
        "logout",
        "stats",
        "api_version",
        "health",
        "open_token_redeem",
        "open_tokens_spec",
    }
)

#: Reachable while the account still owes a password change. Deliberately tiny:
#: a forced rotation that the user can navigate around is not forced.
PASSWORD_CHANGE_ENDPOINTS = frozenset({"account_password", "logout", "static", "favicon"})


class IdentityGate:
    """Per-request identity for the Flask app."""

    def __init__(self, connect: Callable[[], Any] | None = None) -> None:
        self._connect = connect or _default_connect

    # ── per-request connection ─────────────────────────────────────────────

    def connection(self):
        """This request's connection, opened on first use."""
        conn = getattr(g, "_identity_conn", None)
        if conn is None:
            conn = self._connect()
            g._identity_conn = conn
        return conn

    def close(self, _exc: BaseException | None = None) -> None:
        conn = getattr(g, "_identity_conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                logger.debug("Identity connection close failed", exc_info=True)
            g._identity_conn = None

    def users(self) -> users_mod.UserRepository:
        return users_mod.UserRepository(self.connection())

    def sessions(self) -> sessions_mod.SessionStore:
        return sessions_mod.SessionStore(self.connection(), self.users())

    # ── the gate ───────────────────────────────────────────────────────────

    def current_session(self):
        """Resolve this request's session, once per request."""
        if "identity_session" in g:
            return g.identity_session
        resolved = self.sessions().resolve(session.get(SESSION_KEY))
        g.identity_session = resolved
        return resolved

    def current_user(self):
        found = self.current_session()
        return None if found is None else found.user

    def sign_in(self, user) -> None:
        """Start a session and hand the browser its id.

        ``session.clear()`` first: a fresh cookie for a fresh session is what
        stops a pre-authentication cookie from being reused afterwards.
        """
        opened = self.sessions().open(
            user, ip=_client_ip(), user_agent=request.headers.get("User-Agent")
        )
        session.clear()
        session.permanent = True
        session[SESSION_KEY] = str(opened.id)
        g.identity_session = opened

    def sign_out(self) -> None:
        current = session.get(SESSION_KEY)
        if current:
            self.sessions().revoke(current)
        session.clear()
        g.identity_session = None

    def guard(self):
        """``before_request`` handler: require a signed-in user.

        Returns 401 for API paths and a redirect for pages, because a browser
        following a redirect into an XHR is worse than an honest status code.
        """
        if request.endpoint in PUBLIC_ENDPOINTS:
            return None

        current = self.current_session()
        if current is None:
            if request.path.startswith("/api/"):
                return jsonify({"success": False, "error": "Anmeldung erforderlich"}), 401
            return redirect(url_for("login", next=request.full_path or "/"))

        if current.user.must_change_password and request.endpoint not in PASSWORD_CHANGE_ENDPOINTS:
            if request.path.startswith("/api/"):
                return jsonify(
                    {"success": False, "error": "Passwort muss geändert werden"}
                ), 403
            return redirect(url_for("account_password"))

        return None


def _default_connect():
    """Open a connection from the environment.

    ``PLATFORM_DB_DSN`` short-circuits the individual settings. It exists for
    tests, which need to pin a schema, and is documented rather than hidden so
    an operator debugging a connection has one obvious lever.
    """
    import psycopg

    dsn = (os.environ.get("PLATFORM_DB_DSN") or "").strip()
    if dsn:
        return psycopg.connect(dsn, autocommit=True)
    return db.connect()


def _client_ip() -> str | None:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr
