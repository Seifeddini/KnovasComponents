"""RB-3, RB-4: the builder's own tests of the Rollen page and the people page, beside the frozen
test_rb34_roles_page_acceptance.py (work order docs/platform-mvp/work-orders/RB-3-4.md).

What they pin that the frozen file does not:

1. The coordinator's amendment to rule 3 (F3): deactivating a person and removing a person from Administration take
   the same lock (Administration's row) and count inside it, so two at once never leave Administration without an
   active person. Two connections, both orders, and once end to end through the people page.
2. A Core group this request created and could not use is deleted again (a new role whose saving failed;
   Mitglied's group when linking is refused), and a rename that could not be saved gives the group its old name
   back. The audit log names the group by id.
3. Rule 1 in one place: ``roles.grantable`` / ``roles.check_grantable``.
4. The people page swaps one role for another at the 32-group cap (it leaves before it joins).
5. A form that changes nothing writes nothing and no audit row.
6. Deleting a role removes, with their rows, the people who joined it while the Core was asked.
7. No Core group id reaches a log line: a tenant may choose a speaking one.

Nothing reaches a tenant: the Core is a fake.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid

import pytest

import conftest
from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

pytestmark = pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")

from identity import roles as roles_mod  # noqa: E402
from identity.roles import LastAdministratorError, RoleStore  # noqa: E402
from identity.users import UserRepository  # noqa: E402


class Core(conftest.FakeGraphApi):
    """The Core's group calls, in memory. ``speaking`` gives a group the id a tenant might choose: its name."""

    calls: list = []
    groups: list = []
    speaking = False
    on_create = None
    on_delete = None

    def access_groups(self):
        return [dict(g) for g in Core.groups]

    def create_access_group(self, name, parent=None):
        Core.calls.append(("create", name, parent))
        if Core.on_create is not None:
            Core.on_create(name)
        group_id = f"g-{name.lower().replace(' ', '-')}" if Core.speaking else f"g-{uuid.uuid4().hex[:10]}"
        Core.groups.append({"group_id": group_id, "name": name, "parent_id": parent})
        return {"group_id": group_id, "name": name, "parent_id": parent}

    def rename_access_group(self, identifier, name):
        Core.calls.append(("rename", identifier, name))
        return {"group_id": identifier, "name": name}

    def delete_access_group(self, identifier):
        Core.calls.append(("delete", identifier))
        if Core.on_delete is not None:
            Core.on_delete(identifier)
        return True


def _connect(conn):
    """A second connection on ``conn``'s schema."""
    import psycopg

    schema = conn.execute("SELECT current_schema()").fetchone()[0]
    other = psycopg.connect(PLATFORM_DB_TEST_DSN, autocommit=True)
    other.execute(f'SET search_path TO "{schema}"')
    return other


@pytest.fixture
def app(platform_db, tmp_path, monkeypatch):
    Core.calls, Core.speaking, Core.on_create, Core.on_delete = [], False, None, None
    Core.groups = [{"group_id": "g-admins", "name": "Administratoren", "parent_id": None, "is_admin": True}]
    return conftest._identity_app(platform_db, tmp_path, monkeypatch, client_cls=Core)


def _admin(identity_repo, name):
    return conftest._person(identity_repo, f"{name}@kanzlei.ch", name.capitalize(), "admin")


def _only(identity_repo, *admins):
    """The given people are Administration's only active members: the first start's administrator is disabled."""
    identity_repo.disable(identity_repo.get_by_email("bootstrap@kanzlei.ch").id)
    administration = RoleStore(identity_repo._conn).administration()
    active = {m for m in administration.members if identity_repo.get(m).is_active}
    assert active == {a.id for a in admins}


def _token(client):
    with client._client.session_transaction() as sess:
        return sess["csrf_token"]


def _post(client, path, **fields):
    fields["csrf_token"] = _token(client)
    return client._client.post(path, data=fields)


def _rows(conn, action):
    return conn.execute("SELECT target_id, outcome, detail FROM audit_log WHERE action = %s ORDER BY id",
                        (action,)).fetchall()


# === 1. the last administrator: one lock for deactivating and for leaving Administration ==========================


