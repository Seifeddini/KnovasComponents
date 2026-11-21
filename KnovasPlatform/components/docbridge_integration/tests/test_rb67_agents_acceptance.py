"""RB-6 and RB-7 acceptance tests: one claim for every agent, the Knovas Assistent as a member of roles, external
agents as members of roles, and the delivery of their signed claims to the Core's MCP gateway (FEAT-2026-11-19-
platform-mvp; design docs/platform-mvp/design/role-builder.md § 2.3, § 2.4, § 4, § 5, § 6, § 7, § 9; Core requests
CR-R5 and CR-R6 in docs/platform-mvp/core-requests.md; work order docs/platform-mvp/work-orders/RB-6-7.md).

FROZEN. Written for the coordinator before the build; a builder never edits this file. Its git blob id is in
docs/platform-mvp/frozen-tests.txt.

The founder decided on 2026-11-21: "Core gateway only" (the Platform has no MCP endpoint; external agents use the
Core's gateway at mcp.knovas.ch) and "only after «Ein»" on client tenants. So the Platform holds no agent credential:
an agent's key is the Core gateway's, bound to the agent's id (CR-R6). The Platform makes the agent a member of roles,
signs its claims and hands them to the Core ahead of use (CR-R5).

What is pinned:

1. Migration ``0028_agents`` adds new tables only, among them ``agents`` and ``tenant_role_agents``; none of its tables
   has a column for a key, secret, hash or token. Every table that existed before keeps its shape.
2. The Knovas Assistent is exactly one agent (kind ``assistant``). After the migration it is a member of every role but
   Administration; every role created later has it too; an administrator may take it out of a role on the page. No
   agent ever joins Administration.
3. One claim for every agent (RB-6): subject = the agent's id; groups and functions = those of its roles; acting for a
   person, only what that person also holds (a person's direct group grants reach no agent, FU-41). No role, no group.
   Never more than 32 groups: adding an agent acting on its own to a 33rd group is refused, nothing written, and no
   claim is made for an agent that would carry more.
4. The assistant searches with its own claim, narrowed to the signed-in person, on every Core call of an answer, and
   declares itself as the actor with the person as ``on_behalf_of``.
5. Delivery (RB-7), behind ``AGENTS_CORE_GATEWAY`` (off by default; off, nothing reaches the Core): for every live
   external agent ``PUT /secured/agents/<agent id>/claims`` with exactly ``{"claims": [...]}``, exactly 20 claims (the Core's maximum) signed
   by the Platform's broker key, each with the agent's id as subject, its groups and functions, its own ``jti`` and a
   lifetime of at most 300 seconds, and no person's assertion; for a revoked agent, an agent whose person is not
   active and an agent over 32 groups ``DELETE /secured/agents/<agent id>/claims`` and never a ``PUT``. The assistant
   is never delivered (it goes through the Platform). ``app.extensions["agents"]["sync"]()`` delivers now (the periodic
   job calls the same); creating, revoking and a role change on the agent pages deliver at once. A Core that refuses
   or lacks the route never breaks the Platform.
6. The console's pages: ``GET /admin/agents``, ``POST /admin/agents/create`` (``name``, ``on_behalf_of``),
   ``POST /admin/agents/<id>/revoke``, ``POST /admin/roles/<id>/agents/add`` and ``…/agents/remove`` (``agent_id``),
   behind the admin gate and the form's ``csrf_token``. Creating names the agent's id (for the gateway key Knovas
   issues). ``agent.created`` and ``agent.revoked`` are audited by id.
7. No claim, agent name, person name or question reaches a log line, DEBUG included.

The interface (``identity.agents``): ``AgentStore(conn)`` with ``assistant``, ``list_agents``, ``get_agent``,
``create_agent(*, name, on_behalf_of=None, by=None) -> Agent``, ``revoke_agent``, ``add_to_role``,
``remove_from_role``, ``roles_of_agent``, ``claim_for(agent_id, *, on_behalf_of=None) -> AgentClaim``; ``Agent``
(``id``, ``name``, ``kind``, ``on_behalf_of``, ``roles``, ``revoked``), ``AgentClaim`` (``subject``, ``groups``,
``functions``, ``on_behalf_of``); ``AgentError`` and below it ``AgentNotFound``, ``AgentNameTaken``,
``ProtectedAgentError``. Refusals about roles and groups are RB-1's ``ProtectedRoleError`` and ``TooManyGroupsError``.

Nothing here reaches a tenant: the Core is the Platform's own ``KnovasAPIClient`` whose wire is a recording fake that
answers like the Core (and like a Core with CR-R5), and the Knovas Connector is a recording fake.
"""
from __future__ import annotations

import json
import logging
import uuid
from urllib.parse import urlsplit

import pytest
import requests

import conftest
from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

pytestmark = pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")

