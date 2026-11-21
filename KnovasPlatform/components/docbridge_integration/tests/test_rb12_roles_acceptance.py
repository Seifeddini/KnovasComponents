"""RB-1 and RB-2 acceptance tests: roles as bundles of functions, the two roles of a new installation, the move of an
existing installation, and a person's functions and groups read from roles (FEAT-2026-11-19-platform-mvp, design
docs/platform-mvp/design/role-builder.md § 1, § 2.1, § 2.3, § 3, § 6 rules 1 and 3, § 7).

FROZEN. Written for the coordinator before the build; a builder never edits this file. Its git blob id is in
docs/platform-mvp/frozen-tests.txt.

What is pinned:

1. Migration ``0027_roles`` adds new tables only. Every table that existed before keeps its columns, indexes,
   constraints and triggers; ``roles`` keeps its ten keys and ``user_roles`` keeps every row (the way back for one
   release).
2. A new installation has exactly two roles, «Administration» (function ``admin``) and «Mitglied» (function
   ``member``), without members and without a Core group: RB-1 writes nothing to the tenant, RB-4 links the groups.
   The first administrator the Platform creates at its first start is a member of Administration.
3. An existing installation ends in the same state and nobody loses a function. Administration holds everyone who held
   ``admin``, Mitglied holds every person, and there is one role per set of the other keys people hold (an
   administrator's other keys count). Nobody gains a function except ``member``. Every person passes exactly the
   gates they passed before. Starting the Platform on it applies the move and writes nothing to the tenant.
4. ``roles_of`` returns the functions of the person's roles; ``user_roles`` is no longer read where a gate looks,
   including the Experimente module's own two queries. ``grant_role`` and ``revoke_role`` keep their meaning, through
   roles. Every one of the ten functions opens its gates through a role, and leaving the role closes them.
5. ``access_groups_of`` returns the person's running direct grants plus the Core groups of their roles, sorted and
   deduplicated. A 33rd group is refused with TooManyGroupsError and nothing is written; no claim is signed with more
   than 32 groups.
6. The last active administrator cannot leave Administration. Administration and Mitglied cannot be deleted.
   Administration keeps the function ``admin``, and no other role holds it. Granting oneself a role needs no second
   administrator (founder, 21 November).
7. No role name, description, person name or address reaches a log line, DEBUG included.

The interface (``identity.roles``, RB-1): ``RoleStore(conn)`` with ``list_roles``, ``get_role``,
``administration``, ``mitglied``, ``create_role``, ``rename_role``, ``set_functions``, ``set_core_group``,
``delete_role``, ``add_member``, ``remove_member``, ``roles_of_user``; ``Role`` with ``id``, ``name``,
``description``, ``kind`` ("administration" | "mitglied" | "custom"), ``functions``, ``core_group_id``,
``members``; the errors ``RoleError`` and below it ``RoleNotFound``, ``RoleNameTaken``, ``UnknownFunction``,
``ProtectedRoleError``, ``LastAdministratorError``, ``TooManyGroupsError``; ``MAX_CLAIM_GROUPS``.

The expected answers of the gates are those of ``feat/platform-mvp`` at 2d755b17 for the same key sets, checked
before this file was frozen. Nothing here reaches a tenant: the Core, the Knovas Connector and the experiments index
are recording fakes.
"""
from __future__ import annotations

import base64
import functools
import json
import logging
import re
import uuid

import pytest
from flask import url_for

import conftest
from conftest import EXPERIMENTS_TEST_YAML, PLATFORM_DB_TEST_DSN, FakeGraphApi, FakeIndexClient, platform_db_reachable

pytestmark = pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")

from identity import migrate  # noqa: E402
from identity.roles import (  # noqa: E402
    MAX_CLAIM_GROUPS,
    LastAdministratorError,
    ProtectedRoleError,
    RoleError,
    RoleNameTaken,
    RoleNotFound,
    RoleStore,
    TooManyGroupsError,
    UnknownFunction,
)
from identity.users import UserRepository  # noqa: E402

#: The ten keys of the table ``roles``. From RB-1 on they are functions, never roles of their own (design § 2.1).
FUNCTIONS = ("admin", "approver", "ingestion_manager", "member", "experimenter", "experiments_manager",
             "builder_author", "builder_reviewer", "builder_publisher", "builder_viewer")
BUILDER = frozenset({"builder_author", "builder_reviewer", "builder_publisher", "builder_viewer"})
EXPERIMENT_VIEWERS = frozenset({"admin", "experimenter", "experiments_manager"})

#: The last migration before the role builder: an existing installation is a database migrated up to here.
BEFORE_ROLES = "0026_assistant_chats"

