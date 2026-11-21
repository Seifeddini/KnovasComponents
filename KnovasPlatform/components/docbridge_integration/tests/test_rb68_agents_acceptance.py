"""RB-6, RB-7 and RB-8 acceptance tests: one claim for every agent, the agent register and its keys, and the agent
gateway, the Platform's MCP endpoint (FEAT-2026-11-19-platform-mvp, design docs/platform-mvp/design/role-builder.md
§ 2.3, § 2.4, § 4, § 5, § 6 rules 4 and 6, § 7; work order docs/platform-mvp/work-orders/RB-6-8.md).

FROZEN. Written for the coordinator before the build; a builder never edits this file. Its git blob id is in
docs/platform-mvp/frozen-tests.txt.

What is pinned:

1. Migration ``0028_agents`` adds new tables only, among them ``agents``, ``agent_keys`` and ``tenant_role_agents``.
   Every table that existed before keeps its columns, indexes, constraints and triggers.
2. The Knovas Assistent is exactly one agent (kind ``assistant``, no key). After the migration it is a member of every
   role but Administration, and every role created later has it as a member. No agent ever joins Administration.
3. One claim for every agent (RB-6): subject = the agent's id, groups and functions = those of the agent's roles.
   When the agent acts for a person (an external agent bound to one when created, or the assistant for the signed-in
   person) only the groups and functions that person also holds are kept. A person's direct group grants do not reach
   an agent. An agent with no role carries no group. No agent's claim carries more than 32 groups: adding an agent
   that acts on its own to a 33rd group is refused, nothing written, and a claim that would carry more is not signed.
4. Keys (RB-7): ``kna_`` + 8 characters + ``_`` + at least 43 random characters, shown once (``NewAgent.key``), stored
   as the SHA-256 of the whole key and nowhere in clear, compared with ``hmac.compare_digest``. A key ends after at
   most 90 days. Revoking acts at once. A wrong, malformed, expired or revoked key, the key of an agent whose person
   is deactivated, and no key at all get the same 401: same body, same ``WWW-Authenticate``.
5. The gateway (RB-8) is ``POST /mcp``, MCP Streamable HTTP of revision 2025-11-25 in its smallest form: one JSON
   object per answer, no session, GET and DELETE 405, a foreign ``Origin`` 403, an ``MCP-Protocol-Version`` other
   than 2025-11-25, 2025-06-18 or 2025-03-26 400. It answers ``initialize`` and ``tools/list`` and runs
   ``knovas_query`` (and, if offered, ``knovas_find_node``, ``knovas_explain``, ``knovas_neighbors``, nothing else)
   through the Platform's own client, which signs the agent's claim and declares the agent as the actor. No tool
   takes an argument it does not offer (``access_groups`` above all). The session cookie is never a credential, and
   the endpoint sets none; it is the only endpoint the change exempts from the CSRF header gate.
6. One ``audit_log`` row per tool call (``agent.call``, the agent's id, ids and counts only: no question, no passage,
   no pointer, no title, no key). A rate limit per agent (``AGENTS_CALLS_PER_MINUTE``): over it, 429 with
   ``Retry-After`` and nothing reaches the Core. ``AGENTS_ENABLED`` switches the gateway; off, ``/mcp`` is 404. The
   assistant's switch is independent: agents on and the assistant off works.
7. The console's agent pages (``admin.agents``, ``admin.create_agent``, ``admin.revoke_agent``) are behind the admin
   gate and the form's CSRF token. Creating shows the key once; the list never shows it again.
8. No key, no key hash, no question, passage, pointer, agent name or person name reaches a log line, DEBUG included.

The interface (``identity.agents``): ``AgentStore(conn)`` with ``assistant``, ``list_agents``, ``get_agent``,
``create_agent(*, name, on_behalf_of=None, days=MAX_KEY_DAYS, by=None) -> NewAgent``, ``revoke_agent``,
``add_to_role``, ``remove_from_role``, ``roles_of_agent``, ``claim_for(agent_id, *, on_behalf_of=None) ->
AgentClaim``; ``Agent`` (``id``, ``name``, ``kind``, ``on_behalf_of``, ``roles``, ``key_prefix``, ``expires_at``,
``revoked``), ``NewAgent`` (``agent``, ``key``), ``AgentClaim`` (``subject``, ``groups``, ``functions``,
``on_behalf_of``); ``AgentError`` and below it ``AgentNotFound``, ``AgentNameTaken``, ``ProtectedAgentError``;
``MAX_KEY_DAYS``. Refusals about roles and groups are RB-1's ``ProtectedRoleError`` and ``TooManyGroupsError``.

Nothing here reaches a tenant: the Core is the Platform's own ``KnovasAPIClient`` whose wire is a recording fake that
answers like the Core, and the Knovas Connector is a recording fake.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import uuid
from datetime import timedelta
from urllib.parse import urlsplit

import pytest
import requests
from flask import url_for

import conftest
from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

pytestmark = pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")

from identity import migrate  # noqa: E402
from identity.agents import (  # noqa: E402
    MAX_KEY_DAYS,
    AgentError,
    AgentNameTaken,
    AgentNotFound,
    AgentStore,
    NewAgent,
    ProtectedAgentError,
)
from identity.assertion import AssertionVerifier  # noqa: E402
from identity.broker_key import public_pem  # noqa: E402
from identity.roles import MAX_CLAIM_GROUPS, ProtectedRoleError, RoleError, RoleStore, TooManyGroupsError  # noqa: E402
from identity.users import UserRepository  # noqa: E402
from knovas_client import ASSERTION_FIELD, KnovasAPIClient  # noqa: E402

KEY_RE = re.compile(r"kna_[a-z0-9]{8}_[A-Za-z0-9_-]{43,}")
TENANT = "tenant-a"
CORE = "https://core.test"
#: The tools of today's Knovas MCP server that the gateway may offer through the Platform (work order, "Tools").
OFFERED = frozenset({"knovas_query", "knovas_find_node", "knovas_explain", "knovas_neighbors"})
SAMPLE_ARGS = {
    "knovas_query": {"question": "Welche Kuendigungsfrist gilt fuer Zwiebel?"},
    "knovas_find_node": {"type": "Mandat", "name": "Zwiebel"},
    "knovas_explain": {"node_id": "n-1"},
    "knovas_neighbors": {"node_id": "n-1"},
}
QUESTION = SAMPLE_ARGS["knovas_query"]["question"]
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


# --- the Core on the wire: the Platform's own client, a recording fake behind it ----------------------------------------


def _response(status, payload, url):
    resp = requests.Response()
    resp.status_code = status
    resp.reason = "test"
    resp.url = url
    resp._content = json.dumps(payload).encode("utf-8")
    resp.headers["Content-Type"] = "application/json"
    return resp


class Wire:
    """Every request the Platform's client sends, recorded; answered the way the Core answers."""

    def __init__(self):
        self.sent = []

    def request(self, method=None, url=None, json=None, params=None, headers=None, **_kwargs):
        path = urlsplit(url).path
        self.sent.append({"method": str(method).upper(), "path": path, "json": json, "params": params})
        if method.upper() == "POST" and path == "/secured/query":
            return _response(200, {"status": "success", "results": [HIT], "result_count": 1}, url)
        if method.upper() == "POST" and path == "/secured/llm/generate":
            return _response(200, {"status": "success", "message": "ok", "content": "Dazu finde ich nichts.",
                                   "model": "m", "prompt_tokens": 1, "completion_tokens": 1}, url)
        if method.upper() == "GET" and path == "/secured/graph/node-types":
            return _response(200, {"node_types": [{"id": "t-1", "name": "Mandat"}]}, url)
        if method.upper() == "GET" and path == "/secured/graph/nodes":
            return _response(200, {"nodes": [{"id": "n-1", "name": "Mandat Zwiebel", "node_type_id": "t-1"}]}, url)
        if method.upper() == "GET" and path == "/secured/graph/nodes/n-1/dossier":
            return _response(200, {"node": {"id": "n-1", "name": "Mandat Zwiebel"}, "facts": [], "edges": []}, url)
        if method.upper() == "GET" and path == "/secured/graph/nodes/n-1/neighbors":
            return _response(200, {"neighbors": [{"id": "n-2", "name": "Akte Zwiebel"}]}, url)
        return _response(404, {"status": "error", "error_code": "not_found"}, url)

    def reads(self, path):
        return [s for s in self.sent if s["path"] == path]


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


