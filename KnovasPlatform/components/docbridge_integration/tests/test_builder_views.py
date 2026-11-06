"""How a company sees its Brain (build plan step B9): spines, pages, profiles and roles.

The checks, the edits, the review lines, the preview from sample data, the
compiled package and the routes. Client Zero's definitions are checked when
$KNOWLEDGEBASE_CLIENT_ZERO names its folder; test_builder_screens.py runs the
stage in a browser.
"""
import json
import os
from pathlib import Path

import pytest

from builder import editing, package, review, views
from builder_harness import SAMPLE_PACKAGE, MemoryWorkspaceStore, StandInUser, make_app

ROLES = ["admin", "approver", "ingestion_manager", "member"]


def _apply(model, **op):
    return editing.apply(model, op)


@pytest.fixture
def model():
    """The sample package with its profile rebuilt through the operations (the fixture's predates the schema)."""
    model = package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model
    _apply(model, op="profile.remove", id="partner")
    _apply(model, op="profile.create", id="partner", audience="Partner")
    _apply(model, op="profile.update", id="partner", set={"lens_spines": ["SPINE-client"],
                                                          "lens_types": ["Organization", "Mandate", "Deadline"],
                                                          "pages": ["PAGE-mandate"]})
    _apply(model, op="view.add", profile="partner", view={
        "id": "mandate-map", "label": "Mandate", "render": "map",
        "definition": {"root_types": ["Organization"],
                       "paths": [{"label": "Mandate", "path": [{"relation": "client_of", "direction": "out", "to_type": "Mandate"}]}]}})
    return model


def _kinds(model, **kw):
    return sorted(f["kind"] for f in views.validate(model, **kw))


def _profile(model, pid="partner"):
    return next(e["definition"] for e in model["profiles"] if e["definition"]["id"] == pid)


def _page(model, key="PAGE-mandate"):
    return next(p for p in model["pages"]["pages"] if p["key"] == key)


def _spine(model, key="SPINE-client"):
    return next(s for s in model["spines"]["spines"] if s["key"] == key)


class TestTheChecks:
    def test_the_sample_is_clean_but_for_the_types_without_a_page(self, model):
        found = views.validate(model, roles=ROLES)
        assert {(f["kind"], f["part"]) for f in found} == {("page_missing", "Organization"), ("page_missing", "Deadline")}
        assert all(f["severity"] == "warning" for f in found)

    def test_a_page_for_the_type_clears_its_warning(self, model):
        _apply(model, op="page.create", type="Deadline")
        assert ("page_missing", "Deadline") not in {(f["kind"], f["part"]) for f in views.validate(model)}
        assert _page(model, "PAGE-deadline")["sections"][0]["field"] == "description"

    def test_a_level_type_that_does_not_exist(self, model):
        _apply(model, op="level.add", spine="SPINE-client", level={"name": "Akte", "types": ["Mandate"]})
        _spine(model)["levels"][-1]["types"] = ["Nowhere"]
        assert "type_unknown" in _kinds(model)

    def test_a_level_without_a_link_and_a_link_that_does_not_fit(self, model):
        _apply(model, op="level.add", spine="SPINE-client", level={"name": "Noch eine", "types": ["Organization"]})
        assert "link_missing" in _kinds(model)
        level = _spine(model)["levels"][-1]["id"]
        _apply(model, op="level.update", spine="SPINE-client", level=level, set={"link": {"relation": "due_in", "direction": "out"}})
        found = [f for f in views.validate(model) if f["kind"] == "link_mismatch"]
        assert found and "passt nicht zu Von und Nach" in found[0]["message"]

    def test_a_path_through_a_relation_that_does_not_exist(self, model):
        _apply(model, op="slot.add", page="PAGE-mandate", slot={
            "id": "people", "title": {"de": "Personen"}, "render": "list",
            "content": {"paths": [{"path": [{"relation": "knows", "direction": "out", "to_type": "Organization"}]}]}})
        found = [f for f in views.validate(model) if f["kind"] == "path_invalid"]
        assert found and "die Beziehung knows gibt es nicht" in found[0]["message"] and found[0]["part"] == "people"

    def test_a_view_on_a_process_that_does_not_exist_and_a_board_without_one(self, model):
        _apply(model, op="view.add", profile="partner", view={"id": "board", "render": "board",
                                                              "definition": {"root_types": ["Deadline"], "process": "PROC-nowhere"}})
        assert "process_unknown" in _kinds(model)
        _apply(model, op="view.update", profile="partner", view="board", set={"definition": {"process": ""}})
        found = [f for f in views.validate(model) if f["kind"] == "not_drawable"]
        assert found and "kein Prozess hat Deadline als Instanztyp" in found[0]["message"]

    def test_a_profile_lists_a_page_and_a_role_names_a_profile(self, model):
        _apply(model, op="roles.set", role="member", profiles=["partner"])
        assert views.profile_roles(model) == {"member": ["partner"]}
        assert _kinds(model, roles=["admin"]) .count("role_unknown") == 1
        _profile(model)["pages"] = ["PAGE-gone"]
        assert "page_unknown" in _kinds(model)

    def test_the_profile_schema_is_kept(self, model):
        _profile(model)["rights"] = {"read": [], "write": "everything"}
        assert "schema" in _kinds(model)

    def test_client_zero_has_no_critical_problem(self):
        root = os.environ.get("KNOWLEDGEBASE_CLIENT_ZERO")
        if not root:
            pytest.skip("needs $KNOWLEDGEBASE_CLIENT_ZERO")
        model = package.read_package(package.files_from_directory(Path(root))).model
        found = views.validate(model, roles=ROLES)
        assert [f for f in found if f["severity"] == "critical"] == []
        missing = {f["part"] for f in found if f["kind"] == "page_missing"}
        assert {"FeaturePlan", "Decision", "Risk"} <= missing and "Component" not in missing
        view = views.build(model, roles=ROLES)
        assert {p["id"] for p in view["profiles"]} == {"agent", "ci", "cto", "engineer"}
        assert [s["key"] for s in view["spines"]][:2] == ["SPINE-product", "SPINE-organisation"]


