"""RB-6, RB-7: the agent store beyond the frozen acceptance file (identity/agents.py, migration 0028_agents).

What the frozen file (test_rb67_agents_acceptance.py) does not pin and the order asks for: the assistant joins roles
made by a grant too; Administration stays out of a claim even if a row said otherwise; the audit rows by id in the
change's transaction; a revoked agent joins no role; two additions at once cannot give an agent acting on its own a
33rd group; a person-bound agent and the assistant are not capped when they join (they are narrowed to a person).
Work order docs/platform-mvp/work-orders/RB-6-7.md.
"""
from __future__ import annotations

import json
import threading
import time
import uuid

import pytest

import conftest
from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

pytestmark = pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")

from identity.agents import (  # noqa: E402
    AgentError,
    AgentNotFound,
    AgentPrincipal,
    AgentStore,
)
from identity.roles import MAX_CLAIM_GROUPS, RoleStore, TooManyGroupsError  # noqa: E402


def _someone(repo, name, display_name=None):
    return repo.create(email=f"{name}@kanzlei.ch", display_name=display_name or name.capitalize(),
                       password=conftest.PASSWORD)


def _connect(conn):
    import psycopg

    schema = conn.execute("SELECT current_schema()").fetchone()[0]
    other = psycopg.connect(PLATFORM_DB_TEST_DSN, autocommit=True)
    other.execute(f'SET search_path TO "{schema}"')
    return other


def _audit(conn, action):
    return conn.execute("SELECT actor_user_id, target_type, target_id, outcome, detail, actor_email_snapshot "
                        "FROM audit_log WHERE action = %s ORDER BY id", (action,)).fetchall()


@pytest.fixture
def roles(platform_db):
    return RoleStore(platform_db)


@pytest.fixture
def agents(platform_db):
    return AgentStore(platform_db)


def test_a_role_made_by_granting_a_function_has_the_assistant_too(agents, roles, identity_repo):
    anna = _someone(identity_repo, "anna")
    identity_repo.grant_role(anna.id, "approver")                       # role_for_function creates the role
    (made,) = [r for r in roles.list_roles() if r.kind == "custom"]
    assert made.id in agents.assistant().roles
    assert roles.administration().id not in agents.assistant().roles


def test_administration_never_reaches_a_claim(platform_db, agents, roles, identity_repo):
    administration = roles.administration()
    platform_db.execute("UPDATE tenant_roles SET core_group_id = 'g-admin' WHERE id = %s", (administration.id,))
    agent = agents.create_agent(name="Claude Code")
    platform_db.execute("INSERT INTO tenant_role_agents (role_id, agent_id) VALUES (%s, %s)",
                        (administration.id, agent.id))                   # a row no store writes
    claim = agents.claim_for(agent.id)
    assert (claim.groups, claim.functions) == ((), ())


def test_every_change_is_audited_by_id_in_its_transaction(platform_db, agents, roles, identity_repo):
    chef = _someone(identity_repo, "chef", display_name="Chef Zwiebel")
    anna = _someone(identity_repo, "anna", display_name="Anna Zwiebel")
    role = roles.create_role(name="Mandat Zwiebel", core_group_id="g-zwiebel", by=chef.id)
    agent = agents.create_agent(name="Agent Zwiebel", on_behalf_of=anna.id, by=chef.id)
    agents.add_to_role(role.id, agent.id, by=chef.id)
    agents.add_to_role(role.id, agent.id, by=chef.id)                   # no change: no second row
    agents.remove_from_role(role.id, agent.id, by=chef.id)
    agents.remove_from_role(role.id, agent.id, by=chef.id)
    agents.revoke_agent(agent.id, by=chef.id)
    agents.revoke_agent(agent.id, by=chef.id)
    rows = {action: _audit(platform_db, action) for action in
            ("agent.created", "agent.role_added", "agent.role_removed", "agent.revoked")}
    assignments = [r for r in rows["agent.role_added"] if str(r[2]) == str(agent.id)]
    assert [len(rows["agent.created"]), len(assignments), len(rows["agent.role_removed"]),
            len(rows["agent.revoked"])] == [1, 1, 1, 1]
    for row in rows["agent.created"] + assignments + rows["agent.role_removed"] + rows["agent.revoked"]:
        assert (str(row[0]), row[1], row[2], row[3], row[5]) == (str(chef.id), "agent", str(agent.id), "ok", None)
        assert "zwiebel" not in json.dumps(row[4]).lower()
    assert rows["agent.created"][0][4] == {"kind": "external", "on_behalf_of": str(anna.id)}
    assert assignments[0][4] == {"role_id": str(role.id)}
    (joined,) = [r for r in rows["agent.role_added"] if str(r[2]) == str(agents.assistant().id)]
    assert joined[4] == {"role_id": str(role.id), "with_the_role": True} and str(joined[0]) == str(chef.id)