def _app(conn, tmp_path, monkeypatch, *, agents="an", assistant="aus", per_minute=None):
    monkeypatch.setenv("AGENTS_ENABLED", agents)
    monkeypatch.setenv("ASSISTANT_ENABLED", assistant)
    monkeypatch.delenv("ASSISTANT_PROVIDER", raising=False)
    if per_minute is None:
        monkeypatch.delenv("AGENTS_CALLS_PER_MINUTE", raising=False)
    else:
        monkeypatch.setenv("AGENTS_CALLS_PER_MINUTE", str(per_minute))
    monkeypatch.setattr("knovas_connector_client.KnovasConnectorClient", RecordingConnector)
    WireCore.wire = Wire()
    app = conftest._identity_app(conn, tmp_path, monkeypatch, client_cls=WireCore)
    app.config["RB68_WIRE"] = WireCore.wire
    app.config["RB68_KEYS"] = tmp_path / "broker"
    return app


def _wire(app) -> Wire:
    return app.config["RB68_WIRE"]


def _claim(app, sent):
    """The signed claim a request carried, verified with the Platform's own public key."""
    key_dir = app.config["RB68_KEYS"]
    kid = (key_dir / "broker_ed25519.kid").read_text().strip()
    claims = AssertionVerifier({kid: public_pem(key_dir)}).verify(sent["json"][ASSERTION_FIELD], tenant=TENANT)
    return claims


def _mcp(client, key, body, *, headers=None):
    sent = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if key is not None:
        sent["Authorization"] = f"Bearer {key}"
    sent.update(headers or {})
    return client.post("/mcp", data=json.dumps(body), headers=sent)


def _call(client, key, tool, arguments, *, ident=7):
    return _mcp(client, key, {"jsonrpc": "2.0", "id": ident, "method": "tools/call",
                              "params": {"name": tool, "arguments": arguments}})


