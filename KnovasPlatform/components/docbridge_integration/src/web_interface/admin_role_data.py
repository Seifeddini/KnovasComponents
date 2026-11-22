"""«Darf lesen»: a role's data on the Rollen page, the dry run before each change, four eyes, adopting a Core group
(FU-45) and moving direct group grants into roles (FU-41).

Plan FEAT-2026-11-19-platform-mvp, item RB-5; work order docs/platform-mvp/work-orders/RB-5.md; design
docs/platform-mvp/design/role-builder.md (§ 2.2, § 3, § 4, § 6 rules 1, 2, 4, 5 and 7); Core requests CR-R1 to CR-R3
(docs/platform-mvp/core-requests.md). The page is RB-3/RB-4's (``admin_roles.py``); this module adds its part «Darf
lesen», seven routes and the executor of two ``acl_change`` actions.

Load-bearing properties:

    The Core holds the data. A role's data are the folder rules whose groups name the role's Core group and the
    documents whose own rights name it. The Platform keeps no copy (design § 3): the part reads them from the Core on
    every page, and every change is planned again from what the Core holds at the moment it is made; the dry run the
    browser sends back is never trusted.

    A dry run before each change. It says who gains sight and who loses it, computed as the data will act under
    «Ein» whatever the posture is today: a person reads a folder or document that is open (no group), or when one of
    their claim groups (``roles.active_claim_groups``: running direct grants plus their roles' groups) is one of its
    groups or above one of them in the Core's tree (the Core's reader closure, ``builder.access.Tree``).

    No wall without the confirmation. Adding to an open folder or document walls it for everyone else
    (``walls-open``); a removal that leaves nothing walled opens it (``opens``). Both need ``confirm=1``, checked
    again when the change is carried out. A removal never sends an empty set to ``update_folder_rule``: when the role's
    group is a rule's only group, the rule is deleted and its successor governs. No deny (founder): roles only grant.

    Four eyes through the existing path. Adding and removing go ``acl_change`` through ``run_guarded``: with the
    administrators' bypass on, an administrator acts alone and ``approval.bypassed`` records it; with it off, a request
    waits and nothing reaches the Core. ``execute_role_data`` carries a change out (``execute_acl_change`` hands it the
    two role actions): only for an administrator (design rule 1), planned again, refused (raised, nothing sent) when the
    role's group changed or the plan now warns without the confirmation. Adopting a group and moving direct grants
    change no wall and need no second person (decision 2).

    The Core first, then the row (design rules 4 and 5). A data change writes its audit row after the Core answers
    (``record_or_raise``; outcome ``error`` on a refusal, best effort). Adopting and moving write in one transaction
    with their rows; the move compares every touched person's claim groups and functions before and after, and any
    difference undoes it all. Nothing is written to the Core on a GET, at start, by a dry run, by the move.

    Before «Ein». Everything is written to the Core, which stores it and hides nothing while the tenant is not
    enforcing (design rule 7). The words «wirkt mit «Ein»» stay on the part until the client reports the tenant as
    ``brokered`` (``client.rbac_posture()``, which the real client gains with CR-R1); never ``acting_as``: the Core
    takes the caller from the signed claim (CR-R2).

    One gate, the token first. Every route is the console's ``require_admin``, handed in. Every POST reads the form's
    ``csrf_token`` first (400), then finds the role (404), then checks the form (400), then reads the Core (502 when it
    cannot be read).

    No customer text in logs or audit rows: ids, counts and the names of error classes. Never a pointer or prefix, a
    document's title, a role's or group's name, a person's name or address, a Core group id (rule 7 of RB-3/RB-4: a
    tenant may choose a speaking one; the audit row holds it) or a Core error's text. Exceptions raised here carry
    fixed words only (``admin_approvals._execute`` logs an executor's exception).
"""
from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

from flask import abort, request, url_for

from builder.access import Tree
from identity import audit
from identity.approvals import ApprovalService
from identity.roles import (
    ADMIN,
    ADMINISTRATION,
    NAME_MAX,
    RoleError,
    RoleNotFound,
    RoleStore,
    active_claim_groups,
    claim_groups,
)
from web_interface.guarded import run_guarded

logger = logging.getLogger(__name__)

#: What the forms may name (the tests pin the values; the labels are the page's).
OPS = ("add", "remove")
KINDS = ("source", "folder", "document")
KIND_WORDS = {"source": "Quelle", "folder": "Ordner", "document": "Dokument"}

#: The two ``acl_change`` actions RB-5 adds; ``execute_acl_change`` hands them to ``execute_role_data``.
ROLE_DATA_ACTIONS = {"role_data_add": "add", "role_data_remove": "remove"}

#: The audit actions (design rule 4).
DATA_ADDED, DATA_REMOVED = "role.data_added", "role.data_removed"
GROUP_ADOPTED, GRANTS_MOVED = "role.core_group_adopted", "role.direct_grants_moved"

#: A document whose rights are its own carries this folder id (the Core's ACL_FOLDER_NONE).
ACL_FOLDER_NONE = "__acl_folder_none__"

#: The longest pointer or prefix (the Core's FOLDER_RULE_PREFIX_MAX_LEN).
POINTER_MAX = 1024

#: The dry run's warnings (``data-warning``; pinned) and which of them need the confirmation.
WALLS_OPEN, OPENS, ALREADY_READ_IN = "walls-open", "opens", "already-read-in"
NEEDS_CONFIRM = frozenset({WALLS_OPEN, OPENS})
WARNING_WORDS = {
    WALLS_OPEN: ("Heute offen: mit «Ein» sehen es danach nur noch die Mitglieder der Rolle und wer eine Gruppe "
                 "darüber hat. Alle anderen verlieren die Sicht."),
    OPENS: ("Danach hat es keine Gruppe mehr: mit «Ein» sieht es danach jede Person im Mandanten."),
    ALREADY_READ_IN: ("Unter diesem Ordner sind schon Dokumente eingelesen. Die neue Regel erreicht sie erst, wenn "
                      "Knovas sie nachträgt; bis dahin gilt für sie die bisherige Regel."),
}

#: Documents asked of the Core per page, and how many pages one question walks at most (a page may come back short
#: or empty with a cursor). The page itself walks fewer: it is drawn on every visit.
_PAGE_SIZE = 200
_MAX_PAGES = 50
_PAGE_WALK = 5