class TestTheEdits:
    def test_levels_move_and_links_take_the_short_form(self, model):
        _apply(model, op="level.move", spine="SPINE-client", level="L3", to=1)
        assert [lv["id"] for lv in _spine(model)["levels"]] == ["L1", "L3", "L2"]
        _apply(model, op="level.update", spine="SPINE-client", level="L2",
               set={"links": {"Mandate": {"relation": "client_of", "direction": "in"}}})
        level = _spine(model)["levels"][2]
        assert level["link_to_parent"] == {"relation": "client_of", "direction": "in"} and "links_to_parent" not in level

    def test_a_new_hierarchy_comes_with_its_top_level(self, model):
        _apply(model, op="spine.create", key="SPINE-aufbau", name="Aufbau", types=["Organization"], level_name="Firma")
        assert _spine(model, "SPINE-aufbau")["levels"] == [{"id": "L1", "name": "Firma", "types": ["Organization"]}]
        found = [f["message"] for f in views.validate(model) if f["item"] == "SPINE-aufbau"]
        assert found == ["Eine Hierarchie braucht mindestens 2 Ebenen. Fügen Sie unter «Firma» die nächste an."]
        _apply(model, op="level.add", spine="SPINE-aufbau",
               level={"name": "Mandate", "types": ["Mandate"], "link": {"relation": "client_of", "direction": "in"}})
        assert not [f for f in views.validate(model) if f["item"] == "SPINE-aufbau"]

    def test_an_empty_hierarchy_says_what_to_do(self, model):
        _apply(model, op="spine.create", key="SPINE-leer", name="Leer")
        found = [f["message"] for f in views.validate(model) if f["item"] == "SPINE-leer"]
        assert found == ["Die Hierarchie hat noch keine Ebene. Legen Sie die oberste und eine darunter an."]

    def test_a_branch_with_its_own_levels(self, model):
        _apply(model, op="branch.create", spine="SPINE-client", key="billing", name="Abrechnung")
        _apply(model, op="level.add", spine="SPINE-client", branch="billing", level={"name": "Kunde", "types": ["Organization"]})
        branch = _spine(model)["branches"][0]
        assert branch["levels"][0]["id"] == "B1" and branch["levels"][0]["types"] == ["Organization"]
        assert views.normalise_spines(model["spines"])["spines"][0]["branches"][0]["levels"][0]["verified"] is True

    def test_slots_are_ordered_and_keep_what_the_editor_does_not_show(self, model):
        _page(model)["sections"][1]["stale"] = {"rule": "record_older_than_commits", "days": 3}
        _apply(model, op="slot.update", page="PAGE-mandate", slot="deadlines", set={"title": {"de": "Termine", "en": "Dates"}})
        _apply(model, op="slot.move", page="PAGE-mandate", slot="deadlines", to=0)
        sections = _page(model)["sections"]
        assert [s["id"] for s in sections] == ["deadlines", "description"]
        assert sections[0]["stale"]["days"] == 3 and sections[0]["title"] == {"de": "Termine", "en": "Dates"}

    def test_a_slot_needs_content_and_an_id_once(self, model):
        with pytest.raises(editing.EditError, match="Was füllt den Abschnitt"):
            _apply(model, op="slot.add", page="PAGE-mandate", slot={"id": "x", "title": {"de": "X"}, "content": {}})
        with pytest.raises(editing.EditError, match="gibt es schon"):
            _apply(model, op="slot.add", page="PAGE-mandate", slot={"id": "description", "title": {"de": "X"},
                                                                    "content": {"field": "name"}})

    def test_removing_a_page_or_profile_takes_its_references_along(self, model):
        _apply(model, op="roles.set", role="member", profiles=["partner"])
        _apply(model, op="page.remove", key="PAGE-mandate")
        assert _profile(model)["pages"] == []
        changes = _apply(model, op="profile.remove", id="partner")
        assert "profile_roles" not in model
        assert [c["path"] for c in changes] == ["profiles/partner", "profile_roles/member"]

    def test_undo_takes_a_view_back(self, model):
        changes = _apply(model, op="view.add", profile="partner", view={"id": "list", "render": "list",
                                                                        "definition": {"root_types": ["Mandate"]}})
        editing.undo(model, changes)
        assert [v["id"] for v in _profile(model)["views"]] == ["mandate-map"]

    def test_a_new_profile_has_every_part_the_schema_asks_for(self, model):
        _apply(model, op="profile.create", id="associate", audience="Associate")
        entry = next(e for e in model["profiles"] if e["definition"]["id"] == "associate")
        assert entry["file"] == "profiles/associate.yaml"
        assert not [f for f in views.validate(model) if f["item"] == "associate" and f["severity"] == "critical"]


