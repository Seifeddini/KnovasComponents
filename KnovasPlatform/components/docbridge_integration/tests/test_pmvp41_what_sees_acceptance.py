"""PMVP-41 acceptance tests: «Was sieht …?» in Admin (FEAT-2026-11-19-platform-mvp § 4 A-05, spec item
SPEC-platform-mvp-13; work order docs/platform-mvp/work-orders/PMVP-41.md; design docs/platform-mvp/design/role-builder.md
§ 2.2, § 2.3, § 4; RB-5's planning in web_interface/admin_role_data.py; RB-6's claim rule in identity/agents.py).

FROZEN once the coordinator pins it. Written for the coordinator before the build; a builder never edits this file.
Its git blob id goes into docs/platform-mvp/frozen-tests.txt.

An administrator picks a person, a role, the Knovas Assistent (for a person) or an external agent, and asks three
questions: what does X read (the reach), may X read this document (yes or no with the reason), and what does a search
find as X. The Core decides who reads what only once the tenant is ``brokered`` («Ein», CR-R1); before that the
Platform computes what X *would* read under «Ein», from the Core's groups, folder rules and document rights, and says
so («wirkt mit «Ein»»). After «Ein» the yes or no and the search are the Core's own answers, asked as X.

What is pinned (the sections below carry the same numbers):

1. The page and its gates. ``GET /admin/access`` («Zugriff» in the Verwaltung menu) and the three questions
   ``POST /admin/access/reach`` (``wer``, ``fuer``, ``seite``), ``POST /admin/access/check`` (``wer``, ``fuer``,
   ``pointer``) and ``POST /admin/access/search`` (``wer``, ``fuer``, ``query``) sit behind the console's admin gate:
   signed out 302 to the sign-in, every other function 403, each POST checks ``csrf_token`` (400), and a refused
   question asks Knovas nothing and writes no audit row. The page offers people, roles and agents as
   ``person:<id>``, ``role:<id>``, ``agent:<id>`` in the field ``wer`` and people in ``fuer``; opening it writes
   nothing.
2. Whose sight (the claim rule). A person reads through her claim groups (running direct grants and her roles'
   groups, RB-2); a role through its Core group; an agent through RB-6's one claim (its roles' groups, narrowed to
   the person it acts for, never a person's direct grant); the assistant only for a person (``fuer``). A
   deactivated person, a revoked agent and a claim over 32 groups read nothing (``data-reads-nothing``). An unknown
   or malformed subject is refused (400).
3. The reach. Before «Ein» it is computed (``data-effect="vorschau"`` with «wirkt mit «Ein»»): a walled folder rule
   is a row ``data-reads="rule:<rule id>"`` when one of X's groups is one of its groups or above one; a document with
   rights of its own is a row ``data-reads="document:<uuid>"`` on the same rule; a document governed by a rule shows
   through its rule; open folders are no rows. At most 25 rows are drawn; the rest is a count ``data-more`` that
   opens the list page by page (``seite``), every row exactly once. Everything is escaped.
4. One document. ``data-answer="ja"|"nein"`` with the reason (the group that reads it, the rule that gives it).
   Before «Ein» it is computed (``data-source="berechnet"``), whatever Knovas would answer under «Aus»; once the
   client reports ``rbac_posture() == {"mode": "brokered"}`` it is Knovas's own ``document_readable`` asked inside
   ``asserting_for(<the person or the agent's principal>)`` (``data-source="knovas"``). A role has no principal: its
   answer stays computed. An unknown document is refused (400).
5. Search as X. The search runs inside ``asserting_for(X)``; hits are names (``data-hit="<pointer>"``), never passage
   text; at most 25. Before «Ein» each hit carries ``data-would="ja"|"nein"``; after «Ein» the hits are Knovas's
   answer for X and carry no mark. A role does not search (400); a subject that reads nothing sends nothing.
6. Per-thing reads only (L2-PLATFORM-BRAIN-VIEWS POST-03 and B-02, applied here): no unfiltered document listing,
   no whole-graph call, and nothing is written to the Core.
7. Audit. Every answered question writes one row ``access.what_sees`` (target ``user``, ``role`` or ``agent`` and
   the id; ``detail.question`` ``reach`` | ``document`` | ``search``; ``detail.effect``), with ids and counts only.
8. Logs. No pointer, query, title, passage, group or person name, group id or address in any log line, DEBUG
   included (FU-46: the app's own loggers are lowered explicitly).

Nothing reaches a tenant: the Core is a recording fake that answers like the Core's ACL rules.
"""
from __future__ import annotations

import contextlib
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

from identity.agents import AgentStore  # noqa: E402
from identity.roles import RoleStore  # noqa: E402
from identity.users import UserRepository  # noqa: E402

PAGE = "/admin/access"
REACH = "/admin/access/reach"
CHECK = "/admin/access/check"
SEARCH = "/admin/access/search"
EIN_NOTE = "wirkt mit «Ein»"
WHAT_SEES = "access.what_sees"
ACL_FOLDER_NONE = "__acl_folder_none__"
SECRET = "Zwiebelgeheimnis aus dem Ausschnitt"
SUMMARY = "Zwiebelzusammenfassung des Dokuments"

