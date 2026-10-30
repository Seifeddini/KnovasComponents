"""Ansichten, Step 8: software documentation pages from KnowledgeBase's page definitions.

Pages rendered section by section from page-definitions.json (DEC-0028); empty required sections
with the edit map's fix hint; stale sections from the docs completeness report (DEC-0029); the fact
budget and sections loaded on demand; the level diagrams and their deterministic layout; the tree;
the Markdown export and the stable /views/doc/<key> address.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask import Flask

from graph_views import doc_pages as dp, service as views
from graph_views.settings import ViewSettings
from views_fixture import FakeViewsClient, FixtureGraph, KNOVAS_SOURCE
from web_interface.views_routes import create_views_blueprint

REPO = "Seifeddini/KnowledgeBase"


class _NoWait:
    def acquire(self):
        return None


@pytest.fixture
def graph():
    """A small software: one System, two areas, three components, one sub-part, their links."""
    g = FixtureGraph()
    system = g.node("System", "Knovas Semantix", key="SYS-KNOVAS-SEMANTIX",
                    description="The Knovas software in two areas.")
    retrieve = g.node("BoundedContext", "Retrieve", key="L1-RETRIEVE", description="Retrieve: search and scoping.")
    data = g.node("BoundedContext", "Data", key="L1-DATA", description="Data: the stores.")
    for area in (retrieve, data):
        g.edge(area, "part_of", system)
    kg = g.node("Component", "L2-KNOWLEDGE-GRAPH — Knowledge Graph", key="L2-KNOWLEDGE-GRAPH",
                description="Sole owner of the knowledge graph. Second sentence.", alloy_status="Proposed")
    query = g.node("Component", "L2-QUERY-2STAGE — Two-Stage Query Engine", key="L2-QUERY-2STAGE",
                   description="Two-stage retrieval.", alloy_status="Covered")
    store = g.node("Component", "L2-PG-FACADE — PostgreSQL Facade", key="L2-PG-FACADE",
                   description="---", alloy_status="Covered")        # the loader's broken description
    sub = g.node("Component", "L3-SCOPE-STAMP — Scope arrays", key="L3-SCOPE-STAMP", description="Scope arrays.")
    g.edge(kg, "part_of", retrieve)
    g.edge(query, "part_of", retrieve)
    g.edge(store, "part_of", data)
    g.edge(sub, "part_of", kg)
    g.edge(query, "calls", kg)                                    # inside the area
    g.edge(kg, "calls", store)                                    # across areas
    g.edge(query, "calls_back", store)
    cap = g.node("Capability", "Search with cited passages", key="CAP-02")
    g.edge(retrieve, "realises", cap)
    contract = g.node("ComponentContract", "POST-P0-01 (L2-KNOWLEDGE-GRAPH): scope arrays", key="L2-KNOWLEDGE-GRAPH/POST-P0-01",
                      kind="postcondition")
    g.edge(contract, "governs", kg)
    kept = g.node("Invariant", "GI-GRAPH-01 — tenant wall", key="GI-GRAPH-01")
    bare = g.node("Invariant", "GI-GRAPH-02 — no widening", key="GI-GRAPH-02")
    g.edge(kg, "keeps", kept)
    g.edge(kg, "keeps", bare)
    check = g.node("AlloyCheck", "no_cross_tenant_edge (data_plane/kg.als)", key="ALLOY-data_plane/kg.als#no_cross_tenant_edge",
                   outcome="passed")
    g.edge(check, "verifies", kept)
    for i in range(12):
        c = g.node("Commit", f"{i:08x} change {i}", key=f"COMMIT-{i:012x}", committed_on=f"2026-10-{i + 10:02d}", author="Siran")
        g.edge(c, "changes", kg)
    rec = g.node("Record", "README.md — L2-KNOWLEDGE-GRAPH", key="REC-docs/ModernDocs/components/L1-retrieve/L2-knowledge-graph/README.md",
                 description="---", kind="explanation", updated_on="2026-10-18",
                 path="docs/ModernDocs/components/L1-retrieve/L2-knowledge-graph/README.md")
    g.edge(rec, "describes", kg)
    src = g.node("SourceFile", "knovas-software/app/src/services/knowledge_graph/service.py")
    g.edge(src, "part_of", kg)
    return g


def _ctx(graph, **settings):
    client = FakeViewsClient(graph)
    svc = views.ViewService(lambda: client, ViewSettings(**{"config_source": KNOVAS_SOURCE, **settings}), bucket=_NoWait(), markers=lambda: set())
    return svc.context(), svc, client


def _page(graph, key, **kw):
    ctx, svc, client = _ctx(graph, **kw.pop("settings", {}))
    return dp.render(ctx, svc.schemas, graph.by_key[key], repo=REPO, **kw), client


def _sections(page):
    return {s["id"]: s for s in page["sections"]}


# -- page definitions -------------------------------------------------------------------------


class TestDefinitions:
    def test_a_page_per_type_and_key_prefix(self):
        assert dp.page_for("System", "SYS-KNOVAS-SEMANTIX")["key"] == "PAGE-system"
        assert dp.page_for("BoundedContext", "L1-RETRIEVE")["key"] == "PAGE-software-area"
        assert dp.page_for("Component", "L2-KNOWLEDGE-GRAPH")["key"] == "PAGE-component"
        assert dp.page_for("Component", "L3-SCOPE-STAMP")["key"] == "PAGE-sub-part"
        assert dp.page_for("Interface", "IF-x")["key"] == "PAGE-interface"
        assert dp.page_for("Commit", "COMMIT-1") is None

    def test_the_pin_names_the_knowledgebase_commit(self):
        assert len(dp.kb_commit() or "") == 40


# -- rendering -----------------------------------------------------------------------------------


class TestRender:
    def test_sections_follow_the_definition_in_order_with_their_render_hint(self, graph):
        page, _ = _page(graph, "L2-KNOWLEDGE-GRAPH")
        definition = dp.page_for("Component", "L2-KNOWLEDGE-GRAPH")
        assert [s["id"] for s in page["sections"]] == [s["id"] for s in definition["sections"]]
        assert [s["render"] for s in page["sections"]] == [s["render"] for s in definition["sections"]]
        s = _sections(page)
        assert s["description"]["title"] == "Beschreibung" and s["description"]["text"].startswith("Sole owner")
        assert s["contracts"]["items"][0]["cells"] == {"kind": "postcondition"}
        assert s["contracts"]["column_labels"] and all(sec["as_of"] for sec in page["sections"])

    def test_capabilities_come_directly_and_through_the_area(self, graph):
        s = _sections(_page(graph, "L2-KNOWLEDGE-GRAPH")[0])
        assert [(i["name"], i["label"]) for i in s["capabilities"]["items"]] == [("Search with cited passages", "via its software area")]

    def test_a_required_empty_section_says_where_it_is_filled(self, graph):
        s = _sections(_page(graph, "L2-KNOWLEDGE-GRAPH")[0])
        assert s["responsibilities"]["empty"] and s["responsibilities"]["missing"].startswith("Eintragen in docs/ModernDocs/components/")
        assert s["interfaces"]["empty"] and "missing" not in s["interfaces"]          # optional: empty, not missing

    def test_a_locked_source_names_the_fix_at_source(self, graph):
        graph.nodes[graph.by_key["L2-KNOWLEDGE-GRAPH"]]["description"] = ""
        sf = [e for e in graph.edges if e["relation"] == "part_of" and graph.nodes[e["node_lo"]]["name"].endswith("service.py")]
        graph.edges.remove(sf[0])
        s = _sections(_page(graph, "L2-KNOWLEDGE-GRAPH")[0])
        assert s["source_files"]["missing"].startswith("An der Quelle: ")     # SourceFile part_of is not editable

    def test_the_loaders_broken_description_counts_as_empty(self, graph):
        s = _sections(_page(graph, "L2-PG-FACADE")[0])
        assert s["description"]["empty"] and s["description"]["broken"] and s["description"]["missing"]

    def test_invariants_show_their_verification(self, graph):
        s = _sections(_page(graph, "L2-KNOWLEDGE-GRAPH")[0])
        status = {i["name"]: i["status"] for i in s["invariants"]["items"]}
        assert status == {"GI-GRAPH-01 — tenant wall": "passing", "GI-GRAPH-02 — no widening": "none"}

    def test_commits_are_ordered_and_limited(self, graph):
        s = _sections(_page(graph, "L2-KNOWLEDGE-GRAPH")[0])
        commits = s["commits"]
        assert commits["count"] == 10 and commits["total"] == 12
        assert [i["cells"]["committed_on"] for i in commits["items"]][:2] == ["2026-10-21", "2026-10-20"]

    def test_explanation_documents_link_to_their_source_file(self, graph):
        item = _sections(_page(graph, "L2-KNOWLEDGE-GRAPH")[0])["explanation"]["items"][0]
        assert item["url"] == ("https://github.com/Seifeddini/KnowledgeBase/blob/master/"
                               "docs/ModernDocs/components/L1-retrieve/L2-knowledge-graph/README.md")

    def test_stale_sections_come_from_the_kb_report(self, graph, monkeypatch):
        monkeypatch.setattr(dp, "report", lambda: {"generated_at": "2026-10-28T13:25:11Z", "page": "PAGE-component",
                                                   "rows": {"L2-KNOWLEDGE-GRAPH": {"stale": ["explanation"]}}})
        s = _sections(_page(graph, "L2-KNOWLEDGE-GRAPH")[0])
        assert s["explanation"]["stale"] == {"source": "KB-Bericht docs-completeness", "as_of": "2026-10-28T13:25:11Z"}
        assert "stale" not in s["contracts"]

    def test_the_fact_budget_defers_sections_required_first(self, graph):
        page, client = _page(graph, "L2-KNOWLEDGE-GRAPH", settings={"doc_fact_budget": 3})
        s = _sections(page)
        assert not s["contracts"]["deferred"] and s["contracts"]["items"][0]["cells"]   # required, 1 read
        assert s["commits"]["deferred"] and s["commits"]["deferred_reads"] == 12         # optional, ordering needs all
        assert len([c for c in client.calls if c.endswith("/facts")]) <= 1 + 3 + 3

    def test_a_deferred_section_loads_on_its_own(self, graph):
        ctx, svc, _ = _ctx(graph, doc_fact_budget=3)
        one = dp.render(ctx, svc.schemas, graph.by_key["L2-KNOWLEDGE-GRAPH"], repo=REPO, only="commits",
                        budget=50)
        assert [x["id"] for x in one["sections"]] == ["commits"] and not one["sections"][0]["deferred"]

    def test_a_reload_reads_nothing_and_a_loaded_section_stays_loaded(self, graph):
        ctx, svc, client = _ctx(graph, doc_fact_budget=3)
        node = graph.by_key["L2-KNOWLEDGE-GRAPH"]
        reads = lambda: len([c for c in client.calls if c.endswith("/facts")])  # noqa: E731
        first = dp.render(ctx, svc.schemas, node, repo=REPO)
        n = reads()
        again = dp.render(ctx, svc.schemas, node, repo=REPO)
        assert reads() == n and [x["deferred"] for x in again["sections"]] == [x["deferred"] for x in first["sections"]]
        dp.render(ctx, svc.schemas, node, repo=REPO, only="commits", budget=50)       # "Laden"
        after = _sections(dp.render(ctx, svc.schemas, node, repo=REPO))
        assert not after["commits"]["deferred"] and after["commits"]["items"][0]["cells"]

    def test_required_sections_explanation_then_cheapest_first(self, graph):
        """The explanation (judged for staleness, carries the links) first, then as many complete sections as fit."""
        check = graph.node("AlloyCheck", "no_widening (data_plane/kg.als)", key="ALLOY-data_plane/kg.als#no_widening",
                           outcome="passed")
        graph.edge(check, "verifies", graph.by_key["GI-GRAPH-02"])
        page, _ = _page(graph, "L2-KNOWLEDGE-GRAPH", settings={"doc_fact_budget": 2})
        s = _sections(page)
        assert not s["explanation"]["deferred"] and s["explanation"]["items"][0]["url"]
        assert s["invariants"]["deferred"]

    def test_a_node_without_a_page_is_refused(self, graph):
        ctx, svc, _ = _ctx(graph)
        with pytest.raises(dp.NotDocumented):
            dp.render(ctx, svc.schemas, graph.by_key["CAP-02"])


# -- diagrams ------------------------------------------------------------------------------------


class TestDiagrams:
    def test_an_area_draws_its_components_and_their_calls(self, graph):
        page, _ = _page(graph, "L1-RETRIEVE")
        d = page["diagram"]
        assert {b["key"] for b in d["boxes"]} == {"L2-KNOWLEDGE-GRAPH", "L2-QUERY-2STAGE"}
        assert [(e["label"]) for e in d["edges"]] == ["calls"]
        by = {b["key"]: b for b in d["boxes"]}
        assert by["L2-QUERY-2STAGE"]["y"] < by["L2-KNOWLEDGE-GRAPH"]["y"]               # the caller above
        assert by["L2-KNOWLEDGE-GRAPH"]["description"] == "Sole owner of the knowledge graph."

    def test_the_system_counts_calls_between_areas(self, graph):
        d = _page(graph, "SYS-KNOVAS-SEMANTIX")[0]["diagram"]
        assert d["aggregated"] and {b["key"] for b in d["boxes"]} == {"L1-RETRIEVE", "L1-DATA"}
        assert [(e["label"], e["count"]) for e in d["edges"]] == [("calls (2)", 2)]   # calls + calls_back

    def test_the_layout_is_deterministic_and_never_overlaps(self):
        nodes = [f"n{i}" for i in range(9)]
        edges = [(nodes[i], nodes[i + 1]) for i in range(8)] + [(nodes[8], nodes[0])]      # a chain with a cycle
        order = {n: n for n in nodes}
        a = dp.layout(nodes, edges, order)
        b = dp.layout(list(reversed(nodes)), list(reversed(edges)), order)
        assert a == b
        assert len(set(a.values())) == 9
        assert len({y for _, y in a.values()}) == 3                                          # a 3 × 3 grid, not 9 rows

    def test_a_component_draws_callers_above_and_callees_below(self, graph):
        s = _sections(_page(graph, "L2-KNOWLEDGE-GRAPH")[0])
        d = s["calls"]["diagram"]
        by = {b["key"]: b for b in d["boxes"]}
        assert by["L2-QUERY-2STAGE"]["y"] < by["L2-KNOWLEDGE-GRAPH"]["y"] < by["L2-PG-FACADE"]["y"]
        assert by["L2-KNOWLEDGE-GRAPH"]["self"]


# -- tree and export -------------------------------------------------------------------------------


class TestTreeAndExport:
    def test_the_tree_follows_system_areas_l2_l3(self, graph):
        ctx, svc, _ = _ctx(graph)
        roots = dp.roots(ctx)
        assert [ctx.index.nodes[r]["name"] for r in roots] == ["Knovas Semantix"]
        areas = dp.children(ctx, svc.schemas, roots[0], "SYS-KNOVAS-SEMANTIX", 60)
        assert [a["key"] for a in areas] == ["L1-DATA", "L1-RETRIEVE"]
        comps = dp.children(ctx, svc.schemas, graph.by_key["L1-RETRIEVE"], "L1-RETRIEVE", 60)
        assert [c["key"] for c in comps] == ["L2-KNOWLEDGE-GRAPH", "L2-QUERY-2STAGE"]
        kg = next(c for c in comps if c["key"] == "L2-KNOWLEDGE-GRAPH")
        query = next(c for c in comps if c["key"] == "L2-QUERY-2STAGE")
        # by topology alone: a required path without a where filter that reaches nothing
        assert kg["has_children"] and "invariants" not in kg["incomplete"]
        assert {"invariants", "source_files", "explanation"} <= set(query["incomplete"])
        subs = dp.children(ctx, svc.schemas, graph.by_key["L2-KNOWLEDGE-GRAPH"], "L2-KNOWLEDGE-GRAPH", 60)
        assert [s["key"] for s in subs] == ["L3-SCOPE-STAMP"] and subs[0]["page"] == "PAGE-sub-part"
        assert dp.path_to(ctx, svc.schemas, graph.by_key["L3-SCOPE-STAMP"], 60) == [
            roots[0], graph.by_key["L1-RETRIEVE"], graph.by_key["L2-KNOWLEDGE-GRAPH"], graph.by_key["L3-SCOPE-STAMP"]]

    def test_a_key_locates_its_page(self, graph):
        ctx, svc, _ = _ctx(graph)
        assert dp.locate(ctx, svc.schemas, "L2-KNOWLEDGE-GRAPH", 60) == graph.by_key["L2-KNOWLEDGE-GRAPH"]
        assert dp.locate(ctx, svc.schemas, "L1-DATA", 60) == graph.by_key["L1-DATA"]
        assert dp.locate(ctx, svc.schemas, "L9-NOTHING", 60) is None

    def test_the_markdown_export_is_stamped_and_complete(self, graph):
        ctx, svc, _ = _ctx(graph)
        name, text = dp.export(ctx, svc.schemas, graph.by_key["L2-KNOWLEDGE-GRAPH"], repo=REPO)
        assert name == "L2-KNOWLEDGE-GRAPH.md"
        assert text.startswith("# L2-KNOWLEDGE-GRAPH — Knowledge Graph\n")
        assert f"KnowledgeBase {dp.kb_commit()[:12]}" in text and "Stand 20" in text
        assert "## Verantwortlichkeiten\n\n_fehlt — Eintragen in" in text
        assert "```mermaid" in text and "-->|calls|" in text
        assert "[README.md — L2-KNOWLEDGE-GRAPH](https://github.com/Seifeddini/KnowledgeBase/blob/master/" in text
        assert "- ✓ GI-GRAPH-01 — tenant wall" in text

    def test_a_subtree_export_holds_every_page_below(self, graph):
        ctx, svc, client = _ctx(graph)
        name, text = dp.export(ctx, svc.schemas, graph.by_key["L1-RETRIEVE"], scope="subtree", repo=REPO)
        assert name == "L1-RETRIEVE-teilbaum.md"
        for key in ("L1-RETRIEVE", "L2-KNOWLEDGE-GRAPH", "L2-QUERY-2STAGE", "L3-SCOPE-STAMP"):
            assert key in text
        assert text.count("\n---\n") == 3


# -- routes -----------------------------------------------------------------------------------------


def _app(graph):
    client = FakeViewsClient(graph)
    svc = views.ViewService(lambda: client, ViewSettings(config_source=KNOVAS_SOURCE), bucket=_NoWait(), markers=lambda: set())
    app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / "src" / "web_interface" / "templates"))
    app.add_url_rule("/ontology", "ontology_page", lambda: "")
    app.register_blueprint(create_views_blueprint(None, svc, graph_mode=lambda: True, doc_repo=REPO,
                                                  page_context=lambda: {"csrf_token": "t", "asset_version": "1", "brand": "Knovas"}))
    return app.test_client()


class TestRoutes:
    def test_a_stable_address_per_page(self, graph):
        client = _app(graph)
        html = client.get("/views/doc/L2-KNOWLEDGE-GRAPH").get_data(as_text=True)
        assert 'data-doc-key="L2-KNOWLEDGE-GRAPH"' in html and "js/views_docs.js" in html
        body = client.get("/api/views/doc/page?key=L2-KNOWLEDGE-GRAPH").get_json()
        assert body["node"]["key"] == "L2-KNOWLEDGE-GRAPH" and body["page"]["key"] == "PAGE-component"
        assert client.get("/api/views/doc/page?key=L9-NOTHING").status_code == 404

    def test_tree_section_and_export_routes(self, graph):
        client = _app(graph)
        roots = client.get("/api/views/doc/tree").get_json()["nodes"]
        assert roots[0]["key"] == "SYS-KNOVAS-SEMANTIX"
        areas = client.get(f"/api/views/doc/tree?parent={roots[0]['id']}").get_json()["nodes"]
        assert [a["key"] for a in areas] == ["L1-DATA", "L1-RETRIEVE"]
        kg = graph.by_key["L2-KNOWLEDGE-GRAPH"]
        one = client.get(f"/api/views/doc/section?node={kg}&section=commits").get_json()
        assert [s["id"] for s in one["sections"]] == ["commits"]
        res = client.get("/api/views/doc/export?key=L1-RETRIEVE&scope=subtree")
        assert res.status_code == 200 and res.mimetype == "text/markdown"
        assert res.headers["Content-Disposition"] == 'attachment; filename="L1-RETRIEVE-teilbaum.md"'
        assert json.loads(json.dumps(res.get_data(as_text=True)))

    def test_the_default_view_opens_the_documentation(self):
        from graph_views import default_views
        from identity.saved_views import clean_view

        view = next(v for v in default_views.KNOVAS if v["name"] == "Software-Dokumentation")
        assert clean_view(view)["render_kind"] == "doc" and view["source"] == {"spine": "SPINE-product"}