def _ok(response):
    assert response.status_code == 200, response.data[:300]
    body = response.get_json()
    assert "error" not in body, body
    result = body["result"]
    assert result.get("isError") is False, result
    assert result["content"][0]["type"] == "text"
    return result["content"][0]["text"]


def _refused(response):
    """A JSON-RPC error, or a tool result that says it failed."""
    body = response.get_json(silent=True) or {}
    return bool(body.get("error")) or bool((body.get("result") or {}).get("isError"))


def _tools(client, key):
    response = _mcp(client, key, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    assert response.status_code == 200
    return {tool["name"]: tool for tool in response.get_json()["result"]["tools"]}


def _url(app, endpoint):
    with app.test_request_context():
        return url_for(endpoint)


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


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _secret(key):
    return key[13:]


def _all_rows(conn):
    """Every row of every table of this schema, as text."""
    tables = [r[0] for r in conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema() "
        "AND table_type = 'BASE TABLE'").fetchall()]
    out = []
    for table in tables:
        out.extend(r[0] for r in conn.execute(f'SELECT t::text FROM "{table}" t').fetchall())
    return "\n".join(out)


def _audit_rows(conn, action):
    return conn.execute("SELECT actor_user_id, action, target_type, target_id, outcome, detail::text, "
                        "coalesce(ip::text, ''), coalesce(user_agent, ''), coalesce(actor_email_snapshot::text, '') "
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


def test_the_migration_adds_tables_and_changes_no_existing_one(before_agents):
    before = _shape(before_agents)
    migrate.apply(before_agents)
    after = _shape(before_agents)
    assert {table: after.get(table) for table in before} == before
    assert {"agents", "agent_keys", "tenant_role_agents"} <= set(after) - set(before)


def test_after_the_migration_the_assistant_is_in_every_role_but_administration(before_agents):
    migrate.apply(before_agents)
    store, roles = AgentStore(before_agents), RoleStore(before_agents)
    assistant = store.assistant()
    assert (assistant.kind, assistant.name, assistant.key_prefix) == ("assistant", "Knovas Assistent", None)
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
    external = agents.create_agent(name="Copilot Sekretariat").agent
    assert external.kind == "external" and external.roles == frozenset()
    claim = agents.claim_for(external.id)
    assert (claim.subject, claim.groups, claim.functions, claim.on_behalf_of) == (str(external.id), (), (), None)


def test_no_agent_joins_administration(agents, roles):
    external = agents.create_agent(name="Copilot").agent
    for agent in (external, agents.assistant()):
        with pytest.raises(ProtectedRoleError):
            agents.add_to_role(roles.administration().id, agent.id)
        assert roles.administration().id not in agents.get_agent(agent.id).roles


# === 3. one claim for every agent (RB-6) ===============================================================================


def test_an_agents_claim_is_the_groups_and_functions_of_its_roles(agents, roles):
    agent = agents.create_agent(name="Claude Code").agent
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
    agent = agents.create_agent(name="Claude Code Anna", on_behalf_of=anna.id).agent
    assert agent.on_behalf_of == anna.id
    agents.add_to_role(first.id, agent.id)
    agents.add_to_role(second.id, agent.id)
    claim = agents.claim_for(agent.id)
    assert (claim.subject, claim.on_behalf_of) == (str(agent.id), anna.id)
    assert (claim.groups, claim.functions) == (("g-1",), ("approver",))     # not g-2, not g-direct
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
    agent = agents.create_agent(name="Claude Code").agent
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
    agent = agents.create_agent(name="Claude Code").agent
    for i in range(MAX_CLAIM_GROUPS + 1):                                 # stored before the cap existed
        role = _role(roles, f"Mandat {i:02d}", f"g-{i:02d}")
        platform_db.execute("INSERT INTO tenant_role_agents (role_id, agent_id) VALUES (%s, %s)", (role.id, agent.id))
    with pytest.raises(TooManyGroupsError):
        agents.claim_for(agent.id)


# === 4. the register and its keys (RB-7) ===============================================================================


def test_a_key_is_shown_once_and_stored_only_as_its_hash(agents, platform_db):
    issued = agents.create_agent(name="Claude Code")
    assert isinstance(issued, NewAgent)
    key = issued.key
    assert KEY_RE.fullmatch(key) and key[12] == "_"
    assert key not in repr(issued)
    assert issued.agent.key_prefix == key[:12]
    rows = _all_rows(platform_db)
    assert key not in rows and _secret(key) not in rows
    assert rows.count(_sha(key)) == 1
    for agent in (issued.agent, agents.get_agent(issued.agent.id), *agents.list_agents()):
        seen = repr(agent) + repr(vars(agent) if hasattr(agent, "__dict__") else agent)
        assert _secret(key) not in seen and _sha(key) not in seen
    second = agents.create_agent(name="Copilot")
    assert second.key != key and second.key[:12] != key[:12]


def test_a_key_ends_within_90_days(agents, platform_db):
    assert MAX_KEY_DAYS == 90
    for days in (MAX_KEY_DAYS + 1, 0, -3):
        with pytest.raises(AgentError):
            agents.create_agent(name=f"Zu lang {days}", days=days)
    assert [a.name for a in agents.list_agents() if a.name.startswith("Zu lang")] == []
    now = platform_db.execute("SELECT now()").fetchone()[0]
    default = agents.create_agent(name="Standard").agent
    assert timedelta(days=89, hours=23) < default.expires_at - now <= timedelta(days=90, minutes=1)
    short = agents.create_agent(name="Kurz", days=7).agent
    assert timedelta(days=6, hours=23) < short.expires_at - now <= timedelta(days=7, minutes=1)


def test_an_agent_acts_for_an_active_person_or_for_nobody(agents, identity_repo):
    anna, bob = _someone(identity_repo, "anna"), _someone(identity_repo, "bob")
    identity_repo.disable(bob.id)
    assert agents.create_agent(name="Fuer Anna", on_behalf_of=anna.id).agent.on_behalf_of == anna.id
    for person in (bob.id, uuid.uuid4(), "kein-uuid"):
        with pytest.raises(AgentError):
            agents.create_agent(name=f"Fuer {person}", on_behalf_of=person)
    assert sorted(a.name for a in agents.list_agents() if a.kind == "external") == ["Fuer Anna"]


def test_agent_names_are_unique_and_the_assistant_has_no_key(agents):
    agents.create_agent(name="Claude Code")
    for taken in ("claude code", "Knovas Assistent"):
        with pytest.raises(AgentNameTaken):
            agents.create_agent(name=taken)
    for bad in ("", "   ", "x" * 81):
        with pytest.raises(AgentError):
            agents.create_agent(name=bad)
    assistant = agents.assistant()
    assert assistant.key_prefix is None and assistant.expires_at is None
    with pytest.raises(ProtectedAgentError):
        agents.revoke_agent(assistant.id)
    assert issubclass(ProtectedAgentError, AgentError) and issubclass(AgentNotFound, AgentError)
    for unknown in (uuid.uuid4(), "kein-uuid"):
        with pytest.raises(AgentNotFound):
            agents.get_agent(unknown)


# === 5. the gateway (RB-8) =============================================================================================


def test_initialize_and_tools_list(platform_db, tmp_path, monkeypatch):
    app = _app(platform_db, tmp_path, monkeypatch)
    key = AgentStore(platform_db).create_agent(name="Claude Code").key
    client = app.test_client()
    response = _mcp(client, key, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}})
    assert response.status_code == 200 and response.mimetype == "application/json"
    result = response.get_json()["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert "tools" in result["capabilities"] and result["serverInfo"]["name"]
    assert not any(h.lower() == "mcp-session-id" for h in response.headers.keys())
    old = _mcp(client, key, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}})
    assert old.get_json()["result"]["protocolVersion"] in ("2025-11-25", "2025-06-18", "2025-03-26")
    note = _mcp(client, key, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert note.status_code == 202 and note.data == b""
    tools = _tools(client, key)
    assert "knovas_query" in tools and set(tools) <= OFFERED
    for name, tool in tools.items():
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert not {"access_groups", "groups", "tenant", "tenant_id", "client_id"} & set(schema.get("properties", {}))
        assert tool.get("annotations", {}).get("readOnlyHint") is True, name
    assert _wire(app).sent == []                                           # nothing asked of the Core for these


def test_search_runs_through_the_core_with_the_agents_signed_claim(platform_db, tmp_path, monkeypatch):
    app = _app(platform_db, tmp_path, monkeypatch)
    store, roles = AgentStore(platform_db), RoleStore(platform_db)
    issued = store.create_agent(name="Claude Code")
    store.add_to_role(_role(roles, "Prozess", "g-prozess", {"member"}).id, issued.agent.id)
    text = _ok(_call(app.test_client(), issued.key, "knovas_query", {"question": QUESTION}))
    assert HIT["pointer"] in text
    (query,) = _wire(app).reads("/secured/query")
    claim = _claim(app, query)
    assert (claim.subject, claim.groups, claim.roles) == (str(issued.agent.id), ("g-prozess",), ("member",))
    assert query["json"]["actor"] == {"kind": "agent", "id": str(issued.agent.id), "on_behalf_of": None}
    assert QUESTION in json.dumps(query["json"])


def test_an_agent_with_no_role_reads_only_what_is_open_to_all(platform_db, tmp_path, monkeypatch):
    app = _app(platform_db, tmp_path, monkeypatch)
    key = AgentStore(platform_db).create_agent(name="Copilot").key
    _ok(_call(app.test_client(), key, "knovas_query", {"question": QUESTION}))
    (query,) = _wire(app).reads("/secured/query")
    assert _claim(app, query).groups == ()


def test_an_agent_acting_for_a_person_searches_with_what_both_hold(platform_db, tmp_path, monkeypatch):
    app = _app(platform_db, tmp_path, monkeypatch)
    repo, store, roles = UserRepository(platform_db), AgentStore(platform_db), RoleStore(platform_db)
    anna = _someone(repo, "anna")
    repo.grant_access_group(anna.id, "g-direct")
    shared, agent_only = _role(roles, "Prozess", "g-1"), _role(roles, "Akten", "g-2")
    roles.add_member(shared.id, anna.id)
    issued = store.create_agent(name="Claude Code Anna", on_behalf_of=anna.id)
    for role in (shared, agent_only):
        store.add_to_role(role.id, issued.agent.id)
    _ok(_call(app.test_client(), issued.key, "knovas_query", {"question": QUESTION}))
    (query,) = _wire(app).reads("/secured/query")
    claim = _claim(app, query)
    assert (claim.subject, claim.groups) == (str(issued.agent.id), ("g-1",))
    assert query["json"]["actor"] == {"kind": "agent", "id": str(issued.agent.id),
                                      "on_behalf_of": {"kind": "user", "id": str(anna.id)}}


def test_every_offered_tool_reads_the_core_as_the_agent(platform_db, tmp_path, monkeypatch):
    app = _app(platform_db, tmp_path, monkeypatch)
    store, roles = AgentStore(platform_db), RoleStore(platform_db)
    issued = store.create_agent(name="Claude Code")
    store.add_to_role(_role(roles, "Prozess", "g-prozess").id, issued.agent.id)
    client = app.test_client()
    for name in sorted(_tools(client, issued.key)):
        before = len(_wire(app).sent)
        _ok(_call(client, issued.key, name, SAMPLE_ARGS[name]))
        sent = _wire(app).sent[before:]
        assert sent, f"{name} read nothing"
        for request in sent:
            claim = _claim(app, request)
            assert (claim.subject, claim.groups) == (str(issued.agent.id), ("g-prozess",)), name


def test_a_tool_takes_no_argument_it_does_not_offer_and_no_tool_it_does_not_list(platform_db, tmp_path,
                                                                                    monkeypatch):
    app = _app(platform_db, tmp_path, monkeypatch)
    key = AgentStore(platform_db).create_agent(name="Claude Code").key
    client = app.test_client()
    for tool, arguments in (("knovas_query", {"question": QUESTION, "access_groups": ["g-geheim"]}),
                            ("knovas_query", {"question": QUESTION, "tenant_id": "tenant-b"}),
                            ("knovas_query", {}),
                            ("knovas_save_result", {"text": "x"}),
                            ("knovas_fetch_original", {"pointer": HIT["pointer"]})):
        assert _refused(_call(client, key, tool, arguments)), (tool, arguments)
    assert _wire(app).sent == []


def test_an_agent_whose_claim_would_exceed_32_groups_reaches_nothing(platform_db, tmp_path, monkeypatch):
    app = _app(platform_db, tmp_path, monkeypatch)
    store, roles = AgentStore(platform_db), RoleStore(platform_db)
    issued = store.create_agent(name="Claude Code")
    for i in range(MAX_CLAIM_GROUPS + 1):
        role = _role(roles, f"Mandat {i:02d}", f"g-{i:02d}")
        platform_db.execute("INSERT INTO tenant_role_agents (role_id, agent_id) VALUES (%s, %s)",
                            (role.id, issued.agent.id))
    response = _call(app.test_client(), issued.key, "knovas_query", {"question": QUESTION})
    assert _refused(response) or response.status_code >= 400
    assert HIT["pointer"] not in response.get_data(as_text=True)
    assert _wire(app).sent == []


def test_a_wrong_expired_or_revoked_key_gets_the_same_401(platform_db, tmp_path, monkeypatch):
    app = _app(platform_db, tmp_path, monkeypatch)
    repo, store = UserRepository(platform_db), AgentStore(platform_db)
    anna = _someone(repo, "anna")
    good = store.create_agent(name="Gut").key
    expired = store.create_agent(name="Abgelaufen")
    platform_db.execute("UPDATE agent_keys SET created_at = now() - interval '2 days', "
                        "expires_at = now() - interval '1 day' WHERE agent_id = %s", (expired.agent.id,))
    revoked = store.create_agent(name="Widerrufen")
    store.revoke_agent(revoked.agent.id)
    for_anna = store.create_agent(name="Fuer Anna", on_behalf_of=anna.id)
    repo.disable(anna.id)
    unknown = "kna_zzzzzzzz_" + "A" * 43
    wrong_secret = good[:13] + ("B" if good[13] != "B" else "C") + good[14:]
    client = app.test_client()
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "knovas_query", "arguments": {"question": QUESTION}}}
    answers = []
    for key in (None, "", "kaputt", good + "x", unknown, wrong_secret, expired.key, revoked.key, for_anna.key):
        response = _mcp(client, key, body)
        answers.append((response.status_code, response.data, response.headers.get("WWW-Authenticate")))
    no_auth = client.post("/mcp", data=json.dumps(body), headers={"Content-Type": "application/json",
                                                                   "Authorization": f"Basic {good}"})
    answers.append((no_auth.status_code, no_auth.data, no_auth.headers.get("WWW-Authenticate")))
    assert len(set(answers)) == 1, answers
    status, data, challenge = answers[0]
    assert status == 401 and challenge and challenge.lower().startswith("bearer")
    assert _wire(app).sent == []
    assert _mcp(client, good, body).status_code == 200                     # the good key still works