_WRITE = re.compile(r"^(graph_)?(create|rename|move|delete|set|update|put|patch|post|install|deprecate|retire|"
                    r"upload|purge|push|start|stop|requeue|reextract|apply|grant|revoke)(_|$)")
ASKED = ("document_readable", "search_documents", "documents", "document_access")


# --- the Core: a recording fake --------------------------------------------------------------------------------------


class AccessCore(FakeGraphApi):
    """The Core's groups, folder rules and documents as the app sees them through its client.

    A folder rule is a prefix with a set of groups; the longest prefix governs a document unless the document has rights
    of its own. ``asserting_for`` records whose claim a call carries (``user:<id>`` or ``agent:<id>:<person or ''>``).
    ``posture`` is what ``rbac_posture()`` reports: None when the client has no such call (the real client today),
    "raise" when it fails, else the mode. ``document_readable`` answers like the Core: under «Aus» everything, once
    ``brokered`` from ``readable[(principal, pointer)]``. ``search_documents`` returns ``hits_for[principal]`` or
    ``hits``. A whole-tenant read is recorded as ``WHOLE-TENANT`` and fails."""

    calls: list = []
    groups: list = []
    rules: list = []
    docs: list = []
    posture = None
    readable: dict = {}
    hits: list = []
    hits_for: dict = {}
    _principal = None

    def __getattribute__(self, name):
        if name == "rbac_posture" and AccessCore.posture is None:
            raise AttributeError(name)
        attr = object.__getattribute__(self, name)
        if _WRITE.match(name) and callable(attr):
            def recorded(*args, **kwargs):
                AccessCore.calls.append({"call": name, "write": True, "as": AccessCore._as()})
                return attr(*args, **kwargs)
            return recorded
        return attr

    @staticmethod
    def _as():
        principal = AccessCore._principal
        if principal is None:
            return None
        agent = getattr(principal, "agent_id", None)
        if agent is not None:
            return f"agent:{agent}:{getattr(principal, 'on_behalf_of', None) or ''}"
        return f"user:{getattr(principal, 'id', principal)}"

    @staticmethod
    def _record(name, **detail):
        AccessCore.calls.append({"call": name, "as": AccessCore._as(), **detail})

    @contextlib.contextmanager
    def asserting_for(self, user):
        previous = AccessCore._principal
        AccessCore._principal = user
        try:
            yield
        finally:
            AccessCore._principal = previous

    def rbac_posture(self):
        if AccessCore.posture == "raise":
            raise requests.exceptions.ConnectionError("unreachable")
        return {"mode": AccessCore.posture, "backfill_complete": True}

    def access_groups(self):
        self._record("access_groups")
        return [dict(g) for g in AccessCore.groups]

    def folder_rules(self):
        self._record("folder_rules")
        return [dict(r, access_groups=list(r["access_groups"])) for r in AccessCore.rules]

    @staticmethod
    def _governing(pointer):
        covering = [r for r in AccessCore.rules if pointer.startswith(r["pointer_prefix"])]
        return max(covering, key=lambda r: len(r["pointer_prefix"])) if covering else None

    @staticmethod
    def _doc(pointer):
        return next((d for d in AccessCore.docs if d["pointer"] == pointer), None)

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

    def documents(self, after=None, limit=100, prefix=None, group=None, unrestricted=False, conflicts=False,
                  status=None):
        self._record("documents", prefix=prefix, group=group)
        if not prefix and not group:
            self._record("WHOLE-TENANT documents")
            raise RuntimeError("an unfiltered listing reads the whole tenant")
        rows = [self._entry(d) for d in AccessCore.docs if not prefix or d["pointer"].startswith(prefix)]
        if group:
            rows = [r for r in rows if group in r["access_groups"]]
        start = int(after or 0)
        size = max(1, int(limit))
        more = start + size < len(rows)
        return {"documents": rows[start:start + size], "next_after": str(start + size) if more else None,
                "total_count": len(rows)}

    def _whole_tenant(self, *args, **kwargs):
        self._record("WHOLE-TENANT graph")
        raise RuntimeError("a whole-tenant read")

    graph_export = graph_nodes = graph_edges = iter_documents = _whole_tenant

    def document_access(self, pointer):
        self._record("document_access", pointer=pointer)
        doc = self._doc(pointer)
        return None if doc is None else {"pointer": pointer, "access_groups": self._effective(doc), "acl_epoch": 1}

    def document_readable(self, pointer):
        self._record("document_readable", pointer=pointer)
        if AccessCore.posture != "brokered":
            return True                                                   # «Aus»: the Core hides nothing
        return AccessCore.readable.get((self._as(), pointer), False)

    def search_documents(self, query, limit=20, filters=None, *, where=None, return_fields=None):
        self._record("search_documents", query=query)
        rows = AccessCore.hits_for.get(self._as(), AccessCore.hits)
        return {"results": [dict(r) for r in rows][: max(int(limit), 0)], "total": len(rows)}


def _asked(name=None):
    return [c for c in AccessCore.calls if c["call"] in ASKED and (name is None or c["call"] == name)]


