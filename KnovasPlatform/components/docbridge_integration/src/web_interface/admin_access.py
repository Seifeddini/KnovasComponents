"""«Zugriff» in Admin: «Was sieht …?» (PMVP-41; plan FEAT-2026-11-19-platform-mvp § 4 A-05, SPEC-platform-mvp-13).

An administrator picks a person, a role, the Knovas Assistent (for a person) or an external agent, and asks three
questions. The routes, all in the console's blueprint behind its ``require_admin``; every POST reads ``csrf_token``
first (400, nothing asked, no audit row):

    GET  /admin/access          (admin.access)         the page and its three questions; writes nothing, asks no Core
    POST /admin/access/reach    (admin.access_reach)   wer, fuer, seite: what X reads (folder rules, documents with
                                                       rights of their own), at most 25 rows, the rest a count that
                                                       opens the list page by page (``brain_views.limit``)
    POST /admin/access/check    (admin.access_check)   wer, fuer, pointer: may X read this document, yes or no, why
    POST /admin/access/search   (admin.access_search)  wer, fuer, query: what a search finds as X, names only

``wer`` is ``person:<id>``, ``role:<id>`` or ``agent:<id>``; ``fuer`` the person the Knovas Assistent acts for (it
never acts alone), ignored for everyone else. X's groups are exactly what the Core would receive in X's claim
(design ``docs/platform-mvp/design/role-builder.md`` § 2.3, § 4): a person's claim groups (RB-2), a role's Core group,
an agent's one claim (RB-6, ``AgentStore.claim_for``). Who reads what is RB-5's rule, read per thing through RB-5's
readers (``admin_role_data``): a document's groups are its own rights, else those of the longest folder rule its pointer
starts with, else none (open); X reads it when it is open or one of X's groups is one of them or above one.

Before «Ein» every answer is computed ("what X would read under «Ein»") and says so. Once the client reports the
tenant as ``brokered`` (``tenant_brokered``), the yes or no and the search are Knovas's own, asked inside
``client.asserting_for(X)``; the reach stays computed (Knovas has no such route), and so does everything about a role
(a role has no claim). The computation explains and previews; nothing is filtered or hidden on its strength.

Every answered question writes one audit row ``access.what_sees`` by id (best effort: it records a look, not a
change); logs hold ids, counts and error classes only, never a pointer, query, title, name or group.
Work order docs/platform-mvp/work-orders/PMVP-41.md; frozen tests tests/test_pmvp41_what_sees_acceptance.py.
"""
from __future__ import annotations

import logging
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

from flask import render_template, request

from brain_views.limit import MAX_DRAWN, bounded, more_text, page_number, paged
from identity import audit
from identity.agents import ASSISTANT, AgentError, AgentNotFound, AgentPrincipal, AgentStore
from identity.roles import MAX_CLAIM_GROUPS, RoleNotFound, RoleStore, TooManyGroupsError, claim_groups
from web_interface.admin_role_data import (
    PAGE_WALK,
    POINTER_MAX,
    CoreUnreadable,
    Sight,
    WalkUnfinished,
    core_groups,
    governing,
    groups_of,
    live_rules,
    own_rights,
    read_core,
    tenant_brokered,
    walk,
)

logger = logging.getLogger(__name__)

WHAT_SEES = "access.what_sees"
#: The subject's kind in ``wer`` and the audit row's ``target_type``.
TARGET_TYPES = {"person": "user", "role": "role", "agent": "agent"}
QUERY_MAX = 500
VORSCHAU, EIN = "vorschau", "ein"

