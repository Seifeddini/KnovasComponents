"""The members' Brain views as pages (PMVP-30a): the dossier of one thing, and the way to open one of its documents.

What the page shows comes from ``brain_views.dossier`` (read there as the signed-in person, per thing only). This
module adds the pages:

- ``GET /dossier/<node_id>``: Überblick (the facts) and, with ``?tab=dokumente``, the node's documents. It grants
  nothing.
- ``GET /dossier/<node_id>/dokument/<n>``: opens the n-th document of that list (1 is the first). It reads the node's
  documents again as the person, asks the Core whether the person may read that one document
  (``document_readable``, as the person), and only then grants that one document to that person, the way search
  grants its rows (app.py, ``_grant_for_current_user``, handed in as ``grant``), and sends the browser where a search
  hit opens it. Not readable, out of range, or any failure: the same 404 as for a document that is not there.
- **Where a document opens**, as a search hit opens it, from what the web app hands in: the row goes through the
  search's own enrichment (``enhance``: app.py ``_enhance_listing_rows``, open hints and OneDrive links); a document
  with a OneDrive or SharePoint address opens there (``document_external_open``, the hit's «In OneDrive öffnen»); a
  PDF opens in the browser's viewer when the installation shows PDFs inline (``preview_document``, the hit's preview);
  otherwise the file is handed over when the installation offers downloads (``download_document``, the hit's
  «Download»). A document none of these reaches opens only on the person's own computer (the hit's «Öffnen»); the
  page says so and grants nothing.
- **Experiment documents** (``experiments/<domain>/<KEY>``) are left out of the list, of the facts' sources and of
  every grant, by the function search uses to take them out of its answers (``split``, handed in).
- **The switch.** ``brain_views.enabled`` in the settings registry (platform_settings.py), installation-wide, off by
  default, pinned by ``BRAIN_VIEWS_ENABLED``. Off, and without per-user accounts, both addresses answer 404. The login
  gate before every route (identity.webauth) sends a signed-out caller to sign-in first.

Every address is built here with url_for. The routes log no name, value or pointer; only the type of a failed read.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict, List, Mapping, Optional

from flask import Blueprint, abort, redirect, render_template, request, url_for

from brain_views.dossier import Document, Dossier, NotFound, Unavailable, node_documents, read_dossier

logger = logging.getLogger(__name__)

SWITCH = "brain_views.enabled"
_ON = frozenset({"an", "on", "true", "1", "ja", "yes"})
_HTTP = re.compile(r"^https?://", re.IGNORECASE)
TAB_DOCUMENTS = "dokumente"
TAB_OVERVIEW = "ueberblick"


def switch_on(value: Any) -> bool:
    """The switch's value as the page saves it ("an", "aus") or as the environment pins it ("true", "1", ...)."""
    return str(value if value is not None else "").strip().lower() in _ON


def _row(document: Document) -> Dict[str, Any]:
    """A document in the shape of a search row (doc_id and path are the pointer, as on search and listing rows)."""
    return {"doc_id": document.pointer, "path": document.pointer, "pointer": document.pointer,
            "title": document.name, "source": "semantix"}


def open_target(row: Mapping[str, Any], *, pdf_inline: bool, download_allowed: bool) -> Optional[str]:
    """Where a search hit with this (enriched) row opens, as an address of the Platform; None when only the
    person's own computer can open it."""
    pointer = str(row.get("doc_id") or "")
    path = str(row.get("path") or pointer)
    external = str(row.get("external_url") or "").strip()
    if external and _HTTP.match(external):
        return url_for("document_external_open", doc_id=pointer, path=path)
    if pointer.lower().endswith(".pdf") and pdf_inline:
        return url_for("preview_document", doc_id=pointer, path=path)
    if download_allowed:
        return url_for("download_document", doc_id=pointer, path=path)
    return None


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

    @bp.route("/dossier/<node_id>")
    def dossier(node_id: str):
        refuse_unless_open()
        tab = TAB_DOCUMENTS if request.args.get("tab") == TAB_DOCUMENTS else TAB_OVERVIEW
        try:
            found = read_dossier(client(), node_id, listable=listable)
        except NotFound:
            abort(404)
        except Unavailable as exc:
            logger.warning("Dossier: the node read failed (%s)", exc)
            return render_template("dossier.html", **dict(page_context()), dossier=None), 502
        return render_template("dossier.html", **dict(page_context()), **_page(found, tab))

    @bp.route("/dossier/<node_id>/dokument/<int:position>")
    def document(node_id: str, position: int):
        refuse_unless_open()
        knovas = client()
        try:
            documents = node_documents(knovas, node_id, listable=listable)
        except (NotFound, Unavailable) as exc:
            if isinstance(exc, Unavailable):
                logger.warning("Dossier: the node read for a document failed (%s)", exc)
            abort(404)
        if not 1 <= position <= len(documents):
            abort(404)
        chosen = documents[position - 1]
        try:
            readable = knovas.document_readable(chosen.pointer) is True
        except Exception as exc:  # noqa: BLE001 - the client never raises here; if it did, the door stays shut
            logger.warning("Dossier: the readability check failed (%s)", type(exc).__name__)
            readable = False
        if not readable:
            abort(404)
        row = _row(chosen)
        try:
            enriched = (enhance({"results": [dict(row)]}) or {}).get("results") or [row]
        except Exception as exc:  # noqa: BLE001 - without the enrichment the plain row still opens
            logger.warning("Dossier: the open hints failed (%s)", type(exc).__name__)
            enriched = [row]
        target = open_target(enriched[0], pdf_inline=pdf_inline, download_allowed=bool(download_allowed()))
        if target is None:
            return render_template(
                "dossier.html", **dict(page_context()), dossier=None,
                message_title="Nur auf Ihrem Rechner",
                message=("Dieses Dokument öffnet die Plattform nicht im Browser. Suchen Sie es in der Suche und "
                         "öffnen Sie es dort mit «Öffnen» auf Ihrem Rechner.")), 409
        grant([row])
        return redirect(target)

    return bp


def _page(found: Dossier, tab: str) -> Dict[str, Any]:
    """What the template draws, with every address built here. A document opens through its position in the list."""
    here = {"node_id": found.node_id}
    position = {document.pointer: index for index, document in enumerate(found.documents, start=1)}
    return {
        "dossier": found,
        "tab": tab,
        "tabs": [
            {"key": TAB_OVERVIEW, "label": "Überblick", "href": url_for("brain_views.dossier", **here)},
            {"key": TAB_DOCUMENTS, "label": "Dokumente", "count": len(found.documents),
             "href": url_for("brain_views.dossier", tab=TAB_DOCUMENTS, **here)},
        ],
        "open_href": {pointer: url_for("brain_views.document", position=n, **here) for pointer, n in position.items()},
        "ref_href": {fact.ref_node_id: url_for("brain_views.dossier", node_id=fact.ref_node_id)
                     for fact in found.facts if fact.ref_node_id},
    }