from identity import migrate  # noqa: E402
from identity.agents import (  # noqa: E402
    AgentError,
    AgentNameTaken,
    AgentNotFound,
    AgentStore,
    ProtectedAgentError,
)
from identity.assertion import AssertionVerifier  # noqa: E402
from identity.broker_key import public_pem  # noqa: E402
from identity.roles import MAX_CLAIM_GROUPS, ProtectedRoleError, RoleError, RoleStore, TooManyGroupsError  # noqa: E402
from identity.users import UserRepository  # noqa: E402
from knovas_client import ASSERTION_FIELD, KnovasAPIClient  # noqa: E402

TENANT = "tenant-a"
CORE = "https://core.test"
QUESTION = "Welche Kuendigungsfrist gilt fuer Zwiebel?"
PASSAGE = "Die Kuendigungsfrist fuer Zwiebel betraegt drei Monate zum Monatsende."
HIT = {
    "pointer": "Mandate/Zwiebel/mietvertrag.pdf",
    "title": "Mietvertrag Zwiebel",
    "cosine_similarity": 0.81,
    "snippet": PASSAGE,
    "text": PASSAGE,
    "top_chunks": [{"chunk_uuid": "c-1", "page_number": 2, "sentence_number": 14, "cosine_similarity": 0.81,
                    "snippet": PASSAGE}],
}
BEFORE_AGENTS = "0027_roles"
SUCCESS = (200, 302, 303)


# --- the Core on the wire: the Platform's own client, a recording fake behind it ----------------------------------------


def _response(status, payload, url):
    resp = requests.Response()
    resp.status_code = status
    resp.reason = "test"
    resp.url = url
    resp._content = b"" if payload is None else json.dumps(payload).encode("utf-8")
    resp.headers["Content-Type"] = "application/json"
    return resp


class Wire:
    """Every request the Platform's client sends, recorded; answered the way the Core (with CR-R5) answers."""

    def __init__(self):
        self.sent = []
        self.agents_status = 204        # what PUT and DELETE /secured/agents/<id>/claims answer

    def request(self, method=None, url=None, json=None, params=None, headers=None, **_kwargs):
        path = urlsplit(url).path
        method = str(method).upper()
        self.sent.append({"method": method, "path": path, "json": json, "params": params})
        if path.startswith("/secured/agents/") and path.endswith("/claims") and method in ("PUT", "DELETE"):
            body = None if self.agents_status == 204 else {"status": "error", "error_code": "not_found"}
            return _response(self.agents_status, body, url)
        if method == "POST" and path == "/secured/query":
            return _response(200, {"status": "success", "results": [HIT], "result_count": 1}, url)
        if method == "POST" and path == "/secured/llm/generate":
            return _response(200, {"status": "success", "message": "ok", "content": "Dazu finde ich nichts.",
                                   "model": "m", "prompt_tokens": 1, "completion_tokens": 1}, url)
        return _response(404, {"status": "error", "error_code": "not_found"}, url)

    def to_agents(self):
        return [s for s in self.sent if s["path"].startswith("/secured/agents/")]

    def put_for(self, agent_id):
        return [s for s in self.sent if s["method"] == "PUT" and s["path"] == f"/secured/agents/{agent_id}/claims"]

    def delete_for(self, agent_id):
        return [s for s in self.sent
                if s["method"] == "DELETE" and s["path"] == f"/secured/agents/{agent_id}/claims"]


class WireCore(KnovasAPIClient):
    """The app's real Knovas client in secured mode; only its HTTP session is the recording wire."""

    wire = None

    def __init__(self, config):
        super().__init__(config)
        self.base_url = CORE
        self.mtls_enabled = True
        self.cert_auto_renew_enabled = False
        self._request_interval = 0
        self._session = WireCore.wire


class RecordingConnector:
    writes: list = []

    def __init__(self, base_url=None, *, principal_broker=None, session=None, timeout=20.0):
        self.base_url = base_url

    def status(self):
        return {"scheduler_state": "idle"}

    def __getattr__(self, name):
        def call(*args, **kwargs):
            RecordingConnector.writes.append(name)
            return {}
        return call


def _app(conn, tmp_path, monkeypatch, *, gateway="an", assistant="aus"):
    monkeypatch.setenv("AGENTS_CORE_GATEWAY", gateway)
    monkeypatch.setenv("ASSISTANT_ENABLED", assistant)
    monkeypatch.delenv("ASSISTANT_PROVIDER", raising=False)
    monkeypatch.setattr("knovas_connector_client.KnovasConnectorClient", RecordingConnector)
    WireCore.wire = Wire()
    app = conftest._identity_app(conn, tmp_path, monkeypatch, client_cls=WireCore)
    app.config["RB67_WIRE"] = WireCore.wire
    app.config["RB67_KEYS"] = tmp_path / "broker"
    return app


def _wire(app) -> Wire:
    return app.config["RB67_WIRE"]


def _sync(app):
    app.extensions["agents"]["sync"]()


def _verify(app, token):
    key_dir = app.config["RB67_KEYS"]
    kid = (key_dir / "broker_ed25519.kid").read_text().strip()
    return AssertionVerifier({kid: public_pem(key_dir)}).verify(token, tenant=TENANT)


def _claim(app, sent):
    return _verify(app, sent["json"][ASSERTION_FIELD])