def _hit(pointer):
    return {"doc_id": pointer, "path": pointer, "title": pointer.rsplit("/", 1)[-1], "score": 0.8,
            "snippet": SECRET, "ingested_summary": SUMMARY,
            "top_chunks": [{"chunk_uuid": "c-1", "page_number": 1, "snippet": SECRET, "text": SECRET}]}


# --- the world: the real app, an administrator signed in ---------------------------------------------------------------


class Browser:
    def __init__(self, app, email):
        self.client = app.test_client()
        page = self.client.get("/login")
        self.client.post("/login", data={"login_name": email, "password": conftest.PASSWORD,
                                         "csrf_token": conftest._csrf_from(page.data.decode("utf-8"))})
        with self.client.session_transaction() as sess:
            self.token = sess.get("csrf_token")

    def page(self, path):
        response = self.client.get(path)
        assert response.status_code == 200, f"{path} answered {response.status_code}"
        return response.data.decode("utf-8")

    def post(self, path, data=None, *, token=True):
        fields = dict(data or {})
        if token is True:
            fields["csrf_token"] = self.token
        elif token:
            fields["csrf_token"] = token
        return self.client.post(path, data=fields)


def _text(response):
    return response.data.decode("utf-8")


def _values(html, attribute):
    return [htmllib.unescape(v) for v in re.findall(rf'\s{attribute}="([^"]*)"', html)]


def _reads(html):
    return set(_values(html, "data-reads"))


def _more(html):
    found = _values(html, "data-more")
    return int(found[0]) if found else 0


class World:
    def __init__(self, conn, app):
        self.conn, self.app = conn, app
        self.repo = UserRepository(conn)
        self.store = RoleStore(conn)
        self.agents = AgentStore(conn)
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

    def role(self, name, *, group=None, members=()):
        role = self.store.create_role(name=name, functions=set(), core_group_id=group)
        for person in members:
            self.store.add_member(role.id, person.id)
        return self.store.get_role(role.id)

    def browser(self, person):
        return Browser(self.app, person.email)

    def audit(self, action=WHAT_SEES):
        rows = self.conn.execute(
            "SELECT target_type, target_id, outcome, actor_user_id::text, detail FROM audit_log WHERE action = %s "
            "ORDER BY id", (action,)).fetchall()
        return [dict(zip(("target_type", "target_id", "outcome", "actor", "detail"), row)) for row in rows]


@pytest.fixture
def world(platform_db, tmp_path, monkeypatch):
    AccessCore.calls = []
    AccessCore.groups = [{"group_id": "g-admins", "name": "Administratoren", "parent_id": None, "is_admin": True}]
    AccessCore.rules = []
    AccessCore.docs = []
    AccessCore.posture = None
    AccessCore.readable = {}
    AccessCore.hits = []
    AccessCore.hits_for = {}
    AccessCore._principal = None
    app = conftest._identity_app(platform_db, tmp_path, monkeypatch, client_cls=AccessCore)
    return World(platform_db, app)


