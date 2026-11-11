"""Relations and hierarchies from what the tenant's links show (builder.brain_shape) and their route."""
import pytest

from builder import brain_shape, editing, package, views
from builder_harness import MemoryWorkspaceStore, StandInUser, make_app

TYPES = [{"id": f"t-{n}", "name": n} for n in ("System", "Area", "Component", "Commit", "Setting")]


def _graph():
    """One system, two areas, three components, one of them with two parts; calls, commits and settings."""
    nodes, edges = [], []

    def node(nid, type_name):
        nodes.append({"id": nid, "name": nid, "node_type_id": f"t-{type_name}"})

    def edge(lo, hi, rel):
        edges.append({"id": f"{lo}-{rel}-{hi}", "node_lo": lo, "node_hi": hi, "relation": rel})

    node("sys", "System")
    for a in ("area-a", "area-b"):
        node(a, "Area")
        edge(a, "sys", "part_of")
    for c, a in (("c1", "area-a"), ("c2", "area-a"), ("c3", "area-b")):
        node(c, "Component")
        edge(c, a, "part_of")
    for p in ("p1", "p2"):
        node(p, "Component")
        edge(p, "c1", "part_of")
    edge("c1", "c2", "calls")
    edge("c2", "c3", "calls")
    for n in ("k1", "k2", "k3"):
        node(n, "Commit")
        edge(n, "c1", "changes")
        edge(n, "c2", "changes")
    node("s1", "Setting")
    edge("c1", "s1", "configured_by")
    edge("c2", "s1", "configured_by")
    return TYPES, nodes, edges


def _model(relations=()):
    model = package.read_package(package.blank_package("Muster")).model
    model["ontology"]["types"] = [{"key": t["name"], "level": "company", "attributes": [],
                                   "label": {"de": {"Area": "Bereich"}.get(t["name"], t["name"])}} for t in TYPES]
    model["ontology"]["relations"] = list(relations)
    return model


class TestDerive:
    def test_the_hierarchy_the_links_show(self):
        shape = brain_shape.derive(_model(), *_graph())
        [h] = shape.hierarchies
        assert h["key"] == "SPINE-system" and h["name"] == "Aufbau System"
        assert [lv["types"] for lv in h["levels"]] == [["System"], ["Area"], ["Component"], ["Component"]]
        assert [lv["nodes"] for lv in h["levels"]] == [1, 2, 5, 5]
        assert h["levels"][1]["link"] == {"relation": "part_of", "direction": "out"}

    def test_the_relations_with_what_they_link(self):
        shape = brain_shape.derive(_model(), *_graph())
        rel = {r["key"]: r for r in shape.relations}
        assert set(rel) == {"part_of", "calls", "changes", "configured_by"}
        assert rel["part_of"]["domain"] == ["Area", "Component"] and rel["part_of"]["range"] == ["Area", "Component", "System"]
        assert rel["changes"]["domain"] == ["Commit"] and rel["changes"]["links"] == 6 and rel["changes"]["new"]

    def test_an_existing_relation_is_only_extended(self):
        have = {"key": "calls", "level": "company", "domain": ["Component"], "range": ["Component"]}
        part = {"key": "part_of", "level": "company", "domain": ["Component"], "range": ["Component"]}
        shape = brain_shape.derive(_model([have, part]), *_graph())
        keys = {r["key"]: r for r in shape.relations}
        assert "calls" not in keys                        # nothing to add
        assert keys["part_of"]["new"] is False and keys["part_of"]["domain"] == ["Area"]
        assert keys["part_of"]["ops"][0]["op"] == "relation.update"

    def test_a_link_that_is_not_containment_makes_no_hierarchy(self):
        types, nodes, edges = _graph()
        edges = [e for e in edges if e["relation"] != "part_of"]
        assert brain_shape.derive(_model(), types, nodes, edges).hierarchies == []

    def test_types_the_workspace_does_not_have_are_left_out(self):
        model = _model()
        model["ontology"]["types"] = [t for t in model["ontology"]["types"] if t["key"] != "Commit"]
        shape = brain_shape.derive(model, *_graph())
        assert "changes" in shape.skipped and "changes" not in {r["key"] for r in shape.relations}

    def test_taken_over_it_is_a_clean_definition(self):
        model = _model()
        shape = brain_shape.derive(model, *_graph())
        for op in brain_shape.operations(shape, ["spine:System", "rel:part_of"]):
            editing.apply(model, op)
        assert not [f for f in views.validate(model) if f["item"] == "SPINE-system" and f["severity"] == "critical"]
        spine = next(s for s in views.spines_of(model) if s["key"] == "SPINE-system")
        assert [lv["name"] for lv in spine["levels"]] == ["System", "Bereich", "Component", "Component (Teil)"]


class TestTheRoute:
    @pytest.fixture
    def setup(self):
        user = StandInUser({"admin"}, "Alex Beispiel")
        store = MemoryWorkspaceStore({str(user.id): user.display_name})
        app = make_app(user, store, tenant="t-1", graph=_graph())
        wid = store.create(name="Muster", model=_model(), actor_id=user.id, source={"kind": "tenant"}, tenant="t-1")
        c = app.test_client()
        with c.session_transaction() as sess:
            sess["builder_workspace"] = wid
        return c, store, wid, app

    def test_proposals_then_one_batch_one_undo(self, setup):
        c, store, wid, _ = setup
        shown = c.get(f"/builder/api/w/{wid}/brain-shape").get_json()
        assert shown["success"] and [h["id"] for h in shown["hierarchies"]] == ["spine:System"]
        assert "ops" not in shown["hierarchies"][0]       # the browser sends ids, never operations
        body = c.post(f"/builder/api/w/{wid}/brain-shape", json={"chosen": ["spine:System", "rel:part_of"]}).get_json()
        assert body["success"] and body["history"]["undo"] == 1
        assert any(s["key"] == "SPINE-system" for s in body["views"]["spines"])
        undone = c.post(f"/builder/api/w/{wid}/undo?view=views").get_json()
        assert not any(s["key"] == "SPINE-system" for s in undone["views"]["spines"])

    def test_the_stage_offers_it(self, setup):
        c, _, _, _ = setup
        assert "Aus dem Brain ableiten" in c.get("/builder/ansichten").get_data(as_text=True)

    def test_nothing_chosen_and_another_tenant_are_refused(self, setup):
        c, store, wid, _ = setup
        assert c.post(f"/builder/api/w/{wid}/brain-shape", json={"chosen": []}).status_code == 400
        store.rows[wid]["tenant"] = "t-other"
        assert c.get(f"/builder/api/w/{wid}/brain-shape").status_code == 409

    def test_without_a_builder_role_nothing(self, setup):
        c, _, wid, app = setup
        app.extensions["builder_gate"].user = StandInUser({"member"})
        assert c.get(f"/builder/api/w/{wid}/brain-shape").status_code == 403
