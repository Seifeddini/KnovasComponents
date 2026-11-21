"""RB-5 acceptance tests: a role's data. Adding and removing a source, folder or document to a role, the dry run before
each change, four eyes, adopting an existing Core group as a role's group (FU-45) and moving people's direct group
grants into roles (FU-41) (FEAT-2026-11-19-platform-mvp, design docs/platform-mvp/design/role-builder.md § 2.2, § 3,
§ 6, § 7; Core requests CR-R1 to CR-R3 in docs/platform-mvp/core-requests.md; work order
docs/platform-mvp/work-orders/RB-5.md).

FROZEN once the coordinator pins it. Written for the coordinator before the build; a builder never edits this file.
Its git blob id goes into docs/platform-mvp/frozen-tests.txt.

What is pinned (the sections below carry the same numbers):

1. Gates and tokens. The seven RB-5 routes (``/admin/roles/<id>/data/preview``, ``/data/add``, ``/data/remove``,
   ``/admin/roles/<id>/adopt/preview``, ``/adopt``, ``/admin/roles/direct-grants/preview``, ``/direct-grants/move``)
   sit behind the console's admin gate: signed out 302 to the sign-in, every other function and an account without
   one 403, nothing changed, nothing sent to the Core. Each checks the form's ``csrf_token``: 400, nothing changed,
   nothing sent, no audit row, no approval request.
2. The part «Darf lesen». It shows what the Core holds, read back each time (the Platform keeps no copy, design § 3):
   the folder rules naming the role's group (``data-rule-id``) and the documents whose own rights name it
   (``data-document-uuid``), escaped. It offers «+ Daten hinzufügen» (a form to the dry run with ``op``, ``kind``,
   ``pointer``) and a dry run before each removal. «wirkt mit «Ein»» stays while the Platform does not know the tenant
   is ``brokered``; only a client reporting ``rbac_posture() == {"mode": "brokered"}`` takes it away. Opening the page
   writes nothing. Administration gets no data.
3. The dry run (``/data/preview``). It writes nothing anywhere. It names who gains sight and who loses it, as
   counts (``data-gains``, ``data-losses`` on the element ``data-dry-run="data"``) and person ids
   (``data-gains-person``, ``data-loses-person``), computed as the data will act under «Ein»: a person sees a folder
   or document when it is open or when they hold one of its groups or a group above it. A new folder rule keeps the
   groups of the rule that governs the folder today, so adding a walled folder takes sight from nobody; when
   documents are already read in under it, the dry run says the new rule reaches them only after Knovas's backfill
   (CR-R3, ``data-warning="already-read-in"``).
4. The open-folder warning. Adding an open folder or document (no rule, or a rule with no group) walls it for
   everyone else: the dry run warns (``data-warning="walls-open"``) and the change needs ``confirm=1``, else 400 and
   nothing is sent. A removal that would leave a folder's rule without a group deletes the rule instead of emptying
   it (never an empty set to ``update_folder_rule``), warns ``data-warning="opens"`` and needs ``confirm=1`` too.
5. Four eyes. Adding and removing data go the existing path ``acl_change`` (``identity/approvals.py``): with the
   administrators' bypass on, an administrator acts alone and ``approval.bypassed`` records it; with it off, the change
   waits for a second person and nothing reaches the Core. The requester cannot confirm her own change. Only an
   administrator carries it out (an approver's confirmation leaves it approved, nothing sent); it is planned again when
   it is carried out, so a folder that became open meanwhile is not walled without the confirmation. Adopting a group
   and moving direct grants need no second person (draft decision).
6. The Core calls. Through the client's existing calls: ``update_folder_rule`` on the folder's own rule,
   ``create_folder_rule`` where it has none, ``delete_folder_rule``, ``set_document_access`` with the document's
   current groups plus or minus the role's group; never ``acting_as``. Before «Ein» the data are written to the Core
   (they hide nothing under «Aus») and read back with «wirkt mit «Ein»». A Core refusal is 502, says «Knovas», and
   the page claims nothing.
7. Adopting a group (FU-45). A role takes over an existing Core group, «Mitglied» included, with a dry run of who
   gains; never the administrator group, another role's group or an unknown one; never for Administration; never
   while the role's present group still carries data; never a 33rd group. The old group is deleted at the Core.
8. Moving direct grants (FU-41). Each group held directly without an end date becomes one role without functions
   whose Core group is that group, its holders its members, and their direct grants go; a grant of a group a role
   already gives them goes too. Left as they are: grants with an end date, the administrator group, a group the Core
   does not list, a role's group for a person outside that role. Everyone's claim groups and functions stay exactly
   as they were; nothing is written to the Core.
9. Audit. Every change writes its row with ids and counts only: never a pointer, a folder or document name, a
   role's or group's name or a person's name.
10. Logs. No pointer, folder or document name, role or group name, person's name or address reaches a log line,
    DEBUG included, on any path of RB-5 (the approvals' bypass line included).

The interfaces it builds on are RB-1's ``identity.roles.RoleStore`` (pinned by test_rb12_roles_acceptance.py),
RB-3/RB-4's page (test_rb34_roles_page_acceptance.py) and the approvals module. Nothing reaches a tenant: the Core is
a recording fake.
"""
from __future__ import annotations

import html as htmllib
import json
import logging
import re
import uuid
from datetime import date, timedelta

import pytest
import requests

import conftest
from conftest import FakeGraphApi, platform_db_reachable

pytestmark = pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")

from identity.approvals import ApprovalService  # noqa: E402
from identity.roles import RoleStore, claim_groups  # noqa: E402
from identity.users import UserRepository  # noqa: E402

DATA_NOTE = "wirkt mit «Ein»"
ACL_FOLDER_NONE = "__acl_folder_none__"


def PREVIEW(role_id):                                                # noqa: N802 - an address, like a constant
    return f"/admin/roles/{role_id}/data/preview"


def ADD(role_id):                                                    # noqa: N802
    return f"/admin/roles/{role_id}/data/add"


def REMOVE(role_id):                                                 # noqa: N802
    return f"/admin/roles/{role_id}/data/remove"


def ADOPT_PREVIEW(role_id):                                          # noqa: N802
    return f"/admin/roles/{role_id}/adopt/preview"


def ADOPT(role_id):                                                  # noqa: N802
    return f"/admin/roles/{role_id}/adopt"


def ROLE(role_id):                                                   # noqa: N802
    return f"/admin/roles/{role_id}"


def APPROVE(request_id):                                             # noqa: N802
    return f"/admin/approvals/{request_id}/approve"


def EXECUTE(request_id):                                             # noqa: N802
    return f"/admin/approvals/{request_id}/execute"


GRANTS_PREVIEW = "/admin/roles/direct-grants/preview"
GRANTS_MOVE = "/admin/roles/direct-grants/move"

#: The audit actions (target_type "role").
DATA_ADDED, DATA_REMOVED = "role.data_added", "role.data_removed"
ADOPTED, MOVED, CREATED = "role.core_group_adopted", "role.direct_grants_moved", "role.created"
BYPASSED = "approval.bypassed"

ADMIN_GROUP = {"group_id": "g-admins", "name": "Administratoren", "parent_id": None, "is_admin": True}


# --- the Core: a recording fake ----------------------------------------------------------------------------------------

_WRITE = re.compile(r"^(graph_)?(create|rename|move|delete|set|update|put|patch|post|install|deprecate|retire|"
                    r"upload|purge|push|start|stop|requeue|reextract|apply|grant|revoke)(_|$)")