def _race(first_conn, first, second_conn, second):
    """Run ``first`` in a transaction on one connection; while it holds its locks, start ``second`` on another and
    check that it waits; then commit the first. What the second ended in."""
    outcome = {}

    def run_second():
        try:
            with second_conn.transaction():
                second(second_conn)
            outcome["second"] = "done"
        except LastAdministratorError:
            outcome["second"] = "refused"
        except Exception as exc:  # noqa: BLE001 - the test reports what went wrong
            outcome["second"] = type(exc).__name__

    with first_conn.transaction():
        first(first_conn)
        racer = threading.Thread(target=run_second)
        racer.start()
        time.sleep(0.5)
        assert racer.is_alive(), "the second change did not wait for the first"
    racer.join(10)
    assert not racer.is_alive()
    return outcome.get("second")


def _deactivate(person_id):
    def step(conn):
        RoleStore(conn).check_deactivation(person_id)
        UserRepository(conn).disable(person_id)
    return step


def _leave_administration(person_id):
    def step(conn):
        store = RoleStore(conn)
        store.remove_member(store.administration().id, person_id)
    return step


def test_a_removal_then_a_deactivation_at_once_keep_an_active_administrator(platform_db, identity_repo):
    """B leaves Administration while B deactivates A: the deactivation waits, then counts and is refused."""
    anna, bert = _admin(identity_repo, "anna"), _admin(identity_repo, "bert")
    other = _connect(platform_db)
    try:
        assert _race(platform_db, _leave_administration(bert.id), other, _deactivate(anna.id)) == "refused"
    finally:
        other.close()
    assert identity_repo.get(anna.id).is_active
    assert "admin" in identity_repo.roles_of(anna.id) and "admin" not in identity_repo.roles_of(bert.id)


def test_a_deactivation_then_a_removal_at_once_keep_an_active_administrator(platform_db, identity_repo):
    """A is deactivated while B leaves Administration: the removal waits, then counts and is refused."""
    anna, bert = _admin(identity_repo, "anna"), _admin(identity_repo, "bert")
    other = _connect(platform_db)
    try:
        assert _race(platform_db, _deactivate(anna.id), other, _leave_administration(bert.id)) == "refused"
    finally:
        other.close()
    assert not identity_repo.get(anna.id).is_active
    assert "admin" in identity_repo.roles_of(bert.id) and identity_repo.get(bert.id).is_active


def test_deactivating_someone_who_is_not_the_last_administrator_passes(platform_db, identity_repo):
    anna, bert = _admin(identity_repo, "anna"), _admin(identity_repo, "bert")
    member = conftest._person(identity_repo, "cleo@kanzlei.ch", "Cleo", "member")
    store = RoleStore(platform_db)
    with platform_db.transaction():
        store.check_deactivation(member.id)          # not in Administration
        store.check_deactivation(anna.id)            # bert stays
        store.check_deactivation("kein-uuid")        # changes no administrator
    identity_repo.disable(bert.id)
    with pytest.raises(LastAdministratorError):
        with platform_db.transaction():
            store.check_deactivation(anna.id)        # the last active one


def test_the_people_page_refuses_a_deactivation_that_a_removal_made_the_last(app, platform_db, identity_repo):
    """End to end, two connections: chef takes bert out of Administration while bert, on the people page,
    deactivates chef. Bert's request passes the gate, waits for chef's change, then counts: refused, 400."""
    chef, bert = _admin(identity_repo, "chef"), _admin(identity_repo, "bert")
    _only(identity_repo, chef, bert)
    bert_client = conftest._signed_in(app, bert.email)
    answer = {}

    def bert_deactivates_chef():
        answer["status"] = _post(bert_client, "/admin/people/disable", user_id=str(chef.id)).status_code

    store = RoleStore(platform_db)
    with platform_db.transaction():
        store.remove_member(store.administration().id, bert.id)
        racer = threading.Thread(target=bert_deactivates_chef)
        racer.start()
        time.sleep(0.8)
        assert racer.is_alive(), "the deactivation did not wait for the removal"
    racer.join(15)
    assert answer == {"status": 400}
    assert identity_repo.get(chef.id).is_active
    assert "admin" in identity_repo.roles_of(chef.id)


def test_the_people_page_still_deactivates_another_administrator(app, identity_repo):
    chef, bert = _admin(identity_repo, "chef"), _admin(identity_repo, "bert")
    client = conftest._signed_in(app, chef.email)
    assert _post(client, "/admin/people/disable", user_id=str(bert.id)).status_code == 200
    assert not identity_repo.get(bert.id).is_active


# === 2. a Core group the request could not use is deleted again ================================================


