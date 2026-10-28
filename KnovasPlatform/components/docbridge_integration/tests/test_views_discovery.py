"""Ansichten, Step 6b: the sparse-graph regression, start tiles, search, suggestions,
lists, and one selection across tabs remembered per user.
"""
from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path

import pytest
from flask import Flask

from graph_views import discovery, service as views
from graph_views.settings import ViewSettings
from views_fixture import FakeViewsClient, client_zero_graph
from web_interface.views_routes import create_views_blueprint

TODAY = dt.date(2026, 10, 28)


class _NoWait:
    def acquire(self):
        return None


def _svc(graph, **settings):
    client = FakeViewsClient(graph)
    return views.ViewService(lambda: client, ViewSettings(**settings), bucket=_NoWait(), markers=lambda: set()), client


@pytest.fixture
def graph():
    """The graph-native-brain plan as on the tenant: many neighbours of several
    types over several relations, in both directions."""
    g = client_zero_graph()
    proc = g.by_key["PROC-feature-lifecycle"]
    plan = g.node("FeaturePlan", "Feature: Graph-native company brain", key="FEAT-2026-10-05-graph-native-brain",
                  status="approved", owner="Platform", source="docs/ModernDocs/plans/2026-10-05-graph-native-brain.md")
    g.edge(plan, "instance_of", proc)
    g.edge(plan, "in_state", g.by_key["STEP-feature-lifecycle-approved"])
    for i in range(51):
        g.edge(g.node("Decision", f"DEC-{i:04d} — decision {i}", key=f"DEC-{i:04d}",
                      status="decided" if i % 3 else "proposed"), "affects", plan)          # in
    for i in range(24):
        g.edge(g.node("Risk", f"RISK-{i:02d} (graph-native-brain): risk {i}", key=f"FEAT-2026-10-05-graph-native-brain/RISK-{i:02d}",
                      status="mitigating"), "affects", plan)                                 # in
    for i in range(11):
        g.edge(plan, "affects", g.node("Component", f"L2-GN-{i:02d} — part {i}", key=f"L2-GN-{i:02d}"))   # out
    for i in range(2):
        g.edge(g.node("Commit", f"c0ffee{i} merge", key=f"COMMIT-c0ffee{i}"), "implements", plan)        # in
    cap = g.node("Capability", "A brain that improves and extends itself", key="CAP-11", status="research")
    g.edge(plan, "serves", cap)
    goal = g.node("Goal", "The Knovas brain runs on the graph store", key="GOAL-2026-Q4-brain-on-graph-store")
    g.edge(plan, "motivated_by", goal)
    g.node("Goal", "Last quarter's goal", key="GOAL-2026-Q3-old")
    return g


class TestTheSparseGraphRegression:
    """FEAT-2026-10-05-graph-native-brain once showed only RISK-22: the Graph tab
    kept the last centred node while the trace had another selection. The ego
    graph itself must hold every neighbour, grouped per (type, relation, direction)."""

    def test_one_chip_per_type_and_relation_above_the_threshold(self, graph):
        svc, _ = _svc(graph)
        body = views.ego_graph(svc.context(), graph.by_key["FEAT-2026-10-05-graph-native-brain"])
        chips = {(c["type"], c["relation"], c["direction"]): c["count"] for c in body["chips"]}
        assert chips == {("Decision", "affects", "in"): 51, ("Risk", "affects", "in"): 24,
                         ("Component", "affects", "out"): 11}
        drawn = {(n["type"], n["relation"], n["direction"]) for n in body["nodes"]}
        assert drawn == {("Commit", "implements", "in"), ("Capability", "serves", "out"), ("Goal", "motivated_by", "out")}

    def test_every_neighbour_is_reachable(self, graph):
        svc, _ = _svc(graph, page_size=10)
        ctx = svc.context()
        plan = graph.by_key["FEAT-2026-10-05-graph-native-brain"]
        body = views.ego_graph(ctx, plan)
        reached = {n["id"] for n in body["nodes"]}
        for c in body["chips"]:
            page, pages = 0, 1
            while page < pages:
                got = views.group_members(ctx, plan, c["relation"], c["direction"], c["type"], page)
                reached |= {m["id"] for m in got["members"]}
                pages, page = got["pages"], page + 1
        expected = {m for _, _, m in ctx.index.neighbours(plan)} - {
            graph.by_key["PROC-feature-lifecycle"], graph.by_key["STEP-feature-lifecycle-approved"]}  # meta, hidden
        assert reached == expected and len(reached) == 51 + 24 + 11 + 2 + 2

    def test_a_risk_seen_alone_has_the_plan_as_its_only_neighbour(self, graph):
        """What the user saw: the Graph tab centred on RISK-22, not on the plan."""
        svc, _ = _svc(graph)
        risk = graph.by_key["FEAT-2026-10-05-graph-native-brain/RISK-22"]
        body = views.ego_graph(svc.context(), risk)
        assert [n["name"] for n in body["nodes"]] == ["Feature: Graph-native company brain"]