#: Gates that name a key exactly: (what, endpoint, method, the keys that pass it).
GATES = (
    ("Personen", "admin.people", "GET", frozenset({"admin"})),
    ("Freigaben", "admin.approvals", "GET", frozenset({"admin", "approver"})),
    ("Ingestion", "admin.ingestion", "GET", frozenset({"admin", "ingestion_manager"})),
    ("Builder", "builder.home", "GET", frozenset({"admin"}) | BUILDER),
    ("Experimente", "experiments.list_page", "GET", EXPERIMENT_VIEWERS),
    ("Experimente verwalten", "experiments.module_settings", "PUT", frozenset({"admin", "experiments_manager"})),
)

#: An existing installation's people and the keys they hold in ``user_roles``.
STAFF = {
    "chef": ("admin", "approver"),
    "ada": ("admin",),
    "otto": ("admin", "experimenter"),
    "mia": ("member",),
    "ines": ("member", "ingestion_manager"),
    "eva": ("experimenter", "experiments_manager"),
    "ewa": ("member", "experimenter", "experiments_manager"),
    "paul": ("approver",),
    "bea": ("builder_author", "builder_reviewer"),
    "nora": (),
}
#: The sets of other keys (all but admin and member) among STAFF: one role each.
OTHER_SETS = {frozenset(keys) - {"admin", "member"} for keys in STAFF.values()} - {frozenset()}


# --- fakes: nothing may be written to a tenant ---------------------------------------------------------------------

_WRITE = re.compile(r"^(graph_)?(create|rename|move|delete|set|update|put|patch|post|install|deprecate|retire|"
                    r"upload|purge|push|start|stop|requeue|reextract|apply|grant|revoke)(_|$)")


class RecordingCore(FakeGraphApi):
    """The Core as the app sees it. Every call whose name says it writes is recorded, with the calls that creating,
    renaming or deleting a role's Core group would make (design § 3)."""

    writes: list = []

    def __getattribute__(self, name):
        attr = object.__getattribute__(self, name)
        if _WRITE.match(name) and callable(attr):
            def recorded(*args, **kwargs):
                RecordingCore.writes.append(name)
                return attr(*args, **kwargs)
            return recorded
        return attr

    def create_access_group(self, name, parent=None):
        return {"group_id": f"g-{uuid.uuid4().hex[:8]}", "name": name, "parent_id": parent}

    def rename_access_group(self, identifier, name):
        return {"group_id": identifier, "name": name}

    def delete_access_group(self, identifier):
        return True


class RecordingConnector:
    """The Knovas Connector the Ingestion page asks for its state; writes are recorded."""

    writes: list = []

    def __init__(self, base_url=None, *, principal_broker=None, session=None, timeout=20.0):
        self.base_url = base_url
        self.principal_broker = principal_broker

    def status(self):
        return {"scheduler_state": "idle"}

    def __getattr__(self, name):
        def call(*args, **kwargs):
            if _WRITE.match(name):
                RecordingConnector.writes.append(name)
            return {}
        return call


def _app(conn, tmp_path, monkeypatch):
    """The real app over ``conn``'s schema, with the Experimente module on and recording fakes for every service."""
    RecordingCore.writes.clear()
    RecordingConnector.writes.clear()
    index = FakeIndexClient()
    monkeypatch.setenv("ONTOLOGY_SOURCE", "graph")
    monkeypatch.setattr("experiments.indexer.make_index_client", lambda config: index)
    monkeypatch.setattr("knovas_connector_client.KnovasConnectorClient", RecordingConnector)
    app = conftest._identity_app(conn, tmp_path, monkeypatch, client_cls=RecordingCore,
                                 extra_yaml=EXPERIMENTS_TEST_YAML)
    app.config["RB12_INDEX"] = index
    return app


def _no_tenant_write(app):
    assert RecordingCore.writes == [], f"written to the Core: {RecordingCore.writes}"
    assert RecordingConnector.writes == [], f"written to the Knovas Connector: {RecordingConnector.writes}"
    index = app.config["RB12_INDEX"]
    assert index.uploads == [] and index.deleted == [], "written to the experiments index"


# --- gates -----------------------------------------------------------------------------------------------------------


def _refused(what, keys):
    if what == "Experimente verwalten" and keys & EXPERIMENT_VIEWERS:
        return 403
    return 404 if what.startswith("Experimente") else 403


def _answers(app, client):
    with app.test_request_context():
        urls = {what: url_for(endpoint) for what, endpoint, _, _ in GATES}
    seen = {}
    for what, _, method, _ in GATES:
        if method == "PUT":
            seen[what] = client.open(urls[what], method="PUT", json={"show_in_search": False}).status_code
        else:
            seen[what] = client.get(urls[what], follow_redirects=True).status_code
    return seen


def _check_gates(app, client, keys):
    keys = frozenset(keys)
    expected = {what: 200 if keys & allowed else _refused(what, keys) for what, _, _, allowed in GATES}
    assert _answers(app, client) == expected


