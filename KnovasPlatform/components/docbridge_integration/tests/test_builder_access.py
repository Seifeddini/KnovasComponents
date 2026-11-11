"""The access window (build plan step B14): the tree's limits, impact, explanations, members, rules, posture, design.

The law firm of the acceptance: Kanzlei (under the administrators) > Partner > Mandatsteam Meier and Mandatsteam
Huber; the folder /Mandate/Meier belongs to the Meier team. A partner sees the Meier documents, an associate of
the Huber team does not. builder_harness.FakeAccess decides readability as the Brain does.
"""
import datetime as dt
import io

import pytest

from builder import access as A
from builder import editing, package
from builder_harness import (SAMPLE_PACKAGE, FakeAccess, FakeEngine, MemoryDirectory, MemoryWorkspaceStore, StandInUser,
                             make_app)

TENANT = "kanzlei-test"
MEIER_DOC = "/Mandate/Meier/Vollmacht.pdf"


def _tree(*groups):
    return A.Tree.of([{"group_id": g, "name": n, "parent_id": p, "is_admin": g == "adm"} for g, n, p in groups])


LAW = _tree(("adm", "tenant-admin", None), ("k", "Kanzlei", "adm"), ("p", "Partner", "k"), ("m", "Mandatsteam Meier", "p"),
            ("h", "Mandatsteam Huber", "p"))


class TestTheTree:
    def test_ancestors_descendants_and_effective_access(self):
        assert LAW.ancestors("m") == ["p", "k", "adm"]
        assert LAW.descendants("p") == ["h", "m"]
        assert A.effective(LAW, ["p"]) == ["p", "h", "m"] and A.effective(LAW, ["h"]) == ["h"]

    @pytest.mark.parametrize("change,code", [(("move", "adm", "k"), "admin_group_protected"),
                                             (("move", "p", "m"), "cycle"), (("move", "p", "p"), "cycle"),
                                             (("move", "m", "p"), "same_parent"), (("move", "m", "nowhere"), "unknown_group")])
    def test_the_engine_s_limits_on_a_move(self, change, code):
        with pytest.raises(A.AccessError) as caught:
            A.check_move(LAW, change[1], change[2])
        assert caught.value.code == code

    def test_names_are_unique_and_the_tree_is_at_most_32_deep(self):
        with pytest.raises(A.AccessError, match="gibt es schon"):
            A.check_create(LAW, "partner", "k")
        deep = [("adm", "tenant-admin", None)] + [(f"g{i}", f"G{i}", "adm" if i == 0 else f"g{i - 1}") for i in range(31)]
        with pytest.raises(A.AccessError) as caught:
            A.check_create(_tree(*deep), "Zu tief", "g30")
        assert caught.value.code == "depth"

    def test_delete_is_refused_while_anything_is_assigned(self):
        with pytest.raises(A.AccessError) as caught:
            A.check_delete(LAW, "m", {"documents": 3, "folder_rules": 1})
        assert caught.value.code == "group_in_use" and "3 Dokumente" in caught.value.message
        with pytest.raises(A.AccessError, match="nie löschen"):
            A.check_delete(LAW, "adm", {})
        with pytest.raises(A.AccessError, match="Untergruppen"):
            A.check_delete(LAW, "p", {})

    def test_the_impact_of_a_move(self):
        members = {"p": ["paula"], "h": ["hugo"], "k": ["kurt"]}
        found = A.impact(LAW, {"op": "move", "group": "m", "parent": "h"}, members, {"m": 12})
        # Under Huber, Hugo gains the Meier items; Paula and Kurt keep them (they stand above Huber too).
        assert found["gains"] == {"hugo": 12} and found["losses"] == {} and found["items"] == 12
        found = A.impact(LAW, {"op": "move", "group": "m", "parent": "adm"}, members, {"m": 12})
        assert found["losses"] == {"paula": 12, "kurt": 12}

    def test_explain_names_the_path_or_why_not(self):
        yes = A.explain(LAW, "Paula", ["p"], ["m"], governor="die Ordnerregel /Mandate/Meier", readable=True,
                        item="«Vollmacht.pdf»")
        assert yes["path"] == ["p", "m"]
        assert yes["text"] == ("Paula sieht «Vollmacht.pdf» über die Gruppe Partner, die über Mandatsteam Meier steht, "
                               "der die Ordnerregel /Mandate/Meier es zuweist.")
        no = A.explain(LAW, "Hugo", ["h"], ["m"], governor="die Ordnerregel /Mandate/Meier", readable=False,
                       item="«Vollmacht.pdf»")
        assert no["path"] == [] and "weist es Mandatsteam Meier zu" in no["text"] and "Mandatsteam Huber" in no["text"]


