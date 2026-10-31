"""Editing the ontology (build plan step B3): operations, validation, undo and redo.

The operations run on the sample package's model; the routes on the harness
with the in-memory store (test_builder_store.py holds the PostgreSQL store to
the same edit behaviour, test_builder_screens.py runs it in a browser on
Client Zero).
"""
import copy

import pytest

from builder import editing, ontology_view, package
from builder_harness import SAMPLE_PACKAGE, MemoryWorkspaceStore, StandInUser, make_app


@pytest.fixture
def model():
    return package.read_package(package.files_from_directory(SAMPLE_PACKAGE)).model


def _type(model, key):
    return next(t for t in model["ontology"]["types"] if t["key"] == key)


def _kinds(model, target):
    return sorted(p["kind"] for p in ontology_view.build(model)["problems"] if p["target"] == target)


def _apply(model, **op):
    return editing.apply(model, op)


class TestTypes:
    def test_a_new_type_is_the_company_s_and_one_change(self, model):
        changes = _apply(model, op="type.create", key="Invoice", label={"de": "Rechnung", "en": ""})
        assert changes == [{"path": "ontology.types/Invoice", "before": None,
                            "after": {"key": "Invoice", "level": "company", "attributes": [], "label": {"de": "Rechnung"}}}]

    @pytest.mark.parametrize("key", ["invoice", "In voice", "", "Mandate"])
    def test_a_key_must_be_well_formed_and_free(self, model, key):
        with pytest.raises(editing.EditError):
            _apply(model, op="type.create", key=key)

    def test_a_parent_must_exist_and_not_close_a_circle(self, model):
        _apply(model, op="type.create", key="Invoice", parent="Mandate")
        with pytest.raises(editing.EditError, match="unbekannt"):
            _apply(model, op="type.create", key="Bill", parent="xyz:Nowhere")
        _apply(model, op="type.create", key="Reminder", parent="Invoice")
        with pytest.raises(editing.EditError, match="Kreis"):
            _apply(model, op="type.update", key="Invoice", set={"parent": "Reminder"})

    def test_core_and_pack_types_are_read_only(self, model):
        locked = [t["key"] for t in model["ontology"]["types"] if t["level"] != "company"]
        assert locked
        for key in locked:
            with pytest.raises(editing.EditError) as refused:
                _apply(model, op="type.update", key=key, set={"example": "x"})
            assert refused.value.status == 403

    def test_the_description_keeps_its_header_and_follows_the_parent(self, model):
        _apply(model, op="type.create", key="Invoice")
        _type(model, "Invoice")["description"] = {"en": "[level=company; parent=Mandate] Old text."}
        _apply(model, op="type.update", key="Invoice", set={"description": "A bill.", "parent": "Deadline"})
        assert _type(model, "Invoice")["description"]["en"] == "[level=company; parent=Deadline] A bill."

    def test_a_rename_rewrites_what_names_the_type(self, model):
        _apply(model, op="type.create", key="Invoice")
        _apply(model, op="type.create", key="Reminder", parent="Invoice")
        _apply(model, op="attribute.add", key="Reminder", attribute={"name": "about", "datatype": "entity_ref"})
        _apply(model, op="attribute.update", key="Reminder", name="about", set={"target": "Invoice"})
        _apply(model, op="relation.create", key="reminds_of", domain=["Reminder"], range=["Invoice"])
        changes = _apply(model, op="type.rename", key="Invoice", new_key="Bill")
        assert {c["path"] for c in changes} == {"ontology.types/Invoice", "ontology.types/Reminder",
                                                "ontology.relations/reminds_of"}
        reminder = _type(model, "Reminder")
        assert reminder["parent"] == "Bill" and reminder["attributes"][0]["target"] == "Bill"
        assert model["ontology"]["relations"][-1]["range"] == ["Bill"]

    def test_a_type_a_process_uses_keeps_its_key(self, model):
        used = editing.usages(model, [t["key"] for t in model["ontology"]["types"] if t["level"] == "company"])
        key = next(iter(used), None)
        if key is None:
            pytest.skip("the sample's processes name no company type")
        with pytest.raises(editing.EditError) as refused:
            _apply(model, op="type.rename", key=key, new_key="Renamed")
        assert refused.value.details

    def test_retiring_marks_and_keeps_the_type(self, model):
        _apply(model, op="type.create", key="Invoice")
        _apply(model, op="type.retire", key="Invoice")
        assert _type(model, "Invoice")["retired"] is True
        _apply(model, op="type.retire", key="Invoice", retired=False)
        assert "retired" not in _type(model, "Invoice")


