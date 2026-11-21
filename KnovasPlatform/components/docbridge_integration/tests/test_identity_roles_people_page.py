"""The people page's two forms under roles (RB-1, RB-2; builder's own tests).

``set_roles`` and ``set_groups`` catch a ``RoleError`` and show its message on the page with status 400, nothing
changed (work order docs/platform-mvp/work-orders/RB-1-2.md, item 6). RB-4 replaces the roles form; until then it is
the way an administrator meets the last-administrator rule and the 32-group cap.
"""
from __future__ import annotations

import re

import pytest

import conftest
from conftest import platform_db_reachable

pytestmark = pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")

from identity.roles import RoleStore  # noqa: E402


def _groups(count, prefix="g"):
    return [f"{prefix}-{i:02d}" for i in range(count)]


@pytest.fixture
def chef(identity_app, identity_repo):
    """An administrator besides the first start's one, signed in."""
    person = conftest._person(identity_repo, "chef@kanzlei.ch", "Chef", "admin")
    return person, conftest._signed_in(identity_app, "chef@kanzlei.ch")


def _first_administrator(platform_db):
    return platform_db.execute("SELECT id FROM users WHERE email = 'bootstrap@kanzlei.ch'").fetchone()[0]


def _post(client, path, **fields):
    page = client.get("/admin/people")
    fields["csrf_token"] = conftest._csrf_from(page.get_data(as_text=True))
    return client._client.post(path, data=fields)


def test_the_last_administrator_cannot_untick_admin(chef, identity_repo, platform_db):
    person, client = chef
    identity_repo.disable(_first_administrator(platform_db))
    response = _post(client, "/admin/people/roles", user_id=str(person.id), roles=["member"])
    assert response.status_code == 400
    assert "Die Administration braucht mindestens eine aktive Person." in response.get_data(as_text=True)
    assert identity_repo.roles_of(person.id) == {"admin"}


def test_a_refusal_half_way_leaves_every_role_as_it_was(chef, identity_repo):
    """Experimente taken away first, then admin refused (a 33rd group): the person keeps Experimente."""
    _, client = chef
    anna = conftest._person(identity_repo, "anna@kanzlei.ch", "Anna", "experimenter")
    identity_repo.set_access_groups(anna.id, _groups(32))
    store = RoleStore(identity_repo._conn)
    store.set_core_group(store.administration().id, "g-admins")
    response = _post(client, "/admin/people/roles", user_id=str(anna.id), roles=["admin"])
    assert response.status_code == 400
    assert "32" in response.get_data(as_text=True)
    assert identity_repo.roles_of(anna.id) == {"experimenter"}


def test_a_key_from_administration_is_refused_on_the_page(chef, identity_repo):
    person, client = chef
    store = RoleStore(identity_repo._conn)
    store.set_functions(store.administration().id, {"admin", "approver"})
    response = _post(client, "/admin/people/roles", user_id=str(person.id), roles=["admin"])
    assert response.status_code == 400
    assert identity_repo.roles_of(person.id) == {"admin", "approver"}


def test_roles_still_change_through_the_form(chef, identity_repo):
    _, client = chef
    anna = conftest._person(identity_repo, "anna@kanzlei.ch", "Anna", "member")
    response = _post(client, "/admin/people/roles", user_id=str(anna.id), roles=["member", "approver"])
    assert response.status_code == 200
    assert identity_repo.roles_of(anna.id) == {"member", "approver"}
    _post(client, "/admin/people/roles", user_id=str(anna.id), roles=["approver"])
    assert identity_repo.roles_of(anna.id) == {"approver"}


def test_a_33rd_group_is_refused_on_the_page_and_the_old_ones_stay(chef, identity_repo):
    _, client = chef
    anna = conftest._person(identity_repo, "anna@kanzlei.ch", "Anna", "member")
    identity_repo.set_access_groups(anna.id, _groups(2))
    response = _post(client, "/admin/people/groups", user_id=str(anna.id), access_groups=",".join(_groups(33)))
    assert response.status_code == 400
    assert "höchstens 32 Zugriffsgruppen" in response.get_data(as_text=True)
    assert identity_repo.access_groups_of(anna.id) == tuple(_groups(2))


def test_32_groups_are_saved_on_the_page(chef, identity_repo):
    _, client = chef
    anna = conftest._person(identity_repo, "anna@kanzlei.ch", "Anna", "member")
    response = _post(client, "/admin/people/groups", user_id=str(anna.id), access_groups=",".join(_groups(32)))
    assert response.status_code == 200
    assert len(identity_repo.access_groups_of(anna.id)) == 32


def test_the_groups_field_holds_direct_grants_only_so_a_roles_group_does_not_outlive_the_role(chef, identity_repo):
    """Verification note F1: saving the form unchanged must not copy a role's Core group into the direct grants."""
    _, client = chef
    anna = conftest._person(identity_repo, "anna@kanzlei.ch", "Anna", "member")
    identity_repo.set_access_groups(anna.id, ["g-direkt"])
    store = RoleStore(identity_repo._conn)
    role = store.create_role(name="Personal", functions=(), core_group_id="g-role")
    store.add_member(role.id, anna.id)
    assert identity_repo.access_groups_of(anna.id) == ("g-direkt", "g-role")
    page = client.get("/admin/people").get_data(as_text=True)
    field = re.search(rf'id="groups-{anna.id}"[^>]*value="([^"]*)"', page, re.S)
    assert field is not None and field.group(1) == "g-direkt"
    response = _post(client, "/admin/people/groups", user_id=str(anna.id), access_groups=field.group(1))
    assert response.status_code == 200
    store.remove_member(role.id, anna.id)
    assert identity_repo.access_groups_of(anna.id) == ("g-direkt",)