_EXPIRED = "Formular ist abgelaufen. Bitte erneut versuchen."
_WHO = "Bitte eine Person, eine Rolle oder einen Agenten wählen."
_NO_PERSON = "Diese Person gibt es nicht."
_NO_ROLE = "Diese Rolle gibt es nicht."
_NO_AGENT = "Diesen Agenten gibt es nicht."
_FOR_WHOM = "Der Knovas Assistent handelt immer für eine Person: Bitte unter «für» eine Person wählen."
_NO_POINTER = f"Bitte ein Dokument angeben: seinen Pfad, 1 bis {POINTER_MAX} Zeichen, ohne Steuerzeichen."
_UNKNOWN_DOCUMENT = "Dieses Dokument kennt Knovas nicht."
_ROLE_SEARCH = "Eine Rolle sucht nicht: Bitte eine Person oder einen Agenten wählen."
_NO_QUERY = f"Bitte einen Suchbegriff angeben, 1 bis {QUERY_MAX} Zeichen."
_UNREADABLE = "Knovas hat nicht geantwortet. Es wird nichts gezeigt; bitte später erneut versuchen."


class Refused(Exception):
    """A question refused before or while Knovas is asked: plain German, safe to show; ``status`` is the answer's."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Subject:
    """X: whose sight a question asks about, with the groups X's claim would carry."""

    wer: str
    kind: str  # person | role | agent
    id: str
    name: str
    groups: Tuple[str, ...] = ()
    #: whose claim ``asserting_for`` carries: the person, or an ``AgentPrincipal``; None for a role (no claim)
    principal: Any = None
    #: why X reads nothing (the Platform signs no claim): deaktiviert | widerrufen | person-inaktiv | zu-viele-gruppen
    nothing: Optional[str] = None
    #: the person the assistant acts for, by id, and her name for the page
    fuer: Optional[str] = None
    fuer_name: Optional[str] = None

    @property
    def target_type(self) -> str:
        return TARGET_TYPES[self.kind]

    @property
    def label(self) -> str:
        """How the page names X: the name, and for the assistant whom it acts for."""
        return f"{self.name} für {self.fuer_name}" if self.fuer_name else self.name


def _ident(value: Any) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(str(value).strip()) if isinstance(value, str) and value.strip() else None
    except ValueError:
        return None


def resolve(conn, users, wer: Any, fuer: Any) -> Subject:
    """X from the form's ``wer`` (and ``fuer`` for the assistant); ``Refused`` (400) for anything else. Asks no Core."""
    kind, sep, rest = (wer if isinstance(wer, str) else "").strip().partition(":")
    ident = _ident(rest) if sep and kind in TARGET_TYPES else None
    if ident is None:
        raise Refused(_WHO)
    wer = f"{kind}:{ident}"
    if kind == "person":
        person = users.get(ident)
        if person is None:
            raise Refused(_NO_PERSON)
        name = person.display_name or person.email
        if not person.is_active:
            return Subject(wer, kind, str(person.id), name, nothing="deaktiviert")
        groups = tuple(claim_groups(conn, person.id))
        if len(groups) > MAX_CLAIM_GROUPS:
            return Subject(wer, kind, str(person.id), name, nothing="zu-viele-gruppen")
        return Subject(wer, kind, str(person.id), name, groups, principal=person)
    if kind == "role":
        try:
            role = RoleStore(conn).get_role(ident)
        except RoleNotFound:
            raise Refused(_NO_ROLE) from None
        return Subject(wer, kind, str(role.id), role.name, (role.core_group_id,) if role.core_group_id else ())
    store = AgentStore(conn)
    try:
        agent = store.get_agent(ident)
    except AgentNotFound:
        raise Refused(_NO_AGENT) from None
    behalf, person = None, None
    if agent.kind == ASSISTANT:  # «fuer» counts for the assistant only; every other agent's person is its own
        behalf = _ident(fuer)
        person = users.get(behalf) if behalf is not None else None
        if person is None:
            raise Refused(_FOR_WHOM)
    acts_for = {"fuer": str(person.id), "fuer_name": person.display_name or person.email} if person else {}
    try:
        claim = store.claim_for(agent.id, on_behalf_of=behalf)
    except AgentError:
        why = "widerrufen" if agent.revoked else "person-inaktiv"
        return Subject(wer, kind, str(agent.id), agent.name, nothing=why, **acts_for)
    except TooManyGroupsError:
        return Subject(wer, kind, str(agent.id), agent.name, nothing="zu-viele-gruppen", **acts_for)
    return Subject(wer, kind, str(agent.id), agent.name, tuple(claim.groups),
                   principal=AgentPrincipal(agent.id, claim.on_behalf_of), **acts_for)