def _pushed(app, agent_id):
    """The claims of the one PUT for this agent, verified; the body has exactly ``claims``."""
    (put,) = _wire(app).put_for(agent_id)
    assert set(put["json"]) == {"claims"}, put["json"].keys()
    claims = put["json"]["claims"]
    assert len(claims) == 20                                           # the Core's maximum (CR-R5)
    verified = [_verify(app, token) for token in claims]
    assert len({c.jti for c in verified}) == len(verified)
    for claim in verified:
        assert 0 < claim.expires_at - claim.issued_at <= 300
    return verified


# --- people, roles, agents ---------------------------------------------------------------------------------------------


def _someone(repo, name, **kwargs):
    return repo.create(email=f"{name}@kanzlei.ch", display_name=kwargs.pop("display_name", name.capitalize()),
                       password=conftest.PASSWORD, **kwargs)


def _admin(repo, name="chef"):
    person = _someone(repo, name)
    repo.grant_role(person.id, "admin")
    return person


def _role(roles, name, group=None, functions=()):
    return roles.create_role(name=name, functions=set(functions), core_group_id=group)


def _audit_rows(conn, action):
    return conn.execute("SELECT actor_user_id, action, target_type, target_id, outcome, detail::text "
                        "FROM audit_log WHERE action = %s ORDER BY id", (action,)).fetchall()


@pytest.fixture
def roles(platform_db):
    return RoleStore(platform_db)


@pytest.fixture
def agents(platform_db):
    return AgentStore(platform_db)


# --- an installation before the agents --------------------------------------------------------------------------------


def _shape(conn):
    shape: dict[str, list] = {}
    queries = (
        "SELECT table_name, 'column', column_name || ' ' || data_type || ' ' || is_nullable || ' ' || "
        "coalesce(column_default, '') FROM information_schema.columns WHERE table_schema = current_schema()",
        "SELECT tablename, 'index', indexdef FROM pg_indexes WHERE schemaname = current_schema()",
        "SELECT c.conrelid::regclass::text, 'constraint', c.conname || ' ' || pg_get_constraintdef(c.oid) "
        "FROM pg_constraint c WHERE c.connamespace = current_schema()::regnamespace AND c.conrelid <> 0",
        "SELECT event_object_table, 'trigger', trigger_name || ' ' || event_manipulation || ' ' || action_statement "
        "FROM information_schema.triggers WHERE trigger_schema = current_schema()",
    )
    for query in queries:
        for table, kind, text in conn.execute(query).fetchall():
            shape.setdefault(str(table).split(".")[-1].strip('"'), []).append((kind, text))
    return {table: sorted(items) for table, items in shape.items()}


@pytest.fixture
def before_agents():
    """A schema migrated up to 0027_roles, with people whose keys the move turned into roles."""
    import psycopg

    from identity import passwords

    schema = f"t{uuid.uuid4().hex[:12]}"
    with psycopg.connect(PLATFORM_DB_TEST_DSN, autocommit=True) as setup:
        setup.execute(f'CREATE SCHEMA "{schema}"')
    conn = psycopg.connect(PLATFORM_DB_TEST_DSN, autocommit=True)
    conn.execute(f'SET search_path TO "{schema}"')
    try:
        everything = migrate.discover()
        migrate.apply(conn, [m for m in everything if m.version < "0027_"])
        digest = passwords.hash_password(conftest.PASSWORD)
        for name, keys in (("ada", ("admin",)), ("ines", ("member", "ingestion_manager")),
                           ("paul", ("approver",)), ("mia", ("member",))):
            user_id = conn.execute("INSERT INTO users (email, display_name, password_hash) VALUES (%s, %s, %s) "
                                   "RETURNING id", (f"{name}@kanzlei.ch", name, digest)).fetchone()[0]
            for key in keys:
                conn.execute("INSERT INTO user_roles (user_id, role_id) SELECT %s, id FROM roles WHERE key = %s",
                             (user_id, key))
        migrate.apply(conn, [m for m in everything if m.version <= BEFORE_AGENTS])
        yield conn
    finally:
        conn.close()
        with psycopg.connect(PLATFORM_DB_TEST_DSN, autocommit=True) as cleanup:
            cleanup.execute(f'DROP SCHEMA "{schema}" CASCADE')


# === 1. the migration ==================================================================================================


def test_the_migration_is_0028_agents():
    versions = [m.version for m in migrate.discover()]
    assert [v for v in versions if v.startswith("0028_")] == ["0028_agents"]
    assert versions.index("0028_agents") > versions.index(BEFORE_AGENTS)


def test_the_migration_adds_tables_holds_no_credential_and_changes_no_existing_table(before_agents):
    before = _shape(before_agents)
    migrate.apply(before_agents)
    after = _shape(before_agents)
    assert {table: after.get(table) for table in before} == before
    added = set(after) - set(before)
    assert {"agents", "tenant_role_agents"} <= added
    columns = [text.split(" ")[0] for table in added for kind, text in after[table] if kind == "column"]
    assert not [c for c in columns if any(w in c.lower() for w in ("key", "secret", "hash", "token"))], columns


