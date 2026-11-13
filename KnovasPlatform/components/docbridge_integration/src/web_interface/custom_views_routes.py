"""«Eigene Ansichten»: the page, the list, a view's document and its read (spec P-2 to P-4).

Blueprint-factory shape of views_routes: the app passes its collaborators in, so
this module imports nothing from app.py. The blueprint's name is not one of the
CSRF-exempt prefixes, so the app's header gate covers the read like every other
POST. While the feature is off for a request (``enabled``), or the signed-in
person may open no valid view (an administrator: while no view folder exists at
all, ``may_enter``), every route answers the same 404; a view the person may
not open answers the 404 of a view that does not exist.

Plan: docs/superpowers/plans/2026-11-13-custom-views.md (Task 9)
"""
from __future__ import annotations

import logging
import math
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from flask import Blueprint, abort, jsonify, make_response, render_template, request

from custom_views.budget import OneAtATime, shared_budget
from custom_views.document import build_document, new_nonce
from custom_views.query import Forbidden, QueryError, authorize, parse_query
from custom_views.read import BudgetExceeded, TooBroad, run_read
from custom_views.registry import Registry, View
from custom_views.settings import CustomViewsSettings
from graph_views.reader import GraphReader, ReadFailed
from graph_views.topology import subject_key
from identity import audit as audit_log

logger = logging.getLogger(__name__)

PAGE_HEADERS = {
    "X-Frame-Options": "SAMEORIGIN",
    "Content-Security-Policy": "frame-ancestors 'self'; frame-src 'none'; object-src 'none'; base-uri 'self'",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cache-Control": "no-store",
}
DOCUMENT_HEADERS = {
    "Content-Security-Policy": "sandbox",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
}

NOT_FOUND = "Diese Ansicht gibt es nicht."
SIGNED_OUT = "Nicht angemeldet."
REFUSED = "Diese Anfrage ist nicht erlaubt."
BUSY = "Es läuft schon eine Abfrage. Bitte warten, bis sie fertig ist."
BRAIN_DOWN = "Der Wissensgraph antwortet gerade nicht. Bitte erneut versuchen."

AUDIT_ACTION = "custom_view.read"


def _changed_at(mtime: float) -> str:
    return datetime.fromtimestamp(mtime, timezone.utc).isoformat(timespec="seconds")


def _error(status: int, message: str, **headers: str):
    response = jsonify({"success": False, "error": message})
    response.status_code = status
    response.headers.update(headers)
    return response


def _retry_after(seconds: float) -> str:
    return str(max(1, math.ceil(seconds)))


def may_enter(registry: Registry, user: Any) -> bool:
    """Whether this person gets the entry and the page: a valid view they may open; for an
    administrator any view folder at all, valid or invalid, so they can read why a view is
    invalid (spec P-1). A view's own routes still need it valid and allowed."""
    if user is None:
        return False
    if registry.for_roles(user.roles):
        return True
    return "admin" in user.roles and bool(registry.views() or registry.invalid())


class OncePer:
    """Lets a call through at most once per ``seconds``: a warning that would otherwise repeat on
    every request."""

    def __init__(self, seconds: float, clock: Callable[[], float] = time.monotonic):
        self._seconds = float(seconds)
        self._clock = clock
        self._last: Optional[float] = None
        self._lock = threading.Lock()

    def ready(self) -> bool:
        with self._lock:
            now = self._clock()
            if self._last is not None and now - self._last < self._seconds:
                return False
            self._last = now
            return True


