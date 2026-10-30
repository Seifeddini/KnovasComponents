"""Ansichten, KC-3: documentation pages for any type with a page definition.

The Knovas pages render as before from the fixture (System → areas → L2 → L3, with the same diagrams);
a law firm's Matter page renders from its bundle; the tree, the diagrams and the links come from the
page definitions (``children``, ``root`` and ``link`` when a definition has them), and doc_pages.py
names no type and no key prefix.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from graph_views import bundle, doc_pages as dp, service as views
from graph_views.keys import literal_prefixes
from graph_views.settings import ViewSettings
from views_fixture import KNOVAS_BUNDLE, KNOVAS_SOURCE, LAWFIRM_BUNDLE, FakeViewsClient, FixtureGraph

import test_views_docs as tvd

SRC = Path(__file__).resolve().parents[1] / "src"


class _NoWait:
    def acquire(self):
        return None


def _knovas_graph():
    return getattr(tvd.graph, "__wrapped__", tvd.graph)()


def _ctx(graph, source=KNOVAS_SOURCE, **settings):
    svc = views.ViewService(lambda: FakeViewsClient(graph), ViewSettings(**{"config_source": source, **settings}),
                            bucket=_NoWait(), markers=lambda: set())
    return svc.context(), svc


# -- the Knovas pages, as before -----------------------------------------------------------------------


class TestKnovasAsBefore:
    def test_the_component_page_renders_its_definition_in_order(self):
        g = _knovas_graph()
        ctx, svc = _ctx(g)
        kg = g.by_key["L2-KNOWLEDGE-GRAPH"]
        page = dp.render(ctx, svc.schemas, kg)
        definition = next(p for p in bundle.current().pages["pages"] if p["key"] == page["page"]["key"])
        assert page["page"]["key"] == "PAGE-component" and page["node"]["type"] == "Component"
        assert [s["id"] for s in page["sections"]] == [s["id"] for s in definition["sections"]]
        sections = {s["id"]: s for s in page["sections"]}
        assert [i["name"] for i in sections["sub_parts"]["items"]] == ["L3-SCOPE-STAMP — Scope arrays"]
        assert {b["key"] for b in sections["calls"]["diagram"]["boxes"]} == {"L2-KNOWLEDGE-GRAPH", "L2-QUERY-2STAGE", "L2-PG-FACADE"}
        assert [(i["name"], i["status"]) for i in sections["invariants"]["items"]] == [
            ("GI-GRAPH-01 — tenant wall", "passing"), ("GI-GRAPH-02 — no widening", "none")]
        explanation = sections["explanation"]["items"][0]
        assert explanation["path"].endswith("L2-knowledge-graph/README.md") and explanation["url"] is None   # no repo given
        assert page["diagram"] is None                                  # an L2 component draws its calls, not a level
        assert page["path"] == [g.by_key["SYS-KNOVAS-SEMANTIX"], g.by_key["L1-RETRIEVE"], kg]

    def test_the_tree_is_system_areas_l2_l3(self):
        g = _knovas_graph()
        ctx, svc = _ctx(g)
        assert dp.root_types() == ["System"] and dp.roots(ctx) == [g.by_key["SYS-KNOVAS-SEMANTIX"]]
        areas = dp.children(ctx, svc.schemas, g.by_key["SYS-KNOVAS-SEMANTIX"], "SYS-KNOVAS-SEMANTIX", 60)
        assert [a["key"] for a in areas] == ["L1-DATA", "L1-RETRIEVE"]
        comps = dp.children(ctx, svc.schemas, g.by_key["L1-RETRIEVE"], "L1-RETRIEVE", 60)
        assert [c["key"] for c in comps] == ["L2-KNOWLEDGE-GRAPH", "L2-QUERY-2STAGE"]
        assert [c["page"] for c in comps] == ["PAGE-component", "PAGE-component"] and comps[0]["has_children"]
        subs = dp.children(ctx, svc.schemas, g.by_key["L2-KNOWLEDGE-GRAPH"], "L2-KNOWLEDGE-GRAPH", 60)
        assert [(s["key"], s["page"], s["has_children"]) for s in subs] == [("L3-SCOPE-STAMP", "PAGE-sub-part", False)]
        assert dp.path_to(ctx, svc.schemas, g.by_key["L3-SCOPE-STAMP"], 60) == [
            g.by_key[k] for k in ("SYS-KNOVAS-SEMANTIX", "L1-RETRIEVE", "L2-KNOWLEDGE-GRAPH", "L3-SCOPE-STAMP")]

    def test_the_level_diagrams_are_as_before(self):
        g = _knovas_graph()
        ctx, svc = _ctx(g)
        system = dp.render(ctx, svc.schemas, g.by_key["SYS-KNOVAS-SEMANTIX"])["diagram"]
        assert system["aggregated"] and system["title"] == "Software-Bereiche"
        assert [(e["relation"], e["count"], e["label"]) for e in system["edges"]] == [("calls", 2, "calls (2)")]
        area = dp.render(ctx, svc.schemas, g.by_key["L1-RETRIEVE"])["diagram"]
        assert not area["aggregated"] and [(e["relation"], e["label"]) for e in area["edges"]] == [("calls", "calls")]
        assert {b["key"] for b in area["boxes"]} == {"L2-KNOWLEDGE-GRAPH", "L2-QUERY-2STAGE"}

    def test_an_area_s_interactions_draw_its_components_as_boxes(self):
        g = _knovas_graph()
        ctx, svc = _ctx(g)
        sections = {s["id"]: s for s in dp.render(ctx, svc.schemas, g.by_key["L1-RETRIEVE"])["sections"]}
        d = sections["interactions"]["diagram"]
        assert d["details"] == [] and {b["key"] for b in d["boxes"] if not b["self"]} == {"L2-KNOWLEDGE-GRAPH", "L2-PG-FACADE"}


# -- a law firm's Matter page --------------------------------------------------------------------------


def _law_graph():
    g = FixtureGraph()
    matter = g.node("Matter", "Mandat Muster AG", key="M-2026-001", client="Muster AG")
    for i, (status, end) in enumerate((("signed", "2027-12-31"), ("in review", "2026-12-31"))):
        c = g.node("Contract", f"Vertrag {i + 1}", key=f"Akten/M-2026-001/vertrag-{i + 1}.pdf", status=status, term_end=end)
        g.edge(c, "belongs_to_matter", matter)
    return g, matter


@pytest.fixture
def law():
    token = bundle.use(bundle.load_file(LAWFIRM_BUNDLE))
    yield
    bundle.reset(token)


class TestLawFirm:
    def test_a_matter_page_renders_from_the_law_firm_bundle(self, law):
        g, matter = _law_graph()
        ctx, svc = _ctx(g, source=f"file:{LAWFIRM_BUNDLE}")
        page = dp.render(ctx, svc.schemas, matter)
        assert page["page"] == {"key": "matter", "name": "Mandat"} and page["node"]["type"] == "Matter"
        sections = {s["id"]: s for s in page["sections"]}
        assert sections["client"]["title"] == "Klient" and not sections["client"]["empty"]
        rows = {i["name"]: i["cells"] for i in sections["contracts"]["items"]}
        assert rows == {"Vertrag 1": {"status": "signed", "term_end": "2027-12-31"},
                        "Vertrag 2": {"status": "in review", "term_end": "2026-12-31"}}
        assert page["diagram"] is None and page["path"] == [matter]

    def test_the_law_firm_tree_lists_its_matters(self, law):
        g, matter = _law_graph()
        ctx, svc = _ctx(g, source=f"file:{LAWFIRM_BUNDLE}")
        assert dp.documented_types() == ["Matter"] and dp.root_types() == ["Matter"]
        assert dp.roots(ctx) == [matter] and dp.children(ctx, svc.schemas, matter, "M-2026-001", 60) == []

    def test_a_contract_is_not_a_page_and_says_so(self, law):
        g, matter = _law_graph()
        ctx, svc = _ctx(g, source=f"file:{LAWFIRM_BUNDLE}")
        contract = next(n for n in g.by_key.values() if n != matter)
        with pytest.raises(dp.NotDocumented, match="Contract"):
            dp.render(ctx, svc.schemas, contract)


# -- the proposed page-definition fields ------------------------------------------------------------------


def _with(tmp_path, change) -> bundle.Bundle:
    doc = json.loads(LAWFIRM_BUNDLE.read_text(encoding="utf-8"))
    change(doc["sections"]["pages"]["pages"])
    doc["header"]["content_hash"] = bundle.content_hash(doc["sections"])
    (tmp_path / "bundle.json").write_text(json.dumps(doc), encoding="utf-8")
    return bundle.load_file(tmp_path / "bundle.json")


class TestProposedFields:
    def test_children_root_and_link_come_from_the_definition_when_it_has_them(self, tmp_path):
        def change(pages):
            matter = pages[0]
            matter["root"] = True
            matter["children"] = {"section": "contracts"}
            pages.append({"key": "contract", "type": "Contract", "name": {"de": "Vertrag", "en": "Contract"},
                          "sections": [{"id": "matter", "title": {"de": "Mandat", "en": "Matter"}, "render": "list",
                                        "required": True, "link": {"fact": "file"},
                                        "paths": [{"path": [{"relation": "belongs_to_matter", "direction": "out",
                                                             "to_type": "Matter"}]}]}]})

        token = bundle.use(_with(tmp_path, change))
        try:
            g, matter = _law_graph()
            g.facts[matter].append({"label": "file", "value": "Akten/M-2026-001/akte.md"})
            ctx, svc = _ctx(g, source=f"file:{tmp_path / 'bundle.json'}")
            assert dp.root_types() == ["Matter"]
            kids = dp.children(ctx, svc.schemas, matter, "M-2026-001", 60)
            assert [k["name"] for k in kids] == ["Vertrag 1", "Vertrag 2"] and all(k["page"] == "contract" for k in kids)
            assert dp.path_to(ctx, svc.schemas, kids[0]["id"], 60) == [matter, kids[0]["id"]]
            page = dp.render(ctx, svc.schemas, kids[0]["id"], repo="kanzlei/akten")
            item = page["sections"][0]["items"][0]
            assert item["path"] == "Akten/M-2026-001/akte.md"
            assert item["url"] == "https://github.com/kanzlei/akten/blob/master/Akten/M-2026-001/akte.md"
        finally:
            bundle.reset(token)


# -- no type name, no key prefix in doc_pages.py ---------------------------------------------------------


def test_doc_pages_names_no_type_and_no_key_prefix():
    types, prefixes = set(), set()
    for path in (KNOVAS_BUNDLE, LAWFIRM_BUNDLE):
        sections = json.loads(path.read_text(encoding="utf-8"))["sections"]
        types |= {t["name"] for t in sections["types"]["node_types"]}
        for t in sections["types"]["node_types"]:
            for pattern in t.get("id_prefixes") or []:
                prefixes |= {p for p in literal_prefixes(pattern) if p}
        for kind in sections["types"].get("id_kinds") or []:
            prefixes |= {p for p in literal_prefixes(kind["pattern"]) if p}
        prefixes |= {(p.get("filter") or {}).get("key_prefix") for p in sections["pages"]["pages"]} - {None}
    tree = ast.parse((SRC / "graph_views" / "doc_pages.py").read_text(encoding="utf-8"))
    # Docstrings explain (and cite KnowledgeBase decisions such as DEC-0028); the code must name nothing.
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body
                  and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            text = node.value
            found += [f"type {t}" for t in types if re.search(rf"(?<![\w-]){re.escape(t)}(?![\w-])", text)]
            found += [f"prefix {p}" for p in prefixes if len(p) > 1 and re.search(rf"(?<![\w-]){re.escape(p)}", text)]
    assert sorted(set(found)) == []
