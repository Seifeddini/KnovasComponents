"""The members' Brain views as pages (PMVP-30a, 30b, 32a): one blueprint, its switch and its gate.

The views' own routes are attached here from their modules, on this one blueprint (its endpoints stay
``brain_views.<name>``), behind the gate this module gives them:

- the dossier and the way to open one of its documents (``dossier_routes.attach_dossier_routes``: ``GET
  /dossier/<node_id>``, ``GET /dossier/<node_id>/dokument/<n>``);
- the process line (``process_line_routes.attach_process_line_routes``: ``GET /ablauf/<node_id>``).

**The switch.** ``brain_views.enabled`` in the settings registry (platform_settings.py), installation-wide, off by
default, pinned by ``BRAIN_VIEWS_ENABLED``. Off, and without per-user accounts, every address answers 404 and the Core
is not asked. The login gate before every route (identity.webauth) sends a signed-out caller to sign-in first.

**Experiment documents** (``experiments/<domain>/<KEY>``) are never listable: ``listable`` asks the function search
uses to take them out of its answers (``split``, handed in).

Every address is built with url_for. The routes log no name, value or pointer; only the type of a failed read.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Mapping

from flask import Blueprint, abort

from web_interface.dossier_routes import attach_dossier_routes
from web_interface.process_line_routes import attach_process_line_routes

logger = logging.getLogger(__name__)

SWITCH = "brain_views.enabled"
_ON = frozenset({"an", "on", "true", "1", "ja", "yes"})


def switch_on(value: Any) -> bool:
    """The switch's value as the page saves it ("an", "aus") or as the environment pins it ("true", "1", ...)."""
    return str(value if value is not None else "").strip().lower() in _ON


def create_brain_views_blueprint(gate: Any, *, client: Callable[[], Any], settings: Callable[[], Any],
                                 grant: Callable[[List[Dict[str, Any]]], Any],
                                 split: Callable[[Any], Any],
                                 enhance: Callable[[Dict[str, Any]], Dict[str, Any]],
                                 pdf_inline: bool, download_allowed: Callable[[], bool],
                                 page_context: Callable[[], Mapping[str, Any]]) -> Blueprint:
    """``gate``: the identity gate (None without per-user accounts: then nothing answers). ``client``: the one Knovas
    client of the web app, which signs every call as the person signed in. ``settings``: the settings resolver.
    ``grant``: records documents handed to the person (app.py). ``split``: search's split of experiment documents
    from the rest. ``enhance``: search's enrichment of a listing's rows. ``pdf_inline``, ``download_allowed``: the
    installation's open settings as the search page has them. ``page_context``: the frame's values."""
    bp = Blueprint("brain_views", __name__)

    def enabled() -> bool:
        if gate is None:
            return False
        try:
            return switch_on(settings().value(SWITCH))
        except Exception as exc:  # noqa: BLE001 - an unreadable switch is off
            logger.warning("Brain views: the switch is not readable (%s)", type(exc).__name__)
            return False

    def listable(pointer: str) -> bool:
        """False for an experiment document: search's own split keeps nothing of it."""
        kept = (split({"results": [{"doc_id": pointer, "path": pointer, "pointer": pointer}]})[0] or {})
        return bool(kept.get("results"))

    def refuse_unless_open() -> None:
        if not enabled() or gate.current_user() is None:
            abort(404)

    attach_dossier_routes(bp, refuse_unless_open=refuse_unless_open, client=client, listable=listable, grant=grant,
                          enhance=enhance, pdf_inline=pdf_inline, download_allowed=download_allowed,
                          page_context=page_context)
    attach_process_line_routes(bp, refuse_unless_open=refuse_unless_open, client=client, page_context=page_context)
    return bp
