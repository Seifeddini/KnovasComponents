"""RB-5: the builder's own tests of a role's data, beside the frozen test_rb5_role_data_acceptance.py (work order
docs/platform-mvp/work-orders/RB-5.md). They reuse that file's recording fake Core and its scene.

What they pin that the frozen file does not:

1. ``roles.active_claim_groups`` (every active person at once, for the dry runs) agrees with ``roles.claim_groups``.
2. The plan in the corners: a subfolder the role already reads through its folder's rule is no change; a removal that
   deletes a rule leaves the next rule above governing (no ``opens`` when that one is walled); documents under a
   deeper rule do not count for the backfill warning; a pointer over 1,024 characters or with a C1 control character
   is refused.
3. The executor refuses, sending nothing, when the role's group changed after the request, or when the Core changed
   and the audit row cannot be written it says so (500) instead of claiming success.
4. Adopting the role's own group is no change; the old group is deleted only after the new one is saved.
5. The move: names that collide get " (2)"; a group's name with control characters becomes a clean role name; any
   difference in a person's sight undoes everything.
6. The page renders when the Core cannot be read, and says so in the part.
7. No Core group id, pointer or name reaches a log line, with ids a tenant might choose (speaking ones).
8. The Freigaben page's summary of a role's data change names the folder (a page, not a log) and who carries it out.

Nothing reaches a tenant: the Core is the frozen file's recording fake.
"""
from __future__ import annotations

import logging
import uuid

import pytest

from conftest import platform_db_reachable

