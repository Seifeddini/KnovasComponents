"""«Rollendaten» (RB-5): a role's data on the Rollen page, its dry runs, four eyes, adopting a Core group (FU-45),
moving direct grants into roles (FU-41). Work order docs/platform-mvp/work-orders/RB-5.md; the invariants are on the
card docs/components/L2-platform-role-data.card.yaml; the page's words in templates/_role_data.html. Logs and audit
rows hold ids and counts only; exceptions carry fixed words (admin_approvals._execute logs them).
"""
from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from flask import abort, request, url_for

from builder.access import Tree
from identity import audit
from identity.approvals import ApprovalService
from identity.roles import ADMIN, ADMINISTRATION, NAME_MAX, RoleError, RoleNotFound, RoleStore, active_claim_groups, \
    claim_groups
from web_interface.guarded import run_guarded

logger = logging.getLogger(__name__)

OPS, KINDS = ("add", "remove"), ("source", "folder", "document")
#: The two acl_change actions; execute_acl_change hands them to execute_role_data.
ROLE_DATA_ACTIONS = {"role_data_add": "add", "role_data_remove": "remove"}
DATA_ADDED, DATA_REMOVED = "role.data_added", "role.data_removed"
ACL_FOLDER_NONE = "__acl_folder_none__"  # the Core's folder id of a document with rights of its own
POINTER_MAX = 1024  # the Core's FOLDER_RULE_PREFIX_MAX_LEN
WALLS_OPEN, OPENS, ALREADY_READ_IN = "walls-open", "opens", "already-read-in"
NEEDS_CONFIRM = frozenset({WALLS_OPEN, OPENS})
#: Documents per page asked of the Core; pages one question walks at most (the page itself: fewer, on every visit).
_PAGE_SIZE, _MAX_PAGES, _PAGE_WALK = 200, 50, 5

_EXPIRED = "Formular ist abgelaufen. Bitte erneut versuchen."
_NOT_SAVED = "Die Änderung konnte nicht protokolliert werden und wurde deshalb nicht gespeichert."
_CONFIRM_FIRST = "Der Probelauf warnt. Bitte die Warnung bestätigen (Häkchen) und erneut absenden."
_SAME_GROUP = "Die Rolle hat diese Gruppe schon."


class RoleDataError(Exception):
    """A refusal: plain German, safe to show and to log (no pointer, name or group id); ``status`` is the answer's."""

    status = 400
    default = "Diese Änderung an den Daten der Rolle wurde abgelehnt."

    def __init__(self, message: Optional[str] = None) -> None:
        super().__init__(message or self.default)


class DataRefused(RoleDataError):
    pass


class CoreUnreadable(RoleDataError):
    status = 502
    default = "Knovas hat die Gruppen, Ordnerregeln oder Dokumente nicht genannt. Nichts wurde geplant oder geändert."


class CoreRefused(RoleDataError):
    status = 502
    default = "Knovas hat die Änderung nicht gespeichert. Die Daten der Rolle sind unverändert."


class RowMissing(RoleDataError):
    status = 500
    default = "Die Änderung steht bei Knovas, aber ihr Protokolleintrag konnte nicht geschrieben werden."


class _Unfinished(Exception):
    """A walk of the Core's documents stopped at its page limit with a cursor left."""


_ADMINISTRATION = "Die Administration hat keine Daten: ihre Gruppe ist die Administratorgruppe des Mandanten."
_NO_GROUP = "Die Rolle hat noch keine Gruppe bei Knovas."
_BAD_OP, _BAD_KIND = "Diese Änderung gibt es nicht.", "Bitte Quelle, Ordner oder Dokument wählen."
_BAD_POINTER = f"Bitte einen Ordner oder ein Dokument angeben: 1 bis {POINTER_MAX} Zeichen, ohne Steuerzeichen."
_UNKNOWN_DOCUMENT = "Dieses Dokument kennt Knovas nicht."
_NOT_THE_ROLES = "Das gehört nicht zu den Daten der Rolle: keine eigene Regel oder eigene Rechte nennen ihre Gruppe."
_ONLY_ADMINISTRATION = "Die Daten einer Rolle setzt nur eine Person der Administration um (unter «Freigaben»)."
_ROLE_GONE = "Die Rolle gibt es nicht mehr. Nichts wurde geändert."
_GROUP_CHANGED = "Die Gruppe der Rolle hat sich seit der Anfrage geändert. Nichts wurde geändert; bitte neu planen."
_WARNS_NOW = "Die Änderung warnt inzwischen. Nichts wurde geändert; bitte neu planen und die Warnung bestätigen."
_ADOPT_NONE, _ADOPT_UNKNOWN = "Bitte eine Gruppe wählen.", "Diese Gruppe nennt Knovas nicht."
_ADOPT_ADMIN = "Die Administratorgruppe gehört zur Administration, und die Administration behält sie."
_ADOPT_TAKEN = "Diese Gruppe gehört schon einer anderen Rolle: jede Rolle hat ihre eigene Gruppe."
_ADOPT_HAS_DATA = "Die jetzige Gruppe der Rolle hat noch Daten. Entfernen Sie sie zuerst, jede mit ihrem Probelauf."


