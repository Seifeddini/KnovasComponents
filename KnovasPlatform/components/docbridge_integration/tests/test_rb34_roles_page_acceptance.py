"""RB-3 and RB-4 acceptance tests: the «Rollen» page behind the admin gate (RB-3); creating, renaming and deleting
roles, their functions and their people, the people page offering roles, and the design's safety rules 1, 3, 4 and 5
(RB-4) (FEAT-2026-11-19-platform-mvp, design docs/platform-mvp/design/role-builder.md § 1, § 3, § 6, § 7; mock-up
docs/platform-mvp/reference-screens/07-rollen.html; work order docs/platform-mvp/work-orders/RB-3-4.md).

FROZEN. Written for the coordinator before the build; a builder never edits this file. Its git blob id is in
docs/platform-mvp/frozen-tests.txt.

What is pinned:

1. The page (RB-3). ``GET /admin/roles`` shows the first role (Administration), ``GET /admin/roles/<id>`` that role;
   an unknown, foreign or malformed id is 404. Both sit behind the console's admin gate: signed out 302 to the
   sign-in, every other function and an account without one 403, an administrator 200. The Verwaltung menu has
   «Rollen» right after «Personen», marked current on these pages. The words of the mock-up: the title «Rollen», its
   lede, «Rolle anlegen», one tab per role with its number of people, «Umbenennen», the parts «Darf tun» (the
   functions in the words of design § 2.1, never a key; «Ändern»), «Darf lesen» (read only, «wirkt mit «Ein»»; RB-5
   fills it) and «Mitglieder» («Mitglied hinzufügen»), and the closing sentence. Names are escaped. Opening the pages
   writes nothing to the Core.
2. The forms (RB-4): ``POST /admin/roles/create``, ``/admin/roles/<id>/rename``, ``/functions``,
   ``/members/add``, ``/members/remove``, ``/delete``, ``POST /admin/roles/connect`` and the people page's
   ``POST /admin/people/roles``. Each sits behind the admin gate (403 for everybody else, nothing changed) and checks
   the form's ``csrf_token`` (400, nothing changed, nothing sent to the Core).
3. «Rolle anlegen»: what the store would refuse is refused before the Core is asked (400, no Core call). The role's
   Core group is created first, as a root group named like the role; the role is saved only when the Core answers
   with a group (otherwise 502 and no role).
4. «Umbenennen» renames the Core group first and keeps its id; a Core refusal keeps the old name (502).
   Administration and Mitglied keep their names (decision of the work order).
5. «Darf tun»: ``admin`` only in Administration, and Administration keeps it; no other function outside the ten.
6. «Mitglieder»: people join and leave and gain and lose the role's functions; the last active administrator
   cannot leave Administration (rule 3); the 32-group cap; giving oneself a role needs no second administrator
   (rule 1, founder 2026-11-21).
7. Deleting: Administration and Mitglied cannot be deleted; a role in use is deleted in the design's order (its
   members, then its Core group, then the role). A 404 from the Core means the group is already gone: the role is
   deleted and its audit row says so (``core_group_already_absent``). A 409 or any other refusal leaves the role,
   its members stay removed, and the page says so (502) (coordinator, 2026-11-21).
8. Administration and Mitglied get their Core groups on an administrator's action (``POST /admin/roles/connect``):
   Administration the tenant's administrator group, Mitglied a new root group «Mitglied». Never at start, never on a
   GET (RB-1/2 rule 9).
9. The people page offers roles (Administration, Mitglied and the tenant's own roles), never the ten keys; a new
   installation shows exactly the two (founder, 2026-11-21). Saving makes a person's roles exactly the checked ones,
   in one transaction.
10. Every change writes its audit row (the actions below) in the same transaction as the change; the row's target
    and detail hold ids and function keys only (rule 4).
11. No role name, description, person name or address reaches a log line, DEBUG included.

The interface it builds on is RB-1's (``identity.roles.RoleStore``), pinned by test_rb12_roles_acceptance.py.
Nothing here pins the absence of what RB-5 to RB-9 add (data, agents, the probe view). Nothing reaches a tenant: the
Core is a recording fake.
"""
from __future__ import annotations

import html as htmllib
import json
import logging
import re
import uuid

import pytest
import requests

import conftest
from conftest import FakeGraphApi, platform_db_reachable

pytestmark = pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")

from identity.roles import RoleStore  # noqa: E402
from identity.users import UserRepository  # noqa: E402

#: The ten functions and their words (design § 2.1). The page shows the words, never the keys.
FUNCTION_WORDS = {
    "member": "Fragen und suchen",
    "admin": "Verwaltung",
    "approver": "Freigaben bestätigen",
    "ingestion_manager": "Einlesen",
    "experimenter": "Experimente",
    "experiments_manager": "Experimente verwalten",
    "builder_viewer": "Builder lesen",
    "builder_author": "Builder bearbeiten",
    "builder_reviewer": "Builder prüfen",
    "builder_publisher": "Builder veröffentlichen",
}
#: Every function but ``admin``: the gate refuses each of them, and a role of the tenant's own may hold each.
OTHERS = tuple(key for key in FUNCTION_WORDS if key != "admin")

LEDE = "Was jemand tun darf und was er lesen darf, in einer Rolle."
CLOSING = "Eine Rolle gibt nie mehr, als Sie selbst dürfen. Jede Änderung steht im Protokoll."
DATA_NOTE = "wirkt mit «Ein»"

PAGE = "/admin/roles"
CREATE = "/admin/roles/create"
CONNECT = "/admin/roles/connect"
PEOPLE = "/admin/people"
PEOPLE_ROLES = "/admin/people/roles"
PEOPLE_CREATE = "/admin/people/create"


def ROLE(role_id):                                                   # noqa: N802 - an address, like the constants
    return f"/admin/roles/{role_id}"


def RENAME(role_id):                                                 # noqa: N802
    return f"/admin/roles/{role_id}/rename"


def FUNCTIONS(role_id):                                              # noqa: N802
    return f"/admin/roles/{role_id}/functions"


def ADD(role_id):                                                    # noqa: N802
    return f"/admin/roles/{role_id}/members/add"


def REMOVE(role_id):                                                 # noqa: N802
    return f"/admin/roles/{role_id}/members/remove"


def DELETE(role_id):                                                 # noqa: N802
    return f"/admin/roles/{role_id}/delete"


#: The audit actions (rule 4), target_type "role" unless noted.
CREATED, RENAMED, FUNCTIONS_CHANGED = "role.created", "role.renamed", "role.functions_changed"
MEMBER_ADDED, MEMBER_REMOVED, DELETED = "role.member_added", "role.member_removed", "role.deleted"
LINKED, PERSON_ROLES = "role.core_group_linked", "user.roles_changed"          # PERSON_ROLES: target_type "user"
#: In role.deleted's detail: true when the Core answered 404, the group was already gone (coordinator, 2026-11-21).
ALREADY_ABSENT = "core_group_already_absent"