class Scene:
    """A firm's tree, rules, documents, people, roles and agents.

    Groups: Bauabteilung, Steuerrecht, Partner with Mandatsteam below it, Leer. Rules: Steuer (Steuerrecht), Team
    (Mandatsteam), Offen (no group: open), Baurecht (Bauabteilung and Steuerrecht), NurBau (Bauabteilung). Documents:
    one under each rule, one with rights of its own (Bauabteilung) inside the Steuer folder, one in a folder without a
    rule (open), and three with rights of their own in Akten (Steuerrecht, Bauabteilung, Mandatsteam).
    People: anna and bert in the role Bau (Bauabteilung); bert also holds Partner directly; cora holds Steuerrecht
    directly; dora holds nothing; erik is in Bau and deactivated. The role Steuer (Steuerrecht) has no person.
    Agents: the Knovas Assistent (a member of every role but Administration); «Steuer-Bot» on its own in Steuer;
    «Dora-Bot» bound to dora, in Bau; «Alt-Bot» in Bau, revoked.
    """

    def __init__(self, world):
        AccessCore.groups += [
            {"group_id": "g-bau", "name": "Bauabteilung", "parent_id": None},
            {"group_id": "g-steuer", "name": "Steuerrecht", "parent_id": None},
            {"group_id": "g-partner", "name": "Partner", "parent_id": None},
            {"group_id": "g-team", "name": "Mandatsteam", "parent_id": "g-partner"},
            {"group_id": "g-leer", "name": "Leer", "parent_id": None},
        ]
        AccessCore.rules += [
            {"rule_id": "r-steuer", "pointer_prefix": "DMS/Steuer/", "access_groups": ["g-steuer"]},
            {"rule_id": "r-team", "pointer_prefix": "DMS/Team/", "access_groups": ["g-team"]},
            {"rule_id": "r-offen", "pointer_prefix": "DMS/Offen/", "access_groups": []},
            {"rule_id": "r-bau2", "pointer_prefix": "DMS/Baurecht/", "access_groups": ["g-bau", "g-steuer"]},
            {"rule_id": "r-nurbau", "pointer_prefix": "DMS/NurBau/", "access_groups": ["g-bau"]},
        ]
        AccessCore.docs += [
            {"pointer": "DMS/Steuer/a.pdf", "uuid": "u-steuer-a", "own": None},
            {"pointer": "DMS/Steuer/eigen.pdf", "uuid": "u-steuer-eigen", "own": ["g-bau"]},
            {"pointer": "DMS/Team/t.pdf", "uuid": "u-team-t", "own": None},
            {"pointer": "DMS/Offen/x.pdf", "uuid": "u-offen-x", "own": None},
            {"pointer": "DMS/NurBau/b.pdf", "uuid": "u-nurbau-b", "own": None},
            {"pointer": "DMS/Baurecht/c.pdf", "uuid": "u-baurecht-c", "own": None},
            {"pointer": "DMS/Frei/f.pdf", "uuid": "u-frei-f", "own": None},
            {"pointer": "DMS/Akten/steuer.pdf", "uuid": "u-own-steuer", "own": ["g-steuer"]},
            {"pointer": "DMS/Akten/bau.pdf", "uuid": "u-own-bau", "own": ["g-bau"]},
            {"pointer": "DMS/Akten/team.pdf", "uuid": "u-own-team", "own": ["g-team"]},
        ]
        self.anna = world.person("anna", "member", display_name="Anna Ast")
        self.bert = world.person("bert", "member", display_name="Bert Berg", groups=["g-partner"])
        self.cora = world.person("cora", "member", display_name="Cora Clau", groups=["g-steuer"])
        self.dora = world.person("dora", "member", display_name="Dora Dorn")
        self.erik = world.person("erik", "member", display_name="Erik Eich")
        self.bau = world.role("Bau", group="g-bau", members=[self.anna, self.bert, self.erik])
        self.steuer = world.role("Steuer", group="g-steuer")
        world.repo.disable(self.erik.id)
        agents = world.agents
        self.assistant = agents.assistant()
        self.bot = agents.create_agent(name="Steuer-Bot", by=world.chef.id)
        agents.add_to_role(self.steuer.id, self.bot.id, by=world.chef.id)
        self.dora_bot = agents.create_agent(name="Dora-Bot", on_behalf_of=self.dora.id, by=world.chef.id)
        agents.add_to_role(self.bau.id, self.dora_bot.id, by=world.chef.id)
        self.old_bot = agents.create_agent(name="Alt-Bot", by=world.chef.id)
        agents.add_to_role(self.bau.id, self.old_bot.id, by=world.chef.id)
        agents.revoke_agent(self.old_bot.id, by=world.chef.id)
        AccessCore.calls.clear()


@pytest.fixture
def scene(world):
    return Scene(world)


def person(p):
    return f"person:{p.id}"


def role(r):
    return f"role:{r.id}"


def agent(a):
    return f"agent:{a.id}"


ANNA_READS = {"rule:r-bau2", "rule:r-nurbau", "document:u-own-bau", "document:u-steuer-eigen"}
CORA_READS = {"rule:r-steuer", "rule:r-bau2", "document:u-own-steuer"}


def _reach(world, wer, fuer="", **extra):
    response = world.admin.post(REACH, {"wer": wer, "fuer": fuer, **extra})
    assert response.status_code == 200, (wer, response.status_code)
    return _text(response)


def _check(world, wer, pointer, fuer=""):
    response = world.admin.post(CHECK, {"wer": wer, "fuer": fuer, "pointer": pointer})
    assert response.status_code == 200, (wer, pointer, response.status_code)
    html = _text(response)
    answers = _values(html, "data-answer")
    assert len(answers) == 1, answers
    return answers[0], html


def _search(world, wer, query="Frist", fuer=""):
    response = world.admin.post(SEARCH, {"wer": wer, "fuer": fuer, "query": query})
    assert response.status_code == 200, (wer, response.status_code)
    return _text(response)


# === 1. the page and its gates =========================================================================================


ROUTES = (("GET", PAGE), ("POST", REACH), ("POST", CHECK), ("POST", SEARCH))


def test_the_page_and_the_three_questions_admit_administrators_only(world):
    adapter = world.app.url_map.bind("localhost")
    for method, path in ROUTES:
        endpoint, _ = adapter.match(path, method=method)
        view = world.app.view_functions[endpoint]
        assert getattr(view, "allowed_roles", None) == frozenset({"admin"}), (method, path)


def test_signed_out_is_sent_to_the_sign_in_and_nothing_is_asked(world, scene):
    anonymous = world.app.test_client()
    for method, path in ROUTES:
        response = anonymous.open(path, method=method,
                                  data={"wer": person(scene.cora), "pointer": "DMS/Steuer/a.pdf", "query": "Frist"})
        assert response.status_code == 302, (method, path, response.status_code)
        assert "/login" in response.headers["Location"]
    assert _asked() == [] and world.audit() == []


