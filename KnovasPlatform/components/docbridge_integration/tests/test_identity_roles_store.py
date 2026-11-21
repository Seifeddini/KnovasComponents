"""The role store and the move, beyond what the frozen acceptance file pins (RB-1, RB-2; builder's own tests).

Work order docs/platform-mvp/work-orders/RB-1-2.md; the acceptance tests are test_rb12_roles_acceptance.py. Covered
here: the names and words of the move, many key sets, concurrency of the last-administrator rule and of the 32-group
cap, the meaning of grant_role and revoke_role in the corners, the cap's "adds a group" rule, isolation between
installations, and that no group id or name reaches a log line.
"""
from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from pathlib import Path

import pytest

import conftest
from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

pytestmark = pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")

from identity import migrate, roles as roles_mod  # noqa: E402
from identity.roles import (  # noqa: E402
    FUNCTION_WORDS,
    LastAdministratorError,
    ProtectedRoleError,
    RoleError,
    RoleNameTaken,
    RoleNotFound,
    RoleStore,
    TooManyGroupsError,
)
from identity.users import UserRepository  # noqa: E402

MIGRATION = Path(migrate.MIGRATIONS_DIR) / "0027_roles.sql"
OTHER_KEYS = ("approver", "ingestion_manager", "experimenter", "experiments_manager", "builder_viewer",
              "builder_author", "builder_reviewer", "builder_publisher")


def _someone(repo, name):
    return repo.create(email=f"{name}@kanzlei.ch", display_name=name.capitalize(), password=conftest.PASSWORD)


def _groups(count, prefix="g"):
    return [f"{prefix}-{i:02d}" for i in range(count)]


def _connect(conn):
    """A second connection on ``conn``'s schema, for the concurrency tests."""
    import psycopg

    schema = conn.execute("SELECT current_schema()").fetchone()[0]
    other = psycopg.connect(PLATFORM_DB_TEST_DSN, autocommit=True)
    other.execute(f'SET search_path TO "{schema}"')
    return other


@pytest.fixture
def roles(platform_db):
    return RoleStore(platform_db)


@pytest.fixture
def installation():
    """A schema migrated up to 0026 and not further: an installation before the role builder."""
    import psycopg

    schema = f"t{uuid.uuid4().hex[:12]}"
    with psycopg.connect(PLATFORM_DB_TEST_DSN, autocommit=True) as setup:
        setup.execute(f'CREATE SCHEMA "{schema}"')
    conn = psycopg.connect(PLATFORM_DB_TEST_DSN, autocommit=True)
    conn.execute(f'SET search_path TO "{schema}"')
    try:
        migrate.apply(conn, [m for m in migrate.discover() if m.version < "0027_roles"])
        yield conn
    finally:
        conn.close()
        with psycopg.connect(PLATFORM_DB_TEST_DSN, autocommit=True) as cleanup:
            cleanup.execute(f'DROP SCHEMA "{schema}" CASCADE')


def _legacy_person(conn, name, *keys, status="active"):
    user_id = conn.execute("INSERT INTO users (email, display_name, status) VALUES (%s, %s, %s) RETURNING id",
                           (f"{name}@kanzlei.ch", name.capitalize(), status)).fetchone()[0]
    for key in keys:
        conn.execute("INSERT INTO user_roles (user_id, role_id) SELECT %s, id FROM roles WHERE key = %s",
                     (user_id, key))
    return user_id


# === the words of design § 2.1 =========================================================================================


def test_the_migration_names_the_moved_roles_with_the_stores_words_in_its_order():
    """FUNCTION_WORDS (used by grant_role) and the migration's list are one list: same words, same order."""
    text = MIGRATION.read_text(encoding="utf-8")
    listed = re.findall(r"\('([a-z_]+)', '([^']+)', (\d+)\)", text)
    assert [(key, word) for key, word, _ in sorted(listed, key=lambda row: int(row[2]))] == [
        (key, FUNCTION_WORDS[key]) for key in FUNCTION_WORDS if key not in ("admin", "member")]
    assert list(FUNCTION_WORDS)[:2] == ["member", "admin"]