pytestmark = pytest.mark.skipif(not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN")

from identity import roles as roles_mod  # noqa: E402
from identity.roles import claim_groups  # noqa: E402
from test_rb5_role_data_acceptance import (  # noqa: E402,F401 - the frozen file's fake Core, world and scene
    ADD,
    ADOPT,
    ADOPT_PREVIEW,
    DATA_NOTE,
    GRANTS_MOVE,
    PREVIEW,
    REMOVE,
    ROLE,
    DataCore,
    _alert,
    _dry_run,
    _part,
    _text,
    scene,
    world,
)
from web_interface import admin_role_data  # noqa: E402
from web_interface.admin_approvals import _summary  # noqa: E402


# --- 1. one definition of a claim ---------------------------------------------------------------------------------------


def test_active_claim_groups_agrees_with_claim_groups(world, scene):
    world.repo.grant_access_group(scene.dora.id, "g-alt", ends_on="2020-01-01")         # ended: not in a claim
    world.repo.grant_access_group(scene.dora.id, "g-bald", ends_on="2099-01-01")        # running window
    gone = world.person("gone", "member", groups=["g-steuer"])
    world.repo.disable(gone.id)
    everyone = roles_mod.active_claim_groups(world.conn)
    assert str(gone.id) not in everyone
    assert set(everyone) == world.active()
    for person, held in everyone.items():
        assert held == set(claim_groups(world.conn, person)), person


# --- 2. the plan in the corners -------------------------------------------------------------------------------------------


def test_a_subfolder_the_role_reads_through_its_folder_is_no_change(world, scene):
    response = world.admin.post(PREVIEW(scene.bau.id), {"op": "add", "kind": "folder", "pointer": "DMS/NurBau/2024/"})
    assert response.status_code == 200 and "data-dry-run" not in response.data.decode("utf-8")
    assert world.admin.post(ADD(scene.bau.id), {"kind": "folder", "pointer": "DMS/NurBau/2024/"}).status_code == 200
    assert DataCore.calls == []


def test_a_deleted_rule_leaves_the_rule_above_governing(world, scene):
    """NurBau/Akten has a rule for Bau alone below a rule for Steuer: removing it deletes it, Steuer's rule governs."""
    DataCore.rules += [{"rule_id": "r-oben", "pointer_prefix": "DMS/Oben/", "access_groups": ["g-steuer"]},
                       {"rule_id": "r-unten", "pointer_prefix": "DMS/Oben/Akten/", "access_groups": ["g-bau"]}]
    html = world.admin.post(PREVIEW(scene.bau.id), {"op": "remove", "kind": "folder",
                                                    "pointer": "DMS/Oben/Akten/"}).data.decode("utf-8")
    gains, losses, gaining, losing, warnings = _dry_run(html)
    assert "opens" not in warnings
    assert (gains, gaining) == (1, {str(scene.cora.id)}) and losses == 2
    assert world.admin.post(REMOVE(scene.bau.id), {"kind": "folder", "pointer": "DMS/Oben/Akten/"}).status_code == 200
    assert [(c[0], c[1][0]) for c in DataCore.calls] == [("delete_folder_rule", "r-unten")]


def test_documents_under_a_deeper_rule_do_not_count_for_the_backfill_warning(world, scene):
    DataCore.rules.append({"rule_id": "r-tief", "pointer_prefix": "DMS/Neu/Tief/", "access_groups": ["g-steuer"]})
    DataCore.docs.append({"pointer": "DMS/Neu/Tief/a.pdf", "uuid": "d-tief", "own": None})
    html = world.admin.post(PREVIEW(scene.bau.id), {"op": "add", "kind": "folder", "pointer": "DMS/Neu/"})
    assert "already-read-in" not in _dry_run(html.data.decode("utf-8"))[4]
    DataCore.docs.append({"pointer": "DMS/Neu/b.pdf", "uuid": "d-b", "own": None})
    html = world.admin.post(PREVIEW(scene.bau.id), {"op": "add", "kind": "folder", "pointer": "DMS/Neu/"})
    assert "already-read-in" in _dry_run(html.data.decode("utf-8"))[4]


@pytest.mark.parametrize("pointer", ["DMS/" + "x" * 1021, "DMS/\x85/", "DMS/\x7f/"], ids=["long", "c1", "del"])
def test_a_pointer_too_long_or_with_a_control_character_is_refused(pointer, world, scene):
    response = world.admin.post(PREVIEW(scene.bau.id), {"op": "add", "kind": "folder", "pointer": pointer})
    assert response.status_code == 400 and _alert(response.data.decode("utf-8"))
    assert DataCore.calls == []


def test_a_pointer_of_1024_characters_is_planned(world, scene):
    response = world.admin.post(PREVIEW(scene.bau.id), {"op": "add", "kind": "folder", "pointer": "D/" + "x" * 1022})
    assert response.status_code == 200


# --- 3. the executor --------------------------------------------------------------------------------------------------


def test_the_executor_refuses_when_the_roles_group_changed_after_the_request(world, scene):
    world.bypass(False)
    vera = world.person("vera", "admin")
    world.admin.post(ADD(scene.bau.id), {"kind": "folder", "pointer": "DMS/Steuer/"})
    waiting, = world.requests()
    world.store.set_core_group(scene.bau.id, "g-partner")
    world.browser(vera).post(f"/admin/approvals/{waiting['id']}/approve")
    assert DataCore.calls == [] and world.requests()[0]["status"] == "approved"


def test_a_change_whose_row_cannot_be_written_says_so(world, scene, monkeypatch):
    def refuse(*args, **kwargs):
        raise RuntimeError("audit down")

    monkeypatch.setattr(admin_role_data.audit, "record_or_raise", refuse)
    response = world.admin.post(ADD(scene.bau.id), {"kind": "folder", "pointer": "DMS/Steuer/"})
    assert response.status_code == 500
    assert "Protokolleintrag" in _alert(response.data.decode("utf-8"))
    assert [c[0] for c in DataCore.calls] == ["update_folder_rule"]                 # the Core has it


def test_the_page_after_a_change_is_read_back_from_the_core(world, scene):
    html = world.admin.post(ADD(scene.bau.id), {"kind": "document",
                                                "pointer": "DMS/Einzel/gutachten.pdf"}).data.decode("utf-8")
    assert 'data-document-uuid="d-gut"' in _part(html, "data")


# --- 4. adopting ------------------------------------------------------------------------------------------------------


def test_adopting_the_roles_own_group_is_no_change(world, scene):
    before = world.state()
    for path in (ADOPT_PREVIEW(scene.leer.id), ADOPT(scene.leer.id)):
        response = world.admin.post(path, {"group_id": "g-leer"})
        assert response.status_code == 200 and "data-dry-run" not in response.data.decode("utf-8")
    assert world.state() == before and DataCore.calls == []


def test_the_old_group_is_deleted_only_after_the_new_one_is_saved(world, scene, monkeypatch):
    """A save that fails (the 33rd group of an agent or a person) leaves the old group at the Core."""
    loaded = world.person("voll", "member", groups=[f"g-x{i:02d}" for i in range(32)])
    full = world.role("Voll", (), group="g-x00", members=[loaded])
    assert world.admin.post(ADOPT(full.id), {"group_id": "g-steuer"}).status_code == 400
    assert DataCore.calls == [] and world.store.get_role(full.id).core_group_id == "g-x00"


# --- 5. the move ------------------------------------------------------------------------------------------------------


def test_role_names_from_group_names():
    taken = {"bau", "steuer", "steuer (2)"}
    assert admin_role_data.role_name_for("Bau", taken) == "Bau (2)"
    assert admin_role_data.role_name_for("Steuer", taken) == "Steuer (3)"
    assert admin_role_data.role_name_for("  Team \x00 Zürich\t", taken) == "Team Zürich"
    assert admin_role_data.role_name_for("", taken) == "Gruppe"
    long = admin_role_data.role_name_for("x" * 200, {"x" * 80})
    assert len(long) <= 80 and long.endswith(" (2)")


def test_a_group_named_like_a_role_becomes_a_role_with_another_name(world, scene):
    DataCore.groups.append({"group_id": "g-bau-2", "name": "Bau", "parent_id": None})
    world.repo.grant_access_group(scene.dora.id, "g-bau-2")
    assert world.admin.post(GRANTS_MOVE, {}).status_code == 200
    moved = next(r for r in world.store.list_roles() if r.core_group_id == "g-bau-2")
    assert moved.name == "Bau (2)" and {str(m) for m in moved.members} == {str(scene.dora.id)}


def test_a_move_that_would_change_a_persons_sight_is_undone(world, scene, monkeypatch):
    calls = {"n": 0}
    real = admin_role_data.claim_groups

    def drifting(conn, person):
        calls["n"] += 1
        groups = real(conn, person)
        return groups + ("g-drift",) if calls["n"] > 2 else groups

    monkeypatch.setattr(admin_role_data, "claim_groups", drifting)
    before = world.state()
    response = world.admin.post(GRANTS_MOVE, {})
    assert response.status_code == 500 and _alert(response.data.decode("utf-8"))
    assert world.state() == before


# --- 6. the page when the Core cannot be read -------------------------------------------------------------------------


@pytest.mark.parametrize("call", ["folder_rules", "documents", "access_groups"])
def test_the_page_renders_when_the_core_cannot_be_read(call, world, scene):
    DataCore.refuse = {call: "raise"}
    html = world.admin.page(ROLE(scene.bau.id))
    part = _text(_part(html, "data"))
    assert part.startswith("Darf lesen") and DATA_NOTE in part
    if call != "access_groups":
        assert "Knovas hat die Daten dieser Rolle nicht genannt" in part
    else:
        assert "Knovas hat die Gruppen nicht genannt" in part


# --- 7. logs -------------------------------------------------------------------------------------------------------------


def test_no_speaking_group_id_or_pointer_reaches_a_log_line(world, caplog):
    DataCore.groups += [{"group_id": "g-zwiebel-mandat", "name": "Mandat", "parent_id": None},
                        {"group_id": "g-zwiebel-neu", "name": "Neu", "parent_id": None},
                        {"group_id": "g-zwiebel-direkt", "name": "Direkt", "parent_id": None}]
    DataCore.docs += [{"pointer": "Post/Zwiebel/brief.pdf", "uuid": "d-z", "own": None}]
    zora = world.person("zora", "member", groups=["g-zwiebel-direkt"])
    role = world.role("Mandat", (), group="g-zwiebel-mandat", members=[zora])
    loose = world.role("Lose", (), members=[zora])
    for name in (None, "web_interface", "identity", "knovas_client", "builder"):
        caplog.set_level(logging.DEBUG, logger=name)
    caplog.clear()
    world.admin.page(ROLE(role.id))
    assert world.admin.post(ADD(role.id), {"kind": "folder", "pointer": "Akten/Zwiebel/",
                                           "confirm": "1"}).status_code == 200
    DataCore.refuse = {"set_document_access": "raise"}
    assert world.admin.post(ADD(role.id), {"kind": "document", "pointer": "Post/Zwiebel/brief.pdf",
                                           "confirm": "1"}).status_code == 502
    DataCore.refuse = {}
    assert world.admin.post(ADOPT(loose.id), {"group_id": "g-zwiebel-neu"}).status_code == 200
    assert world.admin.post(ADOPT(loose.id), {"group_id": "g-zwiebel-mandat"}).status_code == 400
    assert world.admin.post(GRANTS_MOVE, {}).status_code == 200
    assert any(r.core_group_id == "g-zwiebel-direkt" for r in world.store.list_roles())
    leaked = [r.getMessage() for r in caplog.records if "zwiebel" in r.getMessage().lower()]
    assert leaked == [], leaked


# --- 8. the Freigaben page's line ------------------------------------------------------------------------------------


def test_the_freigaben_summary_names_the_folder_and_who_carries_it_out():
    line = _summary("acl_change", {"action": "role_data_add", "role_id": str(uuid.uuid4()), "kind": "folder",
                                   "pointer": "DMS/Steuer/", "confirm": True, "core_group_id": "g-bau"})
    assert "Ordner DMS/Steuer/ hinzufuegen" in line and "Warnung bestaetigt" in line and "Administration" in line
    line = _summary("acl_change", {"action": "role_data_remove", "kind": "document", "pointer": "a.pdf"})
    assert "Dokument a.pdf entfernen" in line and "bestaetigt" not in line