_EXPIRED = "Formular ist abgelaufen. Bitte erneut versuchen."
_NOT_SAVED = ("Die Änderung konnte nicht protokolliert werden und wurde deshalb nicht gespeichert. "
              "Bitte später erneut versuchen.")
_CONFIRM_FIRST = "Der Probelauf warnt. Bitte die Warnung bestätigen (Häkchen) und erneut absenden."


# -- refusals: fixed words only --------------------------------------------------------------------------------------


class RoleDataError(Exception):
    """A refusal of this module. Plain German, safe to show and to log: no pointer, no name, no group id."""

    status = 400
    default = "Diese Änderung an den Daten der Rolle wurde abgelehnt."

    def __init__(self, message: Optional[str] = None) -> None:
        super().__init__(message or self.default)


class DataRefused(RoleDataError):
    """The form asks for something that is refused before any Core write (400)."""


class CoreUnreadable(RoleDataError):
    """A Core read failed: nothing is planned (502)."""

    status = 502
    default = ("Knovas hat die Gruppen, Ordnerregeln oder Dokumente nicht genannt. Nichts wurde geplant oder "
               "geändert. Bitte später erneut versuchen.")


class CoreRefused(RoleDataError):
    """The Core refused the write, or answered that nothing was written (502)."""

    status = 502
    default = "Knovas hat die Änderung nicht gespeichert. Die Daten der Rolle sind unverändert."


class RowMissing(RoleDataError):
    """The Core changed and the audit row could not be written (500)."""

    status = 500
    default = ("Die Änderung steht bei Knovas, aber ihr Protokolleintrag konnte nicht geschrieben werden. "
               "Bitte die Protokollierung prüfen lassen.")


class _Unfinished(Exception):
    """A walk of the Core's documents stopped at its page limit with a cursor left."""


_ADMINISTRATION = ("Die Administration hat keine Daten: ihre Gruppe ist die Administratorgruppe des Mandanten, "
                   "die zum Ändern berechtigt, nicht zum Lesen.")
_NO_GROUP = ("Die Rolle hat noch keine Gruppe bei Knovas. Mitglied erhält sie mit «Mit Knovas verbinden»; jede Rolle "
             "kann eine bestehende Gruppe übernehmen.")
_BAD_OP = "Diese Änderung gibt es nicht."
_BAD_KIND = "Bitte Quelle, Ordner oder Dokument wählen."
_BAD_POINTER = (f"Bitte einen Ordner oder ein Dokument angeben: 1 bis {POINTER_MAX} Zeichen, ohne Steuerzeichen.")
_UNKNOWN_DOCUMENT = "Dieses Dokument kennt Knovas nicht."
_NOT_THE_ROLES_FOLDER = ("Dieser Ordner gehört nicht zu den Daten der Rolle: er hat keine eigene Regel, die die Gruppe "
                         "der Rolle nennt.")
_NOT_THE_ROLES_DOCUMENT = ("Dieses Dokument gehört nicht zu den Daten der Rolle: es hat keine eigenen Rechte, die die "
                           "Gruppe der Rolle nennen.")
_ONLY_ADMINISTRATION = ("Die Daten einer Rolle setzt nur eine Person der Administration um. Die Anfrage bleibt "
                        "freigegeben; eine Person der Administration führt sie unter «Freigaben» aus.")
_ROLE_GONE = "Die Rolle gibt es nicht mehr. Nichts wurde geändert."
_GROUP_CHANGED = "Die Gruppe der Rolle hat sich seit der Anfrage geändert. Nichts wurde geändert; bitte neu planen."
_WARNS_NOW = ("Die Änderung warnt inzwischen (ein Ordner oder Dokument ist jetzt offen oder würde offen). Nichts "
              "wurde geändert; bitte neu planen und die Warnung bestätigen.")
_ADOPT_ADMINISTRATION = "Die Administration behält die Administratorgruppe des Mandanten."
_ADOPT_NONE = "Bitte eine Gruppe wählen."
_ADOPT_UNKNOWN = "Diese Gruppe nennt Knovas nicht."
_ADOPT_ADMIN_GROUP = "Die Administratorgruppe gehört zur Administration."
_ADOPT_TAKEN = "Diese Gruppe gehört schon einer anderen Rolle: jede Rolle hat ihre eigene Gruppe."
_ADOPT_HAS_DATA = ("Die jetzige Gruppe der Rolle hat noch Daten. Entfernen Sie sie zuerst, jede mit ihrem Probelauf; "
                   "sonst könnte sie niemand mehr lesen.")


# -- reading the Core ------------------------------------------------------------------------------------------------