def test_the_migration_header_names_the_items_the_design_and_the_way_back():
    head = MIGRATION.read_text(encoding="utf-8").split("CREATE TABLE")[0]
    for needed in ("RB-1", "RB-2", "role-builder.md", "2026-11-19-platform-mvp", "user_roles", "way back",
                   "not carried back"):
        assert needed in head, needed


# === the move =========================================================================================================


def test_many_key_sets_get_unique_names_of_at_most_80_characters(installation):
    """Every one of the 255 non-empty sets of other keys: one role each, names unique and short enough."""
    people = {}
    for mask in range(1, 2 ** len(OTHER_KEYS)):
        keys = [k for i, k in enumerate(OTHER_KEYS) if mask >> i & 1]
        people[frozenset(keys)] = _legacy_person(installation, f"p{mask}", *keys)
    migrate.apply(installation)
    store = RoleStore(installation)
    custom = [r for r in store.list_roles() if r.kind == "custom"]
    assert len(custom) == 255
    assert all(1 <= len(r.name) <= 80 for r in custom)
    assert len({r.name.casefold() for r in custom}) == 255
    for role in custom:
        assert set(role.members) == {people[role.functions]}


def test_a_long_name_keeps_its_leading_words_and_a_number(installation):
    _legacy_person(installation, "alle", *OTHER_KEYS)
    _legacy_person(installation, "fast", *OTHER_KEYS[:-1])
    migrate.apply(installation)
    names = sorted(r.name for r in RoleStore(installation).list_roles() if r.kind == "custom")
    assert names == ["Freigaben bestätigen + Einlesen + Experimente + Experimente verwalten + … (1)",
                     "Freigaben bestätigen + Einlesen + Experimente + Experimente verwalten + … (2)"]


def test_a_short_name_follows_the_order_of_the_design_not_of_the_keys(installation):
    _legacy_person(installation, "bea", "builder_publisher", "builder_viewer")
    _legacy_person(installation, "ina", "ingestion_manager", "approver")
    migrate.apply(installation)
    names = {r.name for r in RoleStore(installation).list_roles() if r.kind == "custom"}
    assert names == {"Builder lesen + Builder veröffentlichen", "Freigaben bestätigen + Einlesen"}


def test_a_deactivated_person_is_moved_like_everyone(installation):
    gone = _legacy_person(installation, "alt", "admin", "approver", status="disabled")
    migrate.apply(installation)
    store, repo = RoleStore(installation), UserRepository(installation)
    assert gone in store.mitglied().members and gone in store.administration().members
    assert repo.roles_of(gone) == {"admin", "approver", "member"}


def test_the_moved_roles_have_no_creator_no_core_group_and_say_where_they_come_from(installation):
    _legacy_person(installation, "paul", "approver")
    migrate.apply(installation)
    role, = [r for r in RoleStore(installation).list_roles() if r.kind == "custom"]
    assert (role.name, role.core_group_id) == ("Freigaben bestätigen", None)
    assert "Umstellung" in role.description
    assert installation.execute("SELECT created_by FROM tenant_roles WHERE id = %s",
                                (role.id,)).fetchone()[0] is None


# === the store ========================================================================================================


def test_a_name_is_trimmed_and_its_white_space_collapsed(roles):
    role = roles.create_role(name="  Sekretariat \t Zürich \n", functions=())
    assert role.name == "Sekretariat Zürich"
    with pytest.raises(RoleNameTaken):
        roles.create_role(name="sekretariat   zürich", functions=())


@pytest.mark.parametrize("name", ["", "   ", "x" * 81, None, 7])
def test_a_name_outside_1_to_80_characters_is_refused(roles, name):
    with pytest.raises(RoleError) as refused:
        roles.create_role(name=name, functions=())
    assert type(refused.value) is RoleError
    assert len(roles.list_roles()) == 2


def test_80_characters_are_a_name(roles):
    assert len(roles.create_role(name="x" * 80, functions=()).name) == 80


