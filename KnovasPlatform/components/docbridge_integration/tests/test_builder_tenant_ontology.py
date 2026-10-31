"""A workspace from the ontology a tenant already has (the tenant's node types and schemas)."""
import pytest

from builder import ontology_view, package, tenant_ontology
from builder_harness import MemoryWorkspaceStore, StandInUser, make_app

NODE_TYPES = [
    {"id": "t1", "name": "Company", "managed_by": "bundle:tenant",
     "description": "[level=euo; parent=-; iri=euo:Company] Company / Firma — The company the Brain is about."},
    {"id": "t2", "name": "Opportunity",
     "description": "[level=pack; parent=euo:Transaction; iri=-] Opportunity / Verkaufschance — A deal in the pipeline."},
    {"id": "t3", "name": "Workshop", "description": "Made by hand, no header."},
    {"id": "core1", "name": "Entity", "managed_by": "bundle:core", "description": "the core"},
]
SCHEMAS = {
    "t1": [{"name": "legal_name", "datatype": "text", "required": True}],
    "t2": [{"name": "stage", "datatype": "enum", "enum_values": ["lead", "won"]},
           {"name": "account", "datatype": "entity_ref", "target_node_type_id": "t1"},
           {"name": "old", "datatype": "text", "deprecated": True}],
    "t3": None,
}


def _schema(type_id):
    return SCHEMAS.get(type_id, [])


def test_the_tenant_types_become_the_workspace_types():
    model, counts = tenant_ontology.model_from_tenant("Knovas", NODE_TYPES, _schema)
    types = {t["key"]: t for t in model["ontology"]["types"]}
    assert set(types) == {"Company", "Opportunity", "Workshop"}  # the engine's core is not the company's
    assert counts == {"types": 3, "attributes": 3, "unreadable": 1}
    company, opportunity, workshop = types["Company"], types["Opportunity"], types["Workshop"]
    assert company["level"] == "core" and company["iri"] == "euo:Company" and "parent" not in company
    assert company["label"] == {"en": "Company", "de": "Firma"}
    assert company["description"] == {"en": "The company the Brain is about."}
    assert opportunity["level"] == "pack" and opportunity["parent"] == "euo:Transaction"
    assert [a["name"] for a in opportunity["attributes"]] == ["stage", "account"]
    assert opportunity["attributes"][1]["target"] == "Company"
    assert workshop["level"] == "company" and workshop["description"] == {"en": "Made by hand, no header."}


def test_it_is_a_model_the_builder_reads_and_exports():
    model, _ = tenant_ontology.model_from_tenant("Knovas", NODE_TYPES, _schema)
    files = package.write_package(model)
    assert package.read_package(files).model == model
    view = ontology_view.build(model)
    company = next(n for n in view["nodes"] if n["key"] == "Company")
    assert company["id"] == "euo:Company"  # the core class, as the tenant uses it
    # the whole core is there; Company is the core class as the tenant uses it
    assert view["counts"]["types"] == 3 and view["counts"]["reference"] == 79


class TestTheWizard:
    def _client(self, tenant="af000000", listing=(NODE_TYPES, _schema)):
        user = StandInUser({"admin"})
        store = MemoryWorkspaceStore({str(user.id): user.display_name})
        return make_app(user, store, tenant=tenant, tenant_types=listing).test_client(), store

    def test_the_wizard_offers_the_tenant_ontology(self):
        client, _ = self._client()
        assert 'value="tenant"' in client.get("/builder/arbeitsbereich").get_data(as_text=True)

    def test_a_workspace_from_the_tenant_belongs_to_it(self):
        client, store = self._client()
        response = client.post("/builder/api/workspaces", data={"name": "Knovas", "start": "tenant"})
        assert response.status_code == 201
        [workspace] = store.list()
        assert workspace.tenant == "af000000" and workspace.summary["types"] == 3
        assert store.changes(workspace.id)[0]["after"]["kind"] == "tenant"

    def test_without_a_known_tenant_there_is_no_tenant_start(self):
        client, store = self._client(tenant=None)
        assert 'value="tenant"' not in client.get("/builder/arbeitsbereich").get_data(as_text=True)
        assert client.post("/builder/api/workspaces", data={"name": "Knovas", "start": "tenant"}).status_code == 409
        assert store.list() == []

    def test_a_tenant_that_cannot_be_read_is_said_so(self):
        def unreachable(type_id):
            raise ConnectionError("no route")

        client, store = self._client(listing=(NODE_TYPES, unreachable))
        response = client.post("/builder/api/workspaces", data={"name": "Knovas", "start": "tenant"})
        assert response.status_code == 502 and store.list() == []

    def test_packs_still_combine(self):
        client, store = self._client()
        response = client.post("/builder/api/workspaces", data={"name": "Knovas", "start": "tenant", "packs": ["legal"]})
        assert response.status_code == 201
        assert store.list()[0].summary["types"] == 3 + 8