def _check_functions(conn, repo, user_id, keys):
    """The checks outside the routes: the Builder's permissions, and the Experimente module's own two queries."""
    from builder import team
    from experiments import store as experiments_store

    keys = frozenset(keys)
    expected = frozenset().union(*(team.ROLE_PERMISSIONS.get(k, frozenset()) for k in keys))
    assert team.permissions_of(repo.get(user_id).roles) == expected
    sees = bool(keys & EXPERIMENT_VIEWERS)
    assert experiments_store.user_can_view_experiments(conn, user_id) is sees
    email = str(repo.get(user_id).email)
    listed = [row["user"] for row in experiments_store.viewers_without_groups(conn, ["g-rb12-none"])]
    assert any(email in entry for entry in listed) is sees


# --- helpers ---------------------------------------------------------------------------------------------------------


def _ids(role):
    return {str(m) for m in role.members}


def _someone(repo, name, **kwargs):
    return repo.create(email=f"{name}@kanzlei.ch", display_name=kwargs.pop("display_name", name.capitalize()),
                       password=conftest.PASSWORD, **kwargs)


def _groups(count, prefix="g"):
    return [f"{prefix}-{i:02d}" for i in range(count)]


def _claim(repo, user_id):
    from identity.assertion import AssertionSigner, generate_keypair
    from identity.principal import PrincipalBroker

    signer = AssertionSigner(generate_keypair().private_pem, key_id="rb12")
    token = PrincipalBroker(user_repo=repo, signer=signer, tenant_id="tenant-a").assertion_for(repo.get(user_id))
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


@functools.lru_cache(maxsize=1)
def _password_hash():
    from identity import passwords

    return passwords.hash_password(conftest.PASSWORD)


@pytest.fixture
def roles(platform_db):
    return RoleStore(platform_db)


# --- an existing installation ----------------------------------------------------------------------------------------


class Installation:
    """A database as an installation leaves it before RB-1: people and their keys in ``user_roles``."""

    def __init__(self, conn):
        self.conn = conn
        self.people: dict[str, object] = {}

    def person(self, name, *keys, display_name=None, status="active"):
        user_id = self.conn.execute(
            "INSERT INTO users (email, display_name, password_hash, status) VALUES (%s, %s, %s, %s) RETURNING id",
            (f"{name}@kanzlei.ch", display_name or name.capitalize(), _password_hash(), status)).fetchone()[0]
        for key in keys:
            added = self.conn.execute(
                "INSERT INTO user_roles (user_id, role_id) SELECT %s, id FROM roles WHERE key = %s",
                (user_id, key)).rowcount
            assert added == 1, key
        self.people[name] = user_id
        return user_id

    def staff(self):
        for name, keys in STAFF.items():
            self.person(name, *keys)
        return self

    def user_roles(self):
        return sorted(self.conn.execute(
            "SELECT user_id::text, role_id, granted_at, granted_by FROM user_roles").fetchall())

    def move(self):
        migrate.apply(self.conn)
        return self


@pytest.fixture
def installation():
    """An existing installation: a schema of its own, migrated up to BEFORE_ROLES and not further."""
    import psycopg

    schema = f"t{uuid.uuid4().hex[:12]}"
    with psycopg.connect(PLATFORM_DB_TEST_DSN, autocommit=True) as setup:
        setup.execute(f'CREATE SCHEMA "{schema}"')
    conn = psycopg.connect(PLATFORM_DB_TEST_DSN, autocommit=True)
    conn.execute(f'SET search_path TO "{schema}"')
    try:
        migrate.apply(conn, [m for m in migrate.discover() if m.version <= BEFORE_ROLES])
        yield Installation(conn)
    finally:
        conn.close()
        with psycopg.connect(PLATFORM_DB_TEST_DSN, autocommit=True) as cleanup:
            cleanup.execute(f'DROP SCHEMA "{schema}" CASCADE')


def _shape(conn):
    """Columns, indexes, constraints and triggers of every table in the schema, by table."""
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


# === 1. the migration: new tables only ================================================================================


def test_the_migration_is_0027_roles():
    versions = [m.version for m in migrate.discover()]
    assert "0027_roles" in versions
    assert [v for v in versions if v.startswith("0027_")] == ["0027_roles"]
    assert versions.index("0027_roles") > versions.index(BEFORE_ROLES)


def test_the_migration_adds_tables_and_changes_no_existing_one(installation):
    installation.staff()
    before = _shape(installation.conn)
    keys = installation.conn.execute("SELECT id, key, label, description, is_builtin FROM roles ORDER BY id").fetchall()
    rows = installation.user_roles()
    installation.move()
    after = _shape(installation.conn)
    assert {table: after.get(table) for table in before} == before
    assert set(after) - set(before), "no new table"
    assert installation.conn.execute(
        "SELECT id, key, label, description, is_builtin FROM roles ORDER BY id").fetchall() == keys
    assert installation.user_roles() == rows                      # kept untouched: the way back for one release


def test_the_ten_keys_are_still_there_as_functions(platform_db):
    assert set(FUNCTIONS) <= {r[0] for r in platform_db.execute("SELECT key FROM roles")}   # RB-10 may add more