class TestAttributes:
    def test_add_update_move_remove(self, model):
        _apply(model, op="type.create", key="Invoice")
        for name, datatype in (("number", "text"), ("status", "enum"), ("due_on", "date")):
            _apply(model, op="attribute.add", key="Invoice", attribute={"name": name, "datatype": datatype})
        assert _type(model, "Invoice")["attributes"][1] == {"name": "status", "level": "company", "datatype": "enum",
                                                            "enum_values": []}
        _apply(model, op="attribute.update", key="Invoice", name="status",
               set={"enum_values": ["open", "paid", "open"], "required": True})
        _apply(model, op="attribute.move", key="Invoice", name="due_on", to=0)
        _apply(model, op="attribute.remove", key="Invoice", name="number")
        attrs = _type(model, "Invoice")["attributes"]
        assert [a["name"] for a in attrs] == ["due_on", "status"]
        assert attrs[1]["enum_values"] == ["open", "paid"] and attrs[1]["required"] is True

    def test_a_datatype_change_drops_what_only_the_old_one_used(self, model):
        _apply(model, op="type.create", key="Invoice")
        _apply(model, op="attribute.add", key="Invoice", attribute={"name": "status", "datatype": "enum"})
        _apply(model, op="attribute.update", key="Invoice", name="status", set={"enum_values": ["a"]})
        _apply(model, op="attribute.update", key="Invoice", name="status", set={"datatype": "text"})
        assert "enum_values" not in _type(model, "Invoice")["attributes"][0]

    @pytest.mark.parametrize("attribute", [{"name": "Due Date"}, {"name": "x", "datatype": "number"}, {"name": "name"}])
    def test_refused_attributes(self, model, attribute):
        _apply(model, op="type.create", key="Invoice")
        _apply(model, op="attribute.add", key="Invoice", attribute={"name": "name"})
        with pytest.raises(editing.EditError):
            _apply(model, op="attribute.add", key="Invoice", attribute=attribute)


class TestRelations:
    def test_a_new_relation_and_its_bounds(self, model):
        _apply(model, op="type.create", key="Invoice")
        _apply(model, op="relation.create", key="billed_to", domain=["Invoice"], range=["Mandate"], family="Market",
               inverse="bills", reason="Who pays.", label={"de": "an", "en": "to"}, sentence="Invoice billed_to Mandate.")
        relation = model["ontology"]["relations"][-1]
        assert relation["domain"] == ["Invoice"] and relation["family"] == "Market" and relation["level"] == "company"
        with pytest.raises(editing.EditError):
            _apply(model, op="relation.create", key="billed_to", domain=["Invoice"], range=["Invoice"])
        with pytest.raises(editing.EditError):
            _apply(model, op="relation.create", key="pays", domain=["Nobody"], range=["Invoice"])

    def test_a_core_relation_takes_new_types_but_keeps_its_words(self, model):
        core = next((r for r in model["ontology"]["relations"] if r["level"] == "core"), None)
        if core is None:
            pytest.skip("the sample has no core relation")
        _apply(model, op="type.create", key="Invoice")
        _apply(model, op="relation.update", key=core["key"], set={"domain": list(core.get("domain") or []) + ["Invoice"]})
        with pytest.raises(editing.EditError):
            _apply(model, op="relation.update", key=core["key"], set={"family": "Other"})


class TestValidation:
    def test_problems_appear_and_clear(self, model):
        _apply(model, op="type.create", key="Invoice", label={"de": "Rechnung", "en": "Invoice"})
        assert _kinds(model, "type:Invoice") == ["description_incomplete", "type_no_description"]
        _apply(model, op="attribute.add", key="Invoice", attribute={"name": "status", "datatype": "enum"})
        _apply(model, op="attribute.add", key="Invoice", attribute={"name": "case", "datatype": "entity_ref"})
        kinds = _kinds(model, "type:Invoice")
        assert "enum_no_values" in kinds and "entity_ref_no_target" in kinds
        _apply(model, op="attribute.update", key="Invoice", name="status", set={"enum_values": ["open"]})
        _apply(model, op="attribute.update", key="Invoice", name="case", set={"target": "Mandate"})
        _apply(model, op="type.update", key="Invoice", set={"description": "A bill.", "example": "Invoice 7."})
        assert _kinds(model, "type:Invoice") == ["attribute_description_incomplete", "attribute_not_held"]

    def test_a_retired_type_still_in_use_is_a_problem(self, model):
        _apply(model, op="type.create", key="Invoice")
        _apply(model, op="relation.create", key="billed_to", domain=["Invoice"], range=["Mandate"])
        model["processes"] = {"processes": [{"key": "PROC-billing", "instance_type": "Invoice"}]}
        _apply(model, op="type.retire", key="Invoice")
        assert "retired_in_use" in _kinds(model, "type:Invoice")
        assert "retired_in_relation" in _kinds(model, "relation:billed_to")

    def test_names_taken_twice(self, model):
        model["ontology"]["types"].append(copy.deepcopy(model["ontology"]["types"][0]))
        key = model["ontology"]["types"][0]["key"]
        assert any(p["kind"] == "name_collision" and key in p["message"]
                   for p in ontology_view.build(model)["problems"])