def test_a_role_may_take_its_own_name_in_another_case(roles):
    role = roles.create_role(name="Sekretariat", functions=())
    assert roles.rename_role(role.id, name="SEKRETARIAT").name == "SEKRETARIAT"


def test_the_two_roles_may_be_renamed_and_keep_their_kind(roles):
    renamed = roles.rename_role(roles.mitglied().id, name="Alle")
    assert (renamed.kind, roles.mitglied().name) == ("mitglied", "Alle")


def test_list_roles_puts_administration_and_mitglied_first_then_by_name(roles):
    for name in ("beta", "Alpha", "Gamma"):
        roles.create_role(name=name, functions=())
    assert [r.name for r in roles.list_roles()] == ["Administration", "Mitglied", "Alpha", "beta", "Gamma"]


def test_an_empty_core_group_is_none_and_none_clears_it(roles):
    assert roles.create_role(name="A", functions=(), core_group_id="  ").core_group_id is None
    role = roles.create_role(name="B", functions=(), core_group_id="g-b")
    assert roles.set_core_group(role.id, None).core_group_id is None


def test_mitglied_may_get_more_functions_and_administration_too(roles):
    assert roles.set_functions(roles.administration().id, {"admin", "approver"}).functions == {"admin", "approver"}
    assert roles.set_functions(roles.mitglied().id, {"member", "experimenter"}).functions == {"member", "experimenter"}
    with pytest.raises(ProtectedRoleError):
        roles.set_functions(roles.mitglied().id, {"member", "admin"})


def test_adding_twice_and_removing_a_non_member_are_no_change(roles, identity_repo):
    person = _someone(identity_repo, "anna")
    role = roles.create_role(name="Freigabe", functions={"approver"})
    roles.add_member(role.id, person.id)
    roles.add_member(role.id, person.id)
    other = roles.create_role(name="Leer", functions=())
    roles.remove_member(other.id, person.id)
    assert roles.get_role(role.id).members == {person.id}


@pytest.mark.parametrize("who", [uuid.uuid4(), "keine-id", None])
def test_an_unknown_person_is_a_role_error_and_nothing_is_written(roles, who):
    role = roles.create_role(name="Freigabe", functions={"approver"})
    with pytest.raises(RoleError) as refused:
        roles.add_member(role.id, who)
    assert str(refused.value) == "Person nicht gefunden."
    assert roles.get_role(role.id).members == frozenset()


def test_a_role_of_another_installation_is_not_found(roles, identity_repo, platform_db):
    """One installation per database: another schema's role id answers like a missing one.

    The other installation's search path ends in this test's schema only because the citext extension lives there
    (one per database); its tables are its own.
    """
    import psycopg

    mine = platform_db.execute("SELECT current_schema()").fetchone()[0]
    schema = f"t{uuid.uuid4().hex[:12]}"
    with psycopg.connect(PLATFORM_DB_TEST_DSN, autocommit=True) as other:
        other.execute(f'CREATE SCHEMA "{schema}"')
        other.execute(f'SET search_path TO "{schema}", "{mine}"')
        try:
            migrate.apply(other)
            foreign = RoleStore(other).administration()
            assert other.execute("SELECT to_regclass(%s)", (f'"{schema}".tenant_roles',)).fetchone()[0] is not None
        except Exception:
            other.execute(f'DROP SCHEMA "{schema}" CASCADE')
            raise
    try:
        person = _someone(identity_repo, "anna")
        for call in (lambda: roles.get_role(foreign.id), lambda: roles.add_member(foreign.id, person.id),
                     lambda: roles.set_functions(foreign.id, {"admin"}), lambda: roles.delete_role(foreign.id)):
            with pytest.raises(RoleNotFound) as refused:
                call()
            assert str(refused.value) == "Rolle nicht gefunden."
    finally:
        with psycopg.connect(PLATFORM_DB_TEST_DSN, autocommit=True) as cleanup:
            cleanup.execute(f'DROP SCHEMA "{schema}" CASCADE')


def test_roles_of_user_with_a_malformed_id_is_empty(roles):
    assert roles.roles_of_user("keine-id") == []