def test_a_member_and_an_approver_are_refused_and_nothing_is_asked(world, scene):
    paula = world.person("paula", "approver")
    for someone in (scene.anna, paula):
        browser = world.browser(someone)
        assert browser.client.get(PAGE).status_code == 403
        for path in (REACH, CHECK, SEARCH):
            response = browser.post(path, {"wer": person(scene.cora), "pointer": "DMS/Steuer/a.pdf",
                                           "query": "Frist"})
            assert response.status_code == 403, (path, response.status_code)
    assert _asked() == [] and world.audit() == []


def test_each_question_checks_the_form_token(world, scene):
    for path in (REACH, CHECK, SEARCH):
        for token in (None, "falsch"):
            response = world.admin.post(path, {"wer": person(scene.cora), "pointer": "DMS/Steuer/a.pdf",
                                               "query": "Frist"}, token=token)
            assert response.status_code == 400, (path, token, response.status_code)
    assert _asked() == [] and world.audit() == []


def test_the_menu_offers_zugriff_to_administrators_only(world, scene):
    html = world.admin.page("/admin/people")
    assert re.search(r'href="/admin/access"[^>]*>\s*Zugriff\s*<', html), "no «Zugriff» in the Verwaltung menu"
    member = world.browser(scene.anna).client.get("/")
    assert member.status_code == 200
    assert 'href="/admin/access"' not in _text(member)


def test_the_page_offers_people_roles_and_agents_and_writes_nothing(world, scene):
    html = world.admin.page(PAGE)
    offered = set(_values(html, "value"))
    for wanted in (person(scene.anna), person(scene.cora), role(scene.bau), role(scene.steuer),
                   agent(scene.assistant), agent(scene.bot), agent(scene.dora_bot)):
        assert wanted in offered, wanted
    assert re.search(r'<select[^>]*name="wer"', html) and re.search(r'<select[^>]*name="fuer"', html)
    assert str(scene.dora.id) in offered                                 # a person to act for («fuer»)
    for path in (REACH, CHECK, SEARCH):
        assert f'action="{path}"' in html, path
    assert [c for c in AccessCore.calls if c.get("write")] == []
    assert _values(html, "data-reads") == [] and world.audit() == []


# === 2. whose sight: the claim rule ====================================================================================


def test_a_person_reads_through_direct_grants_roles_and_groups_above(world, scene):
    html = _reach(world, person(scene.bert))
    assert _reads(html) == ANNA_READS | {"rule:r-team", "document:u-own-team"}
    assert _values(html, "data-what-sees") == [person(scene.bert)]
    assert _values(html, "data-effect") == ["vorschau"] and EIN_NOTE in html
    # a document governed by a rule shows through its rule; an open folder is no row
    assert not _reads(html) & {"document:u-team-t", "document:u-baurecht-c", "rule:r-offen", "document:u-frei-f"}
    assert _reads(_reach(world, person(scene.cora))) == CORA_READS


def test_a_person_without_groups_reads_only_what_is_open(world, scene):
    html = _reach(world, person(scene.dora))
    assert _reads(html) == set()
    assert _values(html, "data-reads-nothing") == []


def test_a_role_reads_what_its_group_reads(world, scene):
    assert _reads(_reach(world, role(scene.bau))) == ANNA_READS
    assert _reads(_reach(world, role(scene.steuer))) == CORA_READS


def test_the_assistant_reads_its_roles_narrowed_to_the_person_and_never_a_direct_grant(world, scene):
    html = _reach(world, agent(scene.assistant), fuer=str(scene.bert.id))
    assert _reads(html) == ANNA_READS                                  # bert's direct Partner reaches no agent
    assert _reads(_reach(world, agent(scene.assistant), fuer=str(scene.cora.id))) == CORA_READS


def test_the_assistant_is_asked_about_for_a_person_only(world, scene):
    for fuer in ("", "keine-id", str(uuid.uuid4())):
        response = world.admin.post(REACH, {"wer": agent(scene.assistant), "fuer": fuer})
        assert response.status_code == 400, (fuer, response.status_code)
    assert _asked() == [] and world.audit() == []


def test_an_external_agent_reads_its_roles_and_no_more_than_its_person(world, scene):
    assert _reads(_reach(world, agent(scene.bot))) == CORA_READS
    assert _reads(_reach(world, agent(scene.dora_bot))) == set()       # narrowed to dora, who holds nothing


def test_a_revoked_agent_a_deactivated_person_and_a_claim_over_32_groups_read_nothing(world, scene):
    many = world.person("viele", "member")
    for n in range(33):
        world.conn.execute("INSERT INTO user_access_groups (user_id, group_id) VALUES (%s, %s)",
                           (str(many.id), f"g-viele-{n:02d}"))
    for wer in (agent(scene.old_bot), person(scene.erik), person(many)):
        html = _reach(world, wer)
        assert _values(html, "data-reads-nothing"), wer
        assert _reads(html) == set(), wer
        answer, _ = _check(world, wer, "DMS/Offen/x.pdf")
        assert answer == "nein", wer                                   # not even what is open: no claim is made


