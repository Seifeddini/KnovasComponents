"""A workspace from the tenant takes its definitions too: processes, spines, pages and profiles from the
definitions bundle the views read, and the tenant's own node types beside the bundle's."""
import copy
from pathlib import Path

import pytest

from builder import package, processes, tenant_definitions, tenant_ontology, views
from graph_views import bundle as bundles

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "knovas-bundle" / "bundle.json"

# The tenant's node types as the graph lists them: one the bundle defines, one made by hand.
LISTING = [
    {"id": "t1", "name": "Product",
     "description": "[level=euo; parent=euo:Offering; iri=euo:Product] Product / Produkt — As the tenant has it."},
    {"id": "t3", "name": "Workshop", "description": "Made by hand, no header."},
]


def _schema(type_id):
    return [{"name": "tenant_only", "datatype": "text"}]


@pytest.fixture(scope="module")
def knovas():
    return bundles.load_file(FIXTURE)


def _current(knovas):
    """The fixture bundle with its report rules as the current package writes them: the fixture's content
    (c325397d) predates ``dimension`` and ``severity``, which the builder's schema requires."""
    sections = copy.deepcopy(knovas.sections)
    for rule in sections["processes"]["report_rules"]:
        rule.setdefault("dimension", "completeness")
        rule.setdefault("severity", "violation")
    return bundles.Bundle(knovas.header, sections, knovas.source)


def test_the_bundle_becomes_a_package_the_builder_reads(knovas):
    result = tenant_definitions.model_from_bundle("Knovas GmbH", knovas)
    s = result.summary
    assert (s["types"], s["relations"], s["processes"], s["spines"], s["pages"], s["profiles"]) == (62, 58, 7, 4, 5, 4)
    assert [p["key"] for p in processes.processes_of(result.model)][:2] == ["PROC-feature-lifecycle", "PROC-decision"]
    assert [s["key"] for s in views.spines_of(result.model)] == ["SPINE-product", "SPINE-organisation", "SPINE-process", "SPINE-market"]
    assert sorted(p["id"] for p in views.profiles_of(result.model)) == ["agent", "ci", "cto", "engineer"]
    # the company is the bundle's, not the workspace's name
    assert result.model["company"] == {"key": "KNOVAS", "name": "Knovas", "kind": "software company (Client Zero)"}
    assert result.model["mappings"] == [] and result.model["edit_sources"] is None
    # what the bundle holds is a valid configuration for the builder's checks; the fixture's report rules
    # predate two required fields, and nothing else is reported
    critical = [f for f in processes.validate(result.model) if f["severity"] == "critical"]
    assert all(f["kind"] == "schema" and f["message"].startswith("report_rules/") for f in critical), critical
    assert not [f for f in views.validate(result.model) if f["severity"] == "critical"]
    current = tenant_definitions.model_from_bundle("Knovas GmbH", _current(knovas)).model
    assert not [f for f in processes.validate(current) if f["severity"] == "critical"]
    # and a model of the exact shape an imported package has: it writes and reads back unchanged
    assert package.read_package(package.write_package(result.model)).model == result.model


def test_the_generated_processes_come_back_as_the_package_writes_them(knovas):
    generated = knovas.section("processes")
    doc = processes.from_generated(generated)
    assert doc["version"] == generated["version"] and "generated_at" not in doc
    lifecycle = doc["processes"][0]
    assert lifecycle["key"] == "PROC-feature-lifecycle" and lifecycle["pack"] == "engineering"
    assert ["idea", "brief"] in lifecycle["transitions"]
    assert ["brief", "idea", "rework"] in lifecycle["transitions"]
    assert all(set(step) <= {"id", "name", "owner_role", "description", "requires", "produces", "exit_criteria"}
               for step in lifecycle["steps"])
    again = processes.normalise(doc)
    assert again["processes"] == generated["processes"]
    assert again["rules"] == generated["rules"]
    strip = lambda rows: [{k: v for k, v in r.items() if k != "computed_by"} for r in rows]  # noqa: E731
    assert strip(again["report_rules"]) == strip(generated["report_rules"])