# === the last administrator, two at once ==============================================================================


def test_two_removals_at_once_cannot_take_the_last_two_administrators(platform_db, identity_repo):
    """The second removal waits for the first (Administration's row is held) and then counts no active person."""
    store = RoleStore(platform_db)
    administration = store.administration().id
    chef, vize = _someone(identity_repo, "chef"), _someone(identity_repo, "vize")
    for person in (chef, vize):
        store.add_member(administration, person.id)
    other = _connect(platform_db)
    outcome = {}

    def second():
        try:
            RoleStore(other).remove_member(administration, vize.id)
            outcome["second"] = "removed"
        except LastAdministratorError:
            outcome["second"] = "refused"

    try:
        with platform_db.transaction():
            store.remove_member(administration, chef.id)
            racer = threading.Thread(target=second)
            racer.start()
            time.sleep(0.5)
            assert racer.is_alive(), "the second removal did not wait for the first"
        racer.join(10)
    finally:
        other.close()
    assert outcome == {"second": "refused"}
    assert store.administration().members == {vize.id}


def test_revoking_admin_from_one_of_two_administrators_is_allowed(roles, identity_repo):
    chef, vize = _someone(identity_repo, "chef"), _someone(identity_repo, "vize")
    identity_repo.grant_role(chef.id, "admin")
    identity_repo.grant_role(vize.id, "admin")
    identity_repo.revoke_role(vize.id, "admin")
    assert "admin" not in identity_repo.roles_of(vize.id)


# === the 32-group cap ==================================================================================================


def test_two_grants_at_once_cannot_make_33_groups(platform_db, identity_repo):
    person = _someone(identity_repo, "anna")
    identity_repo.set_access_groups(person.id, _groups(31))
    other = _connect(platform_db)
    outcome = {}

    def second():
        try:
            UserRepository(other).grant_access_group(person.id, "g-zweite")
            outcome["second"] = "granted"
        except TooManyGroupsError:
            outcome["second"] = "refused"

    try:
        with platform_db.transaction():
            identity_repo.grant_access_group(person.id, "g-erste")
            racer = threading.Thread(target=second)
            racer.start()
            time.sleep(0.5)
            assert racer.is_alive(), "the second grant did not wait for the first"
        racer.join(10)
    finally:
        other.close()
    assert outcome == {"second": "refused"}
    assert len(identity_repo.access_groups_of(person.id)) == 32


def test_a_person_stored_over_the_cap_can_still_be_corrected(roles, identity_repo, platform_db):
    """A change that adds no group passes even above 32; one that adds a group does not."""
    person = _someone(identity_repo, "anna")
    for group in _groups(34):
        platform_db.execute("INSERT INTO user_access_groups (user_id, group_id) VALUES (%s, %s)", (person.id, group))
    plain = roles.create_role(name="Freigabe", functions={"approver"})
    roles.add_member(plain.id, person.id)                        # no group: passes
    identity_repo.grant_access_group(person.id, "g-00", ends_on="2099-01-01")   # held already: passes
    identity_repo.set_access_groups(person.id, _groups(33))      # fewer: passes
    with pytest.raises(TooManyGroupsError):
        identity_repo.set_access_groups(person.id, _groups(32) + ["g-neu"])
    with pytest.raises(TooManyGroupsError):
        roles.set_core_group(plain.id, "g-rolle")
    assert identity_repo.access_groups_of(person.id) == tuple(_groups(33))
    identity_repo.set_access_groups(person.id, _groups(32))
    assert len(identity_repo.access_groups_of(person.id)) == 32


def test_two_roles_with_one_group_count_it_once(roles, identity_repo):
    person = _someone(identity_repo, "anna")
    identity_repo.set_access_groups(person.id, _groups(31))
    for name in ("A", "B"):
        roles.add_member(roles.create_role(name=name, functions=(), core_group_id="g-gemeinsam").id, person.id)
    assert len(identity_repo.access_groups_of(person.id)) == 32