def test_a_change_by_an_unknown_person_is_not_made(agents, roles):
    role = roles.create_role(name="Prozess", core_group_id="g-1")
    agent = agents.create_agent(name="Claude Code")
    with pytest.raises(Exception):
        agents.add_to_role(role.id, agent.id, by=uuid.uuid4())         # no such person: nothing is written
    assert agents.get_agent(agent.id).roles == frozenset()


def test_a_revoked_agent_joins_no_role_and_an_unknown_one_is_not_found(agents, roles):
    role = roles.create_role(name="Prozess", core_group_id="g-1")
    agent = agents.create_agent(name="Claude Code")
    agents.revoke_agent(agent.id)
    with pytest.raises(AgentError):
        agents.add_to_role(role.id, agent.id)
    for unknown in (uuid.uuid4(), "kein-uuid", None):
        with pytest.raises(AgentNotFound):
            agents.add_to_role(role.id, unknown)
    assert agents.get_agent(agent.id).roles == frozenset()


def test_an_agent_on_its_own_acts_for_nobody_else(agents, identity_repo):
    anna = _someone(identity_repo, "anna")
    alone = agents.create_agent(name="Allein", on_behalf_of="")
    assert alone.on_behalf_of is None
    with pytest.raises(AgentError):
        agents.claim_for(alone.id, on_behalf_of=anna.id)
    bound = agents.create_agent(name="Fuer Anna", on_behalf_of=str(anna.id))
    assert agents.claim_for(bound.id, on_behalf_of=anna.id).on_behalf_of == anna.id


def test_the_assistant_cannot_act_for_an_inactive_person(agents, identity_repo):
    anna = _someone(identity_repo, "anna")
    identity_repo.disable(anna.id)
    for person in (anna.id, uuid.uuid4(), "kein-uuid"):
        with pytest.raises(AgentError):
            agents.claim_for(agents.assistant().id, on_behalf_of=person)


def test_the_claim_narrows_functions_to_the_persons(agents, roles, identity_repo):
    anna = _someone(identity_repo, "anna")
    identity_repo.grant_role(anna.id, "member")
    role = roles.create_role(name="Freigaben", functions={"approver"}, core_group_id="g-1")
    roles.add_member(role.id, anna.id)
    other = roles.create_role(name="Einlesen", functions={"ingestion_manager"}, core_group_id="g-2")
    claim = agents.claim_for(agents.assistant().id, on_behalf_of=anna.id)
    assert (claim.groups, claim.functions) == (("g-1",), ("approver", "member"))
    assert other.id in agents.assistant().roles                          # the assistant holds it; anna does not


def test_the_assistant_and_a_person_bound_agent_are_not_capped_when_they_join(agents, roles, identity_repo):
    anna = _someone(identity_repo, "anna")
    bound = agents.create_agent(name="Fuer Anna", on_behalf_of=anna.id)
    for i in range(MAX_CLAIM_GROUPS + 1):
        role = roles.create_role(name=f"Mandat {i:02d}", core_group_id=f"g-{i:02d}")
        agents.add_to_role(role.id, bound.id)                             # narrowed to anna, who holds none
    assert len(agents.get_agent(bound.id).roles) == MAX_CLAIM_GROUPS + 1
    assert len(agents.assistant().roles) == MAX_CLAIM_GROUPS + 2          # and Mitglied
    assert agents.claim_for(bound.id).groups == ()
    assert agents.claim_for(agents.assistant().id, on_behalf_of=anna.id).groups == ()


def test_a_group_may_be_taken_away_from_an_agent_over_the_cap(platform_db, agents, roles):
    agent = agents.create_agent(name="Zu viele")
    made = []
    for i in range(MAX_CLAIM_GROUPS + 1):
        role = roles.create_role(name=f"Mandat {i:02d}", core_group_id=f"g-{i:02d}")
        platform_db.execute("INSERT INTO tenant_role_agents (role_id, agent_id) VALUES (%s, %s)", (role.id, agent.id))
        made.append(role)
    roles.set_core_group(made[0].id, "g-00")                              # the same group: passes
    roles.set_core_group(made[0].id, None)                                # fewer: passes
    agents.remove_from_role(made[1].id, agent.id)
    assert len(agents.claim_for(agent.id).groups) == MAX_CLAIM_GROUPS - 1