# === 2. a new installation ============================================================================================


def test_a_new_installation_has_exactly_administration_and_mitglied(roles):
    found = {r.kind: r for r in roles.list_roles()}
    assert set(found) == {"administration", "mitglied"}
    assert len(roles.list_roles()) == 2
    assert (found["administration"].name, set(found["administration"].functions)) == ("Administration", {"admin"})
    assert (found["mitglied"].name, set(found["mitglied"].functions)) == ("Mitglied", {"member"})
    assert roles.administration().id == found["administration"].id
    assert roles.mitglied().id == found["mitglied"].id


def test_the_two_roles_start_without_members_and_without_a_core_group(roles):
    for role in (roles.administration(), roles.mitglied()):
        assert _ids(role) == set()
        assert role.core_group_id is None


def test_the_first_start_puts_the_first_administrator_into_administration(platform_db, tmp_path, monkeypatch):
    app = _app(platform_db, tmp_path, monkeypatch)
    first = platform_db.execute("SELECT id FROM users WHERE email = 'bootstrap@kanzlei.ch'").fetchone()[0]
    store = RoleStore(platform_db)
    assert _ids(store.administration()) == {str(first)}
    assert "admin" in UserRepository(platform_db).roles_of(first)
    assert len(store.list_roles()) == 2
    assert all(r.core_group_id is None for r in store.list_roles())   # RB-4 links the groups, not the start
    _no_tenant_write(app)


# === 3. an existing installation ======================================================================================


def test_an_existing_installation_gets_the_two_roles_and_one_role_per_other_key_set(installation):
    store = RoleStore(installation.staff().move().conn)
    listed = store.list_roles()
    kinds = sorted(r.kind for r in listed)
    assert kinds.count("administration") == 1 and kinds.count("mitglied") == 1
    assert set(store.administration().functions) == {"admin"} and store.administration().name == "Administration"
    assert set(store.mitglied().functions) == {"member"} and store.mitglied().name == "Mitglied"
    custom = [r for r in listed if r.kind == "custom"]
    assert sorted(sorted(r.functions) for r in custom) == sorted(sorted(s) for s in OTHER_SETS)
    assert len({r.name.casefold() for r in listed}) == len(listed)
    assert all(r.name.strip() and r.name not in ("Administration", "Mitglied") for r in custom)
    assert all(r.core_group_id is None for r in listed)            # RB-1 writes nothing to the tenant


def test_every_person_joins_mitglied_and_every_administrator_joins_administration(installation):
    store = RoleStore(installation.staff().move().conn)
    people = {str(uid) for uid in installation.people.values()}
    admins = {str(installation.people[n]) for n, keys in STAFF.items() if "admin" in keys}
    assert _ids(store.mitglied()) == people
    assert _ids(store.administration()) == admins


@pytest.mark.parametrize("keys", sorted(sorted(s) for s in OTHER_SETS), ids=lambda keys: "+".join(keys))
def test_the_people_of_one_key_set_share_one_role(keys, installation):
    store = RoleStore(installation.staff().move().conn)
    role, = [r for r in store.list_roles() if r.kind == "custom" and set(r.functions) == set(keys)]
    holders = {str(installation.people[n]) for n, held in STAFF.items()
               if frozenset(held) - {"admin", "member"} == frozenset(keys)}
    assert _ids(role) == holders


def test_nobody_loses_a_function_and_nobody_gains_one_but_member(installation):
    repo = UserRepository(installation.staff().move().conn)
    for name, keys in STAFF.items():
        held = set(repo.roles_of(installation.people[name]))
        assert set(keys) <= held, name
        assert held - set(keys) <= {"member"}, name


def test_an_administrators_other_keys_are_kept_in_a_role_of_their_own(installation):
    store = RoleStore(installation.staff().move().conn)
    otto = installation.people["otto"]
    assert {frozenset(r.functions) for r in store.roles_of_user(otto)} == {
        frozenset({"admin"}), frozenset({"member"}), frozenset({"experimenter"})}


def test_an_installation_where_nobody_holds_another_key_ends_with_exactly_the_two_roles(installation):
    installation.person("ada", "admin")
    installation.person("max", "admin", "member")
    installation.person("mia", "member")
    installation.person("neu")
    store = RoleStore(installation.move().conn)
    assert sorted(r.kind for r in store.list_roles()) == ["administration", "mitglied"]
    assert len(_ids(store.mitglied())) == 4 and len(_ids(store.administration())) == 2


def test_user_roles_is_no_longer_read(installation):
    conn = installation.staff().move().conn
    repo = UserRepository(conn)
    chef, mia = installation.people["chef"], installation.people["mia"]
    conn.execute("DELETE FROM user_roles WHERE user_id = %s", (chef,))
    assert {"admin", "approver"} <= set(repo.roles_of(chef))
    conn.execute("INSERT INTO user_roles (user_id, role_id) SELECT %s, id FROM roles "
                 "WHERE key IN ('admin', 'experimenter')", (mia,))
    assert set(repo.roles_of(mia)) == {"member"}
    from experiments import store as experiments_store
    assert experiments_store.user_can_view_experiments(conn, mia) is False