def test_an_unknown_or_malformed_subject_is_refused(world, scene):
    for wer in ("", "person:", "person:keine-id", f"person:{uuid.uuid4()}", f"role:{uuid.uuid4()}",
                f"agent:{uuid.uuid4()}", f"gruppe:{scene.anna.id}", str(scene.anna.id)):
        for path in (REACH, CHECK, SEARCH):
            response = world.admin.post(path, {"wer": wer, "pointer": "DMS/Steuer/a.pdf", "query": "Frist"})
            assert response.status_code == 400, (wer, path, response.status_code)
    assert _asked() == [] and world.audit() == []


# === 3. the reach: rows, the cap, escaping =============================================================================


def test_at_most_25_rows_and_the_rest_is_a_count_that_opens_the_list(world, scene):
    AccessCore.docs += [{"pointer": f"DMS/Akten/s-{n:02d}.pdf", "uuid": f"u-s-{n:02d}", "own": ["g-steuer"]}
                        for n in range(60)]
    wanted = CORA_READS | {f"document:u-s-{n:02d}" for n in range(60)}
    html = _reach(world, person(scene.cora))
    first = _values(html, "data-reads")
    assert 0 < len(first) <= 25
    assert _more(html) == len(wanted) - len(first)
    seen, page, rest = list(first), 1, _more(html)
    while rest:
        page += 1
        assert page < 10, "the list never ends"
        html = _reach(world, person(scene.cora), seite=str(page))
        rows = _values(html, "data-reads")
        assert 0 < len(rows) <= 50, (page, len(rows))
        seen += rows
        rest = _more(html)
    assert len(seen) == len(set(seen)) and set(seen) == wanted


def test_prefixes_pointers_and_names_are_escaped(world, scene):
    AccessCore.rules.append({"rule_id": "r-x", "pointer_prefix": "DMS/<b>Fett</b>/", "access_groups": ["g-steuer"]})
    AccessCore.docs.append({"pointer": "DMS/Akten/<i>kursiv</i>.pdf", "uuid": "u-x", "own": ["g-steuer"]})
    AccessCore.groups.append({"group_id": "g-x", "name": "<u>Gruppe</u>", "parent_id": None})
    AccessCore.docs.append({"pointer": "DMS/Akten/<s>weg</s>.pdf", "uuid": "u-y", "own": ["g-x"]})
    html = _reach(world, person(scene.cora))
    assert {"rule:r-x", "document:u-x"} <= _reads(html)
    assert "<b>Fett</b>" not in html and "<i>kursiv</i>" not in html
    _, html = _check(world, person(scene.dora), "DMS/Akten/<s>weg</s>.pdf")
    assert "<u>Gruppe</u>" not in html and "<s>weg</s>" not in html


# === 4. one document ===================================================================================================


def test_before_ein_the_answer_is_computed_and_says_so(world, scene):
    answer, html = _check(world, person(scene.dora), "DMS/Steuer/a.pdf")
    assert answer == "nein"                                            # under «Aus» Knovas would say yes
    assert _values(html, "data-source") == ["berechnet"] and EIN_NOTE in html
    # the reason: the rule that governs it, and the group whose holders (and holders above) read it, by name
    assert _values(html, "data-reason-rule") == ["r-steuer"]
    assert _values(html, "data-reason-group") == ["g-steuer"] and "Steuerrecht" in html
    answer, html = _check(world, person(scene.cora), "DMS/Steuer/a.pdf")
    assert answer == "ja" and _values(html, "data-reason-rule") == ["r-steuer"]
    assert _values(html, "data-what-sees") == [person(scene.cora)]
    answer, html = _check(world, person(scene.cora), "DMS/Steuer/eigen.pdf")     # rights of its own: no rule
    assert answer == "nein" and _values(html, "data-reason-rule") == []
    assert _values(html, "data-reason-group") == ["g-bau"] and "Bauabteilung" in html
    answer, html = _check(world, person(scene.dora), "DMS/Offen/x.pdf")          # open: no group to name
    assert answer == "ja" and _values(html, "data-reason-group") == []


def test_open_documents_groups_above_and_rights_of_its_own(world, scene):
    assert _check(world, person(scene.dora), "DMS/Offen/x.pdf")[0] == "ja"           # a rule without a group
    assert _check(world, person(scene.dora), "DMS/Frei/f.pdf")[0] == "ja"            # no rule at all
    assert _check(world, person(scene.bert), "DMS/Team/t.pdf")[0] == "ja"            # Partner is above the team
    assert _check(world, person(scene.anna), "DMS/Team/t.pdf")[0] == "nein"
    assert _check(world, person(scene.anna), "DMS/Steuer/eigen.pdf")[0] == "ja"      # its own rights decide
    assert _check(world, person(scene.cora), "DMS/Steuer/eigen.pdf")[0] == "nein"
    assert _check(world, agent(scene.assistant), "DMS/Team/t.pdf", fuer=str(scene.bert.id))[0] == "nein"
    assert _check(world, agent(scene.bot), "DMS/Baurecht/c.pdf")[0] == "ja"
    assert _check(world, role(scene.bau), "DMS/NurBau/b.pdf")[0] == "ja"
    assert _check(world, role(scene.steuer), "DMS/NurBau/b.pdf")[0] == "nein"