def test_the_generated_spines_and_pages_come_back_unchanged(knovas):
    spines = views.spines_from_generated(knovas.section("spines"))
    assert "generated_at" not in spines and "verified" not in spines["spines"][0]["levels"][0]
    assert views.normalise_spines(spines) == {**knovas.section("spines"), "generated_at": ""}
    pages = views.pages_from_generated(knovas.section("pages"))
    assert "generated_at" not in pages
    assert views.normalise_pages(pages) == {**knovas.section("pages"), "generated_at": ""}


def test_the_tenant_types_join_the_bundle_types(knovas):
    model, counts = tenant_ontology.model_from_tenant("Knovas GmbH", LISTING, _schema, bundle=knovas)
    keys = [t["key"] for t in model["ontology"]["types"]]
    assert len(keys) == 63 and keys.count("Product") == 1 and keys[-1] == "Workshop"
    product = next(t for t in model["ontology"]["types"] if t["key"] == "Product")
    assert product["description"]["en"] != "As the tenant has it."  # the bundle's definition wins
    workshop = model["ontology"]["types"][-1]
    assert workshop["level"] == "company" and workshop["level_label"] == "knovas"
    assert [a["name"] for a in workshop["attributes"]] == ["tenant_only"]
    assert counts["types"] == 63 and counts["own"] == 1 and counts["attributes"] == 1 and counts["unreadable"] == 0
    assert counts["definitions"] == {"bundle": knovas.label, "relations": 58, "processes": 7, "spines": 4,
                                     "pages": 5, "profiles": 4}
    assert package.read_package(package.write_package(model)).model == model


def test_without_a_bundle_nothing_changes():
    model, counts = tenant_ontology.model_from_tenant("Knovas", LISTING, _schema)
    assert [t["key"] for t in model["ontology"]["types"]] == ["Product", "Workshop"]
    assert counts == {"types": 2, "attributes": 2, "unreadable": 0}
    assert processes.processes_of(model) == [] and views.spines_of(model) == []


# ------------------------------------------------------------------ into a workspace that exists (BB-12)

def test_adopting_adds_what_the_workspace_lacks_and_keeps_its_own(knovas):
    from builder import editing

    model, _ = tenant_ontology.model_from_tenant("Knovas GmbH", LISTING, _schema)  # types only, as the wizard made it
    editing.apply(model, {"op": "process.create", "key": "PROC-hypothesis-testing", "name": "Hypothesis Testing Process",
                          "instance_types": ["Workshop"]})
    before = copy.deepcopy(model)
    source = tenant_definitions.model_from_bundle("Knovas GmbH", knovas).model
    changes = editing.apply(model, {"op": "definitions.adopt", "package": source})

    keys = [p["key"] for p in processes.processes_of(model)]
    assert keys[0] == "PROC-hypothesis-testing" and "PROC-feature-lifecycle" in keys and len(keys) == 8
    assert len(views.spines_of(model)) == 4 and len(views.pages_of(model)) == 5 and len(views.profiles_of(model)) == 4
    assert len(model["ontology"]["relations"]) == 58
    assert len(model["processes"]["rules"]) == 5 and len(model["processes"]["report_rules"]) == 9
    types = {t["key"]: t for t in model["ontology"]["types"]}
    assert len(types) == 63 and types["Workshop"]["description"] == {"en": "Made by hand, no header."}
    product = types["Product"]
    assert product["description"]["en"] == "As the tenant has it."     # the workspace's own text stays
    assert product["id_prefixes"] == ["^PROD-[A-Z]+$"]                  # a field it had no value for is filled
    assert [a["name"] for a in product["attributes"]] == ["tenant_only", "audience"]
    assert {c["path"].split("/")[0] for c in changes} == {
        "ontology.types", "ontology.relations", "processes", "processes.rules", "processes.report_rules",
        "spines", "pages", "profiles"}
    # the adopted processes are sound; the test's own process is empty (no steps yet) and the fixture's report rules
    # predate two required fields, neither of which the adoption touched
    assert not [f for f in processes.validate(model) if f["severity"] == "critical"
                and f["process"] != "PROC-hypothesis-testing" and not f["message"].startswith("report_rules/")]
    # a second adoption changes nothing, and the first is one batch to take back
    assert editing.apply(copy.deepcopy(model), {"op": "definitions.adopt", "package": source}) == []
    editing.undo(model, changes)
    assert model == before