def test_the_key_is_compared_in_constant_time(platform_db, tmp_path, monkeypatch):
    app = _app(platform_db, tmp_path, monkeypatch)
    good = AgentStore(platform_db).create_agent(name="Gut").key
    compared = []
    original = hmac.compare_digest

    def recording(a, b):
        compared.append(1)
        return original(a, b)

    monkeypatch.setattr(hmac, "compare_digest", recording)
    wrong = good[:13] + ("B" if good[13] != "B" else "C") + good[14:]
    response = _mcp(app.test_client(), wrong, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert response.status_code == 401
    assert compared, "the presented key was not compared with hmac.compare_digest"


def test_the_session_cookie_is_not_a_credential_and_the_endpoint_sets_none(platform_db, tmp_path, monkeypatch):
    app = _app(platform_db, tmp_path, monkeypatch)
    repo = UserRepository(platform_db)
    chef = _admin(repo)
    key = AgentStore(platform_db).create_agent(name="Claude Code").key
    browser = conftest._signed_in(app, chef.email)
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    refused = browser.post("/mcp", data=json.dumps(body), headers={"Content-Type": "application/json"})
    assert refused.status_code == 401
    answered = browser.post("/mcp", data=json.dumps(body), headers={"Content-Type": "application/json",
                                                                     "Authorization": f"Bearer {key}"})
    assert answered.status_code == 200
    assert "Set-Cookie" not in answered.headers and "Set-Cookie" not in refused.headers
    plain = app.test_client().post("/mcp", data=json.dumps(body), headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {key}"})  # no CSRF header: exempt
    assert plain.status_code == 200
    member = _someone(repo, "mia")
    repo.grant_role(member.id, "member")
    page = conftest._signed_in(app, member.email, with_csrf=False)
    assert page.post("/api/search", json={"query": "x"}).status_code == 403   # the header gate still stands


def test_the_endpoint_takes_post_only_and_refuses_a_foreign_origin(platform_db, tmp_path, monkeypatch):
    app = _app(platform_db, tmp_path, monkeypatch)
    key = AgentStore(platform_db).create_agent(name="Claude Code").key
    client = app.test_client()
    auth = {"Authorization": f"Bearer {key}"}
    assert client.get("/mcp", headers=auth).status_code == 405
    assert client.delete("/mcp", headers=auth).status_code == 405
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "knovas_query", "arguments": {"question": QUESTION}}}
    assert _mcp(client, key, body, headers={"Origin": "https://evil.example"}).status_code == 403
    assert _wire(app).sent == []
    assert _mcp(client, key, body, headers={"Origin": "http://localhost"}).status_code == 200