def test_an_ended_grant_does_not_count_against_the_cap(roles, identity_repo):
    person = _someone(identity_repo, "anna")
    identity_repo.set_access_groups(person.id, _groups(32))
    identity_repo.grant_access_group(person.id, "g-00", ends_on="2020-01-01")
    roles.add_member(roles.create_role(name="A", functions=(), core_group_id="g-rolle").id, person.id)
    assert len(identity_repo.access_groups_of(person.id)) == 32


def test_the_broker_signs_32_and_refuses_33_without_cutting(identity_repo, platform_db):
    from identity.assertion import AssertionSigner, generate_keypair
    from identity.principal import PrincipalBroker

    person = _someone(identity_repo, "anna")
    identity_repo.set_access_groups(person.id, _groups(32))
    broker = PrincipalBroker(user_repo=identity_repo, tenant_id="tenant-a",
                             signer=AssertionSigner(generate_keypair().private_pem, key_id="t"))
    assert broker.groups_for(identity_repo.get(person.id)) == tuple(_groups(32))
    platform_db.execute("INSERT INTO user_access_groups (user_id, group_id) VALUES (%s, 'g-zu-viel')", (person.id,))
    with pytest.raises(TooManyGroupsError):
        broker.assertion_for(identity_repo.get(person.id))


# === grant_role and revoke_role in the corners ========================================================================


def test_grant_role_puts_two_people_into_one_single_function_role(roles, identity_repo):
    anna, ben = _someone(identity_repo, "anna"), _someone(identity_repo, "ben")
    identity_repo.grant_role(anna.id, "ingestion_manager")
    identity_repo.grant_role(ben.id, "ingestion_manager")
    role, = [r for r in roles.list_roles() if r.kind == "custom"]
    assert (role.name, role.functions, role.members) == ("Einlesen", {"ingestion_manager"}, {anna.id, ben.id})


def test_grant_role_never_uses_a_role_with_a_core_group(roles, identity_repo):
    """Granting a function must not widen what the person may read."""
    linked = roles.create_role(name="Freigaben bestätigen", functions={"approver"}, core_group_id="g-akten")
    person = _someone(identity_repo, "anna")
    identity_repo.grant_role(person.id, "approver")
    assert identity_repo.access_groups_of(person.id) == ()
    mine, = roles.roles_of_user(person.id)
    assert (mine.name, mine.functions) == ("Freigaben bestätigen (2)", {"approver"})
    assert mine.id != linked.id


def test_grant_role_of_an_unknown_person_changes_nothing(roles, identity_repo):
    with pytest.raises(RoleError):
        identity_repo.grant_role(uuid.uuid4(), "approver")
    assert [r for r in roles.list_roles() if r.kind == "custom"] == []


def test_revoke_role_gives_back_the_other_functions_of_the_role_left(roles, identity_repo):
    person = _someone(identity_repo, "anna")
    role = roles.create_role(name="Sekretariat", functions={"approver", "ingestion_manager", "member"})
    roles.add_member(role.id, person.id)
    identity_repo.revoke_role(person.id, "approver")
    assert identity_repo.roles_of(person.id) == {"ingestion_manager", "member"}
    assert {r.kind for r in roles.roles_of_user(person.id)} == {"mitglied", "custom"}
    assert person.id not in roles.get_role(role.id).members


def test_revoke_role_leaves_every_role_that_gives_the_key(roles, identity_repo):
    person = _someone(identity_repo, "anna")
    for name, functions in (("A", {"approver"}), ("B", {"approver", "experimenter"})):
        roles.add_member(roles.create_role(name=name, functions=functions).id, person.id)
    identity_repo.revoke_role(person.id, "approver")
    assert identity_repo.roles_of(person.id) == {"experimenter"}


def test_revoking_a_key_that_comes_from_administration_is_refused(roles, identity_repo):
    chef, vize = _someone(identity_repo, "chef"), _someone(identity_repo, "vize")
    for person in (chef, vize):
        identity_repo.grant_role(person.id, "admin")
    roles.set_functions(roles.administration().id, {"admin", "approver"})
    with pytest.raises(RoleError) as refused:
        identity_repo.revoke_role(chef.id, "approver")
    assert type(refused.value) is RoleError
    assert identity_repo.roles_of(chef.id) == {"admin", "approver"}