def _read(what: str, call, *args, **kwargs):
    """A Core read; any failure (transport, HTTP, an answer of the wrong shape) is ``CoreUnreadable``."""
    try:
        return call(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - every Core failure is shown as one, none is raised raw
        logger.warning("Knovas did not answer a read of %s: %s", what, type(exc).__name__)
        raise CoreUnreadable() from None


def _groups_of(item: Optional[Mapping]) -> Tuple[str, ...]:
    if not isinstance(item, Mapping):
        return ()
    return tuple(sorted({str(g) for g in (item.get("access_groups") or ()) if g}))


def _live_rules(client) -> List[dict]:
    """The tenant's folder rules: rule id, prefix and groups, longest prefix first."""
    answer = _read("the folder rules", client.folder_rules)
    if not isinstance(answer, list):
        raise CoreUnreadable()
    rules = [{"rule_id": str(r["rule_id"]), "pointer_prefix": str(r["pointer_prefix"]), "access_groups": _groups_of(r)}
             for r in answer
             if isinstance(r, Mapping) and r.get("rule_id") and isinstance(r.get("pointer_prefix"), str)]
    return sorted(rules, key=lambda r: (-len(r["pointer_prefix"]), r["pointer_prefix"]))


def _governing(rules: Sequence[dict], pointer: str) -> Optional[dict]:
    """The longest live rule whose prefix the pointer starts with (``folder_rule_service.py``'s resolution)."""
    return next((r for r in rules if pointer.startswith(r["pointer_prefix"])), None)


def _core_groups(client) -> List[dict]:
    answer = _read("the groups", client.access_groups)
    if not isinstance(answer, list):
        raise CoreUnreadable()
    return [dict(g) for g in answer if isinstance(g, Mapping) and g.get("group_id")]


def _walk(client, *, pages: int = _MAX_PAGES, **filters) -> Iterator[Mapping]:
    """The Core's documents for ``filters``, page by page along the cursor. Raises ``_Unfinished`` when ``pages``
    pages were read and the cursor goes on, ``CoreUnreadable`` when a page cannot be read."""
    after, seen = None, set()
    for _ in range(pages):
        page = _read("the documents", client.documents, after=after, limit=_PAGE_SIZE, **filters)
        if not isinstance(page, Mapping) or page.get("unavailable"):
            logger.warning("Knovas did not list its documents (no inventory)")
            raise CoreUnreadable()
        for entry in page.get("documents") or ():
            if isinstance(entry, Mapping):
                yield entry
        after = page.get("next_after")
        if not after or after in seen:
            return
        seen.add(after)
    raise _Unfinished()


def _own_rights(entry: Mapping) -> bool:
    """Rights of the document's own: not governed by a folder rule, and a group set that is not empty (the Core's
    ``acl_backfill_worker._is_explicit_override``)."""
    return entry.get("acl_folder_id") in (None, "", ACL_FOLDER_NONE) and bool(_groups_of(entry))


def _names_pointer(entry: Mapping, pointer: str) -> bool:
    return entry.get("pointer") == pointer or pointer in (entry.get("pointers") or ())


def tenant_brokered(client) -> bool:
    """Whether the client reports the tenant as ``brokered`` («Ein», CR-R1). No such call (the real client until
    CR-R1), an error or any other posture: False, and the part keeps «wirkt mit «Ein»»."""
    getter = getattr(client, "rbac_posture", None)
    if not callable(getter):
        return False
    try:
        posture = getter()
    except Exception as exc:  # noqa: BLE001 - unknown is "not brokered"
        logger.info("Knovas did not report the tenant's posture: %s", type(exc).__name__)
        return False
    return isinstance(posture, Mapping) and posture.get("mode") == "brokered"


# -- who sees what under «Ein» ---------------------------------------------------------------------------------------


class Sight:
    """The Core's tree and every active person's claim groups: who reads a set of groups once the data act."""

    def __init__(self, groups: Iterable[Mapping], people: Mapping[str, set]) -> None:
        self.tree = Tree.of(groups)
        self.people = {str(uid): set(held) for uid, held in people.items()}

    def closure(self, groups: Iterable[str]) -> set:
        """The groups whose holders read something assigned ``groups``: each group and every group above it."""
        out = set()
        for group in groups:
            out.add(group)
            out.update(self.tree.ancestors(group))
        return out

    def readers(self, groups: Sequence[str]) -> frozenset:
        """The active people who read something assigned ``groups``; everyone when it has no group (open)."""
        if not groups:
            return frozenset(self.people)
        closure = self.closure(groups)
        return frozenset(uid for uid, held in self.people.items() if held & closure)

    def reads(self, held: set, group: str) -> bool:
        return bool(set(held) & self.closure([group]))


# -- the plan of a data change (§ 3) ---------------------------------------------------------------------------------


@dataclass
class Plan:
    op: str
    kind: str
    pointer: str
    group: str
    #: "update" | "create" | "delete" (folder rules), "set" (a document's rights), "none" (no change)
    call: str
    before: Tuple[str, ...] = ()
    after: Tuple[str, ...] = ()
    rule_id: Optional[str] = None
    document_uuid: Optional[str] = None
    gains: frozenset = frozenset()
    losses: frozenset = frozenset()
    warnings: Tuple[str, ...] = ()
    #: for a document taken out of its folder rule by the change (shown, not pinned)
    leaves_rule: bool = False
    #: for "none": the role already reads it through a rule of a folder above
    through_folder: bool = False

    @property
    def needs_confirm(self) -> bool:
        return bool(NEEDS_CONFIRM & set(self.warnings))


def clean_pointer(value: Any) -> str:
    """The pointer as planned: trimmed, 1 to 1,024 characters, no control character."""
    text = str(value if isinstance(value, str) else "").strip()
    if not 1 <= len(text) <= POINTER_MAX or any(unicodedata.category(c) == "Cc" for c in text):
        raise DataRefused(_BAD_POINTER)
    return text


def plan_data(client, conn, role, op: Any, kind: Any, pointer: Any) -> Plan:
    """What adding or removing ``pointer`` would send to the Core, and who would gain or lose sight. Writes nothing.

    Refused before any Core read: Administration, an ``op`` or ``kind`` outside the lists, a bad pointer, a role
    without a Core group. Refused after reading: a document the Core does not know, a folder or document that is
    not the role's (removal). A Core read that fails is ``CoreUnreadable``.
    """
    if role.kind == ADMINISTRATION:
        raise DataRefused(_ADMINISTRATION)
    if op not in OPS:
        raise DataRefused(_BAD_OP)
    if kind not in KINDS:
        raise DataRefused(_BAD_KIND)
    pointer = clean_pointer(pointer)
    group = role.core_group_id
    if not group:
        raise DataRefused(_NO_GROUP)

    groups = _core_groups(client)
    rules = _live_rules(client)
    if kind == "document":
        plan = _plan_document(client, rules, op, pointer, group)
    else:
        plan = _plan_folder(client, rules, op, kind, pointer, group)
    if plan.call == "none":
        return plan
    sight = Sight(groups, active_claim_groups(conn))
    was, will = sight.readers(plan.before), sight.readers(plan.after)
    plan.gains, plan.losses = will - was, was - will
    warnings = list(plan.warnings)
    if op == "add" and not plan.before:
        warnings.insert(0, WALLS_OPEN)
    if op == "remove" and not plan.after:
        warnings.insert(0, OPENS)
    plan.warnings = tuple(warnings)
    return plan


def _plan_folder(client, rules, op, kind, pointer, group) -> Plan:
    own = next((r for r in rules if r["pointer_prefix"] == pointer), None)
    if op == "add":
        if own is not None:
            if group in own["access_groups"]:
                return Plan(op, kind, pointer, group, "none", rule_id=own["rule_id"])
            before = own["access_groups"]
            return Plan(op, kind, pointer, group, "update", before=before,
                        after=tuple(sorted(set(before) | {group})), rule_id=own["rule_id"])
        governing = _governing(rules, pointer)
        before = governing["access_groups"] if governing else ()
        if group in before:
            return Plan(op, kind, pointer, group, "none", through_folder=True)
        # A new rule keeps the groups that govern the folder today, so nobody loses sight.
        plan = Plan(op, kind, pointer, group, "create", before=before, after=tuple(sorted(set(before) | {group})))
        if _read_in_under(client, rules, pointer):
            plan.warnings = (ALREADY_READ_IN,)
        return plan

    if own is None or group not in own["access_groups"]:
        raise DataRefused(_NOT_THE_ROLES_FOLDER)
    before = own["access_groups"]
    rest = tuple(g for g in before if g != group)
    if rest:
        return Plan(op, kind, pointer, group, "update", before=before, after=rest, rule_id=own["rule_id"])
    # Never an empty set to update_folder_rule: the rule goes, and the next rule above governs (none: open).
    successor = _governing([r for r in rules if r is not own], pointer)
    return Plan(op, kind, pointer, group, "delete", before=before,
                after=successor["access_groups"] if successor else (), rule_id=own["rule_id"])


def _read_in_under(client, rules, pointer: str) -> bool:
    """Whether the Core lists a document under ``pointer`` that a new rule there reaches only after the backfill
    (CR-R3): not with rights of its own, and not governed by a rule deeper than the new one."""
    deeper = {r["rule_id"] for r in rules if len(r["pointer_prefix"]) > len(pointer)}
    try:
        for entry in _walk(client, prefix=pointer):
            if not _own_rights(entry) and entry.get("acl_folder_id") not in deeper:
                return True
    except _Unfinished:
        return True  # cannot tell: say so (the warning only informs)
    return False


def _document_entry(client, pointer: str) -> Optional[Mapping]:
    try:
        for entry in _walk(client, prefix=pointer):
            if _names_pointer(entry, pointer):
                return entry
    except _Unfinished:
        pass
    return None


def _plan_document(client, rules, op, pointer, group) -> Plan:
    current = _read("a document's rights", client.document_access, pointer)
    if not isinstance(current, Mapping):
        raise DataRefused(_UNKNOWN_DOCUMENT)
    entry = _document_entry(client, pointer)
    uuid_ = str(entry.get("document_uuid")) if entry is not None and entry.get("document_uuid") else None
    if op == "add":
        governing = None
        if entry is not None and not _own_rights(entry):
            folder_id = entry.get("acl_folder_id")
            governing = next((r for r in rules if r["rule_id"] == folder_id), None)
        # The groups that govern it today: its rule's, when a live rule governs it; its own otherwise.
        before = governing["access_groups"] if governing is not None else _groups_of(current)
        if group in before:
            return Plan(op, "document", pointer, group, "none", document_uuid=uuid_, through_folder=bool(governing))
        return Plan(op, "document", pointer, group, "set", before=before, after=tuple(sorted(set(before) | {group})),
                    document_uuid=uuid_, leaves_rule=governing is not None)

    before = _groups_of(current)
    if entry is None or not _own_rights(entry) or group not in before:
        raise DataRefused(_NOT_THE_ROLES_DOCUMENT)
    return Plan(op, "document", pointer, group, "set", before=before,
                after=tuple(g for g in before if g != group), document_uuid=uuid_)


# -- carrying a change out --------------------------------------------------------------------------------------------


def _send(client, plan: Plan) -> None:
    """The plan's one Core call, through the client's existing calls, never with ``acting_as``."""
    if plan.call == "update":
        if not plan.after:  # never an empty set: an empty rule opens a folder (planning never asks for one)
            raise CoreRefused()
        answer = client.update_folder_rule(plan.rule_id, list(plan.after))
    elif plan.call == "create":
        answer = client.create_folder_rule(plan.pointer, list(plan.after))
        if isinstance(answer, Mapping) and answer.get("rule_id"):
            plan.rule_id = str(answer["rule_id"])
    elif plan.call == "delete":
        answer = {} if client.delete_folder_rule(plan.rule_id) is True else None
    elif plan.call == "set":
        answer = client.set_document_access(plan.pointer, list(plan.after))
    else:
        return
    if answer is None:
        raise CoreRefused()


def _detail(plan: Plan) -> dict:
    """The audit row's detail: ids, the kind and counts; never the pointer."""
    detail = {"core_group_id": plan.group, "kind": plan.kind, "gains": len(plan.gains),
              "losses": len(plan.losses)}
    if plan.kind == "document":
        detail["document_uuid"] = plan.document_uuid
    else:
        detail["rule_id"] = plan.rule_id
        detail["call"] = plan.call
    return detail


def execute_role_data(client, payload: Mapping[str, Any], *, actor, conn) -> dict:
    """Carry out one role-data ``acl_change`` (``role_data_add`` / ``role_data_remove``) against the Core.

    Called by ``run_guarded`` when an administrator acts alone, and by the Freigaben page (``execute_acl_change``)
    once a second person confirmed. Only an administrator carries it out; the change is planned again from what the
    Core holds now; refused (raised, nothing sent) when the role's group is no longer the payload's or the plan now
    warns and the payload carries no confirmation (a request stores one only when its own dry run warned). The answer
    holds ids and counts only (it lands in the audit log as the approvals' ``detail.result``).

    The role's row is held (``RoleStore.share``) from the check of its group to the audit row, so an adopt (which holds
    every role's row while it checks that the present group carries no data) waits for a data change of the role in
    flight, and a data change waits for an adopt: the old group never keeps data that no role reads.
    """
    if ADMIN not in (getattr(actor, "roles", None) or ()):
        raise DataRefused(_ONLY_ADMINISTRATION)
    op = ROLE_DATA_ACTIONS.get(str(payload.get("action") or ""))
    if op is None:
        raise DataRefused(_BAD_OP)
    action = DATA_ADDED if op == "add" else DATA_REMOVED
    refused: Dict[str, Any] = {}
    try:
        with conn.transaction():
            try:
                role = RoleStore(conn).share(payload.get("role_id"))
            except RoleNotFound:
                raise DataRefused(_ROLE_GONE) from None
            if (role.core_group_id or None) != (payload.get("core_group_id") or None):
                raise DataRefused(_GROUP_CHANGED)
            plan = plan_data(client, conn, role, op, payload.get("kind"), payload.get("pointer"))
            if plan.needs_confirm and payload.get("confirm") is not True:
                raise DataRefused(_WARNS_NOW)
            result = {"role_id": str(role.id), "kind": plan.kind, "changed": 0}
            if plan.call == "none":
                logger.info("Role %s: data %s is no change", role.id, op)
                return result
            try:
                _send(client, plan)
            except Exception as exc:  # noqa: BLE001 - a refusal is recorded and shown, never raised raw
                refused.update(role=role, plan=plan, error=type(exc).__name__)
                raise CoreRefused() from None
            try:
                audit.record_or_raise(conn, action=action, actor=actor, target_type="role", target_id=str(role.id),
                                      detail=_detail(plan))
            except Exception as exc:  # noqa: BLE001 - the Core has changed; the page says the row is missing
                logger.error("Role %s: data changed at Knovas, audit row not written, %s", role.id,
                             audit._error_code(exc))
                raise RowMissing() from None
    except CoreRefused:
        if refused:  # the refusal's row once the hold is released (best effort; the transaction above is undone)
            role, plan = refused["role"], refused["plan"]
            logger.warning("Role %s: Knovas refused the data change (%s, %s)", role.id, plan.call, refused["error"])
            audit.record(conn, action=action, actor=actor, target_type="role", target_id=str(role.id),
                         outcome="error", detail=dict(_detail(plan), error=refused["error"]))
        raise
    logger.info("Role %s: data %s at Knovas (%s; %d gain, %d lose sight)", role.id, op, plan.call,
                len(plan.gains), len(plan.losses))
    result.update(changed=1, gains=len(plan.gains), losses=len(plan.losses), call=plan.call)
    if plan.kind == "document":
        result["document_uuid"] = plan.document_uuid
    else:
        result["rule_id"] = plan.rule_id
    return result


# -- adopting a group (§ 7, FU-45) ------------------------------------------------------------------------------------


@dataclass
class AdoptPlan:
    group: str
    name: str
    previous: Optional[str]
    gains: frozenset = frozenset()
    same: bool = False


def plan_adopt(client, conn, role, roles: Sequence, group_id: Any) -> AdoptPlan:
    """What adopting ``group_id`` as the role's group would do: the role's active members who do not read the group
    today gain it; nobody loses (the present group must carry no data). Writes nothing."""
    if role.kind == ADMINISTRATION:
        raise DataRefused(_ADOPT_ADMINISTRATION)
    wanted = str(group_id if isinstance(group_id, str) else "").strip()
    if not wanted:
        raise DataRefused(_ADOPT_NONE)
    groups = _core_groups(client)
    found = next((g for g in groups if str(g.get("group_id")) == wanted), None)
    if found is None:
        raise DataRefused(_ADOPT_UNKNOWN)
    if found.get("is_admin") is True:
        raise DataRefused(_ADOPT_ADMIN_GROUP)
    name = str(found.get("name") or "")
    if wanted == role.core_group_id:
        return AdoptPlan(wanted, name, role.core_group_id, same=True)
    if any(r.core_group_id == wanted for r in roles if r.id != role.id):
        raise DataRefused(_ADOPT_TAKEN)
    previous = role.core_group_id
    if previous and _carries_data(client, previous):
        raise DataRefused(_ADOPT_HAS_DATA)
    people = active_claim_groups(conn)
    sight = Sight(groups, people)
    gains = frozenset(str(m) for m in role.members if str(m) in people and not sight.reads(people[str(m)], wanted))
    return AdoptPlan(wanted, name, previous, gains)


def _carries_data(client, group: str) -> bool:
    """A folder rule names the group, or the Core lists a document for it."""
    if any(group in r["access_groups"] for r in _live_rules(client)):
        return True
    try:
        for _ in _walk(client, group=group):
            return True
    except _Unfinished:
        logger.warning("Knovas listed no end of the documents of a group; adopting refused as unreadable")
        raise CoreUnreadable() from None
    return False


# -- moving direct grants (§ 8, FU-41) ---------------------------------------------------------------------------------

#: Why a running direct grant stays direct (the page's words).
LEFT_WORDS = {
    "ends_on": "mit Enddatum (ein Zugriffsfenster; eine Rolle hat kein Enddatum)",
    "admin_group": "die Administratorgruppe (sie berechtigt zum Ändern, nicht zum Lesen)",
    "unknown_group": "eine Gruppe, die Knovas nicht nennt",
    "not_a_member": "die Gruppe einer Rolle, in der die Person nicht ist (die Rolle gäbe ihr auch deren Funktionen)",
}


@dataclass
class MovePlan:
    #: group id -> the people (ids) who hold it directly and get a new role for it
    new: Dict[str, List[str]] = field(default_factory=dict)
    #: (person, group, role id): grants that go because a role of theirs gives the group already
    gone: List[Tuple[str, str, str]] = field(default_factory=list)
    #: (person, group, reason): running grants that stay direct
    left: List[Tuple[str, str, str]] = field(default_factory=list)

    @property
    def moved(self) -> int:
        return sum(len(people) for people in self.new.values()) + len(self.gone)

    @property
    def people(self) -> List[str]:
        return sorted({p for people in self.new.values() for p in people} | {p for p, _, _ in self.gone})


def plan_move(conn, groups: Sequence[Mapping], roles: Sequence) -> MovePlan:
    """Which running direct grants become roles, which go and which stay (the order's table in § 8). Writes nothing."""
    listed = {str(g["group_id"]): g for g in groups}
    by_group: Dict[str, list] = {}
    for role in roles:
        if role.core_group_id:
            by_group.setdefault(role.core_group_id, []).append(role)
    plan = MovePlan()
    rows = conn.execute(
        "SELECT user_id, group_id, ends_on FROM user_access_groups "
        "WHERE ends_on IS NULL OR ends_on >= current_date ORDER BY group_id, user_id").fetchall()
    for user_id, group_id, ends_on in rows:
        person, group = str(user_id), str(group_id)
        if ends_on is not None:
            plan.left.append((person, group, "ends_on"))
        elif group in listed and listed[group].get("is_admin") is True:
            plan.left.append((person, group, "admin_group"))
        elif group not in listed:
            plan.left.append((person, group, "unknown_group"))
        elif group in by_group:
            holder = next((r for r in by_group[group] if user_id in r.members), None)
            if holder is not None:
                plan.gone.append((person, group, str(holder.id)))
            else:
                plan.left.append((person, group, "not_a_member"))
        else:
            plan.new.setdefault(group, []).append(person)
    return plan


def role_name_for(group_name: Any, taken: set) -> str:
    """A new role's name from its group's name at the Core: no control characters, white space collapsed, at most
    80 characters, «Gruppe» when nothing is left, " (2)", " (3)" … when another role has it (case-insensitive)."""
    text = "".join(c for c in str(group_name or "") if unicodedata.category(c)[0] != "C")
    base = " ".join(text.split())[:NAME_MAX].strip() or "Gruppe"
    name, number = base, 1
    while name.lower() in taken:
        number += 1
        suffix = f" ({number})"
        name = base[: NAME_MAX - len(suffix)].rstrip() + suffix
    return name


class _SightChanged(Exception):
    """A touched person's claim groups or functions differ after the move: everything is undone."""


class _NothingToMove(Exception):
    pass


class _NoAdoptChange(Exception):
    """The role has the group already: nothing written, no row."""


# -- the part «Darf lesen» on the page -------------------------------------------------------------------------------


def role_data_part(client, conn, shown, roles) -> dict:
    """What the part «Darf lesen» shows for ``shown``, read from the Core now. Writes nothing; a Core that cannot be
    read leaves the part saying so, and the page renders."""
    part: Dict[str, Any] = {"ein": tenant_brokered(client), "rows": [], "error": None, "partial": False,
                            "addable": False, "why": None, "adopt": None, "adopt_note": None}
    group = shown.core_group_id
    if shown.kind == ADMINISTRATION:
        part["why"] = _ADMINISTRATION
    elif not group:
        part["why"] = _NO_GROUP
    else:
        part["addable"] = True
        rows: List[dict] = []
        try:
            for rule in _live_rules(client):
                if group in rule["access_groups"]:
                    rows.append({"kind": "folder", "id": rule["rule_id"], "pointer": rule["pointer_prefix"]})
            rows.sort(key=lambda r: r["pointer"])
            documents = []
            try:
                for entry in _walk(client, pages=_PAGE_WALK, group=group):
                    if _own_rights(entry) and group in _groups_of(entry) and entry.get("pointer"):
                        documents.append({"kind": "document", "id": str(entry.get("document_uuid") or ""),
                                          "pointer": str(entry["pointer"])})
            except _Unfinished:
                part["partial"] = True
            rows += sorted(documents, key=lambda r: r["pointer"])
        except CoreUnreadable:
            logger.info("Role %s: its data could not be read from Knovas", shown.id)
            part["error"] = "Knovas hat die Daten dieser Rolle nicht genannt. Bitte die Seite später neu laden."
        part["rows"] = rows
    if shown.kind != ADMINISTRATION:
        try:
            taken = {r.core_group_id for r in roles if r.core_group_id}
            part["adopt"] = sorted(
                ({"id": str(g["group_id"]), "name": str(g.get("name") or g["group_id"])}
                 for g in _core_groups(client) if g.get("is_admin") is not True and str(g["group_id"]) not in taken),
                key=lambda g: g["name"].casefold())
            if not part["adopt"]:
                part["adopt_note"] = "Jede Gruppe bei Knovas gehört schon einer Rolle."
        except CoreUnreadable:
            part["adopt"] = []
            part["adopt_note"] = "Knovas hat die Gruppen nicht genannt."
    row = conn.execute(
        "SELECT count(*), count(DISTINCT user_id) FROM user_access_groups WHERE ends_on IS NULL").fetchone()
    part["open_grants"], part["open_grant_people"] = int(row[0]), int(row[1])
    return part


def _names(gate) -> Dict[str, str]:
    """Each person's display name (their address when they have none), by id: for the page only."""
    return {str(p.id): (p.display_name or p.email) for p in gate.users().list_all()}


def _people(ids: Iterable[str], names: Mapping[str, str]) -> List[Tuple[str, str]]:
    return sorted(((i, names.get(i, i)) for i in ids), key=lambda p: (p[1].casefold(), p[0]))


def _data_view(plan: Plan, role, names) -> dict:
    words = KIND_WORDS[plan.kind]
    adding = plan.op == "add"
    notes = []
    if plan.leaves_rule:
        notes.append("Das Dokument verlässt dabei seine Ordnerregel: spätere Änderungen an der Regel des Ordners "
                     "gelten für dieses Dokument nicht mehr.")
    if plan.call == "delete":
        notes.append("Die Regel nennt nur diese Rolle; sie wird gelöscht, und es gilt die Regel des Ordners darüber "
                     "(keine: offen).")
    return {
        "title": f"«{plan.pointer}» als {words} {'hinzufügen' if adding else 'entfernen'}",
        "gains": _people(plan.gains, names),
        "losses": _people(plan.losses, names),
        "warnings": [(code, WARNING_WORDS[code]) for code in plan.warnings],
        "notes": notes,
        "action": url_for("admin.add_role_data" if adding else "admin.remove_role_data", role_id=role.id),
        "fields": [("kind", plan.kind), ("pointer", plan.pointer)],
        "confirm": plan.needs_confirm,
        "button": "Hinzufügen" if adding else "Entfernen",
    }


def _adopt_view(plan: AdoptPlan, role, names) -> dict:
    notes = []
    if plan.previous:
        notes.append("Die bisherige Gruppe der Rolle hat keine Daten; Knovas löscht sie danach.")
    notes.append("Knovas wird keine Gruppe angelegt. Die Mitglieder lesen die Gruppe ab ihrer nächsten Anfrage.")
    return {
        "title": f"Gruppe «{plan.name or plan.group}» übernehmen",
        "gains": _people(plan.gains, names),
        "losses": [],
        "warnings": [],
        "notes": notes,
        "action": url_for("admin.adopt_group", role_id=role.id),
        "fields": [("group_id", plan.group)],
        "confirm": False,
        "button": "Gruppe übernehmen",
    }


def _grants_view(plan: MovePlan, groups: Sequence[Mapping], roles: Sequence, names) -> dict:
    group_names = {str(g["group_id"]): str(g.get("name") or g["group_id"]) for g in groups}
    role_names = {str(r.id): r.name for r in roles}
    return {
        "new": [{"group": group_names.get(gid, gid), "people": [n for _, n in _people(people, names)]}
                for gid, people in sorted(plan.new.items(), key=lambda kv: group_names.get(kv[0], kv[0]).casefold())],
        "gone": [{"person": names.get(p, p), "role": role_names.get(r, r)} for p, _g, r in plan.gone],
        "left": [{"person": names.get(p, p), "group": group_names.get(g, g), "why": LEFT_WORDS[why]}
                 for p, g, why in plan.left],
        "moved": plan.moved,
    }


# -- the routes --------------------------------------------------------------------------------------------------------


def attach_role_data_routes(bp, gate, *, csrf_valid, client_factory, require_admin, page):
    """Mount RB-5's seven routes onto the console's blueprint, behind the console's ``require_admin``.

    ``page(role_id, *, error, notice, status, dry_run, grants, data_form)`` is the Rollen page (``admin_roles._page``):
    every answer is that page, with a dry run, a notice or a refusal in ``role="alert"``.
    """

    def _csrf_ok() -> bool:
        return csrf_valid(str(request.form.get("csrf_token", "") or ""))

    def _role(role_id):
        try:
            return RoleStore(gate.connection()).get_role(role_id)
        except RoleNotFound:
            abort(404)

    def _refused(role, exc: RoleDataError, what: str, **extra):
        logger.info("Role %s: %s refused (%s)", role.id if role is not None else "-", what, type(exc).__name__)
        return page(None if role is None else role.id, error=str(exc), status=exc.status, **extra)

    def _sent() -> dict:
        return {"kind": str(request.form.get("kind", "") or ""), "pointer": str(request.form.get("pointer", "") or "")}

    # -- the dry run of a data change, and the change ----------------------------------------------------------------

    @bp.route("/roles/<role_id>/data/preview", methods=["POST"])
    @require_admin
    def preview_role_data(role_id):
        if not _csrf_ok():
            return page(role_id, error=_EXPIRED, status=400)
        role = _role(role_id)
        op = str(request.form.get("op", "") or "")
        try:
            plan = plan_data(client_factory(), gate.connection(), role, op, request.form.get("kind"),
                             request.form.get("pointer"))
        except RoleDataError as exc:
            return _refused(role, exc, "data preview", data_form=_sent() if op == "add" else None)
        if plan.call == "none":
            return page(role.id, notice=_no_change(plan))
        return page(role.id, dry_run=_data_view(plan, role, _names(gate)))

    def _change(role_id, op: str):
        if not _csrf_ok():
            return page(role_id, error=_EXPIRED, status=400)
        role = _role(role_id)
        me, conn, client = gate.current_user(), gate.connection(), client_factory()
        confirm = str(request.form.get("confirm", "") or "") == "1"
        try:
            plan = plan_data(client, conn, role, op, request.form.get("kind"), request.form.get("pointer"))
        except RoleDataError as exc:
            return _refused(role, exc, f"data {op}")
        if plan.call == "none":
            return page(role.id, notice=_no_change(plan))
        if plan.needs_confirm and not confirm:
            logger.info("Role %s: data %s refused (the dry run warns, no confirmation)", role.id, op)
            return page(role.id, error=_CONFIRM_FIRST, status=400, dry_run=_data_view(plan, role, _names(gate)))
        # The confirmation counts only for the warning it answered: a request planned without one stores none, so a
        # folder that becomes open before a second person confirms is not walled without a warning anyone saw.
        payload = {"action": f"role_data_{op}", "role_id": str(role.id), "core_group_id": role.core_group_id,
                   "kind": plan.kind, "pointer": plan.pointer, "confirm": bool(confirm and plan.needs_confirm)}
        try:
            outcome = run_guarded(
                ApprovalService(conn, gate.users()), me, kind="acl_change", target_ref=f"role:{role.id}",
                payload=payload, execute=lambda: execute_role_data(client, payload, actor=me, conn=conn))
        except RoleDataError as exc:
            return _refused(role, exc, f"data {op}")
        except Exception as exc:  # noqa: BLE001
            logger.error("Role %s: data %s not carried out, %s", role.id, op, audit._error_code(exc))
            return page(role.id, error=_NOT_SAVED, status=500)
        if outcome.queued:
            return page(role.id, notice=(
                f"Zur Freigabe eingereicht (Nr. {str(outcome.request.id)[:8]}). Eine zweite Person bestätigt unter "
                "«Freigaben»; bis dahin ändert sich bei Knovas nichts."))
        return page(role.id, notice="Bei Knovas gespeichert. Die Daten der Rolle unten sind so, wie Knovas sie nennt.")

    @bp.route("/roles/<role_id>/data/add", methods=["POST"])
    @require_admin
    def add_role_data(role_id):
        return _change(role_id, "add")

    @bp.route("/roles/<role_id>/data/remove", methods=["POST"])
    @require_admin
    def remove_role_data(role_id):
        return _change(role_id, "remove")

    # -- adopting a group ------------------------------------------------------------------------------------------

    @bp.route("/roles/<role_id>/adopt/preview", methods=["POST"])
    @require_admin
    def preview_adopt_group(role_id):
        if not _csrf_ok():
            return page(role_id, error=_EXPIRED, status=400)
        role = _role(role_id)
        conn = gate.connection()
        try:
            plan = plan_adopt(client_factory(), conn, role, RoleStore(conn).list_roles(),
                              request.form.get("group_id"))
        except RoleDataError as exc:
            return _refused(role, exc, "adopt preview")
        if plan.same:
            return page(role.id, notice="Die Rolle hat diese Gruppe schon.")
        return page(role.id, dry_run=_adopt_view(plan, role, _names(gate)))

    @bp.route("/roles/<role_id>/adopt", methods=["POST"])
    @require_admin
    def adopt_group(role_id):
        if not _csrf_ok():
            return page(role_id, error=_EXPIRED, status=400)
        role = _role(role_id)
        me, conn, client = gate.current_user(), gate.connection(), client_factory()
        store = RoleStore(conn)
        try:
            with conn.transaction():
                # Every role's row first, then every check: no other role takes the group, and no data change of this
                # role (it holds the row while it writes, execute_role_data) lands between the check that the present
                # group carries no data and the switch. The Core is read while the rows are held.
                store.lock_all()
                held = {r.id: r for r in store.list_roles()}
                if role.id not in held:
                    raise DataRefused(_ROLE_GONE)
                plan = plan_adopt(client, conn, held[role.id], list(held.values()), request.form.get("group_id"))
                if plan.same:
                    raise _NoAdoptChange()
                store.set_core_group(role.id, plan.group, by=me.id)
                audit.record_or_raise(conn, action=GROUP_ADOPTED, actor=me, target_type="role",
                                      target_id=str(role.id),
                                      detail={"core_group_id": plan.group, "previous_core_group_id": plan.previous,
                                              "gains": len(plan.gains)})
        except _NoAdoptChange:
            return page(role.id, notice="Die Rolle hat diese Gruppe schon.")
        except RoleDataError as exc:
            return _refused(role, exc, "adopt")
        except RoleError as exc:  # the 32-group cap (people or an agent of the role)
            logger.info("Role %s: adopt refused (%s)", role.id, type(exc).__name__)
            return page(role.id, error=str(exc), status=400)
        except Exception as exc:  # noqa: BLE001
            logger.error("Role %s: adopt not saved, %s", role.id, audit._error_code(exc))
            return page(role.id, error=_NOT_SAVED, status=500)
        logger.info("Role %s: adopted a Core group (%d members gain it)", role.id, len(plan.gains))
        notice = "Gruppe übernommen. Die Mitglieder lesen mit ihr ab ihrer nächsten Anfrage."
        if plan.previous:
            if _delete_old_group(client, conn, me, role.id, plan.previous):
                notice += " Die bisherige Gruppe wurde bei Knovas gelöscht."
            else:
                notice += " Die bisherige Gruppe konnte bei Knovas nicht gelöscht werden; sie steht im Protokoll."
        return page(role.id, notice=notice)

    # -- moving direct grants into roles -----------------------------------------------------------------------------

    @bp.route("/roles/direct-grants/preview", methods=["POST"])
    @require_admin
    def preview_direct_grants():
        if not _csrf_ok():
            return page(None, error=_EXPIRED, status=400)
        conn = gate.connection()
        try:
            groups = _core_groups(client_factory())
        except RoleDataError as exc:
            return _refused(None, exc, "grants preview")
        roles = RoleStore(conn).list_roles()
        plan = plan_move(conn, groups, roles)
        return page(None, grants=_grants_view(plan, groups, roles, _names(gate)))

    @bp.route("/roles/direct-grants/move", methods=["POST"])
    @require_admin
    def move_direct_grants():
        if not _csrf_ok():
            return page(None, error=_EXPIRED, status=400)
        me, conn = gate.current_user(), gate.connection()
        try:
            groups = _core_groups(client_factory())  # read before any lock is taken
        except RoleDataError as exc:
            return _refused(None, exc, "grants move")
        store, users = RoleStore(conn), gate.users()
        try:
            with conn.transaction():
                plan, created = _move(conn, store, users, groups, me)
        except _NothingToMove:
            return page(None, notice="Keine direkten Gruppenrechte, die eine Rolle übernehmen kann. Was bleibt, "
                                     "zeigt der Probelauf.")
        except _SightChanged:
            logger.error("Direct grants not moved: a person's claim would have changed")
            return page(None, error=("Die direkten Gruppenrechte wurden nicht übernommen: die Sicht einer Person "
                                     "hätte sich geändert. Nichts wurde geändert."), status=500)
        except RoleError as exc:
            logger.info("Direct grants not moved (%s)", type(exc).__name__)
            return page(None, error=str(exc) + " Nichts wurde übernommen.", status=400)
        except Exception as exc:  # noqa: BLE001
            logger.error("Direct grants not moved, %s", audit._error_code(exc))
            return page(None, error=_NOT_SAVED, status=500)
        logger.info("Direct grants moved: %d grants, %d new roles, %d people, %d left", plan.moved, len(created),
                    len(plan.people), len(plan.left))
        return page(None, notice=(
            f"{plan.moved} direkte Gruppenrechte in Rollen übernommen, {len(created)} neue Rollen ohne Funktionen. "
            "Niemand sieht danach mehr oder weniger; Knovas wurde nichts geschrieben."))

    return bp


def _no_change(plan: Plan) -> str:
    if plan.through_folder:
        return ("Die Rolle liest das schon über die Regel eines Ordners darüber. Nichts wurde geändert.")
    if plan.op == "add":
        return "Das gehört schon zu den Daten der Rolle. Nichts wurde geändert."
    return "Nichts zu ändern."


def _delete_old_group(client, conn, me, role_id, group: str) -> bool:
    """The role's previous group at the Core, deleted after an adopt. Best effort: the outcome goes into the audit
    log by id (RB-4's ``role.core_group_abandoned``), never into a log line."""
    try:
        removed = client.delete_access_group(group) is True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Role %s: Knovas kept the previous group: %s", role_id, type(exc).__name__)
        removed = False
    audit.record(conn, action="role.core_group_abandoned", actor=me, target_type="role", target_id=str(role_id),
                 outcome="ok" if removed else "error",
                 detail={"core_group_id": group, "removed_at_core": removed, "after_adopt": True})
    return removed


def _move(conn, store: RoleStore, users, groups, me) -> Tuple[MovePlan, List[str]]:
    """The move, inside the caller's transaction: roles first (``lock_all``), then the people who hold direct grants
    (in id order), then the plan; a member joins before their grant goes, so the 32-group cap never sees a new
    group; every touched person's claim groups and functions are compared before and after."""
    store.lock_all()
    conn.execute("SELECT id FROM users WHERE id IN (SELECT user_id FROM user_access_groups) ORDER BY id "
                 "FOR NO KEY UPDATE")
    roles = store.list_roles()
    plan = plan_move(conn, groups, roles)
    if not plan.moved:
        raise _NothingToMove()
    touched = plan.people

    def sight() -> dict:
        return {p: (claim_groups(conn, p), sorted(users.roles_of(p))) for p in touched}

    before = sight()
    listed = {str(g["group_id"]): g for g in groups}
    taken = {r.name.lower() for r in roles}
    created, grants = [], []
    for group, people in sorted(plan.new.items()):
        name = role_name_for(listed[group].get("name"), taken)
        role = store.create_role(name=name, functions=(), core_group_id=group, by=me.id)
        taken.add(name.lower())
        audit.record_or_raise(conn, action="role.created", actor=me, target_type="role", target_id=str(role.id),
                              detail={"core_group_id": group, "functions": [], "from_direct_grants": True})
        for person in people:
            store.add_member(role.id, person, by=me.id)
            audit.record_or_raise(conn, action="role.member_added", actor=me, target_type="role",
                                  target_id=str(role.id), detail={"user_id": person, "from_direct_grants": True})
            users.revoke_access_group(person, group)
            grants.append([person, group, str(role.id)])
        created.append(str(role.id))
    for person, group, role_id in plan.gone:
        users.revoke_access_group(person, group)
        grants.append([person, group, role_id])
    if sight() != before:
        raise _SightChanged()
    audit.record_or_raise(conn, action=GRANTS_MOVED, actor=me, target_type="role", target_id=None,
                          detail={"roles": created, "people": touched, "grants_moved": plan.moved,
                                  "grants_left": len(plan.left), "grants": grants})
    return plan, created