class DataCore(FakeGraphApi):
    """The Core's groups, folder rules and documents as the app sees them through the client.

    Every call whose name says it writes is recorded with its arguments. A folder rule is a prefix with a set of
    groups; the longest live prefix governs a document unless the document has rights of its own. ``refuse[call]``
    makes a call fail as the real client lets it fail: "none" (a 404: nothing written, the client answers None) or
    "raise" (any other refusal, such as 403 for a group the caller does not dominate). ``posture`` is what the
    client reports from ``rbac_posture()``: None when the client has no such call (the real client today), "raise"
    when it fails, else the mode."""

    calls: list = []
    groups: list = []
    rules: list = []
    docs: list = []
    refuse: dict = {}
    posture = None

    def __getattribute__(self, name):
        if name == "rbac_posture" and DataCore.posture is None:
            raise AttributeError(name)
        attr = object.__getattribute__(self, name)
        if _WRITE.match(name) and callable(attr):
            def recorded(*args, **kwargs):
                DataCore.calls.append((name, args, kwargs))
                return attr(*args, **kwargs)
            return recorded
        return attr

    @staticmethod
    def _mode(call):
        mode = DataCore.refuse.get(call)
        if mode == "raise":
            raise requests.exceptions.HTTPError("403 Client Error: Forbidden")
        return mode

    def rbac_posture(self):
        if DataCore.posture == "raise":
            raise requests.exceptions.ConnectionError("unreachable")
        return {"mode": DataCore.posture}

    # groups
    def access_groups(self):
        self._mode("access_groups")
        return [dict(group) for group in DataCore.groups]

    def create_access_group(self, name, parent=None):
        if self._mode("create_access_group") == "none":
            return None
        group = {"group_id": f"g-{uuid.uuid4().hex[:10]}", "name": name, "parent_id": parent}
        DataCore.groups.append(group)
        return dict(group)

    def rename_access_group(self, identifier, name):
        if self._mode("rename_access_group") == "none":
            return None
        return {"group_id": identifier, "name": name}

    def delete_access_group(self, identifier):
        if self._mode("delete_access_group") == "none":
            return False
        DataCore.groups = [g for g in DataCore.groups if g["group_id"] != identifier]
        return True

    # folder rules
    @staticmethod
    def _rule(rule_id):
        return next((r for r in DataCore.rules if r["rule_id"] == rule_id), None)

    def folder_rules(self):
        self._mode("folder_rules")
        return [dict(r, access_groups=list(r["access_groups"])) for r in DataCore.rules]

    def create_folder_rule(self, pointer_prefix, access_groups, acting_as=None):
        if self._mode("create_folder_rule") == "none":
            return None
        if any(r["pointer_prefix"] == pointer_prefix for r in DataCore.rules):
            raise requests.exceptions.HTTPError("409 Client Error: Conflict")
        rule = {"rule_id": f"r-{uuid.uuid4().hex[:8]}", "pointer_prefix": pointer_prefix,
                "access_groups": sorted(set(access_groups))}
        DataCore.rules.append(rule)
        return dict(rule)

    def update_folder_rule(self, rule_id, access_groups, acting_as=None):
        if self._mode("update_folder_rule") == "none":
            return None
        rule = self._rule(rule_id)
        if rule is None:
            return None
        rule["access_groups"] = sorted(set(access_groups))
        return dict(rule)

    def delete_folder_rule(self, rule_id):
        if self._mode("delete_folder_rule") == "none":
            return False
        rule = self._rule(rule_id)
        if rule is None:
            return False
        DataCore.rules.remove(rule)
        return True

    # documents
    @staticmethod
    def _governing(pointer):
        covering = [r for r in DataCore.rules if pointer.startswith(r["pointer_prefix"])]
        return max(covering, key=lambda r: len(r["pointer_prefix"])) if covering else None

    @staticmethod
    def _doc(pointer):
        return next((d for d in DataCore.docs if d["pointer"] == pointer), None)

    def _effective(self, doc):
        if doc["own"] is not None:
            return list(doc["own"])
        rule = self._governing(doc["pointer"])
        return list(rule["access_groups"]) if rule else []

    def _entry(self, doc):
        rule = None if doc["own"] is not None else self._governing(doc["pointer"])
        return {"pointer": doc["pointer"], "pointers": [doc["pointer"]], "title": doc["pointer"].rsplit("/", 1)[-1],
                "document_uuid": doc["uuid"], "access_groups": self._effective(doc),
                "acl_folder_id": rule["rule_id"] if rule else ACL_FOLDER_NONE, "status": "ready"}

    def document_access(self, pointer):
        self._mode("document_access")
        doc = self._doc(pointer)
        return None if doc is None else {"pointer": pointer, "access_groups": self._effective(doc), "acl_epoch": 1}

    def set_document_access(self, pointer, access_groups, acting_as=None):
        if self._mode("set_document_access") == "none":
            return None
        doc = self._doc(pointer)
        if doc is None:
            return None
        doc["own"] = sorted(set(access_groups))
        return {"pointer": pointer, "access_groups": list(doc["own"]), "acl_epoch": 2}

    def documents(self, after=None, limit=100, prefix=None, group=None, unrestricted=False, conflicts=False,
                  status=None):
        self._mode("documents")
        rows = [self._entry(d) for d in DataCore.docs if prefix is None or d["pointer"].startswith(prefix)]
        if group:
            rows = [r for r in rows if group in r["access_groups"]]
        return {"documents": rows[: int(limit)], "next_after": None, "total_count": len(rows)}


def _writes(call=None):
    return [c for c in DataCore.calls if call is None or c[0] == call]


def _arg(call, index, keyword):
    _, args, kwargs = call
    return args[index] if len(args) > index else kwargs.get(keyword)


# --- the world: the real app, an administrator signed in ---------------------------------------------------------------


class Browser:
    def __init__(self, app, email):
        self.client = app.test_client()
        page = self.client.get("/login")
        self.client.post("/login", data={"login_name": email, "password": conftest.PASSWORD,
                                         "csrf_token": conftest._csrf_from(page.data.decode("utf-8"))})
        with self.client.session_transaction() as sess:
            self.token = sess.get("csrf_token")

    def get(self, path):
        return self.client.get(path)

    def page(self, path):
        response = self.client.get(path)
        assert response.status_code == 200, f"{path} answered {response.status_code}"
        return response.data.decode("utf-8")

    def post(self, path, data=None, *, token=True, follow=True):
        fields = dict(data or {})
        if token is True:
            fields["csrf_token"] = self.token
        elif token:
            fields["csrf_token"] = token
        return self.client.post(path, data=fields, follow_redirects=follow)


class World:
    def __init__(self, conn, app):
        self.conn, self.app = conn, app
        self.repo = UserRepository(conn)
        self.store = RoleStore(conn)
        self.chef = self.person("chef", "admin")
        self.admin = Browser(app, self.chef.email)

    def person(self, name, key=None, *, display_name=None, groups=()):
        user = self.repo.create(email=f"{name}@kanzlei.ch", display_name=display_name or name.capitalize(),
                                password=conftest.PASSWORD)
        if key:
            self.repo.grant_role(user.id, key)
        for group in groups:
            self.repo.grant_access_group(user.id, group)
        return self.repo.get(user.id)

    def role(self, name, functions=(), *, group=None, members=()):
        role = self.store.create_role(name=name, functions=set(functions), core_group_id=group)
        for person in members:
            self.store.add_member(role.id, person.id)
        return self.store.get_role(role.id)

    def browser(self, person):
        return Browser(self.app, person.email)

    def active(self):
        return {str(r[0]) for r in self.conn.execute("SELECT id FROM users WHERE status = 'active'")}

    def bypass(self, enabled):
        ApprovalService(self.conn, self.repo).set_admin_bypass(enabled, by=self.chef)

    def requests(self):
        rows = self.conn.execute(
            "SELECT id::text, kind, target_ref, status, requested_by::text FROM approval_requests ORDER BY requested_at"
        ).fetchall()
        return [dict(zip(("id", "kind", "target_ref", "status", "requested_by"), row)) for row in rows]

    def sight(self):
        """Each person's claim groups and functions: what they may read and do."""
        people = [r[0] for r in self.conn.execute("SELECT id FROM users ORDER BY id")]
        return {str(p): (claim_groups(self.conn, p), sorted(self.repo.roles_of(p))) for p in people}

    def state(self):
        roles = [(str(r.id), r.name, r.kind, sorted(r.functions), r.core_group_id, sorted(str(m) for m in r.members))
                 for r in self.store.list_roles()]
        grants = self.conn.execute(
            "SELECT user_id::text, group_id, ends_on FROM user_access_groups ORDER BY 1, 2").fetchall()
        core = json.dumps([DataCore.groups, DataCore.rules, DataCore.docs], sort_keys=True, default=str)
        return roles, grants, self.requests(), core

    def mark(self):
        return self.conn.execute("SELECT coalesce(max(id), 0) FROM audit_log").fetchone()[0]

    def audit_since(self, mark):
        rows = self.conn.execute(
            "SELECT action, target_type, target_id, outcome, actor_user_id::text, detail FROM audit_log "
            "WHERE id > %s ORDER BY id", (mark,)).fetchall()
        keys = ("action", "target_type", "target_id", "outcome", "actor", "detail")
        return [dict(zip(keys, row)) for row in rows]


@pytest.fixture
def world(platform_db, tmp_path, monkeypatch):
    DataCore.calls = []
    DataCore.groups = [dict(ADMIN_GROUP)]
    DataCore.rules = []
    DataCore.docs = []
    DataCore.refuse = {}
    DataCore.posture = None
    app = conftest._identity_app(platform_db, tmp_path, monkeypatch, client_cls=DataCore)
    assert DataCore.calls == [], f"the start wrote to the Core: {DataCore.calls}"
    return World(platform_db, app)