def test_after_the_migration_the_assistant_is_in_every_role_but_administration(before_agents):
    migrate.apply(before_agents)
    store, roles = AgentStore(before_agents), RoleStore(before_agents)
    assistant = store.assistant()
    assert (assistant.kind, assistant.name) == ("assistant", "Knovas Assistent")
    listed = roles.list_roles()
    assert len(listed) >= 4                                            # the move made custom roles
    assert set(assistant.roles) == {r.id for r in listed if r.kind != "administration"}
    assert [a.id for a in store.list_agents() if a.kind == "assistant"] == [assistant.id]


# === 2. the assistant and the roles ====================================================================================


def test_a_new_installation_has_the_assistant_in_mitglied_only(agents, roles):
    assistant = agents.assistant()
    assert set(assistant.roles) == {roles.mitglied().id}
    assert assistant.on_behalf_of is None and not assistant.revoked


def test_a_role_created_later_has_the_assistant_and_an_external_agent_starts_with_no_role(agents, roles):
    role = _role(roles, "Personal", "g-personal")
    assert role.id in agents.assistant().roles
    external = agents.create_agent(name="Copilot Sekretariat")
    assert external.kind == "external" and external.roles == frozenset()
    claim = agents.claim_for(external.id)
    assert (claim.subject, claim.groups, claim.functions, claim.on_behalf_of) == (str(external.id), (), (), None)


def test_no_agent_joins_administration(agents, roles):
    external = agents.create_agent(name="Copilot")
    for agent in (external, agents.assistant()):
        with pytest.raises(ProtectedRoleError):
            agents.add_to_role(roles.administration().id, agent.id)
        assert roles.administration().id not in agents.get_agent(agent.id).roles


# === 3. one claim for every agent (RB-6) ===============================================================================


def test_an_agents_claim_is_the_groups_and_functions_of_its_roles(agents, roles):
    agent = agents.create_agent(name="Claude Code")
    first = _role(roles, "Prozess", "g-1", {"approver"})
    second = _role(roles, "Akten", "g-2")
    third = _role(roles, "Ohne Gruppe", None, {"experimenter"})
    for role in (first, second, third):
        agents.add_to_role(role.id, agent.id)
    claim = agents.claim_for(agent.id)
    assert claim.subject == str(agent.id)
    assert (claim.groups, claim.functions) == (("g-1", "g-2"), ("approver", "experimenter"))
    agents.remove_from_role(second.id, agent.id)
    assert agents.claim_for(agent.id).groups == ("g-1",)
    assert {r.id for r in agents.roles_of_agent(agent.id)} == {first.id, third.id}


def test_acting_for_a_person_keeps_only_what_the_person_also_holds(agents, roles, identity_repo):
    anna = _someone(identity_repo, "anna")
    identity_repo.grant_access_group(anna.id, "g-direct")
    first = _role(roles, "Prozess", "g-1", {"approver"})
    second = _role(roles, "Akten", "g-2", {"ingestion_manager"})
    roles.add_member(first.id, anna.id)
    agent = agents.create_agent(name="Claude Code Anna", on_behalf_of=anna.id)
    assert agent.on_behalf_of == anna.id
    agents.add_to_role(first.id, agent.id)
    agents.add_to_role(second.id, agent.id)
    claim = agents.claim_for(agent.id)
    assert (claim.subject, claim.on_behalf_of) == (str(agent.id), anna.id)
    assert (claim.groups, claim.functions) == (("g-1",), ("approver",))     # not g-2, not g-direct (FU-41)
    roles.add_member(second.id, anna.id)
    assert agents.claim_for(agent.id).groups == ("g-1", "g-2")
    other = _someone(identity_repo, "otto")
    with pytest.raises(AgentError):
        agents.claim_for(agent.id, on_behalf_of=other.id)


def test_the_assistant_follows_the_same_rule_and_acts_only_for_a_person(agents, roles, identity_repo):
    anna, bob = _someone(identity_repo, "anna"), _someone(identity_repo, "bob")
    identity_repo.grant_access_group(anna.id, "g-direct")
    first, second = _role(roles, "Prozess", "g-1"), _role(roles, "Personal", "g-2")
    roles.add_member(first.id, anna.id)
    roles.add_member(second.id, anna.id)
    assistant = agents.assistant()
    assert agents.claim_for(assistant.id, on_behalf_of=anna.id).groups == ("g-1", "g-2")
    agents.remove_from_role(second.id, assistant.id)                     # "may not be used by AI"
    claim = agents.claim_for(assistant.id, on_behalf_of=anna.id)
    assert (claim.subject, claim.groups, claim.on_behalf_of) == (str(assistant.id), ("g-1",), anna.id)
    assert agents.claim_for(assistant.id, on_behalf_of=bob.id).groups == ()
    with pytest.raises(AgentError):
        agents.claim_for(assistant.id)