def create_custom_views_blueprint(*, gate: Any, registry: Registry, settings: CustomViewsSettings,
                                  client: Callable[[], Any], cache: Any, workers: int,
                                  enabled: Callable[[], bool], page_context: Callable[[], dict],
                                  csrf_valid: Callable[[str], bool]) -> Blueprint:
    """Build the blueprint.

    Args:
        gate: the IdentityGate (the feature exists only with user accounts on).
        registry: the views folder.
        settings: the feature's settings (limits, budget).
        client: zero-arg callable -> the Knovas client the search uses (signed as the user).
        cache: the views' TopologyCache (per-subject topology and facts).
        workers: parallel requests per batch for the reader.
        enabled: True while the feature is on (switch, accounts, graph mode, no folder refusal).
        page_context: template variables of the platform layout (sidebar, brand, csrf, asset version).
        csrf_valid: the app's check of a submitted CSRF token.
    """
    bp = Blueprint("custom_views", __name__)
    # Its own refusing budget: custom views never wait on the views' pacer (spec P-4).
    budget = shared_budget(settings.facts_per_minute, capacity=settings.max_nodes)
    running = OneAtATime()

    def is_api() -> bool:
        return request.path.startswith("/api/")

    def not_found():
        if is_api():
            return _error(404, NOT_FOUND)
        abort(404)

    def user_views(user) -> list:
        return registry.for_roles(user.roles)

    def view_for(user, view_id: str) -> Optional[View]:
        view = registry.get(view_id)
        if view is None or not view.manifest.allows(user.roles):
            return None
        return view

    @bp.before_request
    def require_the_feature():
        """Off, signed out or without a view to enter by (``may_enter``): nothing is here."""
        if not enabled():
            return not_found()
        user = gate.current_user()
        if user is None:            # the identity gate refused first; this is the second line
            return _error(401, SIGNED_OUT) if is_api() else abort(404)
        if not may_enter(registry, user):
            return not_found()
        return None

    @bp.after_request
    def no_store(response):
        if is_api():
            response.headers.setdefault("Cache-Control", "no-store")
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response

    # -- the page -------------------------------------------------------------------

    @bp.route("/custom-views", defaults={"view_id": None})
    @bp.route("/custom-views/<view_id>")
    def page(view_id: Optional[str]):
        user = gate.current_user()
        views = user_views(user)
        if view_id is not None and view_for(user, view_id) is None:
            abort(404)
        is_admin = "admin" in user.roles
        response = make_response(render_template(
            "custom_views.html",
            active_nav="custom_views",
            **page_context(),
            custom_views=[{"id": v.manifest.id, "title": v.manifest.title.get("de", v.manifest.id)} for v in views],
            current_view=view_id,
            is_admin=is_admin,
            invalid_views=[{"id": i.id, "reason": i.reason} for i in registry.invalid()] if is_admin else [],
        ))
        response.headers.update(PAGE_HEADERS)
        return response

    # -- the list ---------------------------------------------------------------------

    @bp.route("/api/custom-views")
    def list_views():
        user = gate.current_user()
        is_admin = "admin" in user.roles
        items = []
        for view in user_views(user):
            manifest = view.manifest
            item = {"id": manifest.id, "title": dict(manifest.title),
                    "description": dict(manifest.description) if manifest.description is not None else None}
            if is_admin:
                item["files"] = [{"name": f.name, "sha256": f.sha256, "changed_at": _changed_at(f.mtime)}
                                 for f in view.files.values()]
            items.append(item)
        invalid = [{"id": i.id, "reason": i.reason} for i in registry.invalid()] if is_admin else []
        return jsonify({"views": items, "invalid": invalid})

    # -- the document -----------------------------------------------------------------

    @bp.route("/api/custom-views/<view_id>/document")
    def document(view_id: str):
        """For the Platform's page only: JSON behind the CSRF header, never a navigation (P-3)."""
        view = view_for(gate.current_user(), view_id)
        if view is None:
            return _error(404, NOT_FOUND)
        if not csrf_valid(str(request.headers.get("X-CSRF-Token", "") or "")):
            return _error(403, REFUSED)
        if (request.headers.get("Sec-Fetch-Dest") or "empty") != "empty":
            return _error(403, REFUSED)
        manifest = view.manifest
        response = jsonify({"title": dict(manifest.title), "html": build_document(view, new_nonce()),
                            "links": list(manifest.link_hosts)})
        response.headers.update(DOCUMENT_HEADERS)
        return response

    # -- the read ---------------------------------------------------------------------

    def audit(user, view_id: str, kind: str, *, nodes: int, complete: bool, outcome: str) -> None:
        """Who read which view, which type, how many nodes, complete or not; never content (P-4)."""
        audit_log.record(gate.connection(), action=AUDIT_ACTION, actor=user, target_type="custom_view",
                         target_id=view_id, outcome="ok" if outcome == "ok" else "denied",
                         detail={"type": kind, "nodes": nodes, "complete": complete, "outcome": outcome})

    @bp.route("/api/custom-views/<view_id>/read", methods=["POST"])
    def read(view_id: str):
        user = gate.current_user()
        view = view_for(user, view_id)
        if view is None:
            return _error(404, NOT_FOUND)
        try:
            query = parse_query(request.get_json(silent=True), max_nodes=settings.max_nodes)
        except QueryError as exc:
            return _error(400, str(exc))
        try:
            authorize(query, view.manifest)     # before anything is read; run_read checks again
        except Forbidden as exc:
            audit(user, view_id, query.type, nodes=0, complete=False, outcome="forbidden")
            return _error(403, str(exc))

        key = str(user.id)
        if not running.try_enter(key):
            return _error(429, BUSY, **{"Retry-After": "1"})
        try:
            knovas = client()
            reader = GraphReader(knovas, acquire=None, workers=workers)
            state = cache.get(subject_key(knovas), reader)
            answer = run_read(query, view.manifest, state, reader, budget, max_nodes=settings.max_nodes)
        except Forbidden as exc:
            audit(user, view_id, query.type, nodes=0, complete=False, outcome="forbidden")
            return _error(403, str(exc))
        except TooBroad as exc:
            return _error(422, str(exc))
        except BudgetExceeded as exc:
            return _error(429, str(exc), **{"Retry-After": _retry_after(exc.retry_after)})
        except PermissionError:
            return _error(401, SIGNED_OUT)
        except ReadFailed as exc:
            logger.warning("custom_views: brain read failed for %s: %s", view_id, exc)
            return _error(502, BRAIN_DOWN)
        finally:
            running.leave(key)
        audit(user, view_id, query.type, nodes=len(answer["nodes"]), complete=bool(answer["complete"]),
              outcome="ok")
        return jsonify(answer)

    return bp