def test_an_unsupported_protocol_version_is_400(platform_db, tmp_path, monkeypatch):
    app = _app(platform_db, tmp_path, monkeypatch)
    key = AgentStore(platform_db).create_agent(name="Claude Code").key
    client = app.test_client()
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    for version in ("2026-07-28", "1900-01-01", "kaputt"):
        assert _mcp(client, key, body, headers={"MCP-Protocol-Version": version}).status_code == 400, version
    for version in ("2025-11-25", "2025-06-18", "2025-03-26"):
        assert _mcp(client, key, body, headers={"MCP-Protocol-Version": version}).status_code == 200, version
    assert _mcp(client, key, body).status_code == 200                       # no header: 2025-03-26


def test_each_tool_call_writes_one_audit_row_with_ids_only(platform_db, tmp_path, monkeypatch):
    app = _app(platform_db, tmp_path, monkeypatch)
    repo, store = UserRepository(platform_db), AgentStore(platform_db)
    anna = _someone(repo, "anna", display_name="Anna Zwiebel")
    issued = store.create_agent(name="Claude Code Zwiebel", on_behalf_of=anna.id)
    client = app.test_client()
    _tools(client, issued.key)
    assert _audit_rows(platform_db, "agent.call") == []                    # a listing is not a call
    _ok(_call(client, issued.key, "knovas_query", {"question": QUESTION}))
    (row,) = _audit_rows(platform_db, "agent.call")
    actor, _, target_type, target_id, outcome, detail, *rest = row
    assert (actor, target_type, target_id, outcome) == (None, "agent", str(issued.agent.id), "ok")
    text = json.dumps([str(v) for v in row])
    assert "knovas_query" in text and str(anna.id) in text
    for secret in (QUESTION, PASSAGE, HIT["pointer"], HIT["title"], "Zwiebel", issued.key, _secret(issued.key),
                   _sha(issued.key)):
        assert secret not in text, secret