class TestStart:
    def test_tiles_follow_the_spines_and_hide_meta_types(self, graph):
        svc, _ = _svc(graph)
        ctx = svc.context()
        body = discovery.start(ctx, svc.definitions(ctx))
        names = [g["spine"] for g in body["groups"]]
        assert names[0] == "SPINE-product" and names[-1] is None
        tiles = {t["name"]: t["count"] for g in body["groups"] for t in g["tiles"]}
        assert tiles["Component"] == 1 + 11 and tiles["Decision"] == 51
        assert "GlossaryTerm" not in tiles and "Process" not in tiles and "ProcessStep" not in tiles
        assert tiles["Event"] == 1                     # mixed: the business event stays, the run event does not

    def test_meta_on_shows_meta_types(self, graph):
        svc, _ = _svc(graph)
        ctx = svc.context()
        tiles = {t["name"] for g in discovery.start(ctx, svc.definitions(ctx), show_meta=True)["groups"] for t in g["tiles"]}
        assert {"GlossaryTerm", "Process", "ProcessStep"} <= tiles


class TestSearch:
    def test_key_prefixes(self, graph):
        svc, _ = _svc(graph)
        ctx = svc.context()
        for q, expect in (("FEAT-2026-10-05", "FeaturePlan"), ("L2-GN-03", "Component"), ("DEC-0007", "Decision"),
                          ("CAP-11", "Capability"), ("GOAL-2026-Q4", "Goal")):
            hit = discovery.search(ctx, svc.schemas, q)["results"][0]
            assert hit["type"] == expect, q
            assert hit["key"] and hit["key"].lower().startswith(q.lower()), q

    def test_names_and_descriptions(self, graph):
        svc, _ = _svc(graph)
        ctx = svc.context()
        assert discovery.search(ctx, svc.schemas, "graph-native company")["results"][0]["key"] == "FEAT-2026-10-05-graph-native-brain"
        assert discovery.search(ctx, svc.schemas, "brain is built on")["results"][0]["type"] == "Component"   # description

    def test_meta_stays_out(self, graph):
        svc, _ = _svc(graph)
        ctx = svc.context()
        assert not discovery.search(ctx, svc.schemas, "Term 1")["results"]
        assert discovery.search(ctx, svc.schemas, "Term 1", show_meta=True)["results"]

    def test_keys_are_read_once_per_type(self, graph):
        svc, client = _svc(graph)
        ctx = svc.context()
        discovery.search(ctx, svc.schemas, "CAP-11")
        before = len([c for c in client.calls if c.endswith("/facts")])
        discovery.search(svc.context(), svc.schemas, "CAP-1")
        assert len([c for c in client.calls if c.endswith("/facts")]) == before


class TestSuggestions:
    def test_the_four_groups(self, graph):
        svc, _ = _svc(graph)
        ctx = svc.context()
        user = type("U", (), {"id": uuid.uuid4(), "display_name": "Platform Team", "email": "platform@knovas.ch"})()
        recent = [graph.by_key["CAP-11"], "gone"]
        body = discovery.suggestions(ctx, svc.definitions(ctx), svc.schemas, user=user, recent=recent, today=TODAY)
        groups = {g["id"]: g for g in body["groups"]}
        assert [i["key"] for i in groups["mine"]["items"]] == ["FEAT-2026-10-05-graph-native-brain"]
        assert [i["key"] for i in groups["recent"]["items"]] == ["CAP-11"]
        assert [i["key"] for i in groups["goals"]["items"]] == ["GOAL-2026-Q4-brain-on-graph-store"]
        assert groups["hubs"]["items"][0]["name"] == "Feature: Graph-native company brain"

    def test_owner_matching(self):
        u = type("U", (), {"display_name": "Siran Seifeddini", "email": "siran@seifeddini.com"})()
        assert discovery._owner_matches("Siran", u) and discovery._owner_matches("siran seifeddini", u)
        assert not discovery._owner_matches("unknown", u) and not discovery._owner_matches("Sira", u)