# -- who reads what: RB-5's rule, read per thing --------------------------------------------------------------------


class _View:
    """X's groups against the Core's tree: whether X reads a set of groups, and the groups at and below X's."""

    def __init__(self, client, subject: Subject) -> None:
        self.sight = Sight(core_groups(client), {})
        self.mine = frozenset(subject.groups)

    def reads(self, groups) -> bool:
        """Open, or one of X's groups is one of ``groups`` or above one of them."""
        return not groups or bool(self.mine & self.sight.closure(groups))

    def via(self, groups) -> List[str]:
        """X's groups through which X reads ``groups`` (none for an open set)."""
        return sorted(self.mine & self.sight.closure(groups)) if groups else []

    def below(self) -> List[str]:
        tree = self.sight.tree
        return sorted({d for g in self.mine for d in (g, *tree.descendants(g))})

    def name(self, group: str) -> str:
        return self.sight.tree.name(group)

    def named(self, groups) -> List[Dict[str, str]]:
        return sorted(({"id": g, "name": self.name(g)} for g in groups), key=lambda g: (g["name"].casefold(), g["id"]))


def reach_rows(client, subject: Subject) -> Tuple[List[dict], bool]:
    """Every walled folder rule and every document with rights of its own that X reads, and whether a walk of the
    Core's documents stopped before its end (the list may then be incomplete). Rules first, by prefix; then documents,
    by pointer. A document governed by a rule shows through its rule; open things are no rows."""
    if subject.nothing:
        return [], False
    view = _View(client, subject)
    rules = [{"key": f"rule:{r['rule_id']}", "kind": "folder", "text": r["pointer_prefix"], "title": None,
              "groups": view.named(r["access_groups"])}
             for r in live_rules(client) if r["access_groups"] and view.reads(r["access_groups"])]
    documents, seen, partial = [], set(), False
    for group in view.below():  # X's groups and every group below them: a document of those X reads
        try:
            for entry in walk(client, pages=PAGE_WALK, group=group):
                ident, pointer = str(entry.get("document_uuid") or ""), entry.get("pointer")
                groups = groups_of(entry)
                if not ident or ident in seen or not isinstance(pointer, str) or not own_rights(entry) \
                        or not view.reads(groups):
                    continue
                seen.add(ident)
                documents.append({"key": f"document:{ident}", "kind": "document", "text": pointer,
                                  "title": str(entry.get("title") or "") or None, "groups": view.named(groups)})
        except WalkUnfinished:
            partial = True
    return (sorted(rules, key=lambda r: (r["text"], r["key"])) + sorted(documents, key=lambda d: (d["text"], d["key"])),
            partial)


def reach_page(rows: List[dict], seite: int) -> dict:
    """Page 1: at most 25 rows (``bounded``), the rest a count. Page 2, 3, …: the rest, 50 rows a page (``paged``)."""
    first = bounded(rows)
    drawn, rest = len(first.shown), rows[len(first.shown):]
    pages = 1 + (paged(rest, 1).pages if rest else 0)
    if seite <= 1 or not rest:
        return {"rows": list(first.shown), "more": first.rest, "page": 1, "pages": pages,
                "first": 1 if drawn else 0, "last": drawn}
    page = paged(rest, seite - 1)  # a page past the last gives the last
    return {"rows": list(page.items), "more": page.total - page.last, "page": page.page + 1, "pages": pages,
            "first": drawn + page.first, "last": drawn + page.last}


