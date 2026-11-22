"""RB-FIX-1, the verifiers' low notes of RB-3/RB-4 and RB-6/RB-7: bad input is the page's own 400, not a 500.

1. A NUL byte in a role's name or description reached the database (which stores no text holding one) and answered
   500, after the Core had been asked for a group. It is refused like any other bad name: the page again with its
   reason in ``role="alert"``, 400, nothing asked of the Core, nothing written. The same for an agent's name.
2. ``/admin/people/disable`` with a malformed ``user_id`` sent the text to the database and answered 500. It answers
   400 with nothing changed: no account, no session, no audit row. So does ``/admin/people/enable``, and an id of
   nobody; an upper-case spelling of one's own id is one's own id.

Nothing reaches a tenant: the Core is a fake.
"""
from __future__ import annotations

import pytest

import conftest
from conftest import platform_db_reachable

pytestmark = pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")

from identity.roles import RoleStore  # noqa: E402
from test_web_admin_roles_rules import Core, _admin, _post  # noqa: E402

NUL_NAMES = ("Part\x00ner", "\x00", "Partner\x00")


@pytest.fixture
def app(platform_db, tmp_path, monkeypatch):
    Core.calls, Core.speaking, Core.on_create, Core.on_delete = [], False, None, None
    Core.groups = [{"group_id": "g-admins", "name": "Administratoren", "parent_id": None, "is_admin": True}]
    return conftest._identity_app(platform_db, tmp_path, monkeypatch, client_cls=Core)


@pytest.fixture
def chef(app, identity_repo):
    person = _admin(identity_repo, "chef")
    return person, conftest._signed_in(app, person.email)


def _alert(response):
    return 'role="alert"' in response.get_data(as_text=True)


def _audit_count(conn):
    return conn.execute("SELECT count(*) FROM audit_log").fetchone()[0]


# === 1. a NUL byte in a role's or an agent's name ===============================================================


@pytest.mark.parametrize("name", NUL_NAMES)
def test_a_nul_byte_in_a_new_roles_name_is_refused_before_the_core(chef, platform_db, name):
    _, client = chef
    before = [r.id for r in RoleStore(platform_db).list_roles()]
    response = _post(client, "/admin/roles/create", name=name, function=["approver"])
    assert response.status_code == 400 and _alert(response)
    assert "unzulässiges Zeichen" in response.get_data(as_text=True)
    assert Core.calls == []
    assert [r.id for r in RoleStore(platform_db).list_roles()] == before


def test_a_nul_byte_in_a_new_roles_description_is_refused_before_the_core(chef, platform_db):
    _, client = chef
    before = [r.id for r in RoleStore(platform_db).list_roles()]
    response = _post(client, "/admin/roles/create", name="Partner", description="Für\x00 alle", function=["approver"])
    assert response.status_code == 400 and _alert(response)
    assert "Die Beschreibung enthält ein unzulässiges Zeichen." in response.get_data(as_text=True)
    assert Core.calls == []
    assert [r.id for r in RoleStore(platform_db).list_roles()] == before


def test_a_nul_byte_in_a_new_name_is_refused_before_the_rename(chef, platform_db):
    person, client = chef
    role = RoleStore(platform_db).create_role(name="Partner", core_group_id="g-partner", by=person.id)
    response = _post(client, f"/admin/roles/{role.id}/rename", name="Part\x00ner")
    assert response.status_code == 400 and _alert(response)
    assert Core.calls == []
    assert RoleStore(platform_db).get_role(role.id).name == "Partner"


def test_a_nul_byte_in_an_agents_name_is_refused(chef, platform_db):
    _, client = chef
    before = platform_db.execute("SELECT count(*) FROM agents").fetchone()[0]
    audit = _audit_count(platform_db)
    response = _post(client, "/admin/agents/create", name="Claude\x00 Code", on_behalf_of="")
    assert response.status_code == 400 and _alert(response)
    assert "Der Agentenname enthält ein unzulässiges Zeichen." in response.get_data(as_text=True)
    assert platform_db.execute("SELECT count(*) FROM agents").fetchone()[0] == before
    assert _audit_count(platform_db) == audit


def test_a_role_without_a_nul_byte_is_still_created(chef, platform_db):
    _, client = chef
    response = _post(client, "/admin/roles/create", name="Partner", description="Für alle Partner",
                     function=["approver"])
    assert response.status_code == 200
    assert [c[0] for c in Core.calls] == ["create"]
    assert "Partner" in [r.name for r in RoleStore(platform_db).list_roles()]


# === 2. a malformed or unknown person on the people page ======================================================


@pytest.mark.parametrize("path", ["/admin/people/disable", "/admin/people/enable"])
@pytest.mark.parametrize("user_id", ["kein-uuid", "", "1234", "00000000-0000-0000-0000-000000000000",
                                     "00000000-0000-0000-0000-00000000000\x00"])
def test_a_malformed_or_unknown_person_is_400_with_nothing_changed(chef, platform_db, identity_repo, path, user_id):
    _, client = chef
    other = _admin(identity_repo, "bert")
    if path.endswith("enable"):
        identity_repo.disable(other.id)
    state = platform_db.execute("SELECT id, status, updated_at FROM users ORDER BY id").fetchall()
    sessions = platform_db.execute("SELECT count(*) FROM sessions WHERE revoked_at IS NULL").fetchone()[0]
    audit = _audit_count(platform_db)
    response = _post(client, path, user_id=user_id)
    assert response.status_code == 400 and _alert(response)
    assert "Person nicht gefunden." in response.get_data(as_text=True)
    assert platform_db.execute("SELECT id, status, updated_at FROM users ORDER BY id").fetchall() == state
    assert platform_db.execute("SELECT count(*) FROM sessions WHERE revoked_at IS NULL").fetchone()[0] == sessions
    assert _audit_count(platform_db) == audit


def test_ones_own_id_in_capitals_is_still_ones_own(chef, identity_repo):
    person, client = chef
    _admin(identity_repo, "bert")
    response = _post(client, "/admin/people/disable", user_id=str(person.id).upper())
    assert response.status_code == 400
    assert "Sie können Ihr eigenes Konto nicht deaktivieren." in response.get_data(as_text=True)
    assert identity_repo.get(person.id).is_active


def test_a_well_formed_person_is_still_disabled_and_enabled(chef, identity_repo):
    _, client = chef
    bert = _admin(identity_repo, "bert")
    assert _post(client, "/admin/people/disable", user_id=f"  {bert.id}  ").status_code == 200
    assert not identity_repo.get(bert.id).is_active
    assert _post(client, "/admin/people/enable", user_id=str(bert.id)).status_code == 200
    assert identity_repo.get(bert.id).is_active