def test_adopting_needs_a_package():
    from builder import editing

    model, _ = tenant_ontology.model_from_tenant("Knovas", LISTING, _schema)
    with pytest.raises(editing.EditError):
        editing.apply(model, {"op": "definitions.adopt"})


class TestAdoptingIntoAWorkspace:
    def _client(self, bundle, tenant="af000000"):
        from builder_harness import MemoryWorkspaceStore, StandInUser, make_app

        user = StandInUser({"admin"})
        store = MemoryWorkspaceStore({str(user.id): user.display_name})
        app = make_app(user, store, tenant=tenant, tenant_types=(LISTING, _schema), tenant_bundle=bundle)
        return app.test_client(), store, user

    def _blank_workspace(self, client, store):
        response = client.post("/builder/api/workspaces", data={"name": "Knovas GmbH", "start": "blank", "tenant": "link"})
        assert response.status_code == 201, response.get_json()
        [workspace] = store.list()
        return workspace

    def test_the_workspace_page_offers_it_and_the_review_counts(self, knovas):
        client, store, _ = self._client(lambda: knovas)
        workspace = self._blank_workspace(client, store)
        assert "Definitionen des Mandanten übernehmen" in client.get(f"/builder/w/{workspace.id}").get_data(as_text=True)
        review = client.get(f"/builder/w/{workspace.id}/definitionen").get_data(as_text=True)
        assert "7 Prozesse" in review and "4 Hierarchien" in review and "62 Typen" in review
        assert store.get(workspace.id).summary["processes"] == 0  # the review changed nothing

    def test_adopting_is_one_edit_that_undo_takes_back(self, knovas):
        client, store, _ = self._client(lambda: knovas)
        workspace = self._blank_workspace(client, store)
        response = client.post(f"/builder/api/w/{workspace.id}/definitions/adopt")
        assert response.status_code == 200, response.get_json()
        summary = store.get(workspace.id).summary
        assert (summary["types"], summary["relations"], summary["processes"], summary["spines"], summary["profiles"]) == (62, 58, 7, 4, 4)
        assert client.get(f"/builder/w/{workspace.id}/definitionen").status_code == 200
        assert client.post(f"/builder/api/w/{workspace.id}/definitions/adopt").status_code == 200  # nothing more to add
        assert client.post(f"/builder/api/w/{workspace.id}/undo").status_code == 200
        assert store.get(workspace.id).summary["processes"] == 0

    def test_without_a_bundle_source_nothing_is_offered(self):
        client, store, _ = self._client(None)
        workspace = self._blank_workspace(client, store)
        assert "Definitionen des Mandanten übernehmen" not in client.get(f"/builder/w/{workspace.id}").get_data(as_text=True)
        assert "liest kein Definitionsbündel" in client.get(f"/builder/w/{workspace.id}/definitionen").get_data(as_text=True)
        assert client.post(f"/builder/api/w/{workspace.id}/definitions/adopt").status_code == 409

    def test_a_bundle_that_cannot_be_read_is_said_so(self):
        def refused():
            raise bundles.BundleError("views.config_source is not set")

        client, store, _ = self._client(refused)
        workspace = self._blank_workspace(client, store)
        response = client.post(f"/builder/api/w/{workspace.id}/definitions/adopt")
        assert response.status_code == 502 and response.get_json()["details"] == ["BundleError"]

    def test_a_workspace_of_another_tenant_is_refused(self, knovas):
        client, store, user = self._client(lambda: knovas)
        model, _ = tenant_ontology.model_from_tenant("Elsewhere", [], _schema)
        workspace_id = store.create(name="Elsewhere", model=model, actor_id=user.id, source={"kind": "blank"}, tenant="ff000000")
        assert client.post(f"/builder/api/w/{workspace_id}/definitions/adopt").status_code == 409
        assert "anderen Mandanten" in client.get(f"/builder/w/{workspace_id}/definitionen").get_data(as_text=True)
