"""Ansichten, Step 4: TREE and MATRIX of a spine, from its definition.

Link directions (out: child -> parent, in: parent -> child), per-type links,
the key-prefix filter on levels, counts, status colouring, and the matrix
cells along the spine.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from flask import Flask

from graph_views import service as views, spine_views as sv
from graph_views.settings import ViewSettings
from views_fixture import FakeViewsClient, client_zero_graph
from web_interface.views_routes import create_views_blueprint

SPINE = "SPINE-product"


class _NoWait:
    def acquire(self):
        return None


@pytest.fixture
def graph():
    g = client_zero_graph()
    product = g.node("Product", "Knovas Platform", key="PROD-PLATFORM")
    tier = g.node("PlatformTier", "Cortex", key="TIER-CORTEX")
    g.edge(tier, "part_of", product)                       # L2 -> L1: part_of, out
    cap = g.node("Capability", "Ask the brain", key="CAP-01", status="live")
    cap2 = g.node("Capability", "File a document", key="CAP-06", status="planned")
    g.edge(tier, "offers", cap)                            # L3: offers, in (parent -> child)
    g.edge(tier, "offers", cap2)
    area = g.node("BoundedContext", "L1-BRAIN — Brain", key="L1-BRAIN")
    g.edge(area, "realises", cap)                          # L4 -> L3: realises, out
    comp = g.by_key["L2-KNOWLEDGE-GRAPH"]
    g.edge(comp, "part_of", area)                          # L5 -> L4: part_of, out, key L2-
    sub = g.node("Component", "L3-KG-FACTS — Facts", key="L3-KG-FACTS")
    g.edge(sub, "part_of", comp)                           # L6 -> L5: part_of, out, key L3-
    stray = g.node("Component", "L3-STRAY — hangs on the area", key="L3-STRAY")
    g.edge(stray, "part_of", area)                         # an L3 under L4: not an L5 node
    g.edge(cap, "offers", tier)                            # a wrong-direction edge: never followed
    return g


def _ctx(graph):
    client = FakeViewsClient(graph)
    svc = views.ViewService(lambda: client, ViewSettings(), bucket=_NoWait(), markers=lambda: set())
    ctx = svc.context()
    return ctx, svc.definitions(ctx), svc, client


class TestTree:
    def test_roots_are_the_first_level(self, graph):
        ctx, d, _, _ = _ctx(graph)
        t = sv.tree(ctx, d, SPINE)
        assert [n["key"] for n in t["nodes"]] == ["PROD-PLATFORM"]
        assert t["level"]["id"] == "L1" and t["next_level"]["id"] == "L2"
        assert t["nodes"][0]["children"] == 1

    def test_out_link_finds_children_pointing_at_the_parent(self, graph):
        ctx, d, _, _ = _ctx(graph)
        t = sv.tree(ctx, d, SPINE, parent=graph.by_key["PROD-PLATFORM"], level="L1")
        assert [n["key"] for n in t["nodes"]] == ["TIER-CORTEX"]

    def test_in_link_finds_children_the_parent_points_at(self, graph):
        ctx, d, _, _ = _ctx(graph)
        t = sv.tree(ctx, d, SPINE, parent=graph.by_key["TIER-CORTEX"], level="L2")
        assert [n["key"] for n in t["nodes"]] == ["CAP-01", "CAP-06"]
        assert {n["key"]: n["status"] for n in t["nodes"]} == {"CAP-01": "live", "CAP-06": "planned"}
        assert t["states"] == [{"status": "live", "count": 1}, {"status": "planned", "count": 1}]

    def test_key_prefix_keeps_l3_components_off_the_l2_level(self, graph):
        ctx, d, _, _ = _ctx(graph)
        t = sv.tree(ctx, d, SPINE, parent=graph.by_key["L1-BRAIN"], level="L4")
        assert [n["key"] for n in t["nodes"]] == ["L2-KNOWLEDGE-GRAPH"]

    def test_the_sub_part_level(self, graph):
        ctx, d, _, _ = _ctx(graph)
        t = sv.tree(ctx, d, SPINE, parent=graph.by_key["L2-KNOWLEDGE-GRAPH"], level="L5")
        assert [n["key"] for n in t["nodes"]] == ["L3-KG-FACTS"]
        assert t["next_level"] is None and "children" not in t["nodes"][0]

    def test_a_count_with_unknown_keys_is_marked_as_an_upper_bound(self, graph):
        ctx, d, _, _ = _ctx(graph)
        t = sv.tree(ctx, d, SPINE, parent=graph.by_key["CAP-01"], level="L3")
        area = t["nodes"][0]
        assert area["key"] == "L1-BRAIN"
        # L2-KNOWLEDGE-GRAPH and L3-STRAY hang on the area; the keys of the
        # grandchildren are not read for a count, so it says "up to 2".
        if not area["children_exact"]:
            assert area["children"] == 2
        else:
            assert area["children"] == 1

    def test_per_type_links_and_the_sales_branch(self, graph):
        ctx, d, _, _ = _ctx(graph)
        acc = graph.node("Account", "Olymp AG", key="ACC-0001")
        opp = graph.node("Opportunity", "PoC Olymp", key="OPP-0001", stage="demo")
        graph.edge(opp, "for_account", acc)
        ctx, d, _, _ = _ctx(graph)
        t = sv.tree(ctx, d, "SPINE-market", branch="sales", parent=acc, level="B1")
        assert [(n["key"], n["status"]) for n in t["nodes"]] == [("OPP-0001", "demo")]

    def test_unknown_spine_level_or_branch(self, graph):
        ctx, d, _, _ = _ctx(graph)
        for kwargs in ({"spine_key": "SPINE-nope"}, {"spine_key": SPINE, "branch": "nope"},
                       {"spine_key": SPINE, "parent": "x", "level": "L9"}):
            with pytest.raises(sv.NotDrawable):
                sv.tree(ctx, d, **kwargs)

    def test_a_level_expansion_reads_only_its_nodes(self, graph):
        ctx, d, _, client = _ctx(graph)
        before = len(client.calls)
        sv.tree(ctx, d, SPINE, parent=graph.by_key["TIER-CORTEX"], level="L2")
        facts = [c for c in client.calls[before:] if c.endswith("/facts")]
        assert len(facts) <= 2 + 2        # the two capabilities (+ cached meta decisions)


class TestMatrix:
    def test_capability_by_component(self, graph):
        ctx, d, _, _ = _ctx(graph)
        m = sv.matrix(ctx, d, SPINE, "L3", "L5")
        assert [r["key"] for r in m["rows"]] == ["CAP-01", "CAP-06"]
        assert [c["key"] for c in m["cols"]] == ["L2-KNOWLEDGE-GRAPH"]
        assert [[x["linked"] for x in line] for line in m["cells"]] == [[True], [False]]
        assert m["unlinked_rows"] == [graph.by_key["CAP-06"]]

    def test_rows_and_columns_can_be_swapped(self, graph):
        ctx, d, _, _ = _ctx(graph)
        m = sv.matrix(ctx, d, SPINE, "L5", "L3")
        assert [r["key"] for r in m["rows"]] == ["L2-KNOWLEDGE-GRAPH"]
        assert [x["linked"] for x in m["cells"][0]] == [True, False]

    def test_a_linked_cell_carries_a_status(self, graph):
        ctx, d, _, _ = _ctx(graph)
        m = sv.matrix(ctx, d, SPINE, "L3", "L5")
        assert m["cells"][0][0]["status"] == "live"     # the component has none; the capability's
        assert m["cells"][1][0]["status"] is None

    def test_same_level_is_refused(self, graph):
        ctx, d, _, _ = _ctx(graph)
        with pytest.raises(sv.NotDrawable):
            sv.matrix(ctx, d, SPINE, "L3", "L3")


def _app(graph):
    client = FakeViewsClient(graph)
    svc = views.ViewService(lambda: client, ViewSettings(), bucket=_NoWait(), markers=lambda: set())
    gate = type("G", (), {"current_user": lambda self: type("U", (), {"id": uuid.uuid4(), "roles": frozenset()})()})()
    app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / "src" / "web_interface" / "templates"))
    app.register_blueprint(create_views_blueprint(gate, svc, graph_mode=lambda: True))
    return app.test_client()


class TestRoutes:
    def test_tree_and_matrix(self, graph):
        app = _app(graph)
        assert app.get(f"/api/views/tree?spine={SPINE}").get_json()["nodes"][0]["key"] == "PROD-PLATFORM"
        assert app.get(f"/api/views/matrix?spine={SPINE}&rows=L3&cols=L5").get_json()["cells"][0][0]["linked"]

    def test_errors(self, graph):
        app = _app(graph)
        assert app.get("/api/views/tree").status_code == 400
        assert app.get("/api/views/tree?spine=SPINE-nope").status_code == 404
        assert app.get(f"/api/views/matrix?spine={SPINE}&rows=L3").status_code == 400