# -- reading the Core ------------------------------------------------------------------------------------------------


def _read(what: str, call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - every Core failure is CoreUnreadable
        logger.warning("Knovas did not answer a read of %s: %s", what, type(exc).__name__)
        raise CoreUnreadable() from None


def _groups_of(item) -> Tuple[str, ...]:
    return tuple(sorted({str(g) for g in (item.get("access_groups") or ()) if g})) if isinstance(item, Mapping) else ()


def _listed(what, call) -> list:
    answer = _read(what, call)
    if not isinstance(answer, list):
        raise CoreUnreadable()
    return [a for a in answer if isinstance(a, Mapping)]


def _live_rules(client) -> List[dict]:
    """The folder rules (id, prefix, groups), longest prefix first: the first that a pointer starts with governs."""
    rules = [{"rule_id": str(r["rule_id"]), "pointer_prefix": r["pointer_prefix"], "access_groups": _groups_of(r)}
             for r in _listed("the folder rules", client.folder_rules)
             if r.get("rule_id") and isinstance(r.get("pointer_prefix"), str)]
    return sorted(rules, key=lambda r: (-len(r["pointer_prefix"]), r["pointer_prefix"]))


def _governing(rules, pointer: str) -> Optional[dict]:
    return next((r for r in rules if pointer.startswith(r["pointer_prefix"])), None)


def _core_groups(client) -> List[dict]:
    return [dict(g) for g in _listed("the groups", client.access_groups) if g.get("group_id")]


def _walk(client, *, pages: int = _MAX_PAGES, **filters):
    """The Core's documents along the cursor; ``_Unfinished`` when ``pages`` pages were read and the cursor goes on."""
    after, seen = None, set()
    for _ in range(pages):
        page = _read("the documents", client.documents, after=after, limit=_PAGE_SIZE, **filters)
        if not isinstance(page, Mapping) or page.get("unavailable"):
            raise CoreUnreadable()
        yield from (e for e in page.get("documents") or () if isinstance(e, Mapping))
        after = page.get("next_after")
        if not after or after in seen:
            return
        seen.add(after)
    raise _Unfinished()


def _own_rights(entry: Mapping) -> bool:
    """Rights of its own: no governing rule and a group set (the Core's acl_backfill_worker._is_explicit_override)."""
    return entry.get("acl_folder_id") in (None, "", ACL_FOLDER_NONE) and bool(_groups_of(entry))


def tenant_brokered(client) -> bool:
    """The client reports the tenant as brokered («Ein», CR-R1). No such call, an error or another mode: False."""
    getter = getattr(client, "rbac_posture", None)
    if not callable(getter):
        return False
    try:
        posture = getter()
    except Exception as exc:  # noqa: BLE001 - unknown is "not brokered"
        logger.info("Knovas did not report the tenant's posture: %s", type(exc).__name__)
        return False
    return isinstance(posture, Mapping) and posture.get("mode") == "brokered"


class Sight:
    """Who reads a set of groups under «Ein»: its holders and the holders of a group above (the Core's reader closure);
    everyone when the set is empty (open). ``people``: every active person's claim groups."""

    def __init__(self, groups, people: Mapping[str, set]) -> None:
        self.tree, self.people = Tree.of(groups), {str(u): set(h) for u, h in people.items()}

    def closure(self, groups) -> set:
        return {a for g in groups for a in (g, *self.tree.ancestors(g))}

    def readers(self, groups) -> frozenset:
        closure = self.closure(groups)
        return frozenset(u for u, held in self.people.items() if not groups or held & closure)


# -- the plan of a data change ----------------------------------------------------------------------------------------


@dataclass
class Plan:
    op: str
    kind: str
    pointer: str
    group: str
    call: str  # update | create | delete (a folder rule), set (a document's rights), none (no change)
    before: Tuple[str, ...] = ()
    after: Tuple[str, ...] = ()
    rule_id: Optional[str] = None
    document_uuid: Optional[str] = None
    gains: frozenset = frozenset()
    losses: frozenset = frozenset()
    warnings: Tuple[str, ...] = ()
    leaves_rule: bool = False  # a document taken out of its folder rule
    through_folder: bool = False  # "none": the role reads it through a rule above

    @property
    def needs_confirm(self) -> bool:
        return bool(NEEDS_CONFIRM & set(self.warnings))


def clean_pointer(value: Any) -> str:
    text = (value if isinstance(value, str) else "").strip()
    if not 1 <= len(text) <= POINTER_MAX or any(unicodedata.category(c) == "Cc" for c in text):
        raise DataRefused(_BAD_POINTER)
    return text


def plan_data(client, conn, role, op: Any, kind: Any, pointer: Any) -> Plan:
    """What adding or removing ``pointer`` would send to the Core and who would gain or lose sight. Writes nothing."""
    if role.kind == ADMINISTRATION:
        raise DataRefused(_ADMINISTRATION)
    if op not in OPS:
        raise DataRefused(_BAD_OP)
    if kind not in KINDS:
        raise DataRefused(_BAD_KIND)
    pointer, group = clean_pointer(pointer), role.core_group_id
    if not group:
        raise DataRefused(_NO_GROUP)
    groups, rules = _core_groups(client), _live_rules(client)
    plan = (_plan_document if kind == "document" else _plan_folder)(client, rules, op, kind, pointer, group)
    if plan.call != "none":
        sight = Sight(groups, active_claim_groups(conn))
        was, will = sight.readers(plan.before), sight.readers(plan.after)
        plan.gains, plan.losses = will - was, was - will
        first = WALLS_OPEN if op == "add" and not plan.before else OPENS if op == "remove" and not plan.after else None
        plan.warnings = ((first,) if first else ()) + plan.warnings
    return plan


def _with(group: str, groups) -> Tuple[str, ...]:
    return tuple(sorted(set(groups) | {group}))


def _plan_folder(client, rules, op, kind, pointer, group) -> Plan:
    own = next((r for r in rules if r["pointer_prefix"] == pointer), None)
    if op == "add":
        if own is not None:
            before = own["access_groups"]
            call = "none" if group in before else "update"
            return Plan(op, kind, pointer, group, call, before, _with(group, before), rule_id=own["rule_id"])
        governing = _governing(rules, pointer)
        before = governing["access_groups"] if governing else ()
        if group in before:
            return Plan(op, kind, pointer, group, "none", through_folder=True)
        # A new rule keeps the groups that govern the folder today: nobody loses sight.
        plan = Plan(op, kind, pointer, group, "create", before, _with(group, before))
        plan.warnings = (ALREADY_READ_IN,) if _read_in_under(client, rules, pointer) else ()
        return plan
    if own is None or group not in own["access_groups"]:
        raise DataRefused(_NOT_THE_ROLES)
    before = own["access_groups"]
    rest = tuple(g for g in before if g != group)
    if rest:
        return Plan(op, kind, pointer, group, "update", before, rest, rule_id=own["rule_id"])
    # Never an empty set to update_folder_rule: the rule goes, and the rule above governs (none: open).
    successor = _governing([r for r in rules if r is not own], pointer)
    after = successor["access_groups"] if successor else ()
    return Plan(op, kind, pointer, group, "delete", before, after, rule_id=own["rule_id"])


def _read_in_under(client, rules, pointer: str) -> bool:
    """The Core lists a document under ``pointer`` that a new rule reaches only after the backfill (CR-R3): without
    rights of its own and not governed by a deeper rule. A walk that cannot finish says yes (it only informs)."""
    deeper = {r["rule_id"] for r in rules if len(r["pointer_prefix"]) > len(pointer)}
    try:
        return any(not _own_rights(e) and e.get("acl_folder_id") not in deeper for e in _walk(client, prefix=pointer))
    except _Unfinished:
        return True


def _plan_document(client, rules, op, kind, pointer, group) -> Plan:
    current = _read("a document's rights", client.document_access, pointer)
    if not isinstance(current, Mapping):
        raise DataRefused(_UNKNOWN_DOCUMENT)
    try:
        entry = next((e for e in _walk(client, prefix=pointer)
                      if e.get("pointer") == pointer or pointer in (e.get("pointers") or ())), None)
    except _Unfinished:
        entry = None
    uuid_ = str(entry["document_uuid"]) if entry is not None and entry.get("document_uuid") else None
    if op == "add":
        # Today's groups: its live rule's when one governs it, else its own (document_access).
        governing = None if entry is None or _own_rights(entry) else next(
            (r for r in rules if r["rule_id"] == entry.get("acl_folder_id")), None)
        before = governing["access_groups"] if governing else _groups_of(current)
        if group in before:
            return Plan(op, kind, pointer, group, "none", document_uuid=uuid_, through_folder=bool(governing))
        return Plan(op, kind, pointer, group, "set", before, _with(group, before), document_uuid=uuid_,
                    leaves_rule=governing is not None)
    before = _groups_of(current)
    if entry is None or not _own_rights(entry) or group not in before:
        raise DataRefused(_NOT_THE_ROLES)
    return Plan(op, kind, pointer, group, "set", before, tuple(g for g in before if g != group), document_uuid=uuid_)


# -- carrying a change out --------------------------------------------------------------------------------------------


def _send(client, plan: Plan) -> None:
    """The plan's one Core call through the client's existing calls; never acting_as, never an empty rule."""
    if plan.call == "update" and plan.after:
        answer = client.update_folder_rule(plan.rule_id, list(plan.after))
    elif plan.call == "create":
        answer = client.create_folder_rule(plan.pointer, list(plan.after))
        plan.rule_id = str(answer["rule_id"]) if isinstance(answer, Mapping) and answer.get("rule_id") else None
    elif plan.call == "delete":
        answer = {} if client.delete_folder_rule(plan.rule_id) is True else None
    elif plan.call == "set":
        answer = client.set_document_access(plan.pointer, list(plan.after))
    else:
        answer = None
    if answer is None:
        raise CoreRefused()


def _detail(plan: Plan) -> dict:
    """The audit row's detail and the executor's answer: ids, the kind and counts; never the pointer."""
    ident = {"document_uuid": plan.document_uuid} if plan.kind == "document" else {"rule_id": plan.rule_id}
    return {"core_group_id": plan.group, "kind": plan.kind, "call": plan.call, "gains": len(plan.gains),
            "losses": len(plan.losses), **ident}


def execute_role_data(client, payload: Mapping[str, Any], *, actor, conn) -> dict:
    """Carry out a role-data acl_change: only for an administrator; the role's row held (``RoleStore.share``) from the
    check of its group to the audit row, so an adopt and a data change of the role wait for each other; planned again;
    refused, nothing sent, when the role's group is not the payload's or the plan now warns without its confirm."""
    if ADMIN not in (getattr(actor, "roles", None) or ()):
        raise DataRefused(_ONLY_ADMINISTRATION)
    op = ROLE_DATA_ACTIONS.get(str(payload.get("action") or ""))
    if op is None:
        raise DataRefused(_BAD_OP)
    action, refused = (DATA_ADDED if op == "add" else DATA_REMOVED), {}
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
            if plan.call == "none":
                return {"role_id": str(role.id), "kind": plan.kind, "changed": 0}
            try:
                _send(client, plan)
            except Exception as exc:  # noqa: BLE001 - recorded and shown, never raised raw
                refused.update(role=role, plan=plan, error=type(exc).__name__)
                raise CoreRefused() from None
            try:
                audit.record_or_raise(conn, action=action, actor=actor, target_type="role", target_id=str(role.id),
                                      detail=_detail(plan))
            except Exception as exc:  # noqa: BLE001 - the Core has changed; the page says the row is missing
                logger.error("Role %s: data changed at Knovas, no audit row, %s", role.id, audit._error_code(exc))
                raise RowMissing() from None
    except CoreRefused:
        if refused:  # its row once the transaction above is undone (best effort)
            role, plan = refused["role"], refused["plan"]
            logger.warning("Role %s: Knovas refused the data change (%s, %s)", role.id, plan.call, refused["error"])
            audit.record(conn, action=action, actor=actor, target_type="role", target_id=str(role.id),
                         outcome="error", detail=dict(_detail(plan), error=refused["error"]))
        raise
    logger.info("Role %s: data %s at Knovas (%s, %d gain, %d lose)", role.id, op, plan.call, len(plan.gains),
                len(plan.losses))
    return {"role_id": str(role.id), "changed": 1, **_detail(plan)}


# -- adopting a group (FU-45) ------------------------------------------------------------------------------------------


@dataclass
class AdoptPlan:
    group: str
    name: str
    previous: Optional[str]
    gains: frozenset = frozenset()
    same: bool = False


def plan_adopt(client, conn, role, roles, group_id: Any) -> AdoptPlan:
    """Adopting ``group_id``: the role's active members who do not read it today gain it; nobody loses (the present
    group must carry no data). Writes nothing."""
    if role.kind == ADMINISTRATION:
        raise DataRefused(_ADOPT_ADMIN)
    wanted = (group_id if isinstance(group_id, str) else "").strip()
    if not wanted:
        raise DataRefused(_ADOPT_NONE)
    groups = _core_groups(client)
    found = next((g for g in groups if str(g["group_id"]) == wanted), None)
    if found is None:
        raise DataRefused(_ADOPT_UNKNOWN)
    if found.get("is_admin") is True:
        raise DataRefused(_ADOPT_ADMIN)
    name = str(found.get("name") or wanted)
    if wanted == role.core_group_id:
        return AdoptPlan(wanted, name, wanted, same=True)
    if any(r.core_group_id == wanted for r in roles if r.id != role.id):
        raise DataRefused(_ADOPT_TAKEN)
    if role.core_group_id and _carries_data(client, role.core_group_id):
        raise DataRefused(_ADOPT_HAS_DATA)
    people = active_claim_groups(conn)
    reads = Sight(groups, people).closure([wanted])
    gains = frozenset(str(m) for m in role.members if str(m) in people and not people[str(m)] & reads)
    return AdoptPlan(wanted, name, role.core_group_id, gains)


def _carries_data(client, group: str) -> bool:
    """A folder rule names the group, or the Core lists a document for it (a walk that cannot finish: unreadable)."""
    if any(group in r["access_groups"] for r in _live_rules(client)):
        return True
    try:
        return any(True for _ in _walk(client, group=group))
    except _Unfinished:
        raise CoreUnreadable() from None


def _delete_old_group(client, conn, me, role_id, group: str) -> bool:
    """After an adopt, best effort; the outcome goes into the audit log by id (RB-4's role.core_group_abandoned)."""
    try:
        removed = client.delete_access_group(group) is True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Role %s: Knovas kept the previous group: %s", role_id, type(exc).__name__)
        removed = False
    audit.record(conn, action="role.core_group_abandoned", actor=me, target_type="role", target_id=str(role_id),
                 outcome="ok" if removed else "error",
                 detail={"core_group_id": group, "removed_at_core": removed, "after_adopt": True})
    return removed


# -- moving direct grants (FU-41) --------------------------------------------------------------------------------------


@dataclass
class MovePlan:
    new: Dict[str, List[str]] = field(default_factory=dict)  # group -> people who get a new role for it
    gone: List[Tuple[str, str, str]] = field(default_factory=list)  # (person, group, role): a role gives it already
    left: List[Tuple[str, str, str]] = field(default_factory=list)  # (person, group, reason): stays direct

    @property
    def moved(self) -> int:
        return sum(len(p) for p in self.new.values()) + len(self.gone)

    @property
    def people(self) -> List[str]:
        return sorted({p for ps in self.new.values() for p in ps} | {p for p, _, _ in self.gone})


def plan_move(conn, groups, roles) -> MovePlan:
    """Which running direct grants become roles, which go and which stay (work order § 8). Writes nothing."""
    listed = {str(g["group_id"]): g for g in groups}
    plan = MovePlan()
    rows = conn.execute("SELECT user_id, group_id, ends_on FROM user_access_groups "
                        "WHERE ends_on IS NULL OR ends_on >= current_date ORDER BY group_id, user_id").fetchall()
    for user_id, group, ends_on in rows:
        person, holders = str(user_id), [r for r in roles if r.core_group_id == group]
        holder = next((r for r in holders if user_id in r.members), None)
        why = ("ends_on" if ends_on is not None else "unknown_group" if group not in listed
               else "admin_group" if listed[group].get("is_admin") is True else None)
        if why is None and holders:
            why = None if holder else "not_a_member"
            if holder:
                plan.gone.append((person, group, str(holder.id)))
                continue
        if why:
            plan.left.append((person, group, why))
        else:
            plan.new.setdefault(group, []).append(person)
    return plan


def role_name_for(group_name: Any, taken: set) -> str:
    """A new role's name from its group's: no control character, white space collapsed, at most 80 characters,
    «Gruppe» when empty, " (2)" … when taken (case-insensitive)."""
    text = "".join(c for c in str(group_name or "") if unicodedata.category(c)[0] != "C")
    base = " ".join(text.split())[:NAME_MAX].strip() or "Gruppe"
    name, number = base, 1
    while name.lower() in taken:
        number += 1
        name = base[: NAME_MAX - len(f" ({number})")].rstrip() + f" ({number})"
    return name


class _SightChanged(Exception):
    pass


class _NoChange(Exception):
    pass


def _move(conn, store: RoleStore, users, groups, me) -> Tuple[MovePlan, List[str]]:
    """In the caller's transaction: every role's row, then the people with direct grants (id order), then the plan; a
    member joins before the grant goes (the 32-group cap never sees a new group); every touched person's claim groups
    and functions are compared before and after, and a difference undoes it all."""
    store.lock_all()
    conn.execute("SELECT id FROM users WHERE id IN (SELECT user_id FROM user_access_groups) ORDER BY id "
                 "FOR NO KEY UPDATE")
    roles = store.list_roles()
    plan = plan_move(conn, groups, roles)
    if not plan.moved:
        raise _NoChange()

    def sight() -> dict:
        return {p: (claim_groups(conn, p), sorted(users.roles_of(p))) for p in plan.people}

    before, listed = sight(), {str(g["group_id"]): g for g in groups}
    taken, created, grants, revoke = {r.name.lower() for r in roles}, [], [], list(plan.gone)
    for group, people in sorted(plan.new.items()):
        name = role_name_for(listed[group].get("name"), taken)
        role = store.create_role(name=name, functions=(), core_group_id=group, by=me.id)
        taken.add(name.lower())
        created.append(str(role.id))
        audit.record_or_raise(conn, action="role.created", actor=me, target_type="role", target_id=str(role.id),
                              detail={"core_group_id": group, "functions": [], "from_direct_grants": True})
        for person in people:
            store.add_member(role.id, person, by=me.id)
            audit.record_or_raise(conn, action="role.member_added", actor=me, target_type="role",
                                  target_id=str(role.id), detail={"user_id": person, "from_direct_grants": True})
            revoke.append((person, group, str(role.id)))
    for person, group, role_id in revoke:
        users.revoke_access_group(person, group)
        grants.append([person, group, role_id])
    if sight() != before:
        raise _SightChanged()
    audit.record_or_raise(conn, action="role.direct_grants_moved", actor=me, target_type="role", target_id=None,
                          detail={"roles": created, "people": plan.people, "grants_moved": len(grants),
                                  "grants_left": len(plan.left), "grants": grants})
    return plan, created


# -- the page ----------------------------------------------------------------------------------------------------------


def role_data_part(client, conn, shown, roles) -> dict:
    """What the part «Darf lesen» shows for ``shown``, read from the Core now. Writes nothing; a Core that cannot be
    read leaves the part saying so."""
    part: Dict[str, Any] = {"ein": tenant_brokered(client), "rows": [], "error": False, "partial": False,
                            "addable": False, "why": None, "adopt": None, "adopt_unread": False}
    group = shown.core_group_id
    if shown.kind == ADMINISTRATION or not group:
        part["why"] = "administration" if shown.kind == ADMINISTRATION else "no-group"
    else:
        part["addable"] = True
        try:
            rules = [{"kind": "folder", "id": r["rule_id"], "pointer": r["pointer_prefix"]}
                     for r in _live_rules(client) if group in r["access_groups"]]
            documents = []
            try:
                documents = [{"kind": "document", "id": str(e.get("document_uuid") or ""), "pointer": str(e["pointer"])}
                             for e in _walk(client, pages=_PAGE_WALK, group=group)
                             if _own_rights(e) and group in _groups_of(e) and e.get("pointer")]
            except _Unfinished:
                part["partial"] = True
            part["rows"] = sorted(rules, key=lambda r: r["pointer"]) + sorted(documents, key=lambda r: r["pointer"])
        except CoreUnreadable:
            part["error"] = True
    if shown.kind != ADMINISTRATION:
        taken = {r.core_group_id for r in roles if r.core_group_id}
        try:
            part["adopt"] = sorted(({"id": str(g["group_id"]), "name": str(g.get("name") or g["group_id"])}
                                    for g in _core_groups(client)
                                    if g.get("is_admin") is not True and str(g["group_id"]) not in taken),
                                   key=lambda g: g["name"].casefold())
        except CoreUnreadable:
            part["adopt"], part["adopt_unread"] = [], True
    counts = conn.execute("SELECT count(*), count(DISTINCT user_id) FROM user_access_groups WHERE ends_on IS NULL")
    part["open_grants"], part["open_grant_people"] = (int(n) for n in counts.fetchone())
    return part


def _people(ids, names) -> List[Tuple[str, str]]:
    return sorted(((i, names.get(i, i)) for i in ids), key=lambda p: (p[1].casefold(), p[0]))


def _no_change(plan: Plan) -> str:
    if plan.through_folder:
        return "Die Rolle liest das schon über die Regel eines Ordners darüber. Nichts wurde geändert."
    return "Das gehört schon zu den Daten der Rolle. Nichts wurde geändert."


# -- the routes --------------------------------------------------------------------------------------------------------


def attach_role_data_routes(bp, gate, *, csrf_valid, client_factory, require_admin, page):
    """Mount the seven routes on the console's blueprint behind its ``require_admin``; every answer is ``page``, the
    Rollen page (``admin_roles._page``), with a dry run, a notice or a refusal. The token first (400), then the role
    (404), then the form (400), then the Core (502 when it cannot be read)."""

    def _csrf_ok() -> bool:
        return csrf_valid(str(request.form.get("csrf_token", "") or ""))

    def _role(role_id):
        try:
            return RoleStore(gate.connection()).get_role(role_id)
        except RoleNotFound:
            abort(404)

    def _names() -> Dict[str, str]:
        return {str(p.id): (p.display_name or p.email) for p in gate.users().list_all()}

    def _refused(role, exc: RoleDataError, what: str, **extra):
        logger.info("Role %s: %s refused (%s)", getattr(role, "id", "-"), what, type(exc).__name__)
        return page(getattr(role, "id", None), error=str(exc), status=exc.status, **extra)

    def _data_view(plan: Plan, role) -> dict:
        names = _names()
        notes = ["leaves-rule"] * plan.leaves_rule + ["rule-deleted"] * (plan.call == "delete")
        return {"what": "data", "op": plan.op, "kind": plan.kind, "pointer": plan.pointer, "warnings": plan.warnings,
                "gains": _people(plan.gains, names), "losses": _people(plan.losses, names), "notes": notes,
                "action": url_for(f"admin.{plan.op}_role_data", role_id=role.id), "confirm": plan.needs_confirm,
                "fields": [("kind", plan.kind), ("pointer", plan.pointer)]}

    @bp.route("/roles/<role_id>/data/preview", methods=["POST"])
    @require_admin
    def preview_role_data(role_id):
        if not _csrf_ok():
            return page(role_id, error=_EXPIRED, status=400)
        role, op = _role(role_id), str(request.form.get("op", "") or "")
        try:
            plan = plan_data(client_factory(), gate.connection(), role, op, request.form.get("kind"),
                             request.form.get("pointer"))
        except RoleDataError as exc:
            sent = {k: str(request.form.get(k, "") or "") for k in ("kind", "pointer")}
            return _refused(role, exc, "data preview", data_form=sent if op == "add" else None)
        if plan.call == "none":
            return page(role.id, notice=_no_change(plan))
        return page(role.id, dry_run=_data_view(plan, role))

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
            return page(role.id, error=_CONFIRM_FIRST, status=400, dry_run=_data_view(plan, role))
        # A confirmation counts only for the warning it answered: a request planned without one stores none (F1).
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
            return page(role.id, notice=f"Zur Freigabe eingereicht (Nr. {str(outcome.request.id)[:8]}). Eine zweite "
                                        "Person bestätigt unter «Freigaben»; bis dahin ändert sich bei Knovas nichts.")
        return page(role.id, notice="Bei Knovas gespeichert. Die Daten der Rolle unten sind so, wie Knovas sie nennt.")

    @bp.route("/roles/<role_id>/data/add", methods=["POST"])
    @require_admin
    def add_role_data(role_id):
        return _change(role_id, "add")

    @bp.route("/roles/<role_id>/data/remove", methods=["POST"])
    @require_admin
    def remove_role_data(role_id):
        return _change(role_id, "remove")

    @bp.route("/roles/<role_id>/adopt/preview", methods=["POST"])
    @require_admin
    def preview_adopt_group(role_id):
        if not _csrf_ok():
            return page(role_id, error=_EXPIRED, status=400)
        role, conn = _role(role_id), gate.connection()
        try:
            plan = plan_adopt(client_factory(), conn, role, RoleStore(conn).list_roles(), request.form.get("group_id"))
        except RoleDataError as exc:
            return _refused(role, exc, "adopt preview")
        if plan.same:
            return page(role.id, notice=_SAME_GROUP)
        return page(role.id, dry_run={
            "what": "adopt", "name": plan.name, "gains": _people(plan.gains, _names()), "losses": [], "warnings": (),
            "notes": ["old-group-deleted"] * bool(plan.previous) + ["no-group-created"], "confirm": False,
            "action": url_for("admin.adopt_group", role_id=role.id), "fields": [("group_id", plan.group)]})

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
                # Every role's row first, then every check (the Core is read while they are held): no other role takes
                # the group, and no data change of this role (it holds the row while it writes) lands in between.
                store.lock_all()
                held = {r.id: r for r in store.list_roles()}
                if role.id not in held:
                    raise DataRefused(_ROLE_GONE)
                plan = plan_adopt(client, conn, held[role.id], list(held.values()), request.form.get("group_id"))
                if plan.same:
                    raise _NoChange()
                store.set_core_group(role.id, plan.group, by=me.id)
                audit.record_or_raise(conn, action="role.core_group_adopted", actor=me, target_type="role",
                                      target_id=str(role.id),
                                      detail={"core_group_id": plan.group, "previous_core_group_id": plan.previous,
                                              "gains": len(plan.gains)})
        except _NoChange:
            return page(role.id, notice=_SAME_GROUP)
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
            notice += (" Die bisherige Gruppe wurde bei Knovas gelöscht." if _delete_old_group(
                client, conn, me, role.id, plan.previous) else " Knovas hat die bisherige Gruppe behalten (Protokoll).")
        return page(role.id, notice=notice)

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
        roles, names = RoleStore(conn).list_roles(), _names()
        plan = plan_move(conn, groups, roles)
        group_names = {str(g["group_id"]): str(g.get("name") or g["group_id"]) for g in groups}
        role_names = {str(r.id): r.name for r in roles}
        return page(None, grants={
            "new": [{"group": group_names[g], "people": [n for _, n in _people(ps, names)]}
                    for g, ps in sorted(plan.new.items(), key=lambda kv: group_names[kv[0]].casefold())],
            "gone": [{"person": names.get(p, p), "role": role_names.get(r, r)} for p, _, r in plan.gone],
            "left": [{"person": names.get(p, p), "group": group_names.get(g, g), "why": why}
                     for p, g, why in plan.left],
            "moved": plan.moved})

    @bp.route("/roles/direct-grants/move", methods=["POST"])
    @require_admin
    def move_direct_grants():
        if not _csrf_ok():
            return page(None, error=_EXPIRED, status=400)
        me, conn = gate.current_user(), gate.connection()
        try:
            groups = _core_groups(client_factory())  # before any lock is taken
        except RoleDataError as exc:
            return _refused(None, exc, "grants move")
        try:
            with conn.transaction():
                plan, created = _move(conn, RoleStore(conn), gate.users(), groups, me)
        except _NoChange:
            return page(None, notice="Keine direkten Gruppenrechte, die eine Rolle übernehmen kann.")
        except _SightChanged:
            logger.error("Direct grants not moved: a person's claim would have changed")
            return page(None, error="Nichts wurde übernommen: die Sicht einer Person hätte sich geändert.", status=500)
        except RoleError as exc:
            logger.info("Direct grants not moved (%s)", type(exc).__name__)
            return page(None, error=str(exc) + " Nichts wurde übernommen.", status=400)
        except Exception as exc:  # noqa: BLE001
            logger.error("Direct grants not moved, %s", audit._error_code(exc))
            return page(None, error=_NOT_SAVED, status=500)
        logger.info("Direct grants moved: %d grants, %d new roles, %d left", plan.moved, len(created), len(plan.left))
        return page(None, notice=f"{plan.moved} direkte Gruppenrechte in Rollen übernommen, {len(created)} neue Rollen "
                                 "ohne Funktionen. Niemand sieht danach mehr oder weniger.")

    return bp