class Scene:
    """A firm's tree, rules and documents, and a role «Bau» whose group already names two folders and a document.

    Groups: Bau, Steuer, Partner with Team below it, Leer (a group without data), Mitglied (made at the Core before
    the Platform linked its role: FU-45). People: anna and bert in Bau; bert also holds Partner directly, cora holds
    Steuer directly, dora holds nothing. Rules: Steuer and Team walled, Offen with no group (open), Baurecht for Bau
    and Steuer, NurBau for Bau alone. Documents: one under Steuer, one under the open folder Bau (no rule), one under
    Steuer/2024 (no rule of its own), one under Baurecht, one with rights of its own for Steuer, one for Bau, one open.
    """

    def __init__(self, world):
        DataCore.groups += [
            {"group_id": "g-bau", "name": "Bau", "parent_id": None},
            {"group_id": "g-steuer", "name": "Steuer", "parent_id": None},
            {"group_id": "g-partner", "name": "Partner", "parent_id": None},
            {"group_id": "g-team", "name": "Team", "parent_id": "g-partner"},
            {"group_id": "g-leer", "name": "Leer", "parent_id": None},
            {"group_id": "g-mitglied-alt", "name": "Mitglied", "parent_id": None},
        ]
        DataCore.rules += [
            {"rule_id": "r-steuer", "pointer_prefix": "DMS/Steuer/", "access_groups": ["g-steuer"]},
            {"rule_id": "r-team", "pointer_prefix": "DMS/Team/", "access_groups": ["g-team"]},
            {"rule_id": "r-offen", "pointer_prefix": "DMS/Offen/", "access_groups": []},
            {"rule_id": "r-bau2", "pointer_prefix": "DMS/Baurecht/", "access_groups": ["g-bau", "g-steuer"]},
            {"rule_id": "r-nurbau", "pointer_prefix": "DMS/NurBau/", "access_groups": ["g-bau"]},
        ]
        DataCore.docs += [
            {"pointer": "DMS/Steuer/bescheid.pdf", "uuid": "d-bescheid", "own": None},
            {"pointer": "DMS/Bau/plan.pdf", "uuid": "d-plan", "own": None},
            {"pointer": "DMS/Steuer/2024/q1.pdf", "uuid": "d-q1", "own": None},
            {"pointer": "DMS/Baurecht/urteil.pdf", "uuid": "d-urteil", "own": None},       # governed by Baurecht
            {"pointer": "DMS/Einzel/gutachten.pdf", "uuid": "d-gut", "own": ["g-steuer"]},
            {"pointer": "DMS/Einzel/bau-gutachten.pdf", "uuid": "d-baugut", "own": ["g-bau"]},
            {"pointer": "DMS/Einzel/offen.pdf", "uuid": "d-offen", "own": None},
        ]
        self.anna = world.person("anna", "member")
        self.bert = world.person("bert", "member", groups=["g-partner"])
        self.cora = world.person("cora", "member", groups=["g-steuer"])
        self.dora = world.person("dora", "member")
        self.bau = world.role("Bau", {"approver"}, group="g-bau", members=[self.anna, self.bert])
        self.leer = world.role("Leer", (), group="g-leer", members=[self.dora])


@pytest.fixture
def scene(world):
    return Scene(world)


# --- reading the page ----------------------------------------------------------------------------------------------------