@pytest.fixture
def firm():
    admin = StandInUser({"admin"}, "Ada Admin", "ada@kanzlei.example")
    second = StandInUser({"admin"}, "Bruno Zweitadmin", "bruno@kanzlei.example")
    store = MemoryWorkspaceStore({str(admin.id): admin.display_name, str(second.id): second.display_name})
    directory = MemoryDirectory(store, [admin, second])
    partner = directory.add(StandInUser({"member"}, "Paula Partnerin", "paula@kanzlei.example"))
    associate = directory.add(StandInUser({"member"}, "Hugo Huber", "hugo@kanzlei.example"))
    brain = FakeAccess(directory, documents=[MEIER_DOC, "/Mandate/Meier/Honorar.docx", "/Mandate/Huber/Klage.pdf"])
    app = make_app(admin, store, tenant=TENANT, engine=FakeEngine(), directory=directory, access=brain)
    return {"http": app.test_client(), "store": store, "brain": brain, "directory": directory, "admin": admin,
            "second": second, "partner": partner, "associate": associate, "gate": app.extensions["builder_gate"]}


def _change(http, **change):
    preview = http.post("/builder/api/access/preview", json=change)
    assert preview.status_code == 200, preview.get_json()
    applied = http.post("/builder/api/access/apply", json={**change, "confirmed": True})
    assert applied.status_code == 200, applied.get_json()
    return preview.get_json()["impact"], applied.get_json()


def _build_the_firm(f):
    http, brain = f["http"], f["brain"]
    _, made = _change(http, op="create", name="Kanzlei", parent="g-admin")
    _, made = _change(http, op="create", name="Partner", parent=made["group"])
    partner_group = made["group"]
    _, meier = _change(http, op="create", name="Mandatsteam Meier", parent=partner_group)
    _, huber = _change(http, op="create", name="Mandatsteam Huber", parent=partner_group)
    assert http.post("/builder/api/access/rules", json={"group": meier["group"], "prefix": "/Mandate/Meier"}).status_code == 201
    for person, group in ((f["partner"], partner_group), (f["associate"], huber["group"])):
        assert http.post("/builder/api/access/members", json={"group": group, "user": str(person.id)}).status_code == 200
    return partner_group, meier["group"], huber["group"]