def test_a_33rd_group_for_an_agent_acting_on_its_own_is_refused_and_nothing_is_written(agents, roles):
    agent = agents.create_agent(name="Claude Code")
    many = [_role(roles, f"Mandat {i:02d}", f"g-{i:02d}") for i in range(MAX_CLAIM_GROUPS + 1)]
    for role in many[:MAX_CLAIM_GROUPS]:
        agents.add_to_role(role.id, agent.id)
    with pytest.raises(TooManyGroupsError) as refused:
        agents.add_to_role(many[-1].id, agent.id)
    assert isinstance(refused.value, RoleError) and "32" in str(refused.value)
    assert many[-1].id not in agents.get_agent(agent.id).roles
    assert len(agents.claim_for(agent.id).groups) == MAX_CLAIM_GROUPS
    plain = _role(roles, "Noch ohne Gruppe")
    agents.add_to_role(plain.id, agent.id)
    with pytest.raises(TooManyGroupsError):
        roles.set_core_group(plain.id, "g-extra")
    assert roles.get_role(plain.id).core_group_id is None


def test_no_claim_is_made_for_an_agent_with_more_than_32_groups(agents, roles, platform_db):
    agent = agents.create_agent(name="Claude Code")
    for i in range(MAX_CLAIM_GROUPS + 1):                                 # stored before the cap existed
        role = _role(roles, f"Mandat {i:02d}", f"g-{i:02d}")
        platform_db.execute("INSERT INTO tenant_role_agents (role_id, agent_id) VALUES (%s, %s)", (role.id, agent.id))
    with pytest.raises(TooManyGroupsError):
        agents.claim_for(agent.id)


# === 4. the register ===================================================================================================


def test_an_agent_acts_for_an_active_person_or_for_nobody(agents, identity_repo):
    anna, bob = _someone(identity_repo, "anna"), _someone(identity_repo, "bob")
    identity_repo.disable(bob.id)
    assert agents.create_agent(name="Fuer Anna", on_behalf_of=anna.id).on_behalf_of == anna.id
    for person in (bob.id, uuid.uuid4(), "kein-uuid"):
        with pytest.raises(AgentError):
            agents.create_agent(name=f"Fuer {person}", on_behalf_of=person)
    assert sorted(a.name for a in agents.list_agents() if a.kind == "external") == ["Fuer Anna"]
    identity_repo.disable(anna.id)
    (fuer_anna,) = [a for a in agents.list_agents() if a.name == "Fuer Anna"]
    with pytest.raises(AgentError):
        agents.claim_for(fuer_anna.id)


def test_names_are_unique_the_assistant_cannot_be_revoked_and_a_revoked_agent_has_no_claim(agents):
    agent = agents.create_agent(name="Claude Code")
    for taken in ("claude code", "Knovas Assistent"):
        with pytest.raises(AgentNameTaken):
            agents.create_agent(name=taken)
    for bad in ("", "   ", "x" * 81):
        with pytest.raises(AgentError):
            agents.create_agent(name=bad)
    with pytest.raises(ProtectedAgentError):
        agents.revoke_agent(agents.assistant().id)
    assert agents.revoke_agent(agent.id).revoked is True
    with pytest.raises(AgentError):
        agents.claim_for(agent.id)
    assert issubclass(ProtectedAgentError, AgentError) and issubclass(AgentNotFound, AgentError)
    for unknown in (uuid.uuid4(), "kein-uuid"):
        with pytest.raises(AgentNotFound):
            agents.get_agent(unknown)


# === 5. delivery to the Core's MCP gateway (RB-7, CR-R5) ===============================================================


def test_with_the_switch_off_nothing_reaches_the_core(platform_db, tmp_path, monkeypatch):
    app = _app(platform_db, tmp_path, monkeypatch, gateway="aus")
    repo, store, roles = UserRepository(platform_db), AgentStore(platform_db), RoleStore(platform_db)
    chef = _admin(repo)
    existing = store.create_agent(name="Vorhanden")
    _sync(app)
    browser = conftest._signed_in(app, chef.email)
    token = browser._token
    assert browser.post("/admin/agents/create", data={"csrf_token": token, "name": "Neu"}).status_code in SUCCESS
    role = _role(roles, "Prozess", "g-1")
    assert browser.post(f"/admin/roles/{role.id}/agents/add",
                        data={"csrf_token": token, "agent_id": str(existing.id)}).status_code in SUCCESS
    assert browser.post(f"/admin/agents/{existing.id}/revoke", data={"csrf_token": token}).status_code in SUCCESS
    assert _wire(app).to_agents() == []
    assert store.get_agent(existing.id).revoked is True and role.id in store.get_agent(existing.id).roles


def test_the_sync_gives_each_external_agent_a_pool_of_its_signed_claims(platform_db, tmp_path, monkeypatch):
    app = _app(platform_db, tmp_path, monkeypatch)
    store, roles = AgentStore(platform_db), RoleStore(platform_db)
    with_role = store.create_agent(name="Claude Code")
    store.add_to_role(_role(roles, "Prozess", "g-prozess", {"member"}).id, with_role.id)
    without = store.create_agent(name="Copilot")
    _wire(app).sent.clear()
    _sync(app)
    for claim in _pushed(app, with_role.id):
        assert (claim.subject, claim.groups, claim.roles) == (str(with_role.id), ("g-prozess",), ("member",))
    for claim in _pushed(app, without.id):
        assert (claim.subject, claim.groups) == (str(without.id), ())     # no role: open documents only
    assistant = store.assistant()
    assert _wire(app).put_for(assistant.id) == [] and _wire(app).delete_for(assistant.id) == []
    assert {s["method"] for s in _wire(app).to_agents()} == {"PUT"}
    assert [s for s in _wire(app).sent if not s["path"].startswith("/secured/agents/")] == []


