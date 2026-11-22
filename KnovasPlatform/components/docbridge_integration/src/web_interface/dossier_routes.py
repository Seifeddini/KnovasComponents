"""The dossier's pages (PMVP-30a, 30b, 32a), on the Brain views' blueprint behind its switch and gate.

- ``GET /dossier/<node_id>``: the Überblick (facts, and the card «Läuft gerade» when the neighbours show the step the
  thing is in); ``?art=<kind>`` one kind's list; ``?tab=dokumente`` the documents. The lists sort, filter and page what
  was read (``listing``). It grants nothing.
- ``GET /dossier/<node_id>/dokument/<n>``: the n-th document of the whole list by name, read again as the person,
  checked with ``document_readable`` as the person, then granted to that person alone (``grant``) and opened where a
  search hit opens it (``open_target``: OneDrive, the browser's PDF viewer, the download); else the same 404.

Every address is built with url_for; the routes log no name, value or pointer, only the type of a failed read.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict, List, Mapping, Optional

from flask import Blueprint, abort, redirect, render_template, request, url_for

from brain_views import related as related_views
from brain_views.dossier import Document, Dossier, NotFound, Unavailable, node_documents, read_dossier
from brain_views.lists import listing
from brain_views.related import NOTHING, Related, read_related

logger = logging.getLogger(__name__)

_HTTP = re.compile(r"^https?://", re.IGNORECASE)
TAB_DOCUMENTS = "dokumente"
TAB_OVERVIEW = "ueberblick"
TAB_KIND = "art-"


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


def attach_dossier_routes(bp: Blueprint, *, refuse_unless_open: Callable[[], None], client: Callable[[], Any],
                          listable: Callable[[str], bool], grant: Callable[[List[Dict[str, Any]]], Any],
                          enhance: Callable[[Dict[str, Any]], Dict[str, Any]], pdf_inline: bool,
                          download_allowed: Callable[[], bool], page_context: Callable[[], Mapping[str, Any]]) -> None:
    """The dossier's two routes, on the Brain views' blueprint, each behind ``refuse_unless_open`` (the switch and the
    gate). The other arguments are create_brain_views_blueprint's."""

    @bp.route("/dossier/<node_id>")
    def dossier(node_id: str):
        refuse_unless_open()
        knovas = client()
        try:
            found = read_dossier(knovas, node_id, listable=listable)
        except NotFound:
            abort(404)
        except Unavailable as exc:
            logger.warning("Dossier: the node read failed (%s)", exc)
            return render_template("dossier.html", **dict(page_context()), dossier=None), 502
        try:
            near, near_failed = read_related(knovas, found.node_id), False
        except related_views.Unavailable as exc:
            logger.warning("Dossier: the neighbours read failed (%s)", exc)
            near, near_failed = NOTHING, True
        logger.debug("Dossier: %d related things in %d kinds", near.total, len(near.kinds))
        return render_template("dossier.html", **dict(page_context()),
                               **_page(found, near, near_failed, request.args))

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


def _page(found: Dossier, near: Related, near_failed: bool, args: Mapping[str, Any]) -> Dict[str, Any]:
    """What the template draws, with every address built here. A document opens through its position in the whole
    list by name, whatever the list's sort, filter and page.

    The tab: ``?tab=dokumente`` the documents; ``?art=<key>`` the list of one kind this thing has (any other key
    shows the Überblick, and no key from the address reaches the page); else the Überblick."""
    here = {"node_id": found.node_id}
    position = {document.pointer: index for index, document in enumerate(found.documents, start=1)}
    documents_tab = args.get("tab") == TAB_DOCUMENTS
    kind = None if documents_tab else near.kind(args.get("art"))
    if documents_tab:
        tab = TAB_DOCUMENTS
    elif kind is not None:
        tab = TAB_KIND + kind.key
    else:
        tab = TAB_OVERVIEW
    tabs = [{"key": TAB_OVERVIEW, "label": "Überblick", "href": url_for("brain_views.dossier", **here)}]
    tabs += [{"key": TAB_KIND + k.key, "label": k.name, "count": k.count,
              "href": url_for("brain_views.dossier", art=k.key, **here)} for k in near.kinds]
    tabs.append({"key": TAB_DOCUMENTS, "label": "Dokumente", "count": len(found.documents),
                 "href": url_for("brain_views.dossier", tab=TAB_DOCUMENTS, **here)})
    page: Dict[str, Any] = {
        "dossier": found,
        "tab": tab,
        "tabs": tabs,
        "near_failed": near_failed,
        "open_href": {pointer: url_for("brain_views.document", position=n, **here) for pointer, n in position.items()},
        "ref_href": {fact.ref_node_id: url_for("brain_views.dossier", node_id=fact.ref_node_id)
                     for fact in found.facts if fact.ref_node_id},
        "kind": kind,
        "listing": None,
        "running": _running(found, near),
    }

    def href(**query: Any) -> str:
        return url_for("brain_views.dossier", **here, **query)

    if documents_tab:
        page["listing"] = listing(found.documents, args, href=href, keep={"tab": TAB_DOCUMENTS}, name=lambda d: d.name)
    elif kind is not None:
        rows = listing(kind.things, args, href=href, keep={"art": kind.key}, name=lambda t: t.name,
                       relation=lambda t: t.relation)
        rows["rows"] = [{"name": t.name, "relation": t.relation,
                         "href": url_for("brain_views.dossier", node_id=t.id)} for t in rows["page"].items]
        page["listing"] = rows
    return page


def _running(found: Dossier, near: Related) -> Optional[Dict[str, str]]:
    """The card «Läuft gerade» (PMVP-32a): the step the thing is in, without the process's prefix, and the address of
    its process line; None when the neighbours show no single step of kind ProcessStep."""
    if near.in_state is None:
        return None
    return {"step": near.step_name, "process": near.process.name if near.process is not None else "",
            "href": url_for("brain_views.ablauf", node_id=found.node_id)}