def test_a_new_roles_group_is_deleted_again_when_the_role_cannot_be_saved(app, platform_db, identity_repo):
    """Another writer takes the name between the check and the save: the store refuses, the group goes again."""
    chef = _admin(identity_repo, "chef")
    client = conftest._signed_in(app, chef.email)
    other = _connect(platform_db)
    Core.on_create = lambda name: RoleStore(other).create_role(name=name)
    try:
        response = _post(client, "/admin/roles/create", name="Partner", function=["approver"])
    finally:
        other.close()
    assert response.status_code == 400
    assert "wieder gelöscht" in response.get_data(as_text=True)
    created = [c for c in Core.calls if c[0] == "create"]
    assert [c[0] for c in Core.calls] == ["create", "delete"]
    assert Core.calls[1][1] == Core.groups[-1]["group_id"]
    roles = [r for r in RoleStore(platform_db).list_roles() if r.name == "Partner"]
    assert len(roles) == 1 and roles[0].core_group_id is None     # the other writer's role, not ours
    (target, outcome, detail), = _rows(platform_db, "role.core_group_abandoned")
    assert (outcome, detail) == ("ok", {"core_group_id": Core.groups[-1]["group_id"], "removed_at_core": True})
    assert created and not _rows(platform_db, "role.created")


def test_a_rename_that_cannot_be_saved_gives_the_group_its_old_name_back(app, platform_db, identity_repo):
    chef = _admin(identity_repo, "chef")
    client = conftest._signed_in(app, chef.email)
    role = RoleStore(platform_db).create_role(name="Sekretariat", core_group_id="g-sek")
    platform_db.execute(
        "CREATE FUNCTION own_no_audit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'nein'; END $$;"
        "CREATE TRIGGER own_no_audit BEFORE INSERT ON audit_log FOR EACH ROW "
        "WHEN (NEW.action = 'role.renamed') EXECUTE FUNCTION own_no_audit();")
    response = _post(client, f"/admin/roles/{role.id}/rename", name="Sekretariat Zürich")
    assert response.status_code == 500
    assert Core.calls == [("rename", "g-sek", "Sekretariat Zürich"), ("rename", "g-sek", "Sekretariat")]
    assert RoleStore(platform_db).get_role(role.id).name == "Sekretariat"


def test_linking_refused_by_the_cap_links_nothing_and_deletes_mitglieds_new_group(app, platform_db, identity_repo):
    chef = _admin(identity_repo, "chef")
    anna = conftest._person(identity_repo, "anna@kanzlei.ch", "Anna", "member")
    identity_repo.set_access_groups(anna.id, [f"g-{i:02d}" for i in range(32)])
    client = conftest._signed_in(app, chef.email)
    response = _post(client, "/admin/roles/connect")
    assert response.status_code == 400 and "32" in response.get_data(as_text=True)
    store = RoleStore(platform_db)
    assert store.administration().core_group_id is None and store.mitglied().core_group_id is None
    created = Core.groups[-1]["group_id"]
    assert Core.calls == [("create", "Mitglied", None), ("delete", created)]
    assert not _rows(platform_db, "role.core_group_linked")


# === 3. rule 1 in one place ===================================================================================


def test_admin_may_give_every_function_and_anyone_else_only_what_she_holds():
    assert roles_mod.grantable({"admin"}) == frozenset(roles_mod.FUNCTION_WORDS)
    assert roles_mod.grantable({"approver", "member"}) == {"approver", "member"}
    assert roles_mod.grantable(()) == frozenset()
    roles_mod.check_grantable({"admin"}, roles_mod.FUNCTION_WORDS)
    roles_mod.check_grantable({"approver"}, {"approver"})
    with pytest.raises(roles_mod.NotGrantableError) as refused:
        roles_mod.check_grantable({"approver"}, {"approver", "builder_author"})
    assert isinstance(refused.value, roles_mod.RoleError)
    assert str(refused.value) == roles_mod.NotGrantableError.default


# === 4. the people page at the cap ================================================================================


def test_the_people_page_swaps_a_role_at_the_cap(app, platform_db, identity_repo):
    chef = _admin(identity_repo, "chef")
    anna = conftest._person(identity_repo, "anna@kanzlei.ch", "Anna", "member")
    store = RoleStore(platform_db)
    alt = store.create_role(name="Alt", functions={"approver"}, core_group_id="g-alt")
    neu = store.create_role(name="Neu", functions={"approver"}, core_group_id="g-neu")
    store.add_member(alt.id, anna.id)
    identity_repo.set_access_groups(anna.id, [f"g-{i:02d}" for i in range(31)])
    assert len(identity_repo.access_groups_of(anna.id)) == 32
    client = conftest._signed_in(app, chef.email)
    response = _post(client, "/admin/people/roles", user_id=str(anna.id),
                     role_id=[str(store.mitglied().id), str(neu.id)])
    assert response.status_code == 200
    assert {r.id for r in store.roles_of_user(anna.id)} == {store.mitglied().id, neu.id}
    assert "g-neu" in identity_repo.access_groups_of(anna.id) and len(identity_repo.access_groups_of(anna.id)) == 32