def test_a_revoked_agent_on_its_own_does_not_block_a_roles_group(agents, roles):
    agent = agents.create_agent(name="Alt")
    plain = roles.create_role(name="Ohne Gruppe")
    agents.add_to_role(plain.id, agent.id)
    for i in range(MAX_CLAIM_GROUPS):
        role = roles.create_role(name=f"Mandat {i:02d}", core_group_id=f"g-{i:02d}")
        agents.add_to_role(role.id, agent.id)
    agents.revoke_agent(agent.id)
    assert roles.set_core_group(plain.id, "g-extra").core_group_id == "g-extra"


def test_two_additions_at_once_cannot_give_an_agent_a_33rd_group(platform_db, agents, roles):
    agent = agents.create_agent(name="Claude Code")
    for i in range(MAX_CLAIM_GROUPS - 1):
        agents.add_to_role(roles.create_role(name=f"Mandat {i:02d}", core_group_id=f"g-{i:02d}").id, agent.id)
    first = roles.create_role(name="Erste", core_group_id="g-erste")
    second = roles.create_role(name="Zweite", core_group_id="g-zweite")
    other = _connect(platform_db)
    outcome = {}

    def racer_adds():
        try:
            AgentStore(other).add_to_role(second.id, agent.id)
            outcome["second"] = "added"
        except TooManyGroupsError:
            outcome["second"] = "refused"

    try:
        with platform_db.transaction():
            agents.add_to_role(first.id, agent.id)
            racer = threading.Thread(target=racer_adds)
            racer.start()
            time.sleep(0.5)
            assert racer.is_alive(), "the second addition did not wait for the first"
        racer.join(10)
    finally:
        other.close()
    assert outcome == {"second": "refused"}
    assert len(agents.claim_for(agent.id).groups) == MAX_CLAIM_GROUPS


def test_the_principal_of_the_assistant_is_signed_with_its_claim(platform_db, agents, roles, identity_repo, tmp_path):
    from identity.assertion import AssertionVerifier
    from identity.broker_key import load_or_create_signer, public_pem
    from identity.principal import PrincipalBroker
    from identity.users import UserRepository

    anna = _someone(identity_repo, "anna")
    role = roles.create_role(name="Prozess", functions={"approver"}, core_group_id="g-1")
    roles.add_member(role.id, anna.id)
    identity_repo.grant_role(anna.id, "member")
    key_dir = tmp_path / "broker"
    key_dir.mkdir()
    broker = PrincipalBroker(user_repo=UserRepository(platform_db), signer=load_or_create_signer(key_dir),
                             tenant_id="tenant-a")
    assistant = agents.assistant()
    token = broker.assertion_for(AgentPrincipal(agent_id=assistant.id, on_behalf_of=anna.id))
    kid = (key_dir / "broker_ed25519.kid").read_text().strip()
    claim = AssertionVerifier({kid: public_pem(key_dir)}).verify(token, tenant="tenant-a")
    assert (claim.subject, claim.groups, claim.roles) == (str(assistant.id), ("g-1",), ("approver", "member"))
    person = AssertionVerifier({kid: public_pem(key_dir)}).verify(broker.assertion_for(identity_repo.get(anna.id)),
                                                                  tenant="tenant-a")
    assert person.subject == str(anna.id)                                 # a person is signed as before
    pool = broker.agent_assertions(assistant.id, on_behalf_of=anna.id, count=3, lifetime_seconds=300)
    assert len(set(pool)) == 3


def test_administrations_group_never_reaches_an_agent_through_another_role(platform_db, agents, roles):
    administration = roles.administration()
    platform_db.execute("UPDATE tenant_roles SET core_group_id = 'g-admin' WHERE id = %s", (administration.id,))
    twin = roles.create_role(name="Zwilling", core_group_id="g-admin")    # a role that carries the same group
    other = roles.create_role(name="Prozess", core_group_id="g-1")
    agent = agents.create_agent(name="Claude Code")
    for role in (twin, other):
        agents.add_to_role(role.id, agent.id)
    assert agents.claim_for(agent.id).groups == ("g-1",)
    assert twin.id in agents.assistant().roles