def test_starting_the_platform_moves_an_existing_installation(installation, tmp_path, monkeypatch):
    installation.staff()
    app = _app(installation.conn, tmp_path, monkeypatch)              # the start applies the migration
    versions = {r[0] for r in installation.conn.execute("SELECT version FROM schema_migrations")}
    assert "0027_roles" in versions
    assert len(RoleStore(installation.conn).list_roles()) == 2 + len(OTHER_SETS)
    assert all(r.core_group_id is None for r in RoleStore(installation.conn).list_roles())
    assert installation.conn.execute(
        "SELECT count(*) FROM users WHERE email = 'bootstrap@kanzlei.ch'").fetchone()[0] == 0
    _no_tenant_write(app)


@pytest.mark.parametrize("name", sorted(STAFF))
def test_after_the_move_a_person_passes_exactly_the_gates_their_keys_passed(name, installation, tmp_path,
                                                                            monkeypatch):
    installation.staff()
    app = _app(installation.conn, tmp_path, monkeypatch)
    client = conftest._signed_in(app, f"{name}@kanzlei.ch")
    _check_gates(app, client, STAFF[name])
    _check_functions(installation.conn, UserRepository(installation.conn), installation.people[name],
                     set(STAFF[name]) | {"member"})
    _no_tenant_write(app)


# === 4. roles_of reads roles ==========================================================================================


@pytest.mark.parametrize("function", FUNCTIONS)
def test_a_function_opens_its_gates_through_a_role_and_leaving_the_role_closes_them(function, platform_db,
                                                                                   tmp_path, monkeypatch):
    """For ``approver``: a member of a role with the function passes the approver gate (Freigaben); taking the
    person out of the role takes it away. ``admin`` is Administration's (the first start's administrator stays in it,
    so this person may leave); every other function is given by a role of the tenant's own."""
    app = _app(platform_db, tmp_path, monkeypatch)
    repo, store = UserRepository(platform_db), RoleStore(platform_db)
    person = _someone(repo, function.replace("_", "-"))
    role = store.administration() if function == "admin" else store.create_role(name=f"Nur {function}",
                                                                                 functions={function})
    store.add_member(role.id, person.id)
    client = conftest._signed_in(app, person.email)
    assert repo.get(person.id).roles == {function}
    _check_gates(app, client, {function})
    _check_functions(platform_db, repo, person.id, {function})

    store.remove_member(role.id, person.id)
    assert repo.get(person.id).roles == frozenset()
    _check_gates(app, client, set())                                  # the same session: read on every request
    _check_functions(platform_db, repo, person.id, set())
    _no_tenant_write(app)


def test_roles_of_is_the_union_of_the_functions_of_the_persons_roles(roles, identity_repo):
    person = _someone(identity_repo, "anna")
    first = roles.create_role(name="Freigabe", functions={"approver"})
    second = roles.create_role(name="Experimente", functions={"experimenter", "experiments_manager"})
    roles.add_member(first.id, person.id)
    roles.add_member(second.id, person.id)
    assert identity_repo.roles_of(person.id) == {"approver", "experimenter", "experiments_manager"}
    assert {r.id for r in roles.roles_of_user(person.id)} == {first.id, second.id}


def test_changing_a_roles_functions_changes_its_members_functions(roles, identity_repo):
    person = _someone(identity_repo, "anna")
    role = roles.create_role(name="Sekretariat", functions={"approver"})
    roles.add_member(role.id, person.id)
    roles.set_functions(role.id, {"ingestion_manager", "member"})
    assert identity_repo.roles_of(person.id) == {"ingestion_manager", "member"}
    assert set(roles.get_role(role.id).functions) == {"ingestion_manager", "member"}


def test_grant_role_gives_the_function_through_a_role_and_not_through_user_roles(roles, identity_repo, platform_db):
    person = _someone(identity_repo, "anna")
    identity_repo.grant_role(person.id, "approver")
    identity_repo.grant_role(person.id, "admin")
    identity_repo.grant_role(person.id, "member")
    assert identity_repo.roles_of(person.id) == {"approver", "admin", "member"}
    assert str(person.id) in _ids(roles.administration())
    assert str(person.id) in _ids(roles.mitglied())
    assert any("approver" in r.functions for r in roles.roles_of_user(person.id) if r.kind == "custom")
    assert platform_db.execute("SELECT count(*) FROM user_roles WHERE user_id = %s",
                               (person.id,)).fetchone()[0] == 0


def test_revoke_role_takes_exactly_that_function_away(roles, identity_repo):
    person = _someone(identity_repo, "anna")
    role = roles.create_role(name="Experimente", functions={"experimenter", "experiments_manager"})
    roles.add_member(role.id, person.id)
    identity_repo.revoke_role(person.id, "experimenter")
    assert identity_repo.roles_of(person.id) == {"experiments_manager"}
    identity_repo.grant_role(person.id, "approver")
    identity_repo.revoke_role(person.id, "approver")
    assert identity_repo.roles_of(person.id) == {"experiments_manager"}