def test_an_unknown_or_empty_document_is_refused(world, scene):
    for pointer in ("DMS/Steuer/fehlt.pdf", "", "   "):
        response = world.admin.post(CHECK, {"wer": person(scene.cora), "pointer": pointer})
        assert response.status_code == 400, (pointer, response.status_code)
    assert world.audit() == []


def test_under_ein_the_answer_is_knovas_own_asked_as_that_person(world, scene):
    AccessCore.posture = "brokered"
    AccessCore.readable = {(f"user:{scene.dora.id}", "DMS/Steuer/a.pdf"): True}     # Knovas differs: it counts
    answer, html = _check(world, person(scene.dora), "DMS/Steuer/a.pdf")
    assert answer == "ja" and _values(html, "data-source") == ["knovas"]
    assert _values(html, "data-effect") == ["ein"] and EIN_NOTE not in html
    assert [c["as"] for c in _asked("document_readable")] == [f"user:{scene.dora.id}"]
    answer, _ = _check(world, person(scene.cora), "DMS/Steuer/a.pdf")
    assert answer == "nein"                                            # computed yes, Knovas no: Knovas counts


def test_under_ein_an_agent_is_asked_with_its_own_claim(world, scene):
    AccessCore.posture = "brokered"
    AccessCore.readable = {(f"agent:{scene.assistant.id}:{scene.bert.id}", "DMS/NurBau/b.pdf"): True,
                           (f"agent:{scene.bot.id}:", "DMS/Steuer/a.pdf"): True}
    assert _check(world, agent(scene.assistant), "DMS/NurBau/b.pdf", fuer=str(scene.bert.id))[0] == "ja"
    assert _check(world, agent(scene.bot), "DMS/Steuer/a.pdf")[0] == "ja"
    assert [c["as"] for c in _asked("document_readable")] == [f"agent:{scene.assistant.id}:{scene.bert.id}",
                                                              f"agent:{scene.bot.id}:"]


def test_under_ein_a_role_has_no_principal_and_stays_computed(world, scene):
    AccessCore.posture = "brokered"
    answer, html = _check(world, role(scene.bau), "DMS/NurBau/b.pdf")
    assert answer == "ja" and _values(html, "data-source") == ["berechnet"]
    assert _asked("document_readable") == []


def test_any_posture_but_brokered_keeps_the_preview(world, scene):
    for posture in (None, "raise", "disabled", "enforcing", "probe"):
        AccessCore.posture = posture
        answer, html = _check(world, person(scene.dora), "DMS/Steuer/a.pdf")
        assert answer == "nein", posture
        assert _values(html, "data-effect") == ["vorschau"] and EIN_NOTE in html, posture
        assert _values(_reach(world, person(scene.dora)), "data-effect") == ["vorschau"], posture


# === 5. search as X ====================================================================================================


def test_before_ein_the_search_runs_as_the_person_and_marks_each_hit(world, scene):
    AccessCore.hits = [_hit("DMS/Steuer/a.pdf"), _hit("DMS/NurBau/b.pdf"), _hit("DMS/Offen/x.pdf")]
    html = _search(world, person(scene.cora))
    assert [c["as"] for c in _asked("search_documents")] == [f"user:{scene.cora.id}"]
    assert _values(html, "data-hit") == ["DMS/Steuer/a.pdf", "DMS/NurBau/b.pdf", "DMS/Offen/x.pdf"]
    assert _values(html, "data-would") == ["ja", "nein", "ja"]
    assert EIN_NOTE in html
    assert SECRET not in html and SUMMARY not in html                  # names only, never passage text


def test_under_ein_the_hits_are_knovas_answer_for_the_person(world, scene):
    AccessCore.posture = "brokered"
    AccessCore.hits = [_hit("DMS/Steuer/a.pdf"), _hit("DMS/NurBau/b.pdf")]
    AccessCore.hits_for = {f"user:{scene.cora.id}": [_hit("DMS/Steuer/a.pdf"), _hit("DMS/Baurecht/c.pdf")]}
    html = _search(world, person(scene.cora))
    assert _values(html, "data-hit") == ["DMS/Steuer/a.pdf", "DMS/Baurecht/c.pdf"]
    assert _values(html, "data-would") == [] and EIN_NOTE not in html
    assert SECRET not in html and SUMMARY not in html


def test_the_assistant_searches_with_its_claim_for_the_person(world, scene):
    AccessCore.hits = [_hit("DMS/NurBau/b.pdf"), _hit("DMS/Team/t.pdf")]
    html = _search(world, agent(scene.assistant), fuer=str(scene.bert.id))
    assert [c["as"] for c in _asked("search_documents")] == [f"agent:{scene.assistant.id}:{scene.bert.id}"]
    assert _values(html, "data-would") == ["ja", "nein"]


def test_a_role_does_not_search_and_a_subject_that_reads_nothing_sends_nothing(world, scene):
    AccessCore.hits = [_hit("DMS/Offen/x.pdf")]
    response = world.admin.post(SEARCH, {"wer": role(scene.bau), "query": "Frist"})
    assert response.status_code == 400
    for wer in (agent(scene.old_bot), person(scene.erik)):
        html = _search(world, wer)
        assert _values(html, "data-reads-nothing") and _values(html, "data-hit") == [], wer
    assert _asked("search_documents") == []


