"""The members' Brain views as pages (PMVP-30a): the dossier of one thing, and the way to open one of its documents.

What the page shows comes from ``brain_views.dossier`` (read there as the signed-in person, per thing only). This
module adds the pages:

- ``GET /dossier/<node_id>``: Überblick (the facts); with ``?art=<kind>`` the list of the related things of one kind
  (PMVP-30b: ``brain_views.related``, one neighbours read and one round of kind reads per page view); with
  ``?tab=dokumente`` the node's documents. The lists sort (``?sort=``), filter (``?filter=``) and page (``?seite=``,
  50 a page, ``brain_views.limit.paged``) what was read. It grants nothing.
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

from brain_views import related as related_views
from brain_views.dossier import Document, Dossier, NotFound, Unavailable, node_documents, read_dossier
from brain_views.limit import page_number, paged
from brain_views.related import NOTHING, Related, read_related

logger = logging.getLogger(__name__)

SWITCH = "brain_views.enabled"
_ON = frozenset({"an", "on", "true", "1", "ja", "yes"})
_HTTP = re.compile(r"^https?://", re.IGNORECASE)
TAB_DOCUMENTS = "dokumente"
TAB_OVERVIEW = "ueberblick"
TAB_KIND = "art-"
SORT_LABELS = {related_views.SORT_NAME: "Name A–Z", related_views.SORT_NAME_DESC: "Name Z–A",
               related_views.SORT_RELATION: "Beziehung"}


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

    return bp


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
    }
    if documents_tab:
        page["listing"] = _listing(found.documents, args, keep={"tab": TAB_DOCUMENTS}, here=here,
                                   name=lambda d: d.name)
    elif kind is not None:
        listing = _listing(kind.things, args, keep={"art": kind.key}, here=here, name=lambda t: t.name,
                           relation=lambda t: t.relation)
        listing["rows"] = [{"name": t.name, "relation": t.relation,
                            "href": url_for("brain_views.dossier", node_id=t.id)} for t in listing["page"].items]
        page["listing"] = listing
    return page


def _listing(items: Any, args: Mapping[str, Any], *, keep: Dict[str, str], here: Dict[str, str],
             name: Callable[[Any], str], relation: Optional[Callable[[Any], Optional[str]]] = None) -> Dict[str, Any]:
    """One list (B-03): filtered and sorted over what was read, 50 a page; the form's fields and the page links.

    Only a known sort key and the cleaned filter go into an address, through url_for; ``keep`` holds the list's own
    key (the tab, or the kind's key as the Core gave it)."""
    items = list(items)
    has_relations = relation is not None and any(relation(item) for item in items)
    needle = related_views.filter_text(args.get("filter"))
    sort = related_views.sort_choice(args.get("sort"), relations=has_relations)
    arranged = related_views.arrange(items, name=name, needle=needle, sort=sort,
                                     relation=relation if has_relations else None)
    shown = paged(arranged, page_number(args.get("seite")))
    query = dict(keep)
    if sort != related_views.SORT_NAME:
        query["sort"] = sort
    if needle:
        query["filter"] = needle

    def at(number: Optional[int]) -> Optional[str]:
        if number is None:
            return None
        return url_for("brain_views.dossier", **here, **query, **({"seite": number} if number > 1 else {}))

    choices = (related_views.SORT_NAME, related_views.SORT_NAME_DESC) + (
        (related_views.SORT_RELATION,) if has_relations else ())
    return {
        "page": shown,
        "total": len(items),
        "filter": needle,
        "sort": sort,
        "sorts": [{"key": key, "label": SORT_LABELS[key]} for key in choices],
        "action": url_for("brain_views.dossier", **here),
        "hidden": keep,
        "previous_href": at(shown.previous),
        "next_href": at(shown.next),
        "next_count": min(shown.size, shown.total - shown.last) if shown.next else 0,
        "rows": [],
    }