class TestTheReview:
    def test_lines_for_a_view_a_slot_and_a_role(self, model):
        before = json.loads(json.dumps(model))
        _apply(model, op="view.add", profile="partner", view={"id": "list", "label": "Liste", "render": "list",
                                                              "definition": {"root_types": ["Mandate"]}})
        _apply(model, op="slot.remove", page="PAGE-mandate", slot="deadlines")
        _apply(model, op="roles.set", role="member", profiles=["partner"])
        groups = {g["path"]: g for g in review.diff([
            {"path": "profiles/partner", "before": before["profiles"][0], "after": model["profiles"][0]},
            {"path": "pages/PAGE-mandate", "before": before["pages"]["pages"][0], "after": model["pages"]["pages"][0]},
            {"path": "profile_roles/member", "before": None, "after": model["profile_roles"][0]}])}
        assert [ln["text"] for ln in groups["profiles/partner"]["lines"]] == ["Ansicht Liste"]
        assert [ln["text"] for ln in groups["pages/PAGE-mandate"]["lines"]] == ["Abschnitt Fristen"]
        assert groups["profile_roles/member"]["lines"][0]["text"] == "sieht partner"


class TestThePreview:
    def test_a_map_from_sample_data(self, model):
        pv = views.preview(model, "partner")
        assert pv["view"]["kind"] == "map"
        [root] = pv["view"]["roots"]
        assert root["type"] == "Organization" and [c["via"] for c in root["children"]] == ["Mandate", "Mandate"]
        assert pv["page"]["key"] == "PAGE-mandate"
        [description, deadlines] = pv["page"]["sections"]
        assert description["text"] and len(deadlines["items"]) == 2 and not deadlines["empty"]

    def test_a_required_slot_that_cannot_be_filled_is_marked(self, model):
        _page(model)["sections"][1]["required"] = True
        _page(model)["sections"][1]["paths"][0]["path"][0]["relation"] = "knows"
        section = views.preview(model, "partner")["page"]["sections"][1]
        assert section["empty"] and section["required"] and section["problems"]

    def test_list_board_and_matrix(self, model):
        _apply(model, op="view.add", profile="partner", view={"id": "list", "render": "list",
                                                              "definition": {"root_types": ["Mandate"], "columns": ["status"]}})
        lst = views.preview(model, "partner", view_id="list")["view"]
        assert lst["kind"] == "list" and len(lst["rows"]) == 5
        _apply(model, op="view.add", profile="partner", view={"id": "matrix", "render": "matrix",
                                                              "definition": {"spine": "SPINE-client"}})
        mx = views.preview(model, "partner", view_id="matrix")["view"]
        assert mx["kind"] == "matrix" and mx["rows"] and "filled" in mx["cells"][0]

    def test_client_zero_cto_start_view(self):
        root = os.environ.get("KNOWLEDGEBASE_CLIENT_ZERO")
        if not root:
            pytest.skip("needs $KNOWLEDGEBASE_CLIENT_ZERO")
        model = package.read_package(package.files_from_directory(Path(root))).model
        pv = views.preview(model, "cto")
        assert pv["view"]["id"] == "system-map" and pv["view"]["roots"][0]["key"] == "SYS-KNOVAS-SEMANTIX"
        board = views.preview(model, "cto", view_id="work-board")["view"]
        assert board["kind"] == "board" and board["process"] == "PROC-feature-lifecycle"
        assert [s["title"] for s in pv["page"]["sections"]][0] == "Beschreibung"