def _text(fragment):
    fragment = re.sub(r"<(script|style|svg)\b.*?</\1>", " ", fragment, flags=re.S)
    return " ".join(htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def _attr(tag, name):
    found = re.search(rf'(?:^|\s){re.escape(name)}="([^"]*)"', tag)
    return None if found is None else htmllib.unescape(found.group(1))


def _main(html):
    found = re.search(r"<main\b.*?</main>", html, flags=re.S)
    assert found, "the page has no <main>"
    return found.group(0)


def _part(html, name):
    found = re.search(rf'<section\b[^>]*\bdata-role-part="{name}"[^>]*>(.*?)</section>', _main(html), flags=re.S)
    assert found, f"the page has no part {name}"
    return found.group(1)


def _card(html):
    main = _main(html)
    at = main.find("data-role-id=")
    assert at >= 0, "no element carries the current role (data-role-id)"
    return main[main.rfind("<", 0, at):]


def _forms(html, action):
    return [body for attrs, body in re.findall(r"<form\b([^>]*)>(.*?)</form>", html, flags=re.S)
            if _attr(attrs, "action") == action and (_attr(attrs, "method") or "get").lower() == "post"]


def _form(html, action):
    found = _forms(html, action)
    return found[0] if found else None


def _as_drawn(body):
    """What a browser sends from a form as drawn: hidden and text fields, checked boxes, the selected option."""
    sent: dict = {}
    for tag in re.findall(r"<input\b[^>]*>", body):
        name, kind = _attr(tag, "name"), (_attr(tag, "type") or "text").lower()
        if not name or re.search(r"\sdisabled\b", tag) or kind in ("submit", "button"):
            continue
        if kind in ("checkbox", "radio") and not re.search(r"\schecked\b", tag):
            continue
        sent.setdefault(name, []).append(_attr(tag, "value") or "")
    return sent


def _fields(body):
    """Every field name a form offers (inputs, selects, text areas)."""
    names = {_attr(tag, "name") for tag in re.findall(r"<(?:input|select|textarea)\b[^>]*>", body)}
    return names - {None}


def _options(body, name):
    found = re.search(rf'<select\b[^>]*\sname="{name}"[^>]*>(.*?)</select>', body, flags=re.S)
    return [] if found is None else [_attr(tag, "value") for tag in re.findall(r"<option\b[^>]*>", found.group(1))]


def _alert(html):
    return " ".join(_text(body) for _, body in re.findall(r'<(\w+)\b[^>]*role="alert"[^>]*>(.*?)</\1>', html,
                                                         flags=re.S))


def _dry_run(html, kind="data"):
    """(gains, losses, ids gaining, ids losing, warnings) of the dry run on the page."""
    main = _main(html)
    tags = [tag for tag in re.findall(r"<[a-z]+\b[^>]*>", main) if _attr(tag, "data-dry-run") == kind]
    assert len(tags) == 1, f"expected one dry run «{kind}» on the page, found {len(tags)}"
    tag = tags[0]
    return (int(_attr(tag, "data-gains")), int(_attr(tag, "data-losses")),
            set(re.findall(r'data-gains-person="([^"]+)"', main)),
            set(re.findall(r'data-loses-person="([^"]+)"', main)),
            set(re.findall(r'data-warning="([^"]+)"', main)))


def _rows(html):
    part = _part(html, "data")
    return (set(re.findall(r'data-rule-id="([^"]+)"', part)), set(re.findall(r'data-document-uuid="([^"]+)"', part)))


def _ids(*people):
    return {str(p.id) for p in people}


def _rule(rule_id):
    return next((r for r in DataCore.rules if r["rule_id"] == rule_id), None)


def _doc(pointer):
    return next(d for d in DataCore.docs if d["pointer"] == pointer)


def _no_core_write():
    return DataCore.calls == []


# === 1. gates and tokens ==================================================================================================

ZERO = "00000000-0000-0000-0000-000000000000"
ROUTES = [PREVIEW(ZERO), ADD(ZERO), REMOVE(ZERO), ADOPT_PREVIEW(ZERO), ADOPT(ZERO), GRANTS_PREVIEW, GRANTS_MOVE]


def _the_forms(world, scene):
    """Each RB-5 form with a form it accepts, and what it changes or shows when it gets through."""
    return {
        "preview": (PREVIEW(scene.bau.id), {"op": "add", "kind": "folder", "pointer": "DMS/Steuer/"}),
        "add": (ADD(scene.bau.id), {"kind": "folder", "pointer": "DMS/Steuer/"}),
        "remove": (REMOVE(scene.bau.id), {"kind": "folder", "pointer": "DMS/Baurecht/"}),
        "adopt-preview": (ADOPT_PREVIEW(scene.leer.id), {"group_id": "g-steuer"}),
        "adopt": (ADOPT(scene.leer.id), {"group_id": "g-steuer"}),
        "grants-preview": (GRANTS_PREVIEW, {}),
        "grants-move": (GRANTS_MOVE, {}),
    }


FORM_NAMES = ["preview", "add", "remove", "adopt-preview", "adopt", "grants-preview", "grants-move"]
CHANGING = {"add", "remove", "adopt", "grants-move"}


def test_every_route_carries_the_console_admin_gate(world):
    adapter = world.app.url_map.bind("localhost")
    for path in ROUTES:
        endpoint, _ = adapter.match(path, method="POST")
        assert endpoint.startswith("admin."), (path, endpoint)
        assert getattr(world.app.view_functions[endpoint], "allowed_roles", None) == frozenset({"admin"}), path


@pytest.mark.parametrize("path", ROUTES)
def test_a_signed_out_visitor_is_sent_to_sign_in(path, world):
    response = world.app.test_client().post(path)
    assert response.status_code == 302 and "/login" in response.headers["Location"]


@pytest.mark.parametrize("key", ["member", "approver", "ingestion_manager", None], ids=lambda k: k or "no-function")
def test_everybody_but_an_administrator_is_refused_every_form_and_nothing_changes(key, world, scene):
    forms = _the_forms(world, scene)
    browser = world.browser(world.person((key or "gast").replace("_", "-"), key))
    before, mark = world.state(), world.mark()
    for name, (path, data) in forms.items():
        assert browser.post(path, data, follow=False).status_code == 403, name
    assert world.state() == before and _no_core_write()
    assert [r for r in world.audit_since(mark) if r["action"].startswith(("role.", "approval."))] == []


@pytest.mark.parametrize("name", FORM_NAMES)
def test_a_form_without_the_token_or_with_a_wrong_one_is_refused_and_changes_nothing(name, world, scene):
    world.person("hans", "member", groups=["g-team"])                    # a direct grant, so the move has work
    path, data = _the_forms(world, scene)[name]
    before, mark = world.state(), world.mark()
    assert world.admin.post(path, data, token=False).status_code == 400
    assert world.admin.post(path, data, token="falsch").status_code == 400
    assert world.state() == before and _no_core_write()
    assert [r for r in world.audit_since(mark) if r["action"].startswith(("role.", "approval."))] == []
    assert world.admin.post(path, data).status_code == 200                # the same form with the token gets through
    if name in CHANGING:
        assert world.state() != before


@pytest.mark.parametrize("name", ["preview", "add", "remove", "adopt-preview", "adopt"])
def test_an_unknown_or_malformed_role_is_404_and_nothing_is_sent(name, world, scene):
    path, data = _the_forms(world, scene)[name]
    for other in (str(uuid.uuid4()), "kein-uuid"):
        wrong = path.replace(str(scene.bau.id), other).replace(str(scene.leer.id), other)
        assert world.admin.post(wrong, data).status_code == 404, wrong
    assert _no_core_write()


# === 2. the part «Darf lesen» =============================================================================================


def test_the_data_part_shows_what_the_core_holds_for_the_roles_group(world, scene):
    html = world.admin.page(ROLE(scene.bau.id))
    rules, docs = _rows(html)
    assert rules == {"r-bau2", "r-nurbau"}                         # not Steuer, Team, Offen: they do not name Bau
    assert docs == {"d-baugut"}                                 # its own rights; folder-governed ones are rules
    shown = _text(_part(html, "data"))
    assert "DMS/Baurecht/" in shown and "DMS/NurBau/" in shown and "bau-gutachten.pdf" in shown
    assert "DMS/Steuer/" not in shown


def test_the_page_keeps_no_copy_of_the_data(world, scene):
    """The part reads the Core each time: a rule changed at the Core directly is shown as the Core holds it."""
    assert "r-nurbau" in _rows(world.admin.page(ROLE(scene.bau.id)))[0]
    _rule("r-nurbau")["access_groups"] = ["g-steuer"]
    DataCore.rules.append({"rule_id": "r-neu", "pointer_prefix": "DMS/Neu/", "access_groups": ["g-bau"]})
    rules, _ = _rows(world.admin.page(ROLE(scene.bau.id)))
    assert "r-nurbau" not in rules and "r-neu" in rules


def test_names_from_the_core_are_escaped(world, scene):
    DataCore.rules.append({"rule_id": "r-xss", "pointer_prefix": "DMS/<b>fett</b>/", "access_groups": ["g-bau"]})
    html = world.admin.page(ROLE(scene.bau.id))
    assert "<b>fett</b>" not in html and "&lt;b&gt;fett&lt;/b&gt;" in html


def test_daten_hinzufuegen_offers_a_dry_run_with_its_fields(world, scene):
    part = _part(world.admin.page(ROLE(scene.bau.id)), "data")
    assert "Daten hinzufügen" in _text(part)
    forms = [f for f in _forms(part, PREVIEW(scene.bau.id)) if _as_drawn(f).get("op") == ["add"]]
    assert len(forms) == 1
    assert {"op", "kind", "pointer", "csrf_token"} <= _fields(forms[0])
    assert set(_options(forms[0], "kind")) == {"source", "folder", "document"}


def test_every_row_offers_its_removal_through_a_dry_run(world, scene):
    part = _part(world.admin.page(ROLE(scene.bau.id)), "data")
    removals = [_as_drawn(f) for f in _forms(part, PREVIEW(scene.bau.id)) if _as_drawn(f).get("op") == ["remove"]]
    drawn = {(f["kind"][0], f["pointer"][0]) for f in removals}
    assert drawn == {("folder", "DMS/Baurecht/"), ("folder", "DMS/NurBau/"),
                     ("document", "DMS/Einzel/bau-gutachten.pdf")}
    assert _forms(part, REMOVE(scene.bau.id)) == []                     # no removal without its dry run


@pytest.mark.parametrize("posture", [None, "disabled", "enforcing", "raise"])
def test_the_data_part_says_wirkt_mit_ein_while_the_tenant_is_not_brokered(posture, world, scene):
    DataCore.posture = posture
    assert DATA_NOTE in _text(_part(world.admin.page(ROLE(scene.bau.id)), "data"))
    assert DATA_NOTE in _text(_part(world.admin.page(ROLE(scene.leer.id)), "data"))


def test_once_the_tenant_is_brokered_the_words_go(world, scene):
    DataCore.posture = "brokered"
    part = _part(world.admin.page(ROLE(scene.bau.id)), "data")
    assert DATA_NOTE not in _text(part)
    assert _rows(world.admin.page(ROLE(scene.bau.id)))[0] == {"r-bau2", "r-nurbau"}


def test_opening_the_pages_writes_nothing(world, scene):
    before, mark = world.state(), world.mark()
    for role in world.store.list_roles():
        world.admin.page(ROLE(role.id))
    world.admin.page("/admin/roles")
    assert _no_core_write() and world.state() == before
    assert [r for r in world.audit_since(mark) if r["action"].startswith(("role.", "approval."))] == []


def test_administration_has_no_data(world, scene):
    """Even linked to the tenant's administrator group (as «Mit Knovas verbinden» links it)."""
    assert world.admin.post("/admin/roles/connect", {}).status_code == 200
    DataCore.calls = []
    administration = world.store.administration()
    assert administration.core_group_id == "g-admins"
    assert _forms(_part(world.admin.page(ROLE(administration.id)), "data"), PREVIEW(administration.id)) == []
    response = world.admin.post(PREVIEW(administration.id), {"op": "add", "kind": "folder", "pointer": "DMS/Steuer/"})
    assert response.status_code == 400
    response = world.admin.post(ADD(administration.id), {"kind": "folder", "pointer": "DMS/Steuer/", "confirm": "1"})
    assert response.status_code == 400 and _no_core_write()


# === 3. the dry run ======================================================================================================


def test_the_dry_run_of_a_walled_folder_names_who_gains_and_nobody_loses(world, scene):
    before, mark = world.state(), world.mark()
    response = world.admin.post(PREVIEW(scene.bau.id), {"op": "add", "kind": "folder", "pointer": "DMS/Steuer/"})
    assert response.status_code == 200
    html = response.data.decode("utf-8")
    gains, losses, gaining, losing, warnings = _dry_run(html)
    assert (gains, losses) == (2, 0)
    assert gaining == _ids(scene.anna, scene.bert) and losing == set()     # cora sees it already through Steuer
    assert "walls-open" not in warnings
    assert DATA_NOTE in _text(_part(html, "data"))
    for person in (scene.anna, scene.bert):                                  # the page names them; ids carry them
        named = re.search(rf'<(\w+)\b[^>]*data-gains-person="{person.id}"[^>]*>(.*?)</\1>', html, flags=re.S)
        assert named and person.display_name in _text(named.group(2))
    assert _no_core_write() and world.state() == before
    assert [r for r in world.audit_since(mark) if r["action"].startswith(("role.", "approval."))] == []


def test_a_group_above_the_folders_group_already_sees_it(world, scene):
    """Team hangs below Partner: bert holds Partner, so he sees the Team folder already and does not gain."""
    html = world.admin.post(PREVIEW(scene.bau.id), {"op": "add", "kind": "folder", "pointer": "DMS/Team/"})
    gains, losses, gaining, losing, _ = _dry_run(html.data.decode("utf-8"))
    assert (gains, losses, gaining, losing) == (1, 0, _ids(scene.anna), set())


def test_the_dry_run_of_a_removal_names_who_loses(world, scene):
    html = world.admin.post(PREVIEW(scene.bau.id), {"op": "remove", "kind": "folder", "pointer": "DMS/Baurecht/"})
    gains, losses, gaining, losing, warnings = _dry_run(html.data.decode("utf-8"))
    assert (gains, losses, gaining, losing) == (0, 2, set(), _ids(scene.anna, scene.bert))
    assert "opens" not in warnings                                         # Steuer keeps the folder walled


def test_the_dry_run_of_a_document_reads_its_current_groups(world, scene):
    html = world.admin.post(PREVIEW(scene.bau.id), {"op": "add", "kind": "document",
                                                    "pointer": "DMS/Einzel/gutachten.pdf"})
    gains, losses, gaining, _, warnings = _dry_run(html.data.decode("utf-8"))
    assert (gains, losses, gaining) == (2, 0, _ids(scene.anna, scene.bert))
    assert "walls-open" not in warnings


def test_a_new_folder_rule_keeps_the_groups_of_the_rule_that_governs_the_folder(world, scene):
    """Steuer/2024 has no rule of its own; Steuer's rule governs it. The new rule names Steuer and Bau: nobody loses."""
    html = world.admin.post(PREVIEW(scene.bau.id), {"op": "add", "kind": "folder", "pointer": "DMS/Steuer/2024/"})
    gains, losses, gaining, losing, warnings = _dry_run(html.data.decode("utf-8"))
    assert (gains, losses, gaining, losing) == (2, 0, _ids(scene.anna, scene.bert), set())
    assert "already-read-in" in warnings                                   # q1.pdf is read in already (CR-R3)
    assert "walls-open" not in warnings
    assert world.admin.post(ADD(scene.bau.id), {"kind": "folder", "pointer": "DMS/Steuer/2024/"}).status_code == 200
    created = _writes("create_folder_rule")
    assert len(created) == 1 and _arg(created[0], 0, "pointer_prefix") == "DMS/Steuer/2024/"
    assert set(_arg(created[0], 1, "access_groups")) == {"g-steuer", "g-bau"}


def test_a_folder_with_its_own_rule_needs_no_backfill(world, scene):
    html = world.admin.post(PREVIEW(scene.bau.id), {"op": "add", "kind": "folder", "pointer": "DMS/Steuer/"})
    assert "already-read-in" not in _dry_run(html.data.decode("utf-8"))[4]


def test_a_source_is_a_folder_rule_too(world, scene):
    assert world.admin.post(ADD(scene.bau.id), {"kind": "source", "pointer": "DMS/Steuer/"}).status_code == 200
    assert [c[0] for c in DataCore.calls] == ["update_folder_rule"]


@pytest.mark.parametrize("data", [
    {"op": "add", "kind": "folder", "pointer": "   "},
    {"op": "add", "kind": "ordner", "pointer": "DMS/Steuer/"},
    {"op": "wegwerfen", "kind": "folder", "pointer": "DMS/Steuer/"},
    {"op": "add", "kind": "folder", "pointer": "DMS/\x00/"},
    {"op": "add", "kind": "document", "pointer": "DMS/gibt-es-nicht.pdf"},
    {"op": "remove", "kind": "folder", "pointer": "DMS/Steuer/"},                 # not the role's
    {"op": "remove", "kind": "document", "pointer": "DMS/Einzel/gutachten.pdf"},  # not the role's
], ids=["empty", "kind", "op", "control", "unknown-document", "foreign-folder", "foreign-document"])
def test_what_the_dry_run_refuses(data, world, scene):
    response = world.admin.post(PREVIEW(scene.bau.id), data)
    assert response.status_code == 400 and _alert(response.data.decode("utf-8"))
    assert _no_core_write()


def test_a_role_without_a_core_group_gets_no_data_until_it_has_one(world, scene):
    loose = world.role("Ohne Gruppe", {"approver"})
    response = world.admin.post(PREVIEW(loose.id), {"op": "add", "kind": "folder", "pointer": "DMS/Steuer/"})
    assert response.status_code == 400
    assert world.admin.post(ADD(loose.id), {"kind": "folder", "pointer": "DMS/Steuer/"}).status_code == 400
    assert _no_core_write()


# === 4. the open-folder warning ==========================================================================================


def test_adding_an_open_folder_warns_that_it_walls_it_for_everyone_else(world, scene):
    html = world.admin.post(PREVIEW(scene.bau.id), {"op": "add", "kind": "folder", "pointer": "DMS/Bau/"})
    gains, losses, gaining, losing, warnings = _dry_run(html.data.decode("utf-8"))
    everyone_else = world.active() - _ids(scene.anna, scene.bert)
    assert "walls-open" in warnings
    assert (gains, gaining) == (0, set())
    assert (losses, losing) == (len(everyone_else), everyone_else)


def test_an_open_folder_is_walled_only_with_the_confirmation(world, scene):
    html = world.admin.post(PREVIEW(scene.bau.id), {"op": "add", "kind": "folder", "pointer": "DMS/Bau/"})
    drawn = _form(_main(html.data.decode("utf-8")), ADD(scene.bau.id))
    assert drawn is not None, "the dry run draws the form that carries the change out"
    sent = _as_drawn(drawn)
    assert sent.get("kind") == ["folder"] and sent.get("pointer") == ["DMS/Bau/"] and "confirm" not in sent
    sent.pop("csrf_token", None)
    response = world.admin.post(ADD(scene.bau.id), sent)                    # as drawn: the box not ticked
    assert response.status_code == 400 and _no_core_write()
    assert world.admin.post(ADD(scene.bau.id), dict(sent, confirm="1")).status_code == 200
    created = _writes("create_folder_rule")
    assert len(created) == 1 and _arg(created[0], 0, "pointer_prefix") == "DMS/Bau/"
    assert list(_arg(created[0], 1, "access_groups")) == ["g-bau"]


def test_a_rule_without_a_group_is_open_too(world, scene):
    html = world.admin.post(PREVIEW(scene.bau.id), {"op": "add", "kind": "folder", "pointer": "DMS/Offen/"})
    assert "walls-open" in _dry_run(html.data.decode("utf-8"))[4]
    assert world.admin.post(ADD(scene.bau.id), {"kind": "folder", "pointer": "DMS/Offen/"}).status_code == 400
    assert world.admin.post(ADD(scene.bau.id), {"kind": "folder", "pointer": "DMS/Offen/",
                                                "confirm": "1"}).status_code == 200
    assert [(c[0], _arg(c, 0, "rule_id"), list(_arg(c, 1, "access_groups"))) for c in DataCore.calls] == [
        ("update_folder_rule", "r-offen", ["g-bau"])]


def test_an_open_document_warns_too(world, scene):
    html = world.admin.post(PREVIEW(scene.bau.id), {"op": "add", "kind": "document", "pointer": "DMS/Einzel/offen.pdf"})
    assert "walls-open" in _dry_run(html.data.decode("utf-8"))[4]
    assert world.admin.post(ADD(scene.bau.id), {"kind": "document", "pointer": "DMS/Einzel/offen.pdf"}).status_code == 400
    assert _no_core_write()
    assert world.admin.post(ADD(scene.bau.id), {"kind": "document", "pointer": "DMS/Einzel/offen.pdf",
                                                "confirm": "1"}).status_code == 200
    assert _doc("DMS/Einzel/offen.pdf")["own"] == ["g-bau"]


def test_a_removal_that_would_open_the_folder_warns_and_deletes_the_rule(world, scene):
    html = world.admin.post(PREVIEW(scene.bau.id), {"op": "remove", "kind": "folder", "pointer": "DMS/NurBau/"})
    gains, losses, gaining, _, warnings = _dry_run(html.data.decode("utf-8"))
    assert "opens" in warnings
    assert (gains, gaining, losses) == (len(world.active()) - 2, world.active() - _ids(scene.anna, scene.bert), 0)
    assert world.admin.post(REMOVE(scene.bau.id), {"kind": "folder", "pointer": "DMS/NurBau/"}).status_code == 400
    assert _no_core_write()
    assert world.admin.post(REMOVE(scene.bau.id), {"kind": "folder", "pointer": "DMS/NurBau/",
                                                   "confirm": "1"}).status_code == 200
    assert [(c[0], _arg(c, 0, "rule_id")) for c in DataCore.calls] == [("delete_folder_rule", "r-nurbau")]


def test_a_folder_rule_is_never_emptied(world, scene):
    """Whatever the path, the Platform never sends an empty set to update_folder_rule: an empty rule opens a folder."""
    world.admin.post(REMOVE(scene.bau.id), {"kind": "folder", "pointer": "DMS/NurBau/", "confirm": "1"})
    world.admin.post(REMOVE(scene.bau.id), {"kind": "folder", "pointer": "DMS/Baurecht/", "confirm": "1"})
    for call in _writes("update_folder_rule"):
        assert list(_arg(call, 1, "access_groups")), call


def test_a_walled_folder_needs_no_confirmation(world, scene):
    assert world.admin.post(ADD(scene.bau.id), {"kind": "folder", "pointer": "DMS/Steuer/"}).status_code == 200
    assert len(_writes("update_folder_rule")) == 1


# === 5. four eyes ========================================================================================================


def test_with_the_bypass_on_an_administrator_acts_alone_and_it_is_recorded(world, scene):
    mark = world.mark()
    assert world.admin.post(ADD(scene.bau.id), {"kind": "folder", "pointer": "DMS/Steuer/"}).status_code == 200
    assert len(_writes("update_folder_rule")) == 1
    rows = world.audit_since(mark)
    bypassed = [r for r in rows if r["action"] == BYPASSED]
    assert len(bypassed) == 1 and bypassed[0]["target_type"] == "acl_change"
    assert bypassed[0]["target_id"] == f"role:{scene.bau.id}"
    assert world.requests() == []


def test_with_the_bypass_off_the_change_waits_for_a_second_person(world, scene):
    world.bypass(False)
    before = _rule("r-steuer")["access_groups"]
    response = world.admin.post(ADD(scene.bau.id), {"kind": "folder", "pointer": "DMS/Steuer/"})
    assert response.status_code == 200
    assert _no_core_write() and _rule("r-steuer")["access_groups"] == before
    waiting, = world.requests()
    assert (waiting["kind"], waiting["status"], waiting["target_ref"]) == ("acl_change", "pending",
                                                                           f"role:{scene.bau.id}")
    assert waiting["requested_by"] == str(world.chef.id)


def test_the_requester_cannot_confirm_her_own_change(world, scene):
    world.bypass(False)
    world.admin.post(ADD(scene.bau.id), {"kind": "folder", "pointer": "DMS/Steuer/"})
    waiting, = world.requests()
    assert world.admin.post(APPROVE(waiting["id"])).status_code == 400
    assert _no_core_write() and world.requests()[0]["status"] == "pending"


def test_a_second_administrator_confirms_and_the_change_reaches_the_core_once(world, scene):
    world.bypass(False)
    vera = world.person("vera", "admin")
    world.admin.post(ADD(scene.bau.id), {"kind": "folder", "pointer": "DMS/Steuer/"})
    waiting, = world.requests()
    mark = world.mark()
    assert world.browser(vera).post(APPROVE(waiting["id"])).status_code == 200
    calls = _writes("update_folder_rule")
    assert len(calls) == 1 and set(_arg(calls[0], 1, "access_groups")) == {"g-steuer", "g-bau"}
    assert world.requests()[0]["status"] == "executed"
    added = [r for r in world.audit_since(mark) if r["action"] == DATA_ADDED]
    assert len(added) == 1 and added[0]["actor"] == str(vera.id) and added[0]["target_id"] == str(scene.bau.id)


def test_an_approver_who_is_not_an_administrator_cannot_carry_it_out(world, scene):
    world.bypass(False)
    paul, vera = world.person("paul", "approver"), world.person("vera", "admin")
    world.admin.post(ADD(scene.bau.id), {"kind": "folder", "pointer": "DMS/Steuer/"})
    waiting, = world.requests()
    world.browser(paul).post(APPROVE(waiting["id"]))
    assert _no_core_write()
    assert world.requests()[0]["status"] == "approved"                     # confirmed by a second person, not done
    assert world.browser(vera).post(EXECUTE(waiting["id"])).status_code == 200
    assert len(_writes("update_folder_rule")) == 1 and world.requests()[0]["status"] == "executed"


def test_the_change_is_planned_again_when_it_is_carried_out(world, scene):
    """Asked for while Steuer was walled; by the time it is confirmed the folder is open. Walling it now needs the
    confirmation the request does not carry: nothing is sent."""
    world.bypass(False)
    vera = world.person("vera", "admin")
    world.admin.post(ADD(scene.bau.id), {"kind": "folder", "pointer": "DMS/Steuer/"})
    waiting, = world.requests()
    _rule("r-steuer")["access_groups"] = []
    world.browser(vera).post(APPROVE(waiting["id"]))
    assert _no_core_write()
    assert world.requests()[0]["status"] != "executed"


def test_a_removal_waits_for_the_second_person_too(world, scene):
    world.bypass(False)
    assert world.admin.post(REMOVE(scene.bau.id), {"kind": "folder", "pointer": "DMS/Baurecht/"}).status_code == 200
    assert _no_core_write() and [r["kind"] for r in world.requests()] == ["acl_change"]


def test_adopting_a_group_and_moving_direct_grants_need_no_second_person(world, scene):
    world.bypass(False)
    world.person("hans", "member", groups=["g-team"])
    assert world.admin.post(ADOPT(scene.leer.id), {"group_id": "g-steuer"}).status_code == 200
    assert world.store.get_role(scene.leer.id).core_group_id == "g-steuer"
    assert world.admin.post(GRANTS_MOVE, {}).status_code == 200
    assert any(r.core_group_id == "g-team" for r in world.store.list_roles())
    assert world.requests() == []


# === 6. the Core calls, before and after «Ein» ===========================================================================


def test_an_added_folder_is_written_to_the_core_and_read_back_with_wirkt_mit_ein(world, scene):
    response = world.admin.post(ADD(scene.bau.id), {"kind": "folder", "pointer": "DMS/Steuer/"})
    assert response.status_code == 200
    call, = DataCore.calls
    assert (call[0], _arg(call, 0, "rule_id")) == ("update_folder_rule", "r-steuer")
    assert set(_arg(call, 1, "access_groups")) == {"g-steuer", "g-bau"}
    html = world.admin.page(ROLE(scene.bau.id))
    assert "r-steuer" in _rows(html)[0]
    assert DATA_NOTE in _text(_part(html, "data"))


def test_a_document_keeps_its_groups_and_gains_the_roles_group(world, scene):
    assert world.admin.post(ADD(scene.bau.id), {"kind": "document",
                                                "pointer": "DMS/Einzel/gutachten.pdf"}).status_code == 200
    call, = DataCore.calls
    assert (call[0], _arg(call, 0, "pointer")) == ("set_document_access", "DMS/Einzel/gutachten.pdf")
    assert set(_arg(call, 1, "access_groups")) == {"g-steuer", "g-bau"}
    assert "d-gut" in _rows(world.admin.page(ROLE(scene.bau.id)))[1]


def test_a_removal_keeps_the_other_groups(world, scene):
    assert world.admin.post(REMOVE(scene.bau.id), {"kind": "folder", "pointer": "DMS/Baurecht/"}).status_code == 200
    call, = DataCore.calls
    assert (call[0], _arg(call, 0, "rule_id"), list(_arg(call, 1, "access_groups"))) == (
        "update_folder_rule", "r-bau2", ["g-steuer"])
    assert world.admin.post(REMOVE(scene.bau.id), {"kind": "document",
                                                   "pointer": "DMS/Einzel/bau-gutachten.pdf",
                                                   "confirm": "1"}).status_code == 200
    assert _doc("DMS/Einzel/bau-gutachten.pdf")["own"] == []
    assert _rows(world.admin.page(ROLE(scene.bau.id))) == ({"r-nurbau"}, set())


def test_no_call_names_the_callers_groups(world, scene):
    """The Core takes the caller from the signed claim (CR-R2), never from acting_as: under «enforcing» it then
    refuses the Platform's writes instead of believing a body-asserted list."""
    world.admin.post(ADD(scene.bau.id), {"kind": "folder", "pointer": "DMS/Steuer/"})
    world.admin.post(ADD(scene.bau.id), {"kind": "folder", "pointer": "DMS/Bau/", "confirm": "1"})
    world.admin.post(ADD(scene.bau.id), {"kind": "document", "pointer": "DMS/Einzel/gutachten.pdf"})
    world.admin.post(REMOVE(scene.bau.id), {"kind": "folder", "pointer": "DMS/Baurecht/"})
    world.admin.post(REMOVE(scene.bau.id), {"kind": "folder", "pointer": "DMS/NurBau/", "confirm": "1"})
    assert len(DataCore.calls) == 5
    for name, args, kwargs in DataCore.calls:
        assert not kwargs.get("acting_as"), name
        assert len(args) <= 2 or not args[2], name                         # acting_as is the third argument


@pytest.mark.parametrize("call, mode", [("update_folder_rule", "none"), ("update_folder_rule", "raise"),
                                        ("set_document_access", "raise")])
def test_a_core_refusal_is_shown_and_nothing_is_claimed(call, mode, world, scene):
    DataCore.refuse = {call: mode}
    mark = world.mark()
    data = ({"kind": "folder", "pointer": "DMS/Steuer/"} if call == "update_folder_rule"
            else {"kind": "document", "pointer": "DMS/Einzel/gutachten.pdf"})
    response = world.admin.post(ADD(scene.bau.id), data)
    assert response.status_code == 502
    assert "Knovas" in _alert(response.data.decode("utf-8"))
    assert [r for r in world.audit_since(mark) if r["action"] == DATA_ADDED and r["outcome"] == "ok"] == []
    assert [r for r in world.audit_since(mark) if r["action"] == BYPASSED] == []


def test_when_the_core_cannot_be_read_nothing_is_planned(world, scene):
    DataCore.refuse = {"folder_rules": "raise"}
    response = world.admin.post(PREVIEW(scene.bau.id), {"op": "add", "kind": "folder", "pointer": "DMS/Steuer/"})
    assert response.status_code == 502 and "Knovas" in _alert(response.data.decode("utf-8"))
    assert world.admin.post(ADD(scene.bau.id), {"kind": "folder", "pointer": "DMS/Steuer/"}).status_code == 502
    assert _no_core_write()


# === 7. adopting a group (FU-45) ==========================================================================================


def test_mitglied_takes_over_the_mitglied_group_the_core_already_has(world, scene):
    """«Mit Knovas verbinden» cannot create a second «Mitglied» group at the Core (FU-45); adopting takes the one
    that exists. Administration is linked afterwards as before."""
    mitglied = world.store.mitglied()
    DataCore.refuse = {"create_access_group": "raise"}
    assert world.admin.post("/admin/roles/connect", {}).status_code == 502
    DataCore.refuse, DataCore.calls = {}, []
    form = _form(_card(world.admin.page(ROLE(mitglied.id))), ADOPT_PREVIEW(mitglied.id))
    assert form is not None
    offered = set(_options(form, "group_id"))
    assert "g-mitglied-alt" in offered and "g-steuer" in offered
    assert "g-admins" not in offered and "g-bau" not in offered and "g-leer" not in offered   # others' groups
    mark = world.mark()
    assert world.admin.post(ADOPT(mitglied.id), {"group_id": "g-mitglied-alt"}).status_code == 200
    assert world.store.mitglied().core_group_id == "g-mitglied-alt"
    assert _writes("create_access_group") == []
    row, = [r for r in world.audit_since(mark) if r["action"] == ADOPTED]
    assert (row["target_type"], row["target_id"]) == ("role", str(mitglied.id))
    assert row["detail"]["core_group_id"] == "g-mitglied-alt" and row["detail"]["previous_core_group_id"] is None
    assert world.admin.post("/admin/roles/connect", {}).status_code == 200
    assert world.store.administration().core_group_id == "g-admins"
    assert world.store.mitglied().core_group_id == "g-mitglied-alt"


def test_the_adopt_dry_run_names_who_gains_and_writes_nothing(world, scene):
    before, mark = world.state(), world.mark()
    response = world.admin.post(ADOPT_PREVIEW(scene.leer.id), {"group_id": "g-steuer"})
    assert response.status_code == 200
    gains, losses, gaining, losing, _ = _dry_run(response.data.decode("utf-8"))
    assert (gains, losses, gaining, losing) == (1, 0, _ids(scene.dora), set())
    assert _form(_main(response.data.decode("utf-8")), ADOPT(scene.leer.id)) is not None
    assert world.state() == before and _no_core_write()
    assert [r for r in world.audit_since(mark) if r["action"].startswith("role.")] == []


def test_a_role_whose_group_carries_no_data_adopts_and_its_old_group_is_deleted(world, scene):
    assert world.admin.post(ADOPT(scene.leer.id), {"group_id": "g-steuer"}).status_code == 200
    assert world.store.get_role(scene.leer.id).core_group_id == "g-steuer"
    assert "g-steuer" in claim_groups(world.conn, scene.dora.id)
    assert [(_arg(c, 0, "identifier")) for c in _writes("delete_access_group")] == ["g-leer"]
    assert [c[0] for c in DataCore.calls] == ["delete_access_group"]


@pytest.mark.parametrize("which, group", [
    ("leer", "g-admins"), ("leer", "g-bau"), ("leer", "g-gibt-es-nicht"), ("leer", "  "),
    ("administration", "g-steuer"), ("bau", "g-steuer"),
], ids=["administrator-group", "another-roles-group", "unknown-group", "no-group", "administration",
        "present-group-has-data"])
def test_what_adopting_refuses(which, group, world, scene):
    role = {"leer": scene.leer, "bau": scene.bau, "administration": world.store.administration()}[which]
    before = world.state()
    for path in (ADOPT_PREVIEW(role.id), ADOPT(role.id)):
        response = world.admin.post(path, {"group_id": group})
        assert response.status_code == 400, path
        assert _alert(response.data.decode("utf-8"))
    assert world.state() == before and _no_core_write()


def test_adopting_a_33rd_group_is_refused(world, scene):
    loaded = world.person("voll", "member", groups=[f"g-x{i:02d}" for i in range(32)])
    loose = world.role("Ohne Gruppe", (), members=[loaded])
    before = world.state()
    assert world.admin.post(ADOPT(loose.id), {"group_id": "g-steuer"}).status_code == 400
    assert world.state() == before and _no_core_write()


# === 8. moving direct grants (FU-41) =====================================================================================


class Grants:
    """Direct grants of every kind: two to move into new roles, one a role already gives, four to leave."""

    def __init__(self, world, scene):
        later = (date.today() + timedelta(days=30)).isoformat()
        DataCore.groups.append({"group_id": "g-weg", "name": "Weg", "parent_id": None})
        self.emil = world.person("emil", "member")
        world.repo.grant_access_group(self.emil.id, "g-team", ends_on=later)       # an access window: stays
        self.frieda = world.person("frieda", "member", groups=["g-admins"])         # the administrator group: stays
        self.gerd = world.person("gerd", "member", groups=["g-unbekannt"])          # unknown at the Core: stays
        self.hans = world.person("hans", "member", groups=["g-bau"])                # Bau's group, not in Bau: stays
        world.repo.grant_access_group(scene.anna.id, "g-bau")                       # Bau gives it already: goes
        # moved: bert's Partner, cora's Steuer


@pytest.fixture
def grants(world, scene):
    return Grants(world, scene)


def test_the_move_dry_run_shows_unchanged_sight_and_writes_nothing(world, scene, grants):
    before, mark = world.state(), world.mark()
    response = world.admin.post(GRANTS_PREVIEW, {})
    assert response.status_code == 200
    html = response.data.decode("utf-8")
    gains, losses, gaining, losing, _ = _dry_run(html, "grants")
    assert (gains, losses, gaining, losing) == (0, 0, set(), set())
    assert _form(_main(html), GRANTS_MOVE) is not None
    assert world.state() == before and _no_core_write()
    assert [r for r in world.audit_since(mark) if r["action"].startswith("role.")] == []


def test_the_move_form_is_offered_while_direct_grants_remain(world, scene, grants):
    assert _form(_main(world.admin.page(ROLE(scene.bau.id))), GRANTS_PREVIEW) is not None


def test_the_move_keeps_every_persons_sight_and_functions_and_writes_nothing_to_the_core(world, scene, grants):
    before = world.sight()
    assert world.admin.post(GRANTS_MOVE, {}).status_code == 200
    assert world.sight() == before
    assert _no_core_write()


def test_each_moved_group_becomes_one_role_without_functions(world, scene, grants):
    names = {r.name for r in world.store.list_roles()}
    world.admin.post(GRANTS_MOVE, {})
    new = [r for r in world.store.list_roles() if r.name not in names]
    assert sorted((r.core_group_id, r.name) for r in new) == [("g-partner", "Partner"), ("g-steuer", "Steuer")]
    for role in new:
        assert role.functions == frozenset() and role.kind == "custom"
    by_group = {r.core_group_id: {str(m) for m in r.members} for r in new}
    assert by_group == {"g-partner": _ids(scene.bert), "g-steuer": _ids(scene.cora)}


def test_what_the_move_takes_and_what_it_leaves(world, scene, grants):
    world.admin.post(GRANTS_MOVE, {})
    direct = {(str(r[0]), r[1]) for r in world.conn.execute("SELECT user_id, group_id FROM user_access_groups")}
    assert direct == {(str(grants.emil.id), "g-team"), (str(grants.frieda.id), "g-admins"),
                      (str(grants.gerd.id), "g-unbekannt"), (str(grants.hans.id), "g-bau")}
    assert str(grants.hans.id) not in {str(m) for m in world.store.get_role(scene.bau.id).members}


def test_a_second_move_changes_nothing(world, scene, grants):
    world.admin.post(GRANTS_MOVE, {})
    before, mark = world.state(), world.mark()
    assert world.admin.post(GRANTS_MOVE, {}).status_code == 200
    assert world.state() == before
    assert [r for r in world.audit_since(mark) if r["action"] == CREATED] == []


def test_when_the_core_cannot_name_its_groups_nothing_moves(world, scene, grants):
    DataCore.refuse = {"access_groups": "raise"}
    before = world.state()
    response = world.admin.post(GRANTS_MOVE, {})
    assert response.status_code == 502 and "Knovas" in _alert(response.data.decode("utf-8"))
    assert world.state() == before


# === 9. audit ============================================================================================================


def _one(rows, action):
    found = [r for r in rows if r["action"] == action]
    assert len(found) == 1, f"{action}: {len(found)} rows in {[r['action'] for r in rows]}"
    return found[0]


def test_every_change_writes_its_row_with_ids_and_counts_only(world, scene):
    written = []

    def step(path, data):
        mark = world.mark()
        assert world.admin.post(path, data).status_code == 200, path
        rows = world.audit_since(mark)
        written.extend(rows)
        return rows

    row = _one(step(ADD(scene.bau.id), {"kind": "folder", "pointer": "DMS/Steuer/"}), DATA_ADDED)
    assert (row["target_type"], row["target_id"], row["actor"], row["outcome"]) == (
        "role", str(scene.bau.id), str(world.chef.id), "ok")
    assert row["detail"]["core_group_id"] == "g-bau" and row["detail"]["kind"] == "folder"
    assert row["detail"]["rule_id"] == "r-steuer"
    assert (row["detail"]["gains"], row["detail"]["losses"]) == (2, 0)

    row = _one(step(ADD(scene.bau.id), {"kind": "document", "pointer": "DMS/Einzel/gutachten.pdf"}), DATA_ADDED)
    assert row["detail"]["kind"] == "document" and row["detail"]["document_uuid"] == "d-gut"

    row = _one(step(REMOVE(scene.bau.id), {"kind": "folder", "pointer": "DMS/Baurecht/"}), DATA_REMOVED)
    assert (row["target_id"], row["detail"]["rule_id"], row["detail"]["losses"]) == (str(scene.bau.id),
                                                                                     "r-bau2", 2)

    row = _one(step(ADOPT(scene.leer.id), {"group_id": "g-team"}), ADOPTED)
    assert (row["target_type"], row["target_id"]) == ("role", str(scene.leer.id))
    assert (row["detail"]["core_group_id"], row["detail"]["previous_core_group_id"]) == ("g-team", "g-leer")

    Grants(world, scene)
    rows = step(GRANTS_MOVE, {})
    row = _one(rows, MOVED)
    assert row["target_type"] == "role"
    new = {str(r.id) for r in world.store.list_roles() if r.core_group_id in ("g-partner", "g-steuer")}
    assert len(new) == 2 and set(row["detail"]["roles"]) == new
    assert set(row["detail"]["people"]) == _ids(scene.anna, scene.bert, scene.cora)
    # gone from the direct grants: bert's Partner, cora's Steuer (into new roles), anna's Bau (Bau gives it);
    # left: emil's access window, frieda's administrator group, gerd's unknown group, hans's Bau outside Bau
    assert (row["detail"]["grants_moved"], row["detail"]["grants_left"]) == (3, 4)
    created = [r for r in rows if r["action"] == CREATED]
    assert sorted(r["detail"]["core_group_id"] for r in created) == ["g-partner", "g-steuer"]
    assert all(r["detail"]["functions"] == [] for r in created)

    for row in written:
        said = (str(row["target_id"]) + json.dumps(row["detail"], ensure_ascii=False)).lower()
        for word in ("dms/", "steuer/", "baurecht", "gutachten", ".pdf", "anna", "bert", "cora", "@"):
            assert word not in said, (word, row)
        for name in ('"bau"', '"steuer"', '"partner"', '"team"', '"leer"'):
            assert name not in said, (name, row)


def test_the_four_eyes_rows_name_the_role_never_the_folder(world, scene):
    world.bypass(False)
    vera = world.person("vera", "admin")
    mark = world.mark()
    world.admin.post(ADD(scene.bau.id), {"kind": "folder", "pointer": "DMS/Steuer/"})
    waiting, = world.requests()
    world.browser(vera).post(APPROVE(waiting["id"]))
    rows = [r for r in world.audit_since(mark) if r["action"].startswith("approval.")]
    assert {r["action"] for r in rows} >= {"approval.requested", "approval.approved", "approval.executed"}
    for row in rows:
        said = (str(row["target_id"]) + json.dumps(row["detail"], ensure_ascii=False)).lower()
        assert "dms/" not in said and "steuer/" not in said, row


def test_a_core_refusal_is_recorded_with_its_outcome(world, scene):
    DataCore.refuse = {"update_folder_rule": "raise"}
    mark = world.mark()
    assert world.admin.post(ADD(scene.bau.id), {"kind": "folder", "pointer": "DMS/Steuer/"}).status_code == 502
    rows = [r for r in world.audit_since(mark) if r["action"] == DATA_ADDED]
    assert len(rows) == 1 and rows[0]["outcome"] == "error" and rows[0]["target_id"] == str(scene.bau.id)


# === 10. logs ============================================================================================================


def test_no_customer_text_reaches_a_log_line(world, caplog):
    DataCore.groups += [{"group_id": "g-1", "name": "Zwiebelgruppe", "parent_id": None},
                        {"group_id": "g-2", "name": "Zwiebel Erben", "parent_id": None},
                        {"group_id": "g-3", "name": "Zwiebelmitglieder", "parent_id": None},
                        {"group_id": "g-4", "name": "Zwiebel Neu", "parent_id": None}]
    DataCore.rules += [{"rule_id": "r-1", "pointer_prefix": "Mandate/Zwiebel/Akten/", "access_groups": ["g-2"]}]
    DataCore.docs += [{"pointer": "Mandate/Zwiebel/Zwiebel_Gutachten.pdf", "uuid": "d-1", "own": None},
                      {"pointer": "Mandate/Zwiebel/Akten/Zwiebel_Brief.pdf", "uuid": "d-2", "own": None}]
    zora = world.person("zora.zwiebel", "member", display_name="Zora Zwiebel", groups=["g-2"])
    zeno = world.person("zeno.zwiebel", "admin", display_name="Zeno Zwiebel")
    zeus = world.person("zeus.zwiebel", "admin", display_name="Zeus Zwiebel")
    paula = world.person("paula.zwiebel", "approver", display_name="Paula Zwiebel")
    role = world.role("Mandat Zwiebel", {"approver"}, group="g-1", members=[zora])
    other = world.role("Zwiebel leer", (), group="g-3", members=[zora])
    as_zeno, as_zeus, as_paula = world.browser(zeno), world.browser(zeus), world.browser(paula)
    caplog.clear()
    # The app holds its own loggers at LOG_LEVEL (INFO in tests): lower them too, or a DEBUG line is never made.
    for name in (None, "web_interface", "identity", "knovas_client", "builder"):
        caplog.set_level(logging.DEBUG, logger=name)

    folder, document, walled = "Mandate/Zwiebel/", "Mandate/Zwiebel/Akten/Zwiebel_Brief.pdf", "Mandate/Zwiebel/Akten/"
    as_zeno.page(ROLE(role.id))
    assert as_zeno.post(PREVIEW(role.id), {"op": "add", "kind": "folder", "pointer": folder}).status_code == 200
    assert as_zeno.post(ADD(role.id), {"kind": "folder", "pointer": folder}).status_code == 400
    assert as_zeno.post(ADD(role.id), {"kind": "folder", "pointer": folder, "confirm": "1"}).status_code == 200
    assert as_zeno.post(PREVIEW(role.id), {"op": "add", "kind": "document", "pointer": document}).status_code == 200
    assert as_zeno.post(ADD(role.id), {"kind": "document", "pointer": document, "confirm": "1"}).status_code == 200
    assert as_zeno.post(PREVIEW(role.id), {"op": "add", "kind": "document",
                                           "pointer": "Mandate/Zwiebel/fehlt.pdf"}).status_code == 400
    DataCore.refuse = {"update_folder_rule": "raise"}
    assert as_zeno.post(ADD(role.id), {"kind": "folder", "pointer": walled}).status_code == 502
    DataCore.refuse = {}
    assert as_zeno.post(PREVIEW(role.id), {"op": "remove", "kind": "folder", "pointer": folder}).status_code == 200
    assert as_zeno.post(REMOVE(role.id), {"kind": "folder", "pointer": folder, "confirm": "1"}).status_code == 200
    as_zeno.page(ROLE(role.id))
    assert as_zeno.post(ADOPT_PREVIEW(other.id), {"group_id": "g-2"}).status_code == 200
    assert as_zeno.post(ADOPT(other.id), {"group_id": "g-1"}).status_code == 400
    assert as_zeno.post(ADOPT(other.id), {"group_id": "g-2"}).status_code == 200
    world.repo.grant_access_group(zora.id, "g-4")                          # becomes a role «Zwiebel Neu»
    assert as_zeno.post(GRANTS_PREVIEW, {}).status_code == 200
    assert as_zeno.post(GRANTS_MOVE, {}).status_code == 200
    assert any(r.core_group_id == "g-4" for r in world.store.list_roles())

    world.bypass(False)
    assert as_zeno.post(ADD(role.id), {"kind": "folder", "pointer": walled}).status_code == 200
    assert as_zeno.post(REMOVE(role.id), {"kind": "document", "pointer": document, "confirm": "1"}).status_code == 200
    first, second = world.requests()[-2:]
    assert as_paula.post(APPROVE(first["id"])).status_code == 200          # an approver: confirmed, not carried out
    assert as_zeus.post(EXECUTE(first["id"])).status_code == 200
    assert as_zeus.post(APPROVE(second["id"])).status_code == 200
    as_zeus.page("/admin/approvals")

    leaked = []
    for record in caplog.records:
        line = record.getMessage()
        if record.exc_info:
            line += logging.Formatter().formatException(record.exc_info)
        if "zwiebel" in line.lower() or "mandate/" in line.lower():
            leaked.append(line)
    assert leaked == [], leaked