class TestTheWindow:
    def test_a_three_level_tree_a_rule_and_two_people(self, firm):
        partner, meier, huber = _build_the_firm(firm)
        tree = firm["brain"].tree()
        assert [tree.name(g) for g in [meier] + tree.ancestors(meier)] == ["Mandatsteam Meier", "Partner", "Kanzlei", "tenant-admin"]
        page = firm["http"].get(f"/builder/zugriff?gruppe={meier}").get_data(as_text=True)
        assert "/Mandate/Meier" in page and "2 Dokumente" in page
        assert "Sieht außerdem, wer darüber steht" in page and "Paula Partnerin" in page

    def test_a_partner_sees_the_meier_documents_and_another_team_does_not(self, firm):
        _build_the_firm(firm)
        http = firm["http"]
        yes = http.post("/builder/api/access/explain", json={"user": str(firm["partner"].id), "pointer": MEIER_DOC}).get_json()
        assert yes["answer"]["readable"] is True
        assert yes["answer"]["path"] == ["Partner", "Mandatsteam Meier"]
        assert "über die Gruppe Partner, die über Mandatsteam Meier steht, der die Ordnerregel /Mandate/Meier es zuweist" in yes["answer"]["text"]
        no = http.post("/builder/api/access/explain", json={"user": str(firm["associate"].id), "pointer": MEIER_DOC}).get_json()
        assert no["answer"]["readable"] is False and no["answer"]["path"] == []
        assert "Hugo Huber sieht «Vollmacht.pdf» nicht" in no["answer"]["text"]

    def test_a_move_shows_its_impact_first(self, firm):
        _, meier, huber = _build_the_firm(firm)
        http = firm["http"]
        assert http.post("/builder/api/access/apply", json={"op": "move", "group": meier, "parent": huber}).status_code == 409
        impact, _ = _change(http, op="move", group=meier, parent=huber)
        assert impact["gains"] == [{"name": "Hugo Huber", "items": 2}] and impact["losses"] == []
        assert "Mandatsteam Meier hängt künftig unter Mandatsteam Huber statt unter Partner" in impact["what"]
        assert firm["brain"].tree().parent(meier) == huber
        # The associate sees them now, as the preview said.
        again = http.post("/builder/api/access/explain", json={"user": str(firm["associate"].id), "pointer": MEIER_DOC}).get_json()
        assert again["answer"]["readable"] is True

    def test_a_brain_that_cannot_move_says_so(self, firm):
        _, meier, huber = _build_the_firm(firm)
        firm["brain"].moves_supported = False
        answer = firm["http"].post("/builder/api/access/apply", json={"op": "move", "group": meier, "parent": huber,
                                                                      "confirmed": True})
        assert answer.status_code == 501 and "noch nicht" in answer.get_json()["error"]

    def test_delete_while_assigned_and_the_admin_group(self, firm):
        _, meier, _ = _build_the_firm(firm)
        http = firm["http"]
        refused = http.post("/builder/api/access/preview", json={"op": "delete", "group": meier})
        assert refused.status_code == 409 and refused.get_json()["code"] == "group_in_use"
        assert http.post("/builder/api/access/preview", json={"op": "delete", "group": "g-admin"}).get_json()["code"] == "admin_group_protected"

    def test_a_membership_ends_on_its_day(self, firm):
        partner, _, _ = _build_the_firm(firm)
        temp = firm["directory"].add(StandInUser({"member"}, "Tina Temporär", "tina@kanzlei.example"))
        http = firm["http"]
        tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
        assert http.post("/builder/api/access/members", json={"group": partner, "user": str(temp.id), "ends_on": tomorrow}).status_code == 200
        assert firm["directory"].access_groups_of(temp.id) == (partner,)
        firm["directory"].grant_access_group(temp.id, partner, ends_on=(dt.date.today() - dt.timedelta(days=1)).isoformat())
        assert firm["directory"].access_groups_of(temp.id) == ()
        assert http.post("/builder/api/access/members", json={"group": partner, "user": str(temp.id),
                                                              "ends_on": "2000-01-01"}).status_code == 400

    def test_a_person_s_effective_access_is_highlighted(self, firm):
        partner, meier, huber = _build_the_firm(firm)
        page = firm["http"].get(f"/builder/zugriff?person={firm['partner'].id}").get_data(as_text=True)
        assert page.count("is-lit") == 3        # Partner, Meier, Huber

    def test_switching_enforcement_takes_a_second_administrator(self, firm):
        http = firm["http"]
        assert http.post("/builder/api/access/posture", json={"mode": "enforcing"}).status_code == 201
        [req] = firm["store"].posture_requests(TENANT)
        assert http.post(f"/builder/api/access/posture/{req['id']}/approve").status_code == 403
        firm["gate"].user = firm["second"]
        assert http.post(f"/builder/api/access/posture/{req['id']}/approve").status_code == 200
        assert firm["store"].posture_requests(TENANT)[0]["approved_by_name"] == "Bruno Zweitadmin"

    def test_enforcing_waits_for_the_backfill(self, firm):
        firm["brain"].posture_state["backfill_complete"] = False
        firm["http"].post("/builder/api/access/posture", json={"mode": "enforcing"})
        [req] = firm["store"].posture_requests(TENANT)
        firm["gate"].user = firm["second"]
        answer = firm["http"].post(f"/builder/api/access/posture/{req['id']}/approve")
        assert answer.status_code == 409 and "Nachführung" in answer.get_json()["error"]

    def test_the_live_tree_is_for_administrators_only(self, firm):
        firm["gate"].user = StandInUser({"builder_author"})
        http = firm["http"]
        assert http.post("/builder/api/access/preview", json={"op": "create", "name": "X", "parent": "g-admin"}).status_code == 403
        assert http.post("/builder/api/access/members", json={}).status_code == 403
        assert http.get("/builder/zugriff").status_code == 302          # to the draft design