def test_the_rate_limit_answers_429_and_nothing_reaches_the_core(platform_db, tmp_path, monkeypatch):
    app = _app(platform_db, tmp_path, monkeypatch, per_minute=3)
    store = AgentStore(platform_db)
    busy, other = store.create_agent(name="Eifrig").key, store.create_agent(name="Ruhig").key
    client = app.test_client()
    for _ in range(3):
        _ok(_call(client, busy, "knovas_query", {"question": QUESTION}))
    refused = _call(client, busy, "knovas_query", {"question": QUESTION})
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) >= 1
    assert len(_wire(app).reads("/secured/query")) == 3
    assert len(_audit_rows(platform_db, "agent.call")) == 3
    _ok(_call(client, other, "knovas_query", {"question": QUESTION}))     # per agent
    assert set(_tools(client, busy))                                       # only tool calls count


def test_revoking_on_the_page_acts_at_once(platform_db, tmp_path, monkeypatch):
    app = _app(platform_db, tmp_path, monkeypatch)
    repo, store = UserRepository(platform_db), AgentStore(platform_db)
    chef = _admin(repo)
    issued = store.create_agent(name="Copilot")
    agent_client = app.test_client()
    _ok(_call(agent_client, issued.key, "knovas_query", {"question": QUESTION}))
    browser = conftest._signed_in(app, chef.email)
    page = browser.post(_url(app, "admin.revoke_agent"),
                        data={"csrf_token": browser._token, "agent_id": str(issued.agent.id)})
    assert page.status_code == 200
    assert _call(agent_client, issued.key, "knovas_query", {"question": QUESTION}).status_code == 401
    assert store.get_agent(issued.agent.id).revoked is True
    (row,) = _audit_rows(platform_db, "agent.revoked")
    assert (str(row[0]), row[3]) == (str(chef.id), str(issued.agent.id))