def test_an_agent_acting_for_a_person_gets_claims_narrowed_to_that_person(platform_db, tmp_path, monkeypatch):
    app = _app(platform_db, tmp_path, monkeypatch)
    repo, store, roles = UserRepository(platform_db), AgentStore(platform_db), RoleStore(platform_db)
    anna = _someone(repo, "anna")
    repo.grant_access_group(anna.id, "g-direct")
    shared, agent_only = _role(roles, "Prozess", "g-1"), _role(roles, "Akten", "g-2")
    roles.add_member(shared.id, anna.id)
    agent = store.create_agent(name="Claude Code Anna", on_behalf_of=anna.id)
    for role in (shared, agent_only):
        store.add_to_role(role.id, agent.id)
    _wire(app).sent.clear()
    _sync(app)
    for claim in _pushed(app, agent.id):
        assert (claim.subject, claim.groups) == (str(agent.id), ("g-1",))


def test_a_revoked_agent_and_one_whose_person_is_inactive_lose_their_claims(platform_db, tmp_path, monkeypatch):
    app = _app(platform_db, tmp_path, monkeypatch)
    repo, store = UserRepository(platform_db), AgentStore(platform_db)
    anna = _someone(repo, "anna")
    revoked = store.create_agent(name="Widerrufen")
    store.revoke_agent(revoked.id)
    for_anna = store.create_agent(name="Fuer Anna", on_behalf_of=anna.id)
    repo.disable(anna.id)
    live = store.create_agent(name="Lebendig")
    _wire(app).sent.clear()
    _sync(app)
    for gone in (revoked, for_anna):
        assert _wire(app).put_for(gone.id) == []
        assert len(_wire(app).delete_for(gone.id)) == 1
    assert len(_pushed(app, live.id)) >= 1


def test_an_agent_over_32_groups_gets_no_claim_and_the_others_still_do(platform_db, tmp_path, monkeypatch):
    app = _app(platform_db, tmp_path, monkeypatch)
    store, roles = AgentStore(platform_db), RoleStore(platform_db)
    crowded, fine = store.create_agent(name="Zu viele"), store.create_agent(name="Passt")
    for i in range(MAX_CLAIM_GROUPS + 1):
        role = _role(roles, f"Mandat {i:02d}", f"g-{i:02d}")
        platform_db.execute("INSERT INTO tenant_role_agents (role_id, agent_id) VALUES (%s, %s)",
                            (role.id, crowded.id))
    _wire(app).sent.clear()
    _sync(app)
    assert _wire(app).put_for(crowded.id) == [] and len(_wire(app).delete_for(crowded.id)) == 1
    assert len(_pushed(app, fine.id)) >= 1


def test_changes_on_the_agent_pages_reach_the_core_at_once(platform_db, tmp_path, monkeypatch):
    app = _app(platform_db, tmp_path, monkeypatch)
    repo, store, roles = UserRepository(platform_db), AgentStore(platform_db), RoleStore(platform_db)
    chef = _admin(repo)
    role = _role(roles, "Prozess", "g-1", {"approver"})
    browser = conftest._signed_in(app, chef.email)
    assert browser.post("/admin/agents/create", data={"csrf_token": browser._token,
                                                      "name": "Claude Code"}).status_code in SUCCESS
    (agent,) = [a for a in store.list_agents() if a.name == "Claude Code"]
    assert [c.groups for c in _pushed(app, agent.id)][0] == ()
    _wire(app).sent.clear()
    assert browser.post(f"/admin/roles/{role.id}/agents/add", data={
        "csrf_token": browser._token, "agent_id": str(agent.id)}).status_code in SUCCESS
    assert {c.groups for c in _pushed(app, agent.id)} == {("g-1",)}
    _wire(app).sent.clear()
    assert browser.post(f"/admin/agents/{agent.id}/revoke", data={"csrf_token": browser._token}).status_code in SUCCESS
    assert _wire(app).put_for(agent.id) == [] and len(_wire(app).delete_for(agent.id)) == 1