def test_at_most_25_hits_are_shown(world, scene):
    AccessCore.hits = [_hit(f"DMS/Offen/n-{n:02d}.pdf") for n in range(40)]
    AccessCore.docs += [{"pointer": f"DMS/Offen/n-{n:02d}.pdf", "uuid": f"u-n-{n:02d}", "own": None}
                        for n in range(40)]
    html = _search(world, person(scene.cora))
    assert 0 < len(_values(html, "data-hit")) <= 25


# === 6. per-thing reads only, nothing written ==========================================================================


def test_no_question_reads_the_whole_tenant_or_writes(world, scene):
    AccessCore.hits = [_hit("DMS/Steuer/a.pdf"), _hit("DMS/Akten/team.pdf")]
    for posture in (None, "brokered"):
        AccessCore.posture = posture
        for wer, fuer in ((person(scene.bert), ""), (agent(scene.assistant), str(scene.bert.id)),
                          (agent(scene.bot), "")):
            _reach(world, wer, fuer)
            _check(world, wer, "DMS/Team/t.pdf", fuer)
            _search(world, wer, fuer=fuer)
        _reach(world, role(scene.bau))
    world.admin.page(PAGE)
    assert [c for c in AccessCore.calls if c["call"].startswith("WHOLE-TENANT")] == []
    assert [c for c in AccessCore.calls if c.get("write")] == []
    assert all(c.get("group") or c.get("prefix") for c in _asked("documents"))


# === 7. audit ==========================================================================================================


def test_every_answered_question_is_audited_by_id_without_customer_text(world, scene):
    AccessCore.hits = [_hit("DMS/Steuer/a.pdf")]
    _reach(world, person(scene.bert))
    _check(world, role(scene.bau), "DMS/NurBau/b.pdf")
    _search(world, agent(scene.assistant), "Kündigungsfrist Zwiebel", fuer=str(scene.bert.id))
    rows = world.audit()
    assert [(r["target_type"], r["target_id"], r["detail"]["question"]) for r in rows] == [
        ("user", str(scene.bert.id), "reach"), ("role", str(scene.bau.id), "document"),
        ("agent", str(scene.assistant.id), "search")]
    assert all(r["actor"] == str(world.chef.id) and r["detail"]["effect"] == "vorschau" for r in rows)
    dump = json.dumps([[r["target_id"], r["detail"]] for r in rows], ensure_ascii=False)
    for text in ("DMS/", "Kündigungsfrist", "Zwiebel", "Bert", "Anna", "Bauabteilung", "Steuerrecht", "@",
                 "g-bau", "g-steuer", "g-partner", "a.pdf", "b.pdf"):
        assert text not in dump, text


# === 8. logs ===========================================================================================================


def test_no_customer_text_reaches_a_log_line(world, scene, caplog):
    AccessCore.groups.append({"group_id": "g-zwiebel", "name": "Zwiebelgruppe", "parent_id": None})
    AccessCore.rules.append({"rule_id": "r-z", "pointer_prefix": "Mandate/Zwiebel/", "access_groups": ["g-zwiebel"]})
    AccessCore.docs.append({"pointer": "Mandate/Zwiebel/Zwiebel_Brief.pdf", "uuid": "u-z", "own": None})
    zora = world.person("zora.zwiebel", "member", display_name="Zora Zwiebel", groups=["g-zwiebel"])
    zbot = world.agents.create_agent(name="Zwiebel-Bot", on_behalf_of=zora.id, by=world.chef.id)
    AccessCore.hits = [_hit("Mandate/Zwiebel/Zwiebel_Brief.pdf")]
    caplog.clear()
    # The app holds its own loggers at LOG_LEVEL (INFO in tests): lower them too, or a DEBUG line is never made.
    for name in (None, "web_interface", "identity", "knovas_client", "builder", "brain_views", "urllib3"):
        caplog.set_level(logging.DEBUG, logger=name)

    for posture in (None, "brokered"):
        AccessCore.posture = posture
        for wer, fuer in ((person(zora), ""), (agent(world.agents.assistant()), str(zora.id)), (agent(zbot), "")):
            _reach(world, wer, fuer)
            _check(world, wer, "Mandate/Zwiebel/Zwiebel_Brief.pdf", fuer)
            _search(world, wer, "Zwiebel Kündigungsfrist", fuer=fuer)
        world.admin.post(CHECK, {"wer": person(zora), "pointer": "Mandate/Zwiebel/fehlt_Zwiebel.pdf"})
        world.admin.page(PAGE)

    leaked = []
    for record in caplog.records:
        line = record.getMessage()
        if record.exc_info:
            line += logging.Formatter().formatException(record.exc_info)
        if any(text in line.lower() for text in ("zwiebel", "mandate/", "zora", "g-zwiebel", "@kanzlei.ch",
                                                 "kündigungsfrist", "zwiebelgeheimnis")):
            leaked.append((record.name, record.levelname, line[:200]))
    assert leaked == []