# === 5. no change, no row =========================================================================================


def test_a_form_that_changes_nothing_writes_nothing(app, platform_db, identity_repo):
    chef = _admin(identity_repo, "chef")
    anna = conftest._person(identity_repo, "anna@kanzlei.ch", "Anna", "member")
    store = RoleStore(platform_db)
    role = store.create_role(name="Freigabe", functions={"approver"})
    store.add_member(role.id, anna.id)
    client = conftest._signed_in(app, chef.email)
    mark = platform_db.execute("SELECT coalesce(max(id), 0) FROM audit_log").fetchone()[0]
    assert _post(client, f"/admin/roles/{role.id}/members/add", user_id=str(anna.id)).status_code == 200
    assert _post(client, f"/admin/roles/{role.id}/functions", function=["approver"]).status_code == 200
    assert _post(client, f"/admin/roles/{role.id}/rename", name="  Freigabe ").status_code == 200
    assert _post(client, f"/admin/roles/{role.id}/members/remove", user_id=str(chef.id)).status_code == 200
    held = [str(r.id) for r in store.roles_of_user(anna.id)]
    assert _post(client, "/admin/people/roles", user_id=str(anna.id), role_id=held).status_code == 200
    rows = platform_db.execute("SELECT action FROM audit_log WHERE id > %s AND (action LIKE 'role.%%' "
                               "OR action = 'user.roles_changed')", (mark,)).fetchall()
    assert rows == [] and Core.calls == []


# === 6. deleting: whoever joined meanwhile leaves too, with a row ===================================================


def test_deleting_a_role_removes_who_joined_while_the_core_was_asked(app, platform_db, identity_repo):
    chef = _admin(identity_repo, "chef")
    anna = conftest._person(identity_repo, "anna@kanzlei.ch", "Anna", "member")
    late = conftest._person(identity_repo, "lena@kanzlei.ch", "Lena", "member")
    store = RoleStore(platform_db)
    role = store.create_role(name="Sekretariat", functions={"approver"}, core_group_id="g-sek")
    store.add_member(role.id, anna.id)
    other = _connect(platform_db)
    Core.on_delete = lambda group: RoleStore(other).add_member(role.id, late.id)
    try:
        response = _post(conftest._signed_in(app, chef.email), f"/admin/roles/{role.id}/delete")
    finally:
        other.close()
    assert response.status_code == 200
    assert role.id not in {r.id for r in store.list_roles()}
    removed = {detail["user_id"] for _, _, detail in _rows(platform_db, "role.member_removed")}
    assert removed == {str(anna.id), str(late.id)}
    (_, outcome, detail), = _rows(platform_db, "role.deleted")
    assert outcome == "ok" and set(detail["members_removed"]) == {str(anna.id), str(late.id)}


# === 7. logs: no Core group id =========================================================================================


def test_no_core_group_id_reaches_a_log_line(app, platform_db, identity_repo, caplog):
    caplog.set_level(logging.DEBUG)
    Core.speaking = True
    Core.groups = [{"group_id": "g-zwiebel-admins", "name": "Zwiebel", "parent_id": None, "is_admin": True}]
    chef = _admin(identity_repo, "chef")
    client = conftest._signed_in(app, chef.email)
    assert _post(client, "/admin/roles/connect").status_code == 200
    assert _post(client, "/admin/roles/create", name="Zwiebel Akten", function=["approver"]).status_code == 200
    role, = [r for r in RoleStore(platform_db).list_roles() if r.name == "Zwiebel Akten"]
    assert role.core_group_id == "g-zwiebel-akten"
    assert _post(client, f"/admin/roles/{role.id}/rename", name="Zwiebel Akten II").status_code == 200
    assert _post(client, f"/admin/roles/{role.id}/delete").status_code == 200
    leaked = [record.getMessage() for record in caplog.records if "zwiebel" in record.getMessage().lower()]
    assert leaked == []