class TestUndoRedo:
    def test_undo_restores_and_redo_repeats(self, model):
        before = copy.deepcopy(model)
        changes = _apply(model, op="type.create", key="Invoice")
        changes += _apply(model, op="attribute.add", key="Invoice", attribute={"name": "number"})
        after = copy.deepcopy(model)
        editing.undo(model, changes)
        assert model == before
        editing.redo(model, changes)
        assert model == after

    def test_a_change_made_since_is_not_overwritten(self, model):
        changes = _apply(model, op="type.create", key="Invoice")
        _apply(model, op="type.update", key="Invoice", set={"example": "x"})
        with pytest.raises(editing.EditError) as refused:
            editing.undo(model, changes)
        assert refused.value.status == 409


class TestTheRoutes:
    @pytest.fixture
    def client(self):
        user = StandInUser({"admin"})
        store = MemoryWorkspaceStore({str(user.id): user.display_name})
        blank = package.read_package(package.blank_package("Muster AG")).model
        workspace_id = store.create(name="Muster AG", model=blank, actor_id=user.id, source={"kind": "blank"})
        return make_app(user, store).test_client(), store, workspace_id

    def test_an_edit_answers_with_the_view_and_is_logged(self, client):
        client, store, workspace_id = client
        response = client.post(f"/builder/api/w/{workspace_id}/ontology",
                               json={"op": "type.create", "key": "Invoice", "parent": "euo:Agreement"})
        body = response.get_json()
        assert response.status_code == 200 and body["history"] == {"undo": 1, "redo": 0}
        assert any(n["key"] == "Invoice" for n in body["ontology"]["nodes"])
        change = store.rows[workspace_id]["changes"][0]
        assert change["path"] == "ontology.types/Invoice" and change["before"] is None and change["actor"]

    def test_undo_then_redo(self, client):
        client, store, workspace_id = client
        client.post(f"/builder/api/w/{workspace_id}/ontology", json={"op": "type.create", "key": "Invoice"})
        undone = client.post(f"/builder/api/w/{workspace_id}/undo").get_json()
        assert undone["history"] == {"undo": 0, "redo": 1} and store.model(workspace_id)["ontology"]["types"] == []
        redone = client.post(f"/builder/api/w/{workspace_id}/redo").get_json()
        assert redone["history"] == {"undo": 1, "redo": 0}
        assert [t["key"] for t in store.model(workspace_id)["ontology"]["types"]] == ["Invoice"]
        assert client.post(f"/builder/api/w/{workspace_id}/redo").status_code == 409

    def test_a_refused_edit_changes_nothing(self, client):
        client, store, workspace_id = client
        response = client.post(f"/builder/api/w/{workspace_id}/ontology", json={"op": "type.create", "key": "bad"})
        assert response.status_code == 400 and response.get_json()["success"] is False
        assert store.rows[workspace_id]["changes"][0]["path"] == "create"
        assert client.post(f"/builder/api/w/{workspace_id}/ontology", json={"op": "drop.table"}).status_code == 400

    def test_without_a_builder_role_nothing_is_edited(self):
        user = StandInUser({"member"})
        store = MemoryWorkspaceStore()
        blank = package.read_package(package.blank_package("Muster AG")).model
        workspace_id = store.create(name="Muster AG", model=blank, actor_id=None, source={"kind": "blank"})
        client = make_app(user, store).test_client()
        for path in ("ontology", "undo", "redo"):
            assert client.post(f"/builder/api/w/{workspace_id}/{path}", json={"op": "type.create", "key": "X"}).status_code == 403
        assert store.model(workspace_id)["ontology"]["types"] == []

    def test_an_unknown_workspace_is_404(self, client):
        client, _, _ = client
        response = client.post("/builder/api/w/00000000-0000-0000-0000-000000000000/ontology",
                               json={"op": "type.create", "key": "X"})
        assert response.status_code == 404