# === 5. access groups and the 32-group cap ============================================================================


def test_access_groups_are_the_direct_grants_and_the_groups_of_the_persons_roles(roles, identity_repo):
    person = _someone(identity_repo, "anna")
    identity_repo.grant_access_group(person.id, "g-direct")
    identity_repo.grant_access_group(person.id, "g-both")
    first = roles.create_role(name="Prozess", functions=(), core_group_id="g-both")
    second = roles.create_role(name="Personal", functions=(), core_group_id="g-role")
    roles.add_member(first.id, person.id)
    roles.add_member(second.id, person.id)
    assert identity_repo.access_groups_of(person.id) == ("g-both", "g-direct", "g-role")


def test_an_ended_direct_grant_does_not_count_and_a_role_without_a_group_adds_none(roles, identity_repo):
    person = _someone(identity_repo, "anna")
    identity_repo.grant_access_group(person.id, "g-ended", ends_on="2020-01-01")
    roles.add_member(roles.mitglied().id, person.id)
    role = roles.create_role(name="Personal", functions=(), core_group_id="g-role")
    roles.add_member(role.id, person.id)
    assert identity_repo.access_groups_of(person.id) == ("g-role",)


def test_the_claim_carries_the_groups_and_functions_of_the_roles_and_loses_them_with_the_role(roles, identity_repo):
    person = _someone(identity_repo, "anna")
    identity_repo.grant_access_group(person.id, "g-direct")
    role = roles.create_role(name="Personal", functions={"approver"}, core_group_id="g-role")
    roles.add_member(role.id, person.id)
    claim = _claim(identity_repo, person.id)
    assert (claim["grp"], claim["rol"]) == (["g-direct", "g-role"], ["approver"])
    roles.remove_member(role.id, person.id)
    claim = _claim(identity_repo, person.id)
    assert (claim["grp"], claim["rol"]) == (["g-direct"], [])


def test_32_groups_are_allowed_and_signed(roles, identity_repo):
    assert MAX_CLAIM_GROUPS == 32
    person = _someone(identity_repo, "anna")
    identity_repo.set_access_groups(person.id, _groups(31))
    role = roles.create_role(name="Personal", functions=(), core_group_id="g-role")
    roles.add_member(role.id, person.id)
    assert len(identity_repo.access_groups_of(person.id)) == 32
    assert len(_claim(identity_repo, person.id)["grp"]) == 32


def test_a_33rd_group_through_a_role_is_refused_and_nothing_is_written(roles, identity_repo):
    person = _someone(identity_repo, "anna")
    identity_repo.set_access_groups(person.id, _groups(32))
    role = roles.create_role(name="Personal", functions={"approver"}, core_group_id="g-extra")
    with pytest.raises(TooManyGroupsError) as refused:
        roles.add_member(role.id, person.id)
    assert isinstance(refused.value, RoleError)
    assert "32" in str(refused.value)
    assert "anna" not in str(refused.value).lower()
    assert _ids(roles.get_role(role.id)) == set()
    assert roles.roles_of_user(person.id) == []
    assert identity_repo.access_groups_of(person.id) == tuple(_groups(32))
    assert "approver" not in identity_repo.roles_of(person.id)


def test_a_group_the_person_holds_already_does_not_count_twice(roles, identity_repo):
    person = _someone(identity_repo, "anna")
    identity_repo.set_access_groups(person.id, _groups(32))
    role = roles.create_role(name="Personal", functions=(), core_group_id="g-07")
    roles.add_member(role.id, person.id)
    assert identity_repo.access_groups_of(person.id) == tuple(_groups(32))


def test_a_33rd_direct_grant_is_refused_and_nothing_is_written(roles, identity_repo):
    person = _someone(identity_repo, "anna")
    role = roles.create_role(name="Personal", functions=(), core_group_id="g-role")
    roles.add_member(role.id, person.id)
    identity_repo.set_access_groups(person.id, _groups(31))
    with pytest.raises(TooManyGroupsError):
        identity_repo.grant_access_group(person.id, "g-new")
    assert identity_repo.access_groups_of(person.id) == tuple(sorted(_groups(31) + ["g-role"]))
    with pytest.raises(TooManyGroupsError):
        identity_repo.set_access_groups(person.id, _groups(32, prefix="h"))
    assert identity_repo.access_groups_of(person.id) == tuple(sorted(_groups(31) + ["g-role"]))