def check_document(client, subject: Subject, pointer: str, ein: bool) -> dict:
    """Yes or no for one document, with its reason. Computed before «Ein» (and always for a role); once the tenant is
    ``brokered``, Knovas's own ``document_readable`` asked as X, which counts even where it differs."""
    current = read_core("a document's rights", client.document_access, pointer)
    if not isinstance(current, Mapping):
        raise Refused(_UNKNOWN_DOCUMENT)
    rules = live_rules(client)
    try:
        entry = next((e for e in walk(client, pages=PAGE_WALK, prefix=pointer)
                      if e.get("pointer") == pointer or pointer in (e.get("pointers") or ())), None)
    except WalkUnfinished:
        entry = None
    own = entry is not None and own_rights(entry)
    rule = None if own else governing(rules, pointer)
    groups = tuple(rule["access_groups"]) if rule is not None else groups_of(current)
    if rule is not None and not groups:
        rule = None  # a rule without a group leaves the document open: no rule gives it groups
    view = _View(client, subject)
    computed = subject.nothing is None and view.reads(groups)
    answer, source = computed, "berechnet"
    if ein and subject.principal is not None and subject.nothing is None:
        with client.asserting_for(subject.principal):
            answer = read_core("a document's readability", client.document_readable, pointer) is True
        source = "knovas"
    return {"pointer": pointer, "answer": "ja" if answer else "nein", "source": source,
            "computed": "ja" if computed else "nein", "own": own and bool(groups),
            "rule": {"id": rule["rule_id"], "prefix": rule["pointer_prefix"]} if rule is not None else None,
            "groups": view.named(groups), "via": [view.name(g) for g in view.via(groups)]}


def search_as(client, subject: Subject, query: str, ein: bool) -> dict:
    """The search inside ``asserting_for(X)``: at most 25 hits, each by name (title, pointer), never passage text.
    Before «Ein» each hit carries what X would read, from ``document_access`` of its pointer (one read a hit)."""
    if subject.nothing:
        return {"hits": [], "total": 0}
    with client.asserting_for(subject.principal):
        found = read_core("a search", client.search_documents, query, limit=MAX_DRAWN)
    rows = [r for r in (found.get("results") or ()) if isinstance(r, Mapping)] if isinstance(found, Mapping) else []
    hits = []
    for row in bounded(rows).shown:
        pointer = str(row.get("doc_id") or row.get("path") or "")
        title = str(row.get("title") or "") or pointer.rsplit("/", 1)[-1]
        hits.append({"pointer": pointer, "title": title, "would": None})
    if not ein and hits:
        view = _View(client, subject)
        for hit in hits:
            try:
                acl = read_core("a document's rights", client.document_access, hit["pointer"]) if hit["pointer"] \
                    else None
            except CoreUnreadable:
                continue  # that hit stays without a mark («unbekannt»)
            hit["would"] = "ja" if isinstance(acl, Mapping) and view.reads(groups_of(acl)) else "nein"
    total = found.get("total") if isinstance(found, Mapping) else None
    return {"hits": hits, "total": total if isinstance(total, int) and not isinstance(total, bool) else len(rows)}


# -- the form's fields -------------------------------------------------------------------------------------------------


def clean_pointer(value: Any) -> str:
    text = (value if isinstance(value, str) else "").strip()
    if not 1 <= len(text) <= POINTER_MAX or any(unicodedata.category(c) == "Cc" for c in text):
        raise Refused(_NO_POINTER)
    return text


def clean_query(value: Any) -> str:
    text = " ".join((value if isinstance(value, str) else "").split())
    if not 1 <= len(text) <= QUERY_MAX or any(unicodedata.category(c) == "Cc" for c in text):
        raise Refused(_NO_QUERY)
    return text


# -- the routes --------------------------------------------------------------------------------------------------------