ADMIN_GROUP = {"group_id": "g-admins", "name": "Administratoren", "parent_id": None, "is_admin": True}


# --- the Core: a recording fake ----------------------------------------------------------------------------------------

_WRITE = re.compile(r"^(graph_)?(create|rename|move|delete|set|update|put|patch|post|install|deprecate|retire|"
                    r"upload|purge|push|start|stop|requeue|reextract|apply|grant|revoke)(_|$)")


class RecordingCore(FakeGraphApi):
    """The Core as the app sees it. Every call whose name says it writes is recorded with its arguments; ``observe``
    runs inside each such call before it answers, so a test sees what the Platform has committed at that moment.
    ``refuse[call]`` makes a group call fail as the real client lets it fail: "none" (a 404: nothing written, the
    client answers None), "false" (``delete_access_group``'s 404: the group is already gone) or "raise" (any other
    refusal, such as 409 for a group that data still name)."""

    calls: list = []
    groups: list = []
    refuse: dict = {}
    observe = None

    def __getattribute__(self, name):
        attr = object.__getattribute__(self, name)
        if _WRITE.match(name) and callable(attr):
            def recorded(*args, **kwargs):
                RecordingCore.calls.append((name, args, kwargs))
                if RecordingCore.observe is not None:
                    RecordingCore.observe(name, args, kwargs)
                return attr(*args, **kwargs)
            return recorded
        return attr

    @staticmethod
    def _mode(call):
        mode = RecordingCore.refuse.get(call)
        if mode == "raise":
            raise requests.exceptions.HTTPError("409 Client Error: Conflict")
        return mode

    def access_groups(self):
        return [dict(group) for group in RecordingCore.groups]

    def create_access_group(self, name, parent=None):
        if self._mode("create_access_group") == "none":
            return None
        group = {"group_id": f"g-{uuid.uuid4().hex[:10]}", "name": name, "parent_id": parent}
        RecordingCore.groups.append(group)
        return dict(group)

    def rename_access_group(self, identifier, name):
        if self._mode("rename_access_group") == "none":
            return None
        return {"group_id": identifier, "name": name}

    def delete_access_group(self, identifier):
        return self._mode("delete_access_group") != "false"


def _core(call=None):
    return [c for c in RecordingCore.calls if call is None or c[0] == call]


def _arg(call, index, keyword):
    _, args, kwargs = call
    return args[index] if len(args) > index else kwargs.get(keyword)


def _created_groups():
    """(name, parent) of every create_access_group call, however the arguments were passed."""
    out = []
    for _, args, kwargs in _core("create_access_group"):
        name = args[0] if args else kwargs.get("name")
        parent = args[1] if len(args) > 1 else kwargs.get("parent")
        out.append((name, parent))
    return out


# --- the world: the real app, an administrator signed in ---------------------------------------------------------------


class Browser:
    """A signed-in browser: it opens pages and posts the console's forms with the session's token."""

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
        fields = {k: v for k, v in (data or {}).items()}
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

    def person(self, name, key=None, *, display_name=None):
        user = self.repo.create(email=f"{name}@kanzlei.ch", display_name=display_name or name.capitalize(),
                                password=conftest.PASSWORD)
        if key:
            self.repo.grant_role(user.id, key)
        return self.repo.get(user.id)

    def role(self, name, functions=(), *, group=None, members=()):
        role = self.store.create_role(name=name, functions=set(functions), core_group_id=group)
        for person in members:
            self.store.add_member(role.id, person.id)
        return self.store.get_role(role.id)

    def only_chef_is_an_active_administrator(self):
        self.repo.disable(self.repo.get_by_email("bootstrap@kanzlei.ch").id)
        active = [m for m in self.store.administration().members if self.repo.get(m).is_active]
        assert active == [self.chef.id]

    def state(self):
        return [(str(r.id), r.name, r.description, r.kind, sorted(r.functions), r.core_group_id,
                 sorted(str(m) for m in r.members)) for r in self.store.list_roles()]

    def member_ids(self, role_id):
        return {str(m) for m in self.store.get_role(role_id).members}

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
    RecordingCore.calls = []
    RecordingCore.groups = [dict(ADMIN_GROUP)]
    RecordingCore.refuse = {}
    RecordingCore.observe = None
    app = conftest._identity_app(platform_db, tmp_path, monkeypatch, client_cls=RecordingCore)
    assert RecordingCore.calls == [], f"the start wrote to the Core: {RecordingCore.calls}"
    return World(platform_db, app)


# --- reading the page ----------------------------------------------------------------------------------------------------