def test_deactivating_the_person_stops_the_agent(platform_db, tmp_path, monkeypatch):
    app = _app(platform_db, tmp_path, monkeypatch)
    repo, store = UserRepository(platform_db), AgentStore(platform_db)
    anna = _someone(repo, "anna")
    issued = store.create_agent(name="Fuer Anna", on_behalf_of=anna.id)
    client = app.test_client()
    _ok(_call(client, issued.key, "knovas_query", {"question": QUESTION}))
    repo.disable(anna.id)
    assert _call(client, issued.key, "knovas_query", {"question": QUESTION}).status_code == 401
    assert len(_wire(app).reads("/secured/query")) == 1


def test_with_agents_switched_off_the_endpoint_is_404(platform_db, tmp_path, monkeypatch):
    app = _app(platform_db, tmp_path, monkeypatch, agents="aus", assistant="an")
    key = AgentStore(platform_db).create_agent(name="Claude Code").key
    client = app.test_client()
    for agent_key in (key, None):
        assert _call(client, agent_key, "knovas_query", {"question": QUESTION}).status_code == 404
    assert client.get("/mcp", headers={"Authorization": f"Bearer {key}"}).status_code == 404
    assert _wire(app).sent == [] and _audit_rows(platform_db, "agent.call") == []


def test_agents_on_and_the_assistant_off_works(platform_db, tmp_path, monkeypatch):
    app = _app(platform_db, tmp_path, monkeypatch, agents="an", assistant="aus")
    repo = UserRepository(platform_db)
    anna = _someone(repo, "anna")
    repo.grant_role(anna.id, "member")
    key = AgentStore(platform_db).create_agent(name="Copilot").key
    _ok(_call(app.test_client(), key, "knovas_query", {"question": QUESTION}))
    person = conftest._signed_in(app, anna.email)
    asked = person.post("/fragen", data={"csrf_token": person._token, "question": QUESTION})
    assert asked.status_code == 404                                        # the assistant stays off


# === 6. the assistant searches with its own claim (RB-6) ===============================================================


def test_the_assistant_searches_with_its_claim_narrowed_to_the_person(platform_db, tmp_path, monkeypatch):
    app = _app(platform_db, tmp_path, monkeypatch, agents="aus", assistant="an")
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

    sent = ask()
    queries = [s for s in sent if s["path"] == "/secured/query"]
    assert queries
    for query in queries:
        assert (_claim(app, query).subject, _claim(app, query).groups) == (str(assistant.id), ("g-1", "g-2"))
        assert query["json"]["actor"] == {"kind": "agent", "id": str(assistant.id),
                                          "on_behalf_of": {"kind": "user", "id": str(anna.id)}}
    store.remove_from_role(second.id, assistant.id)
    sent = ask()
    asked_core = [s for s in sent if s["path"] in ("/secured/query", "/secured/llm/generate")]
    assert [s["path"] for s in asked_core].count("/secured/query") >= 1
    for request in asked_core:
        claim = _claim(app, request)
        assert (claim.subject, claim.groups) == (str(assistant.id), ("g-1",)), request["path"]


# === 7. the console's agent pages ======================================================================================


def test_the_agent_pages_are_behind_the_admin_gate(platform_db, tmp_path, monkeypatch):
    app = _app(platform_db, tmp_path, monkeypatch)
    repo, store = UserRepository(platform_db), AgentStore(platform_db)
    existing = store.create_agent(name="Vorhanden").agent
    mia = _someone(repo, "mia")
    repo.grant_role(mia.id, "member")
    urls = {e: _url(app, e) for e in ("admin.agents", "admin.create_agent", "admin.revoke_agent")}
    assert app.test_client().get(urls["admin.agents"]).status_code in (302, 303)
    member = conftest._signed_in(app, mia.email)
    assert member.get(urls["admin.agents"]).status_code == 403
    assert member.post(urls["admin.create_agent"],
                       data={"csrf_token": member._token, "name": "Heimlich"}).status_code == 403
    assert member.post(urls["admin.revoke_agent"],
                       data={"csrf_token": member._token, "agent_id": str(existing.id)}).status_code == 403
    assert [a.name for a in store.list_agents() if a.kind == "external"] == ["Vorhanden"]
    assert store.get_agent(existing.id).revoked is False