def test_giving_a_role_a_group_is_refused_when_a_member_would_hold_33(roles, identity_repo):
    person = _someone(identity_repo, "anna")
    identity_repo.set_access_groups(person.id, _groups(32))
    role = roles.create_role(name="Personal", functions=())
    roles.add_member(role.id, person.id)
    with pytest.raises(TooManyGroupsError):
        roles.set_core_group(role.id, "g-extra")
    assert roles.get_role(role.id).core_group_id is None
    assert identity_repo.access_groups_of(person.id) == tuple(_groups(32))


def test_no_claim_is_signed_with_more_than_32_groups(identity_repo, platform_db):
    person = _someone(identity_repo, "anna")
    for group in _groups(33):                                        # stored before the cap existed
        platform_db.execute("INSERT INTO user_access_groups (user_id, group_id) VALUES (%s, %s)", (person.id, group))
    with pytest.raises(TooManyGroupsError):
        _claim(identity_repo, person.id)


# === 6. the last administrator, the protected roles, granting oneself a role ==========================================


def test_the_last_administrator_cannot_leave_administration(roles, identity_repo):
    chef = _someone(identity_repo, "chef")
    roles.add_member(roles.administration().id, chef.id)
    with pytest.raises(LastAdministratorError) as refused:
        roles.remove_member(roles.administration().id, chef.id)
    assert isinstance(refused.value, RoleError)
    assert _ids(roles.administration()) == {str(chef.id)}
    assert "admin" in identity_repo.roles_of(chef.id)


def test_with_two_administrators_one_may_leave(roles, identity_repo):
    chef, vize = _someone(identity_repo, "chef"), _someone(identity_repo, "vize")
    roles.add_member(roles.administration().id, chef.id)
    roles.add_member(roles.administration().id, vize.id)
    roles.remove_member(roles.administration().id, vize.id)
    assert _ids(roles.administration()) == {str(chef.id)}
    assert "admin" not in identity_repo.roles_of(vize.id)


def test_a_deactivated_administrator_does_not_count(roles, identity_repo):
    chef, alt = _someone(identity_repo, "chef"), _someone(identity_repo, "alt")
    roles.add_member(roles.administration().id, chef.id)
    roles.add_member(roles.administration().id, alt.id)
    identity_repo.disable(alt.id)
    with pytest.raises(LastAdministratorError):
        roles.remove_member(roles.administration().id, chef.id)
    roles.remove_member(roles.administration().id, alt.id)
    assert _ids(roles.administration()) == {str(chef.id)}


def test_revoking_the_last_administrators_key_is_refused_too(roles, identity_repo):
    chef = _someone(identity_repo, "chef")
    identity_repo.grant_role(chef.id, "admin")
    with pytest.raises(LastAdministratorError):
        identity_repo.revoke_role(chef.id, "admin")
    assert "admin" in identity_repo.roles_of(chef.id)


def test_administration_and_mitglied_cannot_be_deleted(roles, identity_repo):
    for name in ("chef", "vize"):
        roles.add_member(roles.administration().id, _someone(identity_repo, name).id)
    for role in (roles.administration(), roles.mitglied()):
        with pytest.raises(ProtectedRoleError):
            roles.delete_role(role.id)
    assert sorted(r.kind for r in roles.list_roles()) == ["administration", "mitglied"]


def test_administration_keeps_the_admin_function(roles, identity_repo):
    for name in ("chef", "vize"):
        roles.add_member(roles.administration().id, _someone(identity_repo, name).id)
    with pytest.raises(ProtectedRoleError):
        roles.set_functions(roles.administration().id, {"approver"})
    with pytest.raises(ProtectedRoleError):
        roles.set_functions(roles.administration().id, set())
    assert set(roles.administration().functions) == {"admin"}


def test_no_other_role_holds_the_admin_function(roles):
    with pytest.raises(ProtectedRoleError):
        roles.create_role(name="Partner", functions={"admin", "approver"})
    assert [r for r in roles.list_roles() if r.name == "Partner"] == []
    role = roles.create_role(name="Partner", functions={"approver"})
    with pytest.raises(ProtectedRoleError):
        roles.set_functions(role.id, {"admin", "approver"})
    assert set(roles.get_role(role.id).functions) == {"approver"}


def test_a_person_may_give_themselves_a_role_without_a_second_administrator(roles, identity_repo, platform_db):
    chef = _someone(identity_repo, "chef")
    roles.add_member(roles.administration().id, chef.id)
    role = roles.create_role(name="Freigabe", functions={"approver"})
    roles.add_member(role.id, chef.id, by=chef.id)
    assert "approver" in identity_repo.roles_of(chef.id)
    assert platform_db.execute("SELECT count(*) FROM approval_requests").fetchone()[0] == 0


# === the store: create, rename, delete, names, unknown ids ============================================================