def _text(fragment):
    fragment = re.sub(r"<(script|style|svg)\b.*?</\1>", " ", fragment, flags=re.S)
    return " ".join(htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def _shown(fragment):
    """The text of ``fragment`` without its forms: what it says, not what it offers to change."""
    return _text(re.sub(r"<form\b.*?</form>", " ", fragment, flags=re.S))


def _attr(tag, name):
    found = re.search(rf'(?:^|\s){re.escape(name)}="([^"]*)"', tag)
    return None if found is None else htmllib.unescape(found.group(1))


def _main(html):
    found = re.search(r"<main\b.*?</main>", html, flags=re.S)
    assert found, "the page has no <main>"
    return found.group(0)


def _nav(html):
    sidebar = re.search(r"<aside\b[^>]*\bapp-sidebar\b[^>]*>.*?</aside>", html, flags=re.S)
    assert sidebar, "the page has no navigation"
    nav = re.search(r"<nav\b[^>]*>.*?</nav>", sidebar.group(0), flags=re.S)
    assert nav, "the navigation has no <nav>"
    return nav.group(0)


def _hrefs(fragment):
    return [htmllib.unescape(h) for h in re.findall(r'href="([^"]+)"', fragment)]


def _current(fragment):
    return _hrefs(" ".join(re.findall(r'<a\b[^>]*aria-current="page"[^>]*>', fragment)))


def _tabs(html):
    """(href, text, current) of each tab in the row of roles."""
    nav = re.search(r"<nav\b[^>]*\bk-tabs\b[^>]*>.*?</nav>", _main(html), flags=re.S)
    assert nav, "the page has no row of tabs (parts.tabs)"
    return [(_attr(attrs, "href"), _text(body), "k-tab--on" in (_attr(attrs, "class") or ""))
            for attrs, body in re.findall(r"<a\b([^>]*)>(.*?)</a>", nav.group(0), flags=re.S)]


def _card(html):
    """From the element that carries the current role's id to the closing sentence."""
    main = _main(html)
    at = main.find("data-role-id=")
    assert at >= 0, "no element carries the current role (data-role-id)"
    return main[main.rfind("<", 0, at):]


def _part(html, name):
    found = re.search(rf'<section\b[^>]*\bdata-role-part="{name}"[^>]*>(.*?)</section>', _main(html), flags=re.S)
    assert found, f"the page has no part {name} (<section data-role-part=\"{name}\">)"
    return found.group(1)


def _form(html, action):
    """The body of the form that posts to ``action``, or None."""
    for attrs, body in re.findall(r"<form\b([^>]*)>(.*?)</form>", html, flags=re.S):
        if _attr(attrs, "action") == action and (_attr(attrs, "method") or "get").lower() == "post":
            return body
    return None


def _forms(html, action):
    return [body for attrs, body in re.findall(r"<form\b([^>]*)>(.*?)</form>", html, flags=re.S)
            if _attr(attrs, "action") == action]


def _boxes(body, name):
    """(value, checked) of every checkbox called ``name``."""
    return [(_attr(tag, "value"), bool(re.search(r"\schecked\b", tag)))
            for tag in re.findall(r"<input\b[^>]*>", body)
            if _attr(tag, "name") == name and (_attr(tag, "type") or "").lower() == "checkbox"]


def _options(body, name):
    found = re.search(rf'<select\b[^>]*\sname="{name}"[^>]*>(.*?)</select>', body, flags=re.S)
    return [] if found is None else [_attr(tag, "value") for tag in re.findall(r"<option\b[^>]*>", found.group(1))]


def _as_drawn(body):
    """What a browser sends from a form as drawn: hidden and text fields, checked boxes; nothing disabled."""
    sent: dict = {}
    for tag in re.findall(r"<input\b[^>]*>", body):
        name, kind = _attr(tag, "name"), (_attr(tag, "type") or "text").lower()
        if not name or re.search(r"\sdisabled\b", tag) or kind in ("submit", "button"):
            continue
        if kind in ("checkbox", "radio") and not re.search(r"\schecked\b", tag):
            continue
        sent.setdefault(name, []).append(_attr(tag, "value") or "")
    return sent


def _alert(html):
    return " ".join(_text(body) for _, body in re.findall(r'<(\w+)\b[^>]*role="alert"[^>]*>(.*?)</\1>', html,
                                                         flags=re.S))


def _person_form(html, person_id):
    for body in _forms(html, PEOPLE_ROLES):
        if f'value="{person_id}"' in body:
            return body
    raise AssertionError(f"no roles form for {person_id} on the people page")


# === 1. the page (RB-3) ===================================================================================================


def test_the_administrator_opens_the_page_on_administration(world):
    html = world.admin.page(PAGE)
    main = _main(html)
    assert _text(re.search(r"<h1\b.*?</h1>", main, flags=re.S).group(0)) == "Rollen"
    assert LEDE in _text(main)
    assert "Rolle anlegen" in _text(main)
    assert _form(main, CREATE) is not None
    roles = world.store.list_roles()
    tabs = _tabs(html)
    assert [href for href, _, _ in tabs] == [ROLE(r.id) for r in roles]
    assert [on for _, _, on in tabs] == [True] + [False] * (len(roles) - 1)
    assert roles[0].kind == "administration"
    assert main.count(f'data-role-id="{roles[0].id}"') == 1
    assert CLOSING in _text(main)


def test_a_role_page_shows_that_role_with_the_words_of_the_mock_up(world):
    people = [world.person(n, display_name=d) for n, d in (("lea", "Lea Brunner"), ("jonas", "Jonas Meier"),
                                                           ("sara", "Sara Keller"))]
    role = world.store.create_role(name="Mandatsteam Bau", description="Bearbeitet die Mandate im Baurecht.",
                                   functions={"member", "approver", "builder_viewer"}, core_group_id="g-bau")
    for person in people:
        world.store.add_member(role.id, person.id)
    html = world.admin.page(ROLE(role.id))
    tabs = {href: (text, on) for href, text, on in _tabs(html)}
    assert tabs[ROLE(role.id)] == ("Mandatsteam Bau 3", True)
    assert [on for _, on in tabs.values()].count(True) == 1
    card = _text(_card(html))
    assert "Mandatsteam Bau" in card and card.index("Mandatsteam Bau") < card.index("Darf tun")
    assert "Bearbeitet die Mandate im Baurecht." in card
    assert "Umbenennen" in card
    functions = _shown(_part(html, "functions"))
    assert functions.startswith("Darf tun")
    for word in ("Fragen und suchen", "Freigaben bestätigen", "Builder lesen"):
        assert word in functions
    assert "Einlesen" not in functions and "Builder bearbeiten" not in functions
    assert "Ändern" in _text(_part(html, "functions"))
    data = _text(_part(html, "data"))
    assert data.startswith("Darf lesen") and DATA_NOTE in data
    members = _text(_part(html, "members"))
    assert members.startswith("Mitglieder") and "Mitglied hinzufügen" in members
    for person in people:
        assert person.display_name in members
    assert CLOSING in _text(_main(html))


def test_every_tab_shows_the_roles_name_and_its_number_of_people(world):
    anna = world.person("anna")
    world.role("Sekretariat", {"approver"}, members=[anna, world.chef])
    world.role("Praktikum")
    for role in world.store.list_roles():
        tabs = {href: text for href, text, _ in _tabs(world.admin.page(ROLE(role.id)))}
        assert tabs == {ROLE(r.id): f"{r.name} {len(r.members)}" for r in world.store.list_roles()}


@pytest.mark.parametrize("kind", ["administration", "mitglied", "custom"])
def test_the_data_part_is_read_only_and_says_it_acts_with_ein(kind, world):
    role = {"administration": world.store.administration, "mitglied": world.store.mitglied,
            "custom": lambda: world.role("Partner", {"approver"}, group="g-partner")}[kind]()
    data = _text(_part(world.admin.page(ROLE(role.id)), "data"))
    assert "Darf lesen" in data and DATA_NOTE in data


def test_functions_are_shown_in_plain_words_never_as_keys(world):
    role = world.role("Alles", OTHERS)
    html = world.admin.page(ROLE(role.id))
    shown = _shown(_part(html, "functions"))
    for key in OTHERS:
        assert FUNCTION_WORDS[key] in shown, key
    visible = _text(_main(html))
    for key in FUNCTION_WORDS:
        assert not re.search(rf"\b{key}\b", visible), f"the key {key} is shown"
    assert "Verwaltung" in _shown(_part(world.admin.page(ROLE(world.store.administration().id)), "functions"))


def test_names_and_descriptions_are_escaped(world):
    role = world.role("<b>Fett</b>", {"approver"})
    world.store.rename_role(role.id, name="<b>Fett</b> & Co")
    created = world.store.create_role(name="Skript", description="<script>alert(1)</script>")
    for role_id in (role.id, created.id):
        html = world.admin.page(ROLE(role_id))
        assert "<b>Fett</b>" not in html and "<script>alert(1)</script>" not in html
    assert "&lt;b&gt;Fett&lt;/b&gt; &amp; Co" in world.admin.page(ROLE(role.id))
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in world.admin.page(ROLE(created.id))


@pytest.mark.parametrize("address", ["random", "malformed"])
def test_an_unknown_or_malformed_role_is_404(address, world):
    assert world.admin.get(ROLE(world.store.mitglied().id)).status_code == 200       # a known one answers
    path = {"random": ROLE(uuid.uuid4()), "malformed": ROLE("kein-uuid")}[address]
    assert world.admin.get(path).status_code == 404


@pytest.mark.parametrize("which", ["page", "role", "people"])
def test_the_menu_has_rollen_right_after_personen(which, world):
    path = {"page": PAGE, "role": ROLE(world.store.mitglied().id), "people": PEOPLE}[which]
    nav = _nav(world.admin.page(path))
    entries = _hrefs(nav)
    assert PAGE in entries and entries.index(PAGE) == entries.index(PEOPLE) + 1
    assert "Rollen" in _text(nav)
    assert _current(nav) == ([PEOPLE] if which == "people" else [PAGE])


def test_an_approver_has_no_rollen_entry(world):
    paul = world.person("paul", "approver")
    assert PAGE not in _hrefs(_nav(Browser(world.app, paul.email).page("/admin/approvals")))
    assert PAGE in _hrefs(_nav(world.admin.page("/admin/approvals")))   # the administrator's menu on the same page


@pytest.mark.parametrize("key", list(OTHERS) + [None], ids=lambda k: k or "no-function")
def test_every_other_function_and_none_is_refused_the_pages(key, world):
    person = world.person((key or "gast").replace("_", "-"), key)
    browser = Browser(world.app, person.email)
    assert browser.get(PAGE).status_code == 403
    assert browser.get(ROLE(world.store.administration().id)).status_code == 403


def test_opening_the_pages_writes_nothing_to_the_core_and_nothing_to_the_roles(world):
    world.role("Partner", {"approver"}, group="g-partner", members=[world.chef])
    before, mark = world.state(), world.mark()
    world.admin.page(PAGE)
    for role in world.store.list_roles():
        world.admin.page(ROLE(role.id))
    world.admin.page(PEOPLE)
    assert RecordingCore.calls == []
    assert world.state() == before
    assert [row for row in world.audit_since(mark) if row["action"].startswith("role.")] == []


# === 2. every form: the gate and the token ================================================================================


def _the_forms(world):
    """Each form of RB-3/4 with the smallest form it accepts, and what it changes when it gets through."""
    anna, bert = world.person("anna"), world.person("bert")
    role = world.role("Sekretariat", {"approver"}, group="g-sek", members=[anna])
    mitglied = world.store.mitglied()
    return {
        "create": (CREATE, {"name": "Partner", "function": ["approver"]}),
        "rename": (RENAME(role.id), {"name": "Sekretariat Zürich"}),
        "functions": (FUNCTIONS(role.id), {"function": ["approver", "ingestion_manager"]}),
        "add": (ADD(role.id), {"user_id": str(bert.id)}),
        "remove": (REMOVE(role.id), {"user_id": str(anna.id)}),
        "delete": (DELETE(role.id), {}),
        "connect": (CONNECT, {}),
        "people": (PEOPLE_ROLES, {"user_id": str(anna.id), "role_id": [str(mitglied.id)]}),
    }


FORM_NAMES = ["create", "rename", "functions", "add", "remove", "delete", "connect", "people"]
ROUTES = [(PAGE, "GET"), ("/admin/roles/00000000-0000-0000-0000-000000000000", "GET"), (CREATE, "POST"),
          (CONNECT, "POST"), (PEOPLE_ROLES, "POST")] + [
    (f("00000000-0000-0000-0000-000000000000"), "POST") for f in (RENAME, FUNCTIONS, ADD, REMOVE, DELETE)]


def test_every_route_carries_the_console_admin_gate(world):
    """The set the console's gate checks (``allowed_roles``, put on the view by ``_require_roles``) is admin alone."""
    adapter = world.app.url_map.bind("localhost")
    for path, method in ROUTES:
        endpoint, _ = adapter.match(path, method=method)
        assert endpoint.startswith("admin."), (path, endpoint)
        assert getattr(world.app.view_functions[endpoint], "allowed_roles", None) == frozenset({"admin"}), path


@pytest.mark.parametrize("path, method", ROUTES)
def test_a_signed_out_visitor_is_sent_to_sign_in(path, method, world):
    """The app's own sign-in check answers before any route runs, so this alone cannot catch a missing gate; the
    gate is pinned by the test above and by the 403 tests."""
    response = world.app.test_client().open(path, method=method)
    assert response.status_code == 302 and "/login" in response.headers["Location"]


@pytest.mark.parametrize("key", ["member", "ingestion_manager", "approver", None], ids=lambda k: k or "no-function")
def test_everybody_but_an_administrator_is_refused_every_form_and_nothing_changes(key, world):
    forms = _the_forms(world)
    person = world.person((key or "gast").replace("_", "-"), key)
    browser = Browser(world.app, person.email)
    before = world.state()
    for name, (path, data) in forms.items():
        assert browser.post(path, data, follow=False).status_code == 403, name
    assert world.state() == before
    assert RecordingCore.calls == []


@pytest.mark.parametrize("name", FORM_NAMES)
def test_a_form_without_the_token_or_with_a_wrong_one_is_refused_and_changes_nothing(name, world):
    path, data = _the_forms(world)[name]
    before, mark = world.state(), world.mark()
    assert world.admin.post(path, data, token=False).status_code == 400
    assert world.admin.post(path, data, token="falsch").status_code == 400
    assert world.state() == before and RecordingCore.calls == []
    assert [r for r in world.audit_since(mark) if r["action"].startswith("role.") or r["action"] == PERSON_ROLES] == []
    assert world.admin.post(path, data).status_code == 200                # the same form with the token gets through
    assert world.state() != before


# === 3. «Rolle anlegen»: the Core group first ===========================================================================


def test_rolle_anlegen_creates_the_core_group_first_and_saves_the_role_with_it(world):
    seen = []
    RecordingCore.observe = lambda call, args, kwargs: seen.append(
        [r.name for r in RoleStore(world.conn).list_roles() if r.name == "Partner"])
    response = world.admin.post(CREATE, {"name": "Partner", "description": "Die Partnerinnen und Partner",
                                         "function": ["approver", "member"]})
    assert response.status_code == 200
    assert _created_groups() == [("Partner", None)]                      # a root group, named like the role
    assert seen == [[]]                                                  # the role did not exist yet
    role, = [r for r in world.store.list_roles() if r.name == "Partner"]
    group = RecordingCore.groups[-1]["group_id"]
    assert (role.kind, role.description, set(role.functions), role.core_group_id) == (
        "custom", "Die Partnerinnen und Partner", {"approver", "member"}, group)
    assert f'data-role-id="{role.id}"' in response.data.decode("utf-8")     # the new role is shown
    assert len(_core()) == 1


@pytest.mark.parametrize("mode", ["none", "raise"])
def test_when_the_core_refuses_the_group_no_role_is_saved(mode, world):
    RecordingCore.refuse = {"create_access_group": mode}
    before = world.state()
    response = world.admin.post(CREATE, {"name": "Partner", "function": ["approver"]})
    assert response.status_code == 502
    assert "Knovas" in _alert(response.data.decode("utf-8"))
    assert world.state() == before


@pytest.mark.parametrize("data", [
    {"name": ""}, {"name": "   "}, {"name": "x" * 81}, {"name": "Mitglied"}, {"name": "administration"},
    {"name": "sekretariat"}, {"name": "Partner", "function": ["wizard"]},
    {"name": "Partner", "function": ["admin", "approver"]},
], ids=["empty", "blank", "too-long", "taken", "taken-case", "taken-own", "unknown-function", "admin"])
def test_what_the_store_would_refuse_is_refused_before_the_core_is_asked(data, world):
    world.role("Sekretariat", {"approver"}, group="g-sek")
    before = world.state()
    response = world.admin.post(CREATE, data)
    assert response.status_code == 400
    assert _alert(response.data.decode("utf-8"))
    assert world.state() == before
    assert RecordingCore.calls == []


# === 4. «Umbenennen»: the Core group first, its id kept =================================================================


def test_umbenennen_renames_the_core_group_first_and_keeps_its_id(world):
    anna = world.person("anna")
    role = world.role("Sekretariat", {"approver"}, group="g-sek", members=[anna])
    seen = []
    RecordingCore.observe = lambda call, args, kwargs: seen.append(RoleStore(world.conn).get_role(role.id).name)
    assert world.admin.post(RENAME(role.id), {"name": "Sekretariat Zürich"}).status_code == 200
    call, = _core()
    assert call[0] == "rename_access_group"
    assert (_arg(call, 0, "identifier"), _arg(call, 1, "name")) == ("g-sek", "Sekretariat Zürich")
    assert seen == ["Sekretariat"]                                       # the Core first
    renamed = world.store.get_role(role.id)
    assert (renamed.name, renamed.core_group_id, world.member_ids(role.id)) == (
        "Sekretariat Zürich", "g-sek", {str(anna.id)})


@pytest.mark.parametrize("mode", ["none", "raise"])
def test_when_the_core_refuses_the_rename_the_role_keeps_its_name(mode, world):
    role = world.role("Sekretariat", {"approver"}, group="g-sek")
    RecordingCore.refuse = {"rename_access_group": mode}
    response = world.admin.post(RENAME(role.id), {"name": "Sekretariat Zürich"})
    assert response.status_code == 502
    assert "Knovas" in _alert(response.data.decode("utf-8"))
    assert world.store.get_role(role.id).name == "Sekretariat"


def test_a_role_without_a_core_group_is_renamed_without_the_core(world):
    role = world.role("Freigaben bestätigen", {"approver"})              # as the move of RB-1 leaves it
    assert world.admin.post(RENAME(role.id), {"name": "Freigabe"}).status_code == 200
    assert world.store.get_role(role.id).name == "Freigabe"
    assert RecordingCore.calls == []


@pytest.mark.parametrize("name", ["", "Mitglied", "x" * 81])
def test_a_taken_or_invalid_name_is_refused_before_the_core(name, world):
    role = world.role("Sekretariat", {"approver"}, group="g-sek")
    assert world.admin.post(RENAME(role.id), {"name": name}).status_code == 400
    assert world.store.get_role(role.id).name == "Sekretariat"
    assert RecordingCore.calls == []


def test_administration_and_mitglied_keep_their_names(world):
    custom = world.role("Sekretariat", {"approver"}, group="g-sek")
    for role in (world.store.administration(), world.store.mitglied()):
        assert world.admin.post(RENAME(role.id), {"name": "Andere"}).status_code == 400
        assert world.store.get_role(role.id).name == role.name
        assert _form(world.admin.page(ROLE(role.id)), RENAME(role.id)) is None
    assert _form(world.admin.page(ROLE(custom.id)), RENAME(custom.id)) is not None
    assert RecordingCore.calls == []


# === 5. «Darf tun» ======================================================================================================


def test_the_functions_form_offers_every_function_but_admin_and_saves_them(world):
    anna = world.person("anna")
    role = world.role("Sekretariat", {"approver", "member"}, members=[anna])
    body = _form(world.admin.page(ROLE(role.id)), FUNCTIONS(role.id))
    assert body is not None
    boxes = dict(_boxes(body, "function"))
    assert set(boxes) == set(OTHERS)
    assert {key for key, checked in boxes.items() if checked} == {"approver", "member"}
    assert world.admin.post(FUNCTIONS(role.id), {"function": ["approver", "experimenter"]}).status_code == 200
    assert set(world.store.get_role(role.id).functions) == {"approver", "experimenter"}
    assert set(world.repo.roles_of(anna.id)) == {"approver", "experimenter"}
    assert RecordingCore.calls == []


def test_admin_is_refused_outside_administration(world):
    role = world.role("Partner", {"approver"})
    response = world.admin.post(FUNCTIONS(role.id), {"function": ["admin", "approver"]})
    assert response.status_code == 400 and _alert(response.data.decode("utf-8"))
    assert set(world.store.get_role(role.id).functions) == {"approver"}


def test_administration_keeps_admin_and_its_own_form_keeps_it(world):
    administration = world.store.administration()
    assert world.admin.post(FUNCTIONS(administration.id), {"function": ["approver"]}).status_code == 400
    assert set(world.store.administration().functions) == {"admin"}
    sent = _as_drawn(_form(world.admin.page(ROLE(administration.id)), FUNCTIONS(administration.id)))
    sent["function"] = sent.get("function", []) + ["approver"]          # one more box ticked
    assert world.admin.post(FUNCTIONS(administration.id), sent, token=False).status_code == 200
    assert set(world.store.administration().functions) == {"admin", "approver"}


def test_a_function_outside_the_ten_is_refused(world):
    role = world.role("Partner", {"approver"})
    assert world.admin.post(FUNCTIONS(role.id), {"function": ["approver", "wizard"]}).status_code == 400
    assert set(world.store.get_role(role.id).functions) == {"approver"}


# === 6. «Mitglieder» ====================================================================================================


def test_a_person_joins_and_leaves_a_role_and_gains_and_loses_its_functions(world):
    anna = world.person("anna", display_name="Anna Amsler")
    role = world.role("Freigabe", {"approver"}, group="g-frei")
    assert world.admin.post(ADD(role.id), {"user_id": str(anna.id)}).status_code == 200
    assert world.member_ids(role.id) == {str(anna.id)}
    assert "approver" in world.repo.roles_of(anna.id)
    html = world.admin.page(ROLE(role.id))
    assert "Anna Amsler" in _text(_part(html, "members"))
    removals = _forms(html, REMOVE(role.id))
    assert [_as_drawn(body).get("user_id") for body in removals] == [[str(anna.id)]]
    assert world.admin.post(REMOVE(role.id), {"user_id": str(anna.id)}).status_code == 200
    assert world.member_ids(role.id) == set()
    assert "approver" not in world.repo.roles_of(anna.id)
    assert RecordingCore.calls == []                                     # members live in the Platform


def test_the_add_form_offers_the_people_who_are_not_members(world):
    anna, bert = world.person("anna"), world.person("bert")
    role = world.role("Freigabe", {"approver"}, members=[anna])
    offered = _options(_form(world.admin.page(ROLE(role.id)), ADD(role.id)), "user_id")
    assert str(bert.id) in offered and str(anna.id) not in offered


@pytest.mark.parametrize("user_id", [str(uuid.uuid4()), "kein-uuid", ""])
def test_an_unknown_person_is_refused(user_id, world):
    role = world.role("Freigabe", {"approver"})
    before = world.state()
    assert world.admin.post(ADD(role.id), {"user_id": user_id}).status_code == 400
    assert world.state() == before


def test_the_last_active_administrator_cannot_leave_administration(world):
    administration = world.store.administration()
    world.only_chef_is_an_active_administrator()
    response = world.admin.post(REMOVE(administration.id), {"user_id": str(world.chef.id)})
    assert response.status_code == 400 and _alert(response.data.decode("utf-8"))
    assert str(world.chef.id) in world.member_ids(administration.id)
    assert "admin" in world.repo.roles_of(world.chef.id)
    vize = world.person("vize", "admin")                                  # with a second one, one may leave
    assert world.admin.post(REMOVE(administration.id), {"user_id": str(vize.id)}).status_code == 200
    assert str(vize.id) not in world.member_ids(administration.id)


def test_a_33rd_group_is_refused_on_the_page(world):
    anna = world.person("anna")
    world.repo.set_access_groups(anna.id, [f"g-{i:02d}" for i in range(32)])
    role = world.role("Personal", {"approver"}, group="g-personal")
    response = world.admin.post(ADD(role.id), {"user_id": str(anna.id)})
    assert response.status_code == 400 and "32" in _alert(response.data.decode("utf-8"))
    assert world.member_ids(role.id) == set()


def test_giving_oneself_a_role_needs_no_second_administrator(world):
    from identity.approvals import ApprovalService

    ApprovalService(world.conn, world.repo).set_admin_bypass(False, by=world.chef)
    freigabe = world.role("Freigabe", {"approver"})
    einlesen = world.role("Einlesen", {"ingestion_manager"})
    assert world.admin.post(ADD(freigabe.id), {"user_id": str(world.chef.id)}).status_code == 200
    assert "approver" in world.repo.roles_of(world.chef.id)
    mine = [str(r.id) for r in world.store.roles_of_user(world.chef.id)] + [str(einlesen.id)]
    assert world.admin.post(PEOPLE_ROLES, {"user_id": str(world.chef.id), "role_id": mine}).status_code == 200
    assert "ingestion_manager" in world.repo.roles_of(world.chef.id)
    assert world.conn.execute("SELECT count(*) FROM approval_requests").fetchone()[0] == 0


# === 7. deleting ========================================================================================================


def test_administration_and_mitglied_cannot_be_deleted(world):
    custom = world.role("Partner", {"approver"}, group="g-partner")
    before = world.state()
    for role in (world.store.administration(), world.store.mitglied()):
        response = world.admin.post(DELETE(role.id), {})
        assert response.status_code == 400 and _alert(response.data.decode("utf-8"))
        assert _form(world.admin.page(ROLE(role.id)), DELETE(role.id)) is None
    assert world.state() == before and RecordingCore.calls == []
    assert _form(world.admin.page(ROLE(custom.id)), DELETE(custom.id)) is not None


def test_a_role_in_use_is_deleted_members_first_then_its_core_group_then_the_role(world):
    anna, bert = world.person("anna"), world.person("bert")
    role = world.role("Sekretariat", {"approver"}, group="g-sek", members=[anna, bert])
    seen = []
    RecordingCore.observe = lambda call, args, kwargs: seen.append(
        (call, {str(m) for m in RoleStore(world.conn).get_role(role.id).members}))
    mark = world.mark()
    assert world.admin.post(DELETE(role.id), {}).status_code == 200
    call, = _core()
    assert (call[0], _arg(call, 0, "identifier")) == ("delete_access_group", "g-sek")
    assert seen == [("delete_access_group", set())]                     # the role still there, its members gone
    assert role.id not in {r.id for r in world.store.list_roles()}
    assert "approver" not in world.repo.roles_of(anna.id) | world.repo.roles_of(bert.id)
    assert not _one(world.audit_since(mark), DELETED)["detail"].get(ALREADY_ABSENT)   # the Core deleted it


def test_a_core_404_means_the_group_is_already_gone_and_the_role_is_deleted(world):
    anna = world.person("anna")
    role = world.role("Sekretariat", {"approver"}, group="g-sek", members=[anna])
    RecordingCore.refuse = {"delete_access_group": "false"}             # the client's 404: unknown at the Core
    mark = world.mark()
    assert world.admin.post(DELETE(role.id), {}).status_code == 200
    assert [c[0] for c in _core()] == ["delete_access_group"]
    assert role.id not in {r.id for r in world.store.list_roles()}
    assert "approver" not in world.repo.roles_of(anna.id)
    row = _one(world.audit_since(mark), DELETED)
    assert (row["target_type"], row["target_id"], row["outcome"]) == ("role", str(role.id), "ok")
    assert row["detail"].get(ALREADY_ABSENT) is True


def test_a_core_refusal_leaves_the_role_without_its_members_and_says_so(world):
    anna = world.person("anna")
    role = world.role("Sekretariat", {"approver"}, group="g-sek", members=[anna])
    RecordingCore.refuse = {"delete_access_group": "raise"}             # 409: data still name the group
    mark = world.mark()
    response = world.admin.post(DELETE(role.id), {})
    assert response.status_code == 502
    assert "Knovas" in _alert(response.data.decode("utf-8"))
    kept = world.store.get_role(role.id)
    assert (kept.name, kept.core_group_id, set(kept.functions)) == ("Sekretariat", "g-sek", {"approver"})
    assert world.member_ids(role.id) == set()                            # removed first, as the design orders it
    assert not [r for r in world.audit_since(mark) if r["action"] == DELETED and r["outcome"] == "ok"]


def test_a_role_without_a_core_group_is_deleted_without_the_core(world):
    role = world.role("Freigaben bestätigen", {"approver"}, members=[world.person("anna")])
    assert world.admin.post(DELETE(role.id), {}).status_code == 200
    assert role.id not in {r.id for r in world.store.list_roles()}
    assert RecordingCore.calls == []


@pytest.mark.parametrize("route", [RENAME, FUNCTIONS, ADD, REMOVE, DELETE], ids=lambda f: f.__name__.lower())
def test_an_unknown_role_is_404_for_every_form(route, world):
    anna = world.person("anna")
    known = world.role("Freigabe", {"approver"}, group="g-frei")
    data = {"name": "X", "function": ["approver"], "user_id": str(anna.id)}
    before = world.state()
    assert world.admin.post(route(known.id), data, token=False).status_code == 400   # the route is there
    response = world.admin.post(route(uuid.uuid4()), data)
    assert response.status_code == 404
    assert world.state() == before and RecordingCore.calls == []


# === 8. Administration's and Mitglied's Core groups =====================================================================


def test_the_two_roles_get_their_core_groups_on_an_administrators_action(world):
    html = world.admin.page(PAGE)
    assert world.store.administration().core_group_id is None and world.store.mitglied().core_group_id is None
    assert _form(_main(html), CONNECT) is not None
    assert RecordingCore.calls == []                                     # not on a GET
    assert world.admin.post(CONNECT, {}).status_code == 200
    assert world.store.administration().core_group_id == ADMIN_GROUP["group_id"]
    assert _created_groups() == [("Mitglied", None)]                     # Mitglied: a new root group
    created = [g["group_id"] for g in RecordingCore.groups if g.get("name") == "Mitglied"]
    assert world.store.mitglied().core_group_id == created[0]
    assert _form(_main(world.admin.page(PAGE)), CONNECT) is None
    calls = len(RecordingCore.calls)
    assert world.admin.post(CONNECT, {}).status_code == 200              # a second time changes nothing
    assert len(RecordingCore.calls) == calls
    assert world.store.mitglied().core_group_id == created[0]


def test_without_an_administrator_group_at_the_core_administration_is_not_linked(world):
    RecordingCore.groups = [{"group_id": "g-lit", "name": "Litigation", "parent_id": None}]
    response = world.admin.post(CONNECT, {})
    assert response.status_code == 502 and "Knovas" in _alert(response.data.decode("utf-8"))
    assert world.store.administration().core_group_id is None
    assert not [name for name, _ in _created_groups() if name != "Mitglied"]   # never a new administrator group


# === 9. the people page offers roles ====================================================================================


def test_a_new_installation_offers_exactly_administration_and_mitglied(world):
    html = world.admin.page(PEOPLE)
    two = {str(world.store.administration().id), str(world.store.mitglied().id)}
    form = _person_form(html, world.chef.id)
    assert {value for value, _ in _boxes(form, "role_id")} == two
    assert "Administration" in _text(form) and "Mitglied" in _text(form)
    assert {value for body in _forms(html, PEOPLE_ROLES) for value, _ in _boxes(body, "role_id")} == two
    assert 'name="roles"' not in html
    for key in FUNCTION_WORDS:
        assert f'value="{key}"' not in html, f"the people page offers the key {key}"


def test_the_tenants_own_roles_are_offered_too_and_checked_for_their_members(world):
    assert world.admin.post(CREATE, {"name": "Sekretariat", "function": ["approver", "ingestion_manager"]}).status_code == 200
    sekretariat, = [r for r in world.store.list_roles() if r.name == "Sekretariat"]
    paul = world.person("paul", "approver")                              # a key given the old way: a role of its own
    html = world.admin.page(PEOPLE)
    offered = {value for value, _ in _boxes(_person_form(html, world.chef.id), "role_id")}
    assert offered == {str(r.id) for r in world.store.list_roles()}
    assert str(sekretariat.id) in offered and len(offered) == 4
    checked = {value for value, on in _boxes(_person_form(html, paul.id), "role_id") if on}
    assert checked == {str(r.id) for r in world.store.roles_of_user(paul.id)} != set()


def test_saving_makes_a_persons_roles_exactly_the_checked_ones(world):
    anna = world.person("anna", "member")
    sekretariat = world.role("Sekretariat", {"approver"})
    mitglied = world.store.mitglied()
    data = {"user_id": str(anna.id), "role_id": [str(mitglied.id), str(sekretariat.id)]}
    assert world.admin.post(PEOPLE_ROLES, data).status_code == 200
    assert {r.id for r in world.store.roles_of_user(anna.id)} == {mitglied.id, sekretariat.id}
    assert set(world.repo.roles_of(anna.id)) == {"member", "approver"}
    assert world.admin.post(PEOPLE_ROLES, {"user_id": str(anna.id), "role_id": [str(sekretariat.id)]}).status_code == 200
    assert {r.id for r in world.store.roles_of_user(anna.id)} == {sekretariat.id}


def test_the_form_as_drawn_keeps_a_persons_roles(world):
    paul = world.person("paul", "approver")
    world.store.add_member(world.store.mitglied().id, paul.id)
    before = {r.id for r in world.store.roles_of_user(paul.id)}
    sent = _as_drawn(_person_form(world.admin.page(PEOPLE), paul.id))
    assert set(sent.get("role_id", [])) == {str(r) for r in before}       # the roles, as checked boxes
    assert world.admin.post(PEOPLE_ROLES, sent, token=False).status_code == 200
    assert {r.id for r in world.store.roles_of_user(paul.id)} == before
    assert "approver" in world.repo.roles_of(paul.id)


def test_the_people_page_no_longer_takes_keys(world):
    anna = world.person("anna", "member")
    world.admin.post(PEOPLE_ROLES, {"user_id": str(anna.id), "roles": ["approver", "admin"]})
    assert not {"approver", "admin"} & set(world.repo.roles_of(anna.id))
    assert str(anna.id) not in world.member_ids(world.store.administration().id)


def test_the_last_administrator_cannot_leave_through_the_people_page_and_nothing_else_changes(world):
    world.only_chef_is_an_active_administrator()
    sekretariat = world.role("Sekretariat", {"approver"})
    before = {r.id for r in world.store.roles_of_user(world.chef.id)}
    data = {"user_id": str(world.chef.id), "role_id": [str(world.store.mitglied().id), str(sekretariat.id)]}
    response = world.admin.post(PEOPLE_ROLES, data)
    assert response.status_code == 400 and _alert(response.data.decode("utf-8"))
    assert {r.id for r in world.store.roles_of_user(world.chef.id)} == before      # one transaction


def test_the_33rd_group_is_refused_on_the_people_page(world):
    anna = world.person("anna")
    world.repo.set_access_groups(anna.id, [f"g-{i:02d}" for i in range(32)])
    role = world.role("Personal", {"approver"}, group="g-personal")
    response = world.admin.post(PEOPLE_ROLES, {"user_id": str(anna.id), "role_id": [str(role.id)]})
    assert response.status_code == 400 and "32" in _alert(response.data.decode("utf-8"))
    assert world.member_ids(role.id) == set()


def test_a_person_created_on_the_people_page_joins_mitglied_only(world):
    data = {"email": "neu@kanzlei.ch", "display_name": "Neu", "password": "ein-ganz-neues-passwort"}
    assert world.admin.post(PEOPLE_CREATE, data).status_code == 200
    neu = world.repo.get_by_email("neu@kanzlei.ch")
    assert [r.kind for r in world.store.roles_of_user(neu.id)] == ["mitglied"]


# === 10. the audit rows: one per change, same transaction, ids only =====================================================


def _one(rows, action):
    found = [r for r in rows if r["action"] == action]
    assert len(found) == 1, f"{action}: {len(found)} rows in {[r['action'] for r in rows]}"
    return found[0]


def test_every_change_writes_its_audit_row_with_ids_only(world):
    zora = world.person("zora.zwiebel", "member", display_name="Zora Zwiebel")
    chef, written = str(world.chef.id), []

    def step(path, data):
        mark = world.mark()
        assert world.admin.post(path, data).status_code == 200, path
        rows = world.audit_since(mark)
        written.extend(rows)
        return rows

    rows = step(CONNECT, {})
    linked = [r for r in rows if r["action"] == LINKED]
    assert {r["target_id"] for r in linked} == {str(world.store.administration().id), str(world.store.mitglied().id)}
    assert ADMIN_GROUP["group_id"] in json.dumps([r["detail"] for r in linked])

    row = _one(step(CREATE, {"name": "Mandat Zwiebel", "description": "Akten Zwiebel", "function": ["approver"]}),
               CREATED)
    role, = [r for r in world.store.list_roles() if r.name == "Mandat Zwiebel"]
    assert (row["target_type"], row["target_id"], row["actor"], row["outcome"]) == ("role", str(role.id), chef, "ok")
    assert role.core_group_id in json.dumps(row["detail"])

    row = _one(step(RENAME(role.id), {"name": "Mandat Zwiebel II"}), RENAMED)
    assert (row["target_type"], row["target_id"]) == ("role", str(role.id))

    row = _one(step(FUNCTIONS(role.id), {"function": ["approver", "member"]}), FUNCTIONS_CHANGED)
    assert row["target_id"] == str(role.id)
    assert (set(row["detail"]["before"]), set(row["detail"]["after"])) == ({"approver"}, {"approver", "member"})

    row = _one(step(ADD(role.id), {"user_id": str(zora.id)}), MEMBER_ADDED)
    assert (row["target_id"], row["detail"]["user_id"]) == (str(role.id), str(zora.id))
    row = _one(step(REMOVE(role.id), {"user_id": str(zora.id)}), MEMBER_REMOVED)
    assert (row["target_id"], row["detail"]["user_id"]) == (str(role.id), str(zora.id))

    before = {str(r.id) for r in world.store.roles_of_user(zora.id)}
    row = _one(step(PEOPLE_ROLES, {"user_id": str(zora.id), "role_id": sorted(before | {str(role.id)})}),
               PERSON_ROLES)
    assert (row["target_type"], row["target_id"], row["actor"]) == ("user", str(zora.id), chef)
    assert (set(row["detail"]["before"]), set(row["detail"]["after"])) == (before, before | {str(role.id)})

    rows = step(DELETE(role.id), {})
    row = _one(rows, DELETED)
    assert (row["target_id"], row["outcome"]) == (str(role.id), "ok")
    assert str(zora.id) in json.dumps([r["detail"] for r in rows])       # the member it removed is named, by id
    assert role.core_group_id in json.dumps([r["detail"] for r in rows])

    for row in written:
        said = (str(row["target_id"]) + json.dumps(row["detail"], ensure_ascii=False)).lower()
        assert "zwiebel" not in said and "zora" not in said and "akten" not in said, row


_NO_AUDIT = """
CREATE FUNCTION rb34_no_audit() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'audit refused'; END $$;
CREATE TRIGGER rb34_no_audit BEFORE INSERT ON audit_log FOR EACH ROW
WHEN (NEW.action LIKE 'role.%' OR NEW.action = 'user.roles_changed') EXECUTE FUNCTION rb34_no_audit();
"""


@pytest.mark.parametrize("name", ["create", "functions", "add", "remove", "people"])
def test_a_change_whose_audit_row_cannot_be_written_is_not_kept(name, world):
    path, data = _the_forms(world)[name]
    before = world.state()
    world.conn.execute(_NO_AUDIT)
    try:
        status = world.admin.post(path, data).status_code
    except Exception:                                                    # noqa: BLE001 - the error reached the test
        status = None
    assert status is None or status >= 400
    assert world.state() == before
    world.conn.execute("DROP TRIGGER rb34_no_audit ON audit_log")
    assert world.admin.post(path, data).status_code == 200               # with its row, the same change is kept
    assert world.state() != before


# === 11. logs ===========================================================================================================


def test_no_role_or_person_name_reaches_a_log_line(world, caplog):
    caplog.set_level(logging.DEBUG)
    zora = world.person("zora.zwiebel", "member", display_name="Zora Zwiebel")
    post = world.admin.post
    assert post(CONNECT, {}).status_code == 200
    assert post(CREATE, {"name": "Mandat Zwiebel", "description": "Akten Zwiebel",
                         "function": ["approver"]}).status_code == 200
    role, = [r for r in world.store.list_roles() if r.name == "Mandat Zwiebel"]
    assert post(CREATE, {"name": "Mandat Zwiebel"}).status_code == 400
    RecordingCore.refuse = {"create_access_group": "raise", "rename_access_group": "raise",
                            "delete_access_group": "raise"}
    assert post(CREATE, {"name": "Zwiebel Neu"}).status_code == 502
    assert post(RENAME(role.id), {"name": "Mandat Zwiebel II"}).status_code == 502
    RecordingCore.refuse = {}
    assert post(RENAME(role.id), {"name": "Mandat Zwiebel II"}).status_code == 200
    assert post(FUNCTIONS(role.id), {"function": ["approver", "member"]}).status_code == 200
    assert post(FUNCTIONS(role.id), {"function": ["admin"]}).status_code == 400
    assert post(ADD(role.id), {"user_id": str(zora.id)}).status_code == 200
    assert post(PEOPLE_ROLES, {"user_id": str(zora.id), "role_id": [str(world.store.mitglied().id)]}).status_code == 200
    assert post(ADD(role.id), {"user_id": str(zora.id)}).status_code == 200
    world.only_chef_is_an_active_administrator()
    assert post(REMOVE(world.store.administration().id), {"user_id": str(world.chef.id)}).status_code == 400
    world.admin.page(ROLE(role.id))
    world.admin.page(PEOPLE)
    RecordingCore.refuse = {"delete_access_group": "raise"}
    assert post(DELETE(role.id), {}).status_code == 502
    RecordingCore.refuse = {}
    assert post(DELETE(role.id), {}).status_code == 200
    leaked = []
    for record in caplog.records:
        line = record.getMessage()
        if record.exc_info:
            line += logging.Formatter().formatException(record.exc_info)
        if "zwiebel" in line.lower() or "zora" in line.lower():
            leaked.append(line)
    assert leaked == [], leaked