def attach_access_routes(bp, gate, *, csrf_valid, csrf_token, page_context, client_factory, require_admin):
    """Mount the page and its three questions on the console's blueprint, behind its ``require_admin``."""

    def _csrf_ok() -> bool:
        return csrf_valid(str(request.form.get("csrf_token", "") or ""))

    def _offer() -> dict:
        """Everyone the page offers: people (deactivated ones marked), roles, agents that are not revoked."""
        conn = gate.connection()
        people = sorted(({"id": str(p.id), "name": p.display_name or p.email, "active": p.is_active}
                         for p in gate.users().list_all()), key=lambda p: (p["name"].casefold(), p["id"]))
        roles = [{"id": str(r.id), "name": r.name} for r in RoleStore(conn).list_roles()]
        agents = [{"id": str(a.id), "name": a.name, "assistant": a.kind == ASSISTANT}
                  for a in AgentStore(conn).list_agents() if not a.revoked]
        return {"people": people, "roles": roles, "agents": agents}

    def _page(*, answer=None, error=None, status=200, chosen=None):
        return render_template(
            "admin_access.html", active_nav="admin", **page_context(), **_offer(), answer=answer, error=error,
            chosen=chosen or {}, csrf_token=csrf_token(), more_text=more_text,
        ), status

    @bp.route("/access")
    @require_admin
    def access():
        return _page()

    def _chosen() -> dict:
        return {"wer": str(request.form.get("wer", "") or "").strip(),
                "fuer": str(request.form.get("fuer", "") or "").strip()}

    def _ask(question: str, answer_of):
        """The steps every question shares: the token, X, the question's own fields, «Ein» or not, the answer, its
        audit row. A refusal draws the page with its reason and writes nothing; nothing is asked before X is known."""
        if not _csrf_ok():
            return _page(error=_EXPIRED, status=400)
        chosen = _chosen()
        conn, client = gate.connection(), client_factory()
        try:
            subject = resolve(conn, gate.users(), request.form.get("wer"), request.form.get("fuer"))
            fields = answer_of.fields(subject)
            ein = tenant_brokered(client)
            answer, detail = answer_of.answer(client, subject, fields, ein)
        except Refused as exc:
            logger.info("What-sees %s refused (%s)", question, exc.status)
            return _page(error=str(exc), status=exc.status, chosen=chosen)
        except CoreUnreadable:
            logger.warning("What-sees %s not answered: Knovas did not answer", question)
            return _page(error=_UNREADABLE, status=502, chosen=chosen)
        effect = EIN if ein else VORSCHAU
        answer.update(question=question, wer=subject.wer, kind=subject.kind, name=subject.label,
                      nothing=subject.nothing, effect=effect, fuer=subject.fuer)
        detail = {"question": question, "effect": effect, **detail}
        if subject.nothing:
            detail["reads_nothing"] = subject.nothing
        if subject.fuer:
            detail["fuer"] = subject.fuer
        audit.record(conn, action=WHAT_SEES, actor=gate.current_user(), target_type=subject.target_type,
                     target_id=subject.id, detail=detail)
        logger.info("What-sees %s for %s %s answered (%s)", question, subject.kind, subject.id, effect)
        if subject.fuer:
            chosen["fuer"] = subject.fuer
        chosen["wer"] = subject.wer
        return _page(answer=answer, chosen=chosen)

    class _Reach:
        @staticmethod
        def fields(subject):
            return page_number(request.form.get("seite"))

        @staticmethod
        def answer(client, subject, seite, ein):
            rows, partial = reach_rows(client, subject)
            shown = reach_page(rows, seite)
            answer = dict(shown, total=len(rows), partial=partial, next=shown["page"] + 1,
                          previous=shown["page"] - 1 if shown["page"] > 1 else None)
            return answer, {"rows": len(rows), "page": shown["page"], "partial": partial}

    class _Check:
        @staticmethod
        def fields(subject):
            return clean_pointer(request.form.get("pointer"))

        @staticmethod
        def answer(client, subject, pointer, ein):
            answer = check_document(client, subject, pointer, ein)
            return answer, {"answer": answer["answer"], "source": answer["source"]}

    class _Search:
        @staticmethod
        def fields(subject):
            if subject.kind == "role":
                raise Refused(_ROLE_SEARCH)
            return clean_query(request.form.get("query"))

        @staticmethod
        def answer(client, subject, query, ein):
            answer = search_as(client, subject, query, ein)
            return dict(answer, query=query), {"hits": len(answer["hits"])}

    @bp.route("/access/reach", methods=["POST"])
    @require_admin
    def access_reach():
        return _ask("reach", _Reach)

    @bp.route("/access/check", methods=["POST"])
    @require_admin
    def access_check():
        return _ask("document", _Check)

    @bp.route("/access/search", methods=["POST"])
    @require_admin
    def access_search():
        return _ask("search", _Search)

    return bp