class TestList:
    def test_rows_columns_sort_filter_and_paging(self, graph):
        svc, _ = _svc(graph, page_size=10)
        ctx = svc.context()
        d = svc.definitions(ctx)
        body = discovery.list_view(ctx, d, svc.schemas, "FeaturePlan", sort="key")
        assert body["total"] == 2 and body["complete_facts"]
        row = next(r for r in body["rows"] if r["key"] == "FEAT-2026-10-05-graph-native-brain")
        assert (row["status"], row["owner"], row["process"], row["step"]) == ("approved", "Platform", "PROC-feature-lifecycle", "approved")
        assert discovery.list_view(ctx, d, svc.schemas, "FeaturePlan", owner="Platform")["total"] == 1
        decisions = discovery.list_view(ctx, d, svc.schemas, "Decision", page=2, full_max=20)
        assert decisions["pages"] == 6 and len(decisions["rows"]) == 10 and not decisions["complete_facts"]

    def test_tree_path_for_baum(self, graph):
        product = graph.node("Product", "Knovas Platform", key="PROD-PLATFORM")
        tier = graph.node("PlatformTier", "Cortex", key="TIER-CORTEX")
        graph.edge(tier, "part_of", product)
        cap = graph.by_key["CAP-11"]
        graph.edge(tier, "offers", cap)
        svc, _ = _svc(graph)
        ctx = svc.context()
        path = discovery.tree_path(ctx, svc.definitions(ctx), cap)
        assert path == {"spine": "SPINE-product", "branch": "", "expand": [product, tier], "node": cap}


# -- routes and per-user memory --------------------------------------------------------

def _app(graph, gate=None, prefs=None):
    svc, _ = _svc(graph)
    app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / "src" / "web_interface" / "templates"))
    app.register_blueprint(create_views_blueprint(gate, svc, graph_mode=lambda: True, view_prefs=prefs))
    return app.test_client()


class TestRoutes:
    def test_start_search_list_locate(self, graph):
        app = _app(graph)
        assert app.get("/api/views/start").get_json()["groups"]
        assert app.get("/api/views/search?q=FEAT-2026-10-05").get_json()["results"][0]["type"] == "FeaturePlan"
        assert app.get("/api/views/list?type=Decision").get_json()["total"] == 51
        assert app.get("/api/views/list?type=Nope").status_code == 404
        loc = app.get(f"/api/views/locate/{graph.by_key['FEAT-2026-10-05-graph-native-brain']}").get_json()
        assert loc["process"] == "PROC-feature-lifecycle" and loc["node"]["key"] == "FEAT-2026-10-05-graph-native-brain"

    def test_selection_without_accounts_is_not_stored(self, graph):
        app = _app(graph)
        assert app.put("/api/views/selection", json={"node": str(uuid.uuid4())}).get_json()["stored"] is False
        assert app.get("/api/views/selection").get_json()["selection"] is None


from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable  # noqa: E402


@pytest.mark.skipif(not platform_db_reachable(), reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}")
class TestRememberedSelection:
    def test_last_selection_and_recent_per_user(self, graph, platform_db, identity_repo):
        from identity import view_prefs

        anna = identity_repo.create(email="anna@kanzlei.ch", display_name="Anna", password="korrektes-pferd-batterie")
        ben = identity_repo.create(email="ben@kanzlei.ch", display_name="Ben", password="korrektes-pferd-batterie")
        gate = type("G", (), {"user": anna, "current_user": lambda self: self.user, "connection": lambda self: platform_db})()
        app = _app(graph, gate=gate, prefs=view_prefs)
        a, b = str(uuid.uuid4()), str(uuid.uuid4())
        assert app.put("/api/views/selection", json={"node": a, "kind": "trace"}).get_json()["stored"]
        app.put("/api/views/selection", json={"node": b, "kind": "graph"})
        app.put("/api/views/selection", json={"node": a, "kind": "list"})
        got = app.get("/api/views/selection").get_json()
        assert got["selection"] == a and got["recent"] == [a, b] and got["kind"] == "list"
        gate.user = ben
        assert app.get("/api/views/selection").get_json()["selection"] is None
        assert app.put("/api/views/selection", json={"node": "not-an-id"}).status_code == 400
