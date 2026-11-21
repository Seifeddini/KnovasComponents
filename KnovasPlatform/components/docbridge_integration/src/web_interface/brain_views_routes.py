"""The members' Brain views as pages (PMVP-30a): ``GET /dossier/<node_id>``, the dossier of one thing.

What the page shows comes from ``brain_views.dossier`` (read there as the signed-in person, per thing only). This
module adds the page:

- **The switch.** ``brain_views.enabled`` in the settings registry (platform_settings.py), installation-wide, off by
  default, pinned by ``BRAIN_VIEWS_ENABLED``. Off, and without per-user accounts, the address answers 404. The login
  gate before every route (identity.webauth) sends a signed-out caller to sign-in first.
- **The same 404** for a node that does not exist and one the person may not see (the Core answers both alike);
  the page says nothing about which.
- **Opening a document.** The node's documents are the ones the Core listed for this person (it narrows them by
  the person's access before it answers). They are granted to the person the way the search page grants its rows
  (app.py, _grant_for_current_user, handed in as ``grant``), so the Platform's file routes serve them; nothing else
  is granted. Every address is built here with url_for: a PDF opens in the browser's viewer, the way the search
  page shows a PDF hit (``preview_document``), any other file is handed over (``download_document``).
- **Tabs**: Überblick (the facts) and Dokumente, as ``?tab=dokumente``.

The route logs no name, value or pointer; only the type of a failed read.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Mapping

from flask import Blueprint, abort, render_template, request, url_for

from brain_views.dossier import Dossier, NotFound, Unavailable, read_dossier

logger = logging.getLogger(__name__)

SWITCH = "brain_views.enabled"
_ON = frozenset({"an", "on", "true", "1", "ja", "yes"})
TAB_DOCUMENTS = "dokumente"
TAB_OVERVIEW = "ueberblick"


def switch_on(value: Any) -> bool:
    """The switch's value as the page saves it ("an", "aus") or as the environment pins it ("true", "1", ...)."""
    return str(value if value is not None else "").strip().lower() in _ON


def open_href(pointer: str) -> str:
    """Where a document opens: a PDF in the browser's own viewer, as a search hit does; any other file handed over."""
    if pointer.lower().endswith(".pdf"):
        return url_for("preview_document", doc_id=pointer, path=pointer)
    return url_for("download_document", doc_id=pointer, path=pointer)


def create_brain_views_blueprint(gate: Any, *, client: Callable[[], Any], settings: Callable[[], Any],
                                 grant: Callable[[List[Dict[str, Any]]], Any],
                                 page_context: Callable[[], Mapping[str, Any]]) -> Blueprint:
    """``gate``: the identity gate (None without per-user accounts: then nothing answers). ``client``: the one Knovas
    client of the web app, which signs every call as the person signed in. ``settings``: the settings resolver.
    ``grant``: records documents a listing handed the person (app.py). ``page_context``: the frame's values."""
    bp = Blueprint("brain_views", __name__)

    def enabled() -> bool:
        if gate is None:
            return False
        try:
            return switch_on(settings().value(SWITCH))
        except Exception as exc:  # noqa: BLE001 - an unreadable switch is off
            logger.warning("Brain views: the switch is not readable (%s)", type(exc).__name__)
            return False

    @bp.route("/dossier/<node_id>")
    def dossier(node_id: str):
        if not enabled() or gate.current_user() is None:
            abort(404)
        tab = TAB_DOCUMENTS if request.args.get("tab") == TAB_DOCUMENTS else TAB_OVERVIEW
        try:
            found = read_dossier(client(), node_id)
        except NotFound:
            abort(404)
        except Unavailable as exc:
            logger.warning("Dossier: the node read failed (%s)", exc)
            return render_template("dossier.html", **dict(page_context()), dossier=None, unavailable=True), 502
        grant([{"pointer": document.pointer} for document in found.documents])
        return render_template("dossier.html", **dict(page_context()), **_page(found, tab))

    return bp


def _page(found: Dossier, tab: str) -> Dict[str, Any]:
    """What the template draws, with every address built here."""
    here = {"node_id": found.node_id}
    return {
        "dossier": found,
        "unavailable": False,
        "tab": tab,
        "tabs": [
            {"key": TAB_OVERVIEW, "label": "Überblick", "href": url_for("brain_views.dossier", **here)},
            {"key": TAB_DOCUMENTS, "label": "Dokumente", "count": len(found.documents),
             "href": url_for("brain_views.dossier", tab=TAB_DOCUMENTS, **here)},
        ],
        "open_href": {document.pointer: open_href(document.pointer) for document in found.documents},
        "ref_href": {fact.ref_node_id: url_for("brain_views.dossier", node_id=fact.ref_node_id)
                     for fact in found.facts if fact.ref_node_id},
    }
