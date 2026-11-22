"""RB-FIX-1, FU-42: every reader of "who holds which group" counts a group held through a role.

Since RB-4 a role carries its own Core group, and a person holds it by being in the role: the claim
(``UserRepository.access_groups_of``, ``roles.claim_groups``) has counted it since RB-2. Three readers still read the
direct grants only and under-reported such a person: the Experimente search's "missing group" hint
(``experiments.store.user_access_groups``), ``UserRepository.members_of_groups`` and the group part of
``experiments.store.viewers_without_groups``. They now read by the claim's rule: a direct grant that still runs, or a
role's Core group. Real PostgreSQL, a schema per test; nothing reaches a Core.
"""
from __future__ import annotations

import datetime as dt

import pytest

from conftest import _person, platform_db_reachable

pytestmark = pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")

from experiments import store as experiments_store  # noqa: E402
from experiments.service import MSG_MISSING_GROUP, ExperimentService  # noqa: E402
from experiments.settings import ExperimentsSettings  # noqa: E402
from identity import roles  # noqa: E402

SETTINGS = ExperimentsSettings(enabled=True, index_enabled=True, index_access_groups=("g-exp",),
                               index_debounce_seconds=60, worker_enabled=False)
#: Well in the past: the database counts days in its own time zone (UTC), the test in the local one.
ENDED = (dt.date.today() - dt.timedelta(days=10)).isoformat()


@pytest.fixture
def firm(platform_db, identity_repo):
    """Four people who see Experimente: through a role's Core group, directly, through an ended grant, not at all."""
    store = roles.RoleStore(platform_db)
    team = store.create_role(name="Team Experimente", functions={"member"}, core_group_id="g-exp")
    other = store.create_role(name="Andere", functions={"member"}, core_group_id="g-andere")

    class Firm:
        conn = platform_db
        repo = identity_repo
        by_role = _person(identity_repo, "rolle@kanzlei.ch", "Rolf", "experimenter")
        direct = _person(identity_repo, "direkt@kanzlei.ch", "Dora", "experimenter")
        ended = _person(identity_repo, "vorbei@kanzlei.ch", "Viktor", "experimenter")
        none = _person(identity_repo, "ohne@kanzlei.ch", "Olga", "experimenter")

    store.add_member(team.id, Firm.by_role.id)
    store.add_member(other.id, Firm.none.id)
    identity_repo.grant_access_group(Firm.direct.id, "g-exp")
    identity_repo.grant_access_group(Firm.ended.id, "g-exp", ends_on=ENDED)
    Firm.team, Firm.other = team, other
    return Firm


def test_the_search_hint_counts_a_group_held_through_a_role(firm):
    def knovas(query, limit):
        return {"results": []}

    def search(person):
        service = ExperimentService(firm.conn, person, SETTINGS, knovas_search=knovas,
                                    request_meta={"ip": "10.0.0.1", "user_agent": "pytest", "token_id": None})
        return service.search("Karussell")

    assert experiments_store.user_access_groups(firm.conn, firm.by_role.id) == ("g-exp",)
    assert search(firm.by_role) == {"source": "knovas", "warning": None, "items": []}    # Knovas was asked
    assert search(firm.direct)["warning"] is None
    # An ended grant no longer counts, as in the claim: Knovas would not show the documents.
    assert experiments_store.user_access_groups(firm.conn, firm.ended.id) == ()
    assert search(firm.ended)["warning"] == MSG_MISSING_GROUP
    assert experiments_store.user_access_groups(firm.conn, firm.none.id) == ("g-andere",)
    assert search(firm.none)["warning"] == MSG_MISSING_GROUP
    assert experiments_store.user_access_groups(firm.conn, "kein-uuid") == ()


def test_the_readers_follow_the_claim_for_each_person(firm):
    for person in (firm.by_role, firm.direct, firm.ended, firm.none):
        assert experiments_store.user_access_groups(firm.conn, person.id) == firm.repo.access_groups_of(person.id)


def test_viewers_without_the_group_leave_out_who_holds_it_through_a_role(firm):
    listed = {row["user"] for row in experiments_store.viewers_without_groups(firm.conn, ["g-exp"])}
    assert listed == {"Viktor (vorbei@kanzlei.ch)", "Olga (ohne@kanzlei.ch)"}
    # Any one of the groups is enough; a role's group counts like a direct grant.
    listed = {row["user"] for row in experiments_store.viewers_without_groups(firm.conn, ["g-x", "g-andere"])}
    assert "Olga (ohne@kanzlei.ch)" not in listed and "Rolf (rolle@kanzlei.ch)" in listed


def test_leaving_the_role_takes_the_group_away_again(firm):
    roles.RoleStore(firm.conn).remove_member(firm.team.id, firm.by_role.id)
    assert experiments_store.user_access_groups(firm.conn, firm.by_role.id) == ()
    listed = {row["user"] for row in experiments_store.viewers_without_groups(firm.conn, ["g-exp"])}
    assert "Rolf (rolle@kanzlei.ch)" in listed
    assert str(firm.by_role.id) not in firm.repo.members_of_groups().get("g-exp", [])


def test_members_of_groups_counts_the_roles_groups(firm):
    members = firm.repo.members_of_groups()
    assert members["g-exp"] == sorted([str(firm.by_role.id), str(firm.direct.id)])
    assert members["g-andere"] == [str(firm.none.id)]
    assert str(firm.ended.id) not in members["g-exp"]


def test_a_person_with_the_group_twice_is_listed_once(firm):
    firm.repo.grant_access_group(firm.by_role.id, "g-exp")
    assert firm.repo.members_of_groups()["g-exp"].count(str(firm.by_role.id)) == 1
    assert experiments_store.user_access_groups(firm.conn, firm.by_role.id) == ("g-exp",)


def test_the_shared_rows_hold_to_the_claim(firm):
    """``roles.CLAIM_GROUP_ROWS`` (the many-people readers) and ``roles.claim_groups`` (the claim) say the same."""
    rows = firm.conn.execute(
        f"SELECT user_id::text, group_id FROM ({roles.CLAIM_GROUP_ROWS}) c ORDER BY 1, 2").fetchall()
    by_person: dict = {}
    for user_id, group_id in rows:
        by_person.setdefault(user_id, []).append(group_id)
    people = [r[0] for r in firm.conn.execute("SELECT id::text FROM users").fetchall()]
    for person in people:
        assert tuple(sorted(by_person.get(person, []))) == tuple(sorted(roles.claim_groups(firm.conn, person)))