def test_revoking_admin_keeps_administrations_other_functions(roles, identity_repo):
    chef, vize = _someone(identity_repo, "chef"), _someone(identity_repo, "vize")
    for person in (chef, vize):
        identity_repo.grant_role(person.id, "admin")
    roles.set_functions(roles.administration().id, {"admin", "approver"})
    identity_repo.revoke_role(vize.id, "admin")
    assert identity_repo.roles_of(vize.id) == {"approver"}


def test_revoking_member_leaves_mitglied(roles, identity_repo):
    person = _someone(identity_repo, "anna")
    identity_repo.grant_role(person.id, "member")
    identity_repo.revoke_role(person.id, "member")
    assert roles.roles_of_user(person.id) == []


def test_revoking_a_key_nobody_gave_is_no_change(identity_repo):
    person = _someone(identity_repo, "anna")
    identity_repo.grant_role(person.id, "approver")
    identity_repo.revoke_role(person.id, "experimenter")
    identity_repo.revoke_role(person.id, "wizard")
    assert identity_repo.roles_of(person.id) == {"approver"}


def test_a_refused_revoke_changes_nothing_even_after_leaving_a_role(roles, identity_repo):
    """Giving a function back can be refused (Mitglied's group would be the 33rd); the person keeps the role left."""
    person = _someone(identity_repo, "anna")
    role = roles.create_role(name="Sekretariat", functions={"approver", "member"})
    roles.add_member(role.id, person.id)
    identity_repo.set_access_groups(person.id, _groups(32))
    roles.set_core_group(roles.mitglied().id, "g-mitglied")
    with pytest.raises(TooManyGroupsError):
        identity_repo.revoke_role(person.id, "approver")
    assert person.id in roles.get_role(role.id).members
    assert identity_repo.roles_of(person.id) == {"approver", "member"}


# === logs =============================================================================================================


def test_no_group_id_or_name_reaches_a_log_line(roles, identity_repo, platform_db, caplog):
    from identity.assertion import AssertionSigner, generate_keypair
    from identity.principal import PrincipalBroker

    caplog.set_level(logging.DEBUG)
    zora = identity_repo.create(email="zora.zwiebel@kanzlei.ch", display_name="Zora Zwiebel",
                                password=conftest.PASSWORD)
    identity_repo.set_access_groups(zora.id, _groups(31, prefix="g-zwiebel"))
    identity_repo.grant_access_group(zora.id, "g-zwiebel-akte")
    with pytest.raises(TooManyGroupsError):
        identity_repo.grant_access_group(zora.id, "g-zwiebel-zuviel")
    role = roles.create_role(name="Zwiebel", functions=(), core_group_id="g-zwiebel-rolle")
    with pytest.raises(TooManyGroupsError):
        roles.add_member(role.id, zora.id)
    platform_db.execute("INSERT INTO user_access_groups (user_id, group_id) VALUES (%s, 'g-zwiebel-alt')", (zora.id,))
    broker = PrincipalBroker(user_repo=identity_repo, tenant_id="tenant-a",
                             signer=AssertionSigner(generate_keypair().private_pem, key_id="t"))
    with pytest.raises(TooManyGroupsError):
        broker.assertion_for(identity_repo.get(zora.id))
    identity_repo.grant_role(zora.id, "approver")
    lines = [record.getMessage() for record in caplog.records]
    assert [line for line in lines if "zwiebel" in line.lower() or "zora" in line.lower()] == []
    assert any(str(zora.id) in line for line in lines)                 # ids, not names


def test_the_messages_name_nobody(roles, identity_repo):
    """Every refusal's text is the class's own words: nothing the tenant typed."""
    for error in (roles_mod.RoleError, RoleNotFound, RoleNameTaken, roles_mod.UnknownFunction, ProtectedRoleError,
                  LastAdministratorError, TooManyGroupsError):
        assert str(error()) == error.default
    assert "32" in str(TooManyGroupsError())