def test_a_role_is_created_renamed_and_deleted(roles, identity_repo):
    person = _someone(identity_repo, "anna")
    role = roles.create_role(name="Sekretariat", functions={"approver", "ingestion_manager"},
                             description="Empfang und Ablage", core_group_id="g-sek")
    assert (role.kind, role.name, role.description, role.core_group_id) == (
        "custom", "Sekretariat", "Empfang und Ablage", "g-sek")
    assert set(role.functions) == {"approver", "ingestion_manager"}
    assert role.id in {r.id for r in roles.list_roles()}
    roles.add_member(role.id, person.id)

    renamed = roles.rename_role(role.id, name="Sekretariat Zürich")
    assert (renamed.id, renamed.name, renamed.core_group_id) == (role.id, "Sekretariat Zürich", "g-sek")
    assert _ids(renamed) == {str(person.id)}

    roles.delete_role(role.id)
    assert role.id not in {r.id for r in roles.list_roles()}
    with pytest.raises(RoleNotFound):
        roles.get_role(role.id)
    assert identity_repo.roles_of(person.id) == frozenset()


def test_role_names_are_unique(roles):
    role = roles.create_role(name="Sekretariat", functions={"approver"})
    count = len(roles.list_roles())
    for taken in ("Sekretariat", "Administration", "Mitglied"):
        with pytest.raises(RoleNameTaken):
            roles.create_role(name=taken, functions=())
    with pytest.raises(RoleNameTaken):
        roles.rename_role(role.id, name="Mitglied")
    assert len(roles.list_roles()) == count
    assert roles.get_role(role.id).name == "Sekretariat"


def test_a_function_outside_the_ten_is_refused_and_nothing_is_written(roles):
    with pytest.raises(UnknownFunction):
        roles.create_role(name="Zauberei", functions={"wizard"})
    assert [r for r in roles.list_roles() if r.name == "Zauberei"] == []
    role = roles.create_role(name="Freigabe", functions={"approver"})
    with pytest.raises(UnknownFunction):
        roles.set_functions(role.id, {"approver", "wizard"})
    assert set(roles.get_role(role.id).functions) == {"approver"}


def test_an_unknown_role_is_a_named_error(roles, identity_repo):
    person = _someone(identity_repo, "anna")
    missing = uuid.uuid4()
    for call in (lambda: roles.get_role(missing), lambda: roles.add_member(missing, person.id),
                 lambda: roles.rename_role(missing, name="X"), lambda: roles.delete_role(missing),
                 lambda: roles.get_role("kein-uuid")):
        with pytest.raises(RoleNotFound):
            call()


def test_a_removed_person_leaves_their_roles(roles, identity_repo, platform_db):
    person = _someone(identity_repo, "anna")
    role = roles.create_role(name="Freigabe", functions={"approver"})
    roles.add_member(role.id, person.id)
    platform_db.execute("DELETE FROM users WHERE id = %s", (person.id,))
    assert _ids(roles.get_role(role.id)) == set()


# === 7. logs ==========================================================================================================


def test_no_role_or_person_name_reaches_a_log_line(roles, identity_repo, caplog):
    caplog.set_level(logging.DEBUG)
    chef = _someone(identity_repo, "chef")
    roles.add_member(roles.administration().id, chef.id)
    zora = identity_repo.create(email="zora.zwiebel@kanzlei.ch", display_name="Zora Zwiebel",
                                password=conftest.PASSWORD)
    role = roles.create_role(name="Mandat Zwiebel", description="Akten Zwiebel", functions={"approver"},
                             core_group_id="g-zwiebel")
    roles.rename_role(role.id, name="Mandat Zwiebel II", by=chef.id)
    roles.add_member(role.id, zora.id, by=chef.id)
    roles.set_functions(role.id, {"approver", "member"})
    roles.set_core_group(role.id, "g-zwiebel-2", by=chef.id)
    identity_repo.grant_role(zora.id, "admin")
    identity_repo.revoke_role(zora.id, "admin")
    identity_repo.grant_role(zora.id, "experimenter")
    identity_repo.revoke_role(zora.id, "experimenter")
    for refused in (lambda: roles.create_role(name="Mandat Zwiebel II", functions=()),
                    lambda: roles.remove_member(roles.administration().id, chef.id),
                    lambda: roles.create_role(name="Zwiebel Admin", functions={"admin"})):
        with pytest.raises(RoleError):
            refused()
    identity_repo.set_access_groups(zora.id, _groups(31))
    with pytest.raises(TooManyGroupsError):
        roles.add_member(roles.create_role(name="Zwiebel Daten", functions=(), core_group_id="g-z").id, zora.id)
    roles.remove_member(role.id, zora.id, by=chef.id)
    roles.delete_role(role.id, by=chef.id)
    _assert_no_name(caplog)


def test_the_move_logs_no_name(installation, caplog):
    installation.person("zora", "approver", "experimenter", display_name="Zora Zwiebel")
    installation.person("chef", "admin", display_name="Chef Zwiebel")
    caplog.set_level(logging.DEBUG)
    installation.move()
    _assert_no_name(caplog)


def _assert_no_name(caplog):
    lines = [record.getMessage() for record in caplog.records]
    leaked = [line for line in lines if "zwiebel" in line.lower() or "zora" in line.lower()]
    assert leaked == [], leaked