def test_a_core_without_the_route_leaves_the_platform_working(platform_db, tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    app = _app(platform_db, tmp_path, monkeypatch)
    repo, store = UserRepository(platform_db), AgentStore(platform_db)
    chef = _admin(repo)
    store.create_agent(name="Claude Code")
    for status in (404, 503):
        _wire(app).agents_status = status
        _sync(app)                                                         # never raises
    browser = conftest._signed_in(app, chef.email)
    assert browser.post("/admin/agents/create", data={"csrf_token": browser._token,
                                                      "name": "Copilot"}).status_code in SUCCESS
    assert [a.name for a in store.list_agents() if a.kind == "external"] == ["Claude Code", "Copilot"]
    assert browser.get("/admin/agents").status_code == 200
    tokens = [t for s in _wire(app).to_agents() if s["json"] for t in s["json"].get("claims", [])]
    assert tokens
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert not [t for t in tokens if t in text or t.split(".")[2] in text]


def test_a_client_with_only_external_agents(platform_db, tmp_path, monkeypatch):
    app = _app(platform_db, tmp_path, monkeypatch, gateway="an", assistant="aus")
    repo, store = UserRepository(platform_db), AgentStore(platform_db)
    anna = _someone(repo, "anna")
    repo.grant_role(anna.id, "member")
    agent = store.create_agent(name="Copilot")
    _sync(app)
    assert len(_pushed(app, agent.id)) >= 1
    person = conftest._signed_in(app, anna.email)
    assert person.post("/fragen", data={"csrf_token": person._token, "question": QUESTION}).status_code == 404
    assert [s for s in _wire(app).sent if s["path"] == "/secured/query"] == []


# === 6. the assistant (RB-6) ===========================================================================================


def test_the_assistant_searches_with_its_claim_narrowed_to_the_person(platform_db, tmp_path, monkeypatch):
    app = _app(platform_db, tmp_path, monkeypatch, gateway="aus", assistant="an")
    repo, store, roles = UserRepository(platform_db), AgentStore(platform_db), RoleStore(platform_db)
    anna = _someone(repo, "anna")
    repo.grant_role(anna.id, "member")
    repo.grant_access_group(anna.id, "g-direct")
    first, second = _role(roles, "Prozess", "g-1"), _role(roles, "Personal", "g-2")
    roles.add_member(first.id, anna.id)
    roles.add_member(second.id, anna.id)
    assistant = store.assistant()
    person = conftest._signed_in(app, anna.email)

    def ask():
        before = len(_wire(app).sent)
        assert person.post("/fragen", data={"csrf_token": person._token, "question": QUESTION}).status_code == 200
        return _wire(app).sent[before:]

    queries = [s for s in ask() if s["path"] == "/secured/query"]
    assert queries
    for query in queries:
        assert (_claim(app, query).subject, _claim(app, query).groups) == (str(assistant.id), ("g-1", "g-2"))
        assert query["json"]["actor"] == {"kind": "agent", "id": str(assistant.id),
                                          "on_behalf_of": {"kind": "user", "id": str(anna.id)}}
    store.remove_from_role(second.id, assistant.id)
    asked_core = [s for s in ask() if s["path"] in ("/secured/query", "/secured/llm/generate")]
    assert [s["path"] for s in asked_core].count("/secured/query") >= 1
    for request in asked_core:
        claim = _claim(app, request)
        assert (claim.subject, claim.groups) == (str(assistant.id), ("g-1",)), request["path"]
    assert _wire(app).to_agents() == []                                   # the assistant goes through the Platform


def test_an_administrator_may_take_the_assistant_out_of_a_role_and_no_agent_into_administration(
        platform_db, tmp_path, monkeypatch):
    app = _app(platform_db, tmp_path, monkeypatch)
    repo, store, roles = UserRepository(platform_db), AgentStore(platform_db), RoleStore(platform_db)
    chef = _admin(repo)
    personal = _role(roles, "Personal", "g-personal")
    assistant = store.assistant()
    browser = conftest._signed_in(app, chef.email)
    assert browser.post(f"/admin/roles/{personal.id}/agents/remove", data={
        "csrf_token": browser._token, "agent_id": str(assistant.id)}).status_code in SUCCESS
    assert personal.id not in store.assistant().roles
    assert browser.post(f"/admin/roles/{personal.id}/agents/add", data={
        "csrf_token": browser._token, "agent_id": str(assistant.id)}).status_code in SUCCESS
    assert personal.id in store.assistant().roles
    external = store.create_agent(name="Copilot")
    for agent in (assistant, external):
        refused = browser.post(f"/admin/roles/{roles.administration().id}/agents/add", data={
            "csrf_token": browser._token, "agent_id": str(agent.id)})
        assert refused.status_code == 400
        assert roles.administration().id not in store.get_agent(agent.id).roles


# === 7. the console's pages ============================================================================================


def test_the_agent_pages_are_behind_the_admin_gate(platform_db, tmp_path, monkeypatch):
    app = _app(platform_db, tmp_path, monkeypatch)
    repo, store, roles = UserRepository(platform_db), AgentStore(platform_db), RoleStore(platform_db)
    existing = store.create_agent(name="Vorhanden")
    role = _role(roles, "Prozess", "g-1")
    mia = _someone(repo, "mia")
    repo.grant_role(mia.id, "member")
    assert app.test_client().get("/admin/agents").status_code in (302, 303)
    member = conftest._signed_in(app, mia.email)
    token = member._token
    assert member.get("/admin/agents").status_code == 403
    for path, data in (("/admin/agents/create", {"name": "Heimlich"}),
                       (f"/admin/agents/{existing.id}/revoke", {}),
                       (f"/admin/roles/{role.id}/agents/add", {"agent_id": str(existing.id)}),
                       (f"/admin/roles/{role.id}/agents/remove", {"agent_id": str(store.assistant().id)})):
        assert member.post(path, data={"csrf_token": token, **data}).status_code == 403, path
    assert [a.name for a in store.list_agents() if a.kind == "external"] == ["Vorhanden"]
    assert store.get_agent(existing.id).revoked is False and existing.roles == store.get_agent(existing.id).roles
    assert role.id in store.assistant().roles
    assert _wire(app).to_agents() == []


def test_every_agent_form_needs_its_token(platform_db, tmp_path, monkeypatch):
    app = _app(platform_db, tmp_path, monkeypatch)
    repo, store, roles = UserRepository(platform_db), AgentStore(platform_db), RoleStore(platform_db)
    chef = _admin(repo)
    existing = store.create_agent(name="Vorhanden")
    role = _role(roles, "Prozess", "g-1")
    browser = conftest._signed_in(app, chef.email)                       # sends the X-CSRF-Token header, no field
    for path, data in (("/admin/agents/create", {"name": "Ohne Token"}),
                       (f"/admin/agents/{existing.id}/revoke", {}),
                       (f"/admin/roles/{role.id}/agents/add", {"agent_id": str(existing.id)}),
                       (f"/admin/roles/{role.id}/agents/remove", {"agent_id": str(store.assistant().id)})):
        assert browser.post(path, data=data).status_code == 400, path
    assert [a.name for a in store.list_agents() if a.kind == "external"] == ["Vorhanden"]
    assert store.get_agent(existing.id).revoked is False and store.get_agent(existing.id).roles == frozenset()
    assert role.id in store.assistant().roles
    assert _wire(app).to_agents() == []


def test_the_page_creates_an_agent_names_its_id_and_audits_by_id(platform_db, tmp_path, monkeypatch):
    app = _app(platform_db, tmp_path, monkeypatch)
    repo, store = UserRepository(platform_db), AgentStore(platform_db)
    chef, anna = _admin(repo), _someone(repo, "anna", display_name="Anna Zwiebel")
    browser = conftest._signed_in(app, chef.email)
    created = browser.post("/admin/agents/create", data={
        "csrf_token": browser._token, "name": "Claude Code Zwiebel", "on_behalf_of": str(anna.id)},
        follow_redirects=True)
    assert created.status_code == 200
    (agent,) = [a for a in store.list_agents() if a.name == "Claude Code Zwiebel"]
    assert agent.on_behalf_of == anna.id
    assert str(agent.id) in created.get_data(as_text=True)                # Knovas binds the gateway key to this id
    listed = browser.get("/admin/agents").get_data(as_text=True)
    assert "Knovas Assistent" in listed and "Claude Code Zwiebel" in listed
    browser.post(f"/admin/agents/{agent.id}/revoke", data={"csrf_token": browser._token})
    for action in ("agent.created", "agent.revoked"):
        (row,) = _audit_rows(platform_db, action)
        assert (str(row[0]), row[2], row[3]) == (str(chef.id), "agent", str(agent.id))
        assert "Zwiebel" not in json.dumps([str(v) for v in row])


# === 8. logs ===========================================================================================================


def test_no_claim_name_or_question_reaches_a_log_line(platform_db, tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    app = _app(platform_db, tmp_path, monkeypatch, gateway="an", assistant="an")
    repo, store, roles = UserRepository(platform_db), AgentStore(platform_db), RoleStore(platform_db)
    chef = _admin(repo)
    zora = _someone(repo, "zora", display_name="Zora Zwiebel")
    repo.grant_role(zora.id, "member")
    role = _role(roles, "Mandat Zwiebel", "g-zwiebel")
    roles.add_member(role.id, zora.id)
    browser = conftest._signed_in(app, chef.email)
    browser.post("/admin/agents/create", data={"csrf_token": browser._token, "name": "Agent Zwiebel",
                                               "on_behalf_of": str(zora.id)})
    (agent,) = [a for a in store.list_agents() if a.name == "Agent Zwiebel"]
    browser.post(f"/admin/roles/{role.id}/agents/add", data={"csrf_token": browser._token, "agent_id": str(agent.id)})
    _sync(app)
    person = conftest._signed_in(app, zora.email)
    person.post("/fragen", data={"csrf_token": person._token, "question": QUESTION})
    browser.post(f"/admin/agents/{agent.id}/revoke", data={"csrf_token": browser._token})
    tokens = [t for s in _wire(app).to_agents() if s["json"] for t in s["json"].get("claims", [])]
    tokens += [s["json"][ASSERTION_FIELD] for s in _wire(app).sent if s["json"] and ASSERTION_FIELD in s["json"]]
    assert tokens
    lines = []
    for record in caplog.records:
        lines.append(record.getMessage())
        if record.exc_text:
            lines.append(record.exc_text)
    text = "\n".join(lines)
    for secret in [QUESTION, PASSAGE, HIT["pointer"], *[t.split(".")[2] for t in tokens]]:
        assert secret not in text, secret[:20]
    leaked = [line for line in lines if "zwiebel" in line.lower()]
    assert leaked == [], leaked