def test_creating_and_revoking_need_the_forms_token(platform_db, tmp_path, monkeypatch):
    app = _app(platform_db, tmp_path, monkeypatch)
    repo, store = UserRepository(platform_db), AgentStore(platform_db)
    chef = _admin(repo)
    existing = store.create_agent(name="Vorhanden").agent
    browser = conftest._signed_in(app, chef.email)                       # sends the X-CSRF-Token header, no field
    assert browser.post(_url(app, "admin.create_agent"), data={"name": "Ohne Token"}).status_code == 400
    assert browser.post(_url(app, "admin.revoke_agent"), data={"agent_id": str(existing.id)}).status_code == 400
    assert [a.name for a in store.list_agents() if a.kind == "external"] == ["Vorhanden"]
    assert store.get_agent(existing.id).revoked is False


def test_the_page_shows_a_new_key_once_and_never_again(platform_db, tmp_path, monkeypatch):
    app = _app(platform_db, tmp_path, monkeypatch)
    repo, store = UserRepository(platform_db), AgentStore(platform_db)
    chef, anna = _admin(repo), _someone(repo, "anna")
    browser = conftest._signed_in(app, chef.email)
    created = browser.post(_url(app, "admin.create_agent"), data={
        "csrf_token": browser._token, "name": "Claude Code Anna", "on_behalf_of": str(anna.id), "days": "30"})
    assert created.status_code == 200
    found = KEY_RE.findall(created.get_data(as_text=True))
    assert len(set(found)) == 1
    key = found[0]
    (agent,) = [a for a in store.list_agents() if a.name == "Claude Code Anna"]
    assert (agent.on_behalf_of, agent.key_prefix) == (anna.id, key[:12])
    now = platform_db.execute("SELECT now()").fetchone()[0]
    assert timedelta(days=29, hours=23) < agent.expires_at - now <= timedelta(days=30, minutes=1)
    listed = browser.get(_url(app, "admin.agents"))
    assert listed.status_code == 200
    html = listed.get_data(as_text=True)
    assert KEY_RE.search(html) is None and _secret(key) not in html and _sha(key) not in html
    assert key[:12] in html and "Knovas Assistent" in html
    assert _mcp(app.test_client(), key, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code == 200
    (row,) = _audit_rows(platform_db, "agent.created")
    assert (str(row[0]), row[3]) == (str(chef.id), str(agent.id))
    assert _secret(key) not in json.dumps([str(v) for v in row]) and _sha(key) not in json.dumps([str(v) for v in row])


# === 8. logs ===========================================================================================================


def test_no_key_hash_question_or_name_reaches_a_log_line(platform_db, tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    app = _app(platform_db, tmp_path, monkeypatch, agents="an", assistant="an")
    repo, store, roles = UserRepository(platform_db), AgentStore(platform_db), RoleStore(platform_db)
    chef = _admin(repo)
    zora = _someone(repo, "zora", display_name="Zora Zwiebel")
    repo.grant_role(zora.id, "member")
    roles.add_member(_role(roles, "Mandat Zwiebel", "g-zwiebel").id, zora.id)
    browser = conftest._signed_in(app, chef.email)
    created = browser.post(_url(app, "admin.create_agent"), data={
        "csrf_token": browser._token, "name": "Agent Zwiebel", "on_behalf_of": str(zora.id)})
    key = KEY_RE.search(created.get_data(as_text=True)).group(0)
    (agent,) = [a for a in store.list_agents() if a.name == "Agent Zwiebel"]
    client = app.test_client()
    _mcp(client, key, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})
    for name in sorted(_tools(client, key)):
        _call(client, key, name, SAMPLE_ARGS[name])
    wrong = key[:13] + ("B" if key[13] != "B" else "C") + key[14:]
    for bad in (wrong, key + "x", "kna_zzzzzzzz_" + _secret(key)):
        assert _call(client, bad, "knovas_query", {"question": QUESTION}).status_code == 401
    person = conftest._signed_in(app, zora.email)
    person.post("/fragen", data={"csrf_token": person._token, "question": QUESTION})
    browser.post(_url(app, "admin.revoke_agent"), data={"csrf_token": browser._token, "agent_id": str(agent.id)})
    assert _call(client, key, "knovas_query", {"question": QUESTION}).status_code == 401
    lines = []
    for record in caplog.records:
        lines.append(record.getMessage())
        if record.exc_text:
            lines.append(record.exc_text)
    text = "\n".join(lines)
    for secret in (key, _secret(key), _sha(key), _sha(wrong), QUESTION, PASSAGE, HIT["pointer"]):
        assert secret not in text, secret[:20]
    leaked = [line for line in lines if "zwiebel" in line.lower()]
    assert leaked == [], leaked