class TestTheDesign:
    def test_a_draft_designs_the_tree_and_publishing_creates_it(self, firm):
        http, store = firm["http"], firm["store"]
        data = package.zip_files(package.files_from_directory(SAMPLE_PACKAGE))
        http.post("/builder/api/import", data={"package": (io.BytesIO(data), "p.zip"), "name": "Kanzlei Muster"},
                  content_type="multipart/form-data")
        [workspace] = store.list()
        store.link_tenant(workspace.id, TENANT)
        q = "?entwurf=1"
        for op in ({"op": "create", "name": "Kanzlei", "parent": "admin"},):
            assert http.post("/builder/api/access/apply" + q, json={**op, "confirmed": True}).status_code == 200
        assert http.post("/builder/api/access/apply" + q, json={"op": "create", "name": "Partner", "parent": "kanzlei",
                                                                "confirmed": True}).status_code == 200
        assert http.post("/builder/api/access/apply" + q, json={"op": "create", "name": "Mandatsteam Meier",
                                                                "parent": "partner", "confirmed": True}).status_code == 200
        assert http.post("/builder/api/access/rules" + q, json={"group": "mandatsteam-meier", "prefix": "/Mandate/Meier"}).status_code == 201
        model = store.model(workspace.id)
        assert [g["name"] for g in model["access"]["groups"]] == ["Kanzlei", "Partner", "Mandatsteam Meier"]
        assert "access.groups/partner" in [c["path"] for c in store.draft_changes(workspace.id)]
        page = http.get("/builder/zugriff?entwurf=1").get_data(as_text=True)
        assert "Mandatsteam Meier" in page and "/Mandate/Meier" in page
        # Reviewed and published: the groups and the rule appear on the tenant.
        http.post(f"/builder/api/w/{workspace.id}/dry-run")
        draft = store.draft(workspace.id)
        firm["gate"].user = firm["second"]
        assert http.post(f"/builder/api/w/{workspace.id}/approve", json={"content_hash": draft["content_hash"]}).status_code == 200
        result = http.post(f"/builder/api/w/{workspace.id}/publish", json={"content_hash": draft["content_hash"]}).get_json()
        assert result["result"]["access"] == {"written": True, "groups": 3, "rules": 1}
        tree = firm["brain"].tree()
        meier = tree.by_name("Mandatsteam Meier")
        assert [tree.name(x) for x in tree.ancestors(meier)] == ["Partner", "Kanzlei", "tenant-admin"]
        assert firm["brain"].rules()[0]["access_groups"] == [meier]

    def test_the_design_keeps_the_engine_s_limits(self, firm):
        store = firm["store"]
        model = package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model
        editing.apply(model, {"op": "access.group.create", "name": "Kanzlei"})
        editing.apply(model, {"op": "access.group.create", "name": "Partner", "parent": "kanzlei"})
        with pytest.raises(editing.EditError, match="unter sich selbst"):
            editing.apply(model, {"op": "access.group.move", "key": "kanzlei", "parent": "partner"})
        with pytest.raises(editing.EditError, match="gibt es schon"):
            editing.apply(model, {"op": "access.group.create", "name": "kanzlei"})
        editing.apply(model, {"op": "access.rule.add", "prefix": "/Mandate", "groups": ["partner"]})
        with pytest.raises(editing.EditError, match="zugewiesen"):
            editing.apply(model, {"op": "access.group.remove", "key": "partner"})

    def test_a_consultant_designs_the_tree_of_their_workspace(self, firm):
        http, store = firm["http"], firm["store"]
        data = package.zip_files(package.files_from_directory(SAMPLE_PACKAGE))
        http.post("/builder/api/import", data={"package": (io.BytesIO(data), "p.zip"), "name": "Kanzlei Muster"},
                  content_type="multipart/form-data")
        [workspace] = store.list()
        consultant = firm["directory"].add(StandInUser(frozenset(), "Bea Berater", "bea@extern.example"))
        store.grant_access(workspace.id, user_id=consultant.id, email=consultant.email, roles=["builder_author"],
                           ends_on=(dt.date.today() + dt.timedelta(days=7)).isoformat(), actor_id=firm["admin"].id)
        firm["gate"].user = consultant
        http.get(f"/builder/w/{workspace.id}")
        assert http.get("/builder/zugriff").status_code == 302              # the live tree is not hers
        assert http.get("/builder/zugriff?entwurf=1").status_code == 200
        assert http.post("/builder/api/access/apply?entwurf=1", json={"op": "create", "name": "Kanzlei", "parent": "admin",
                                                                      "confirmed": True}).status_code == 200
        assert http.post("/builder/api/access/members", json={}).status_code == 403
        assert [g["name"] for g in store.model(workspace.id)["access"]["groups"]] == ["Kanzlei"]