class TestThePackage:
    def test_an_unchanged_package_keeps_its_generated_files(self):
        root = os.environ.get("KNOWLEDGEBASE_CLIENT_ZERO")
        if not root:
            pytest.skip("needs $KNOWLEDGEBASE_CLIENT_ZERO")
        files = package.files_from_directory(Path(root))
        model = package.read_package(files).model
        out = package.write_package(model)
        for path in ("ontology/generated/spine-definitions.json", "ontology/generated/page-definitions.json"):
            assert out[path] == files[path]
        _apply(model, op="slot.move", page="PAGE-component", slot="calls", to=0)
        written = json.loads(package.write_package(model)["ontology/generated/page-definitions.json"])
        component = next(p for p in written["pages"] if p["key"] == "PAGE-component")
        assert component["sections"][0]["id"] == "calls"

    def test_the_role_mapping_is_the_builder_s_own_part_of_the_package(self, model):
        """Not in brain-runner's files (the manifest, the profiles): in the Builder's own file beside them, so the
        export keeps it (KB-10). The package brain-runner reads leaves it out."""
        _apply(model, op="roles.set", role="member", profiles=["partner"])
        files = package.write_package(model)
        assert [path for path, data in files.items() if b"profile_roles" in data] == [package.WORKSPACE_FILE]
        assert package.content_hash(model) == package.content_hash(package.read_package(files).model)
        assert package.WORKSPACE_FILE not in package.write_package(model, workspace_parts=False)


class TestTheRoutes:
    @pytest.fixture
    def client(self):
        user = StandInUser({"admin"}, "Alex Beispiel")
        store = MemoryWorkspaceStore({str(user.id): user.display_name})
        written = []
        app = make_app(user, store, tenant="t-1", roles=ROLES + ["builder_viewer"],
                       roles_sink=lambda mapping, who: written.append(mapping))
        model = package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model
        workspace_id = store.create(name="Beispiel AG", model=model, actor_id=user.id, source={"kind": "zip"}, tenant="t-1")
        c = app.test_client()
        with c.session_transaction() as sess:
            sess["builder_workspace"] = workspace_id
        return c, store, workspace_id, written

    def test_the_stage_renders(self, client):
        c, _, _, _ = client
        response = c.get("/builder/ansichten")
        assert response.status_code == 200
        text = response.get_data(as_text=True)
        assert 'id="b-views-data"' in text and "Hierarchien" in text

    def test_an_edit_answers_with_the_views(self, client):
        c, store, wid, _ = client
        response = c.post(f"/builder/api/w/{wid}/ontology?view=views", json={"op": "page.create", "type": "Deadline"})
        body = response.get_json()
        assert response.status_code == 200 and any(p["key"] == "PAGE-deadline" for p in body["views"]["pages"])
        assert [r["role"] for r in body["views"]["roles"]] == sorted(ROLES + ["builder_viewer"])

    def test_a_role_the_platform_does_not_have_is_refused(self, client):
        c, _, wid, _ = client
        response = c.post(f"/builder/api/w/{wid}/ontology?view=views", json={"op": "roles.set", "role": "nobody", "profiles": []})
        assert response.status_code == 404

    def test_the_preview(self, client):
        c, _, wid, _ = client
        body = c.post(f"/builder/api/w/{wid}/views/preview", json={"profile": "partner", "page": "PAGE-mandate"}).get_json()
        assert body["success"] and body["preview"]["page"]["key"] == "PAGE-mandate"
        assert c.post(f"/builder/api/w/{wid}/views/preview", json={"profile": "nobody"}).status_code == 404
