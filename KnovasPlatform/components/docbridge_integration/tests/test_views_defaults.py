"""Ansichten, Step 6: the views the Knovas tenant opens most render against the fixture graph.

The shared views themselves come from the tenant's profiles since KC-2 (tests/test_views_profiles.py).
"""
from __future__ import annotations

import datetime as dt

import pytest

from graph_views import process_views as pv, service as views, spine_views as sv
from graph_views.descriptions import hop_fits
from graph_views.settings import ViewSettings
from views_fixture import FakeViewsClient, KNOVAS_SOURCE, client_zero_graph

TODAY = dt.date(2026, 10, 27)


class _NoWait:
    def acquire(self):
        return None


@pytest.fixture
def graph():
    g = client_zero_graph()
    comp = g.by_key["L2-KNOWLEDGE-GRAPH"]
    dec = g.node("Decision", "DEC-0099 — keep the graph store", key="DEC-0099", status="decided")
    g.edge(dec, "affects", comp)
    risk = g.node("Risk", "RISK-20 — hub nodes", key="RISK-20", status="owned")
    g.edge(risk, "affects", comp)
    seg = g.node("Segment", "ICP-03 — law firms", key="ICP-03")
    persona = g.node("Persona", "P-10 — senior lawyer", key="P-10")
    g.edge(persona, "member_of", seg)
    hyp = g.node("Hypothesis", "P-10.H01 — loses an hour a day", key="P-10.H01", status="asserted")
    g.edge(hyp, "targets", persona)
    product = g.node("Product", "Knovas Platform", key="PROD-PLATFORM")
    tier = g.node("PlatformTier", "Cortex", key="TIER-CORTEX")
    g.edge(tier, "part_of", product)
    return g


def _ctx(graph):
    client = FakeViewsClient(graph)
    svc = views.ViewService(lambda: client, ViewSettings(config_source=KNOVAS_SOURCE), bucket=_NoWait(), markers=lambda: set())
    ctx = svc.context()
    return ctx, svc.definitions(ctx)


class TestEachDefaultRenders:
    def test_development_cycle(self, graph):
        ctx, d = _ctx(graph)
        t = pv.trace(ctx, d, "PROC-feature-lifecycle", graph.by_key["FEAT-2026-09-08-adaptive-rate-limits"], today=TODAY)
        assert t["current"]["step"] == "implementing" and t["lanes"]

    def test_development_board(self, graph):
        ctx, d = _ctx(graph)
        assert pv.board(ctx, d, "PROC-feature-lifecycle", today=TODAY)["total"] == 1

    def test_management_tree(self, graph):
        ctx, d = _ctx(graph)
        t = sv.tree(ctx, d, "SPINE-product")
        assert [n["key"] for n in t["nodes"]] == ["PROD-PLATFORM"] and t["nodes"][0]["children"] == 1

    def test_decisions_and_risks_for_a_plan(self, graph):
        ctx, d = _ctx(graph)
        t = pv.context_only(ctx, d, "PROC-feature-lifecycle", graph.by_key["FEAT-2026-09-08-adaptive-rate-limits"])
        # Decision, Risk (affects, mitigated_by), RunEvent, and three "via component (all)" paths (DEC-0023)
        assert t["lanes"] == [] and t["skipped"] == [] and len(t["context"]) == 7

    def test_decisions_and_risks_for_a_component(self, graph):
        ctx, d = _ctx(graph)
        t = pv.context_only(ctx, d, "PROC-feature-lifecycle", graph.by_key["L2-KNOWLEDGE-GRAPH"])
        found = {a["type"]: [i["name"] for i in a["items"]] for a in t["context"] if a["path"] == "←affects– " + a["type"]}
        assert found == {"Decision": ["DEC-0099 — keep the graph store"], "Risk": ["RISK-20 — hub nodes"]}
        # mitigated_by never ends at a Component, and the "via component" paths start with the plan's
        # affects (relations.yaml): left out, and said so.
        assert [s["path"] for s in t["skipped"]][0] == "←mitigated_by– Risk"
        assert all(s["path"].startswith(("←mitigated_by", "→affects→ Component")) for s in t["skipped"])

    def test_market_tree(self, graph):
        ctx, d = _ctx(graph)
        seg = graph.by_key["ICP-03"]
        assert [n["key"] for n in sv.tree(ctx, d, "SPINE-market")["nodes"]] == ["ICP-03"]
        personas = sv.tree(ctx, d, "SPINE-market", parent=seg, level="L1")
        assert [n["key"] for n in personas["nodes"]] == ["P-10"] and personas["nodes"][0]["children"] == 1


def test_hop_fits_reads_relations_yaml():
    assert hop_fits("Component", {"relation": "affects", "direction": "in", "to_type": "Decision"})
    assert not hop_fits("Component", {"relation": "mitigated_by", "direction": "in", "to_type": "Risk"})
    assert hop_fits("FeaturePlan", {"relation": "affects", "direction": "out", "to_type": "Component"})
    assert not hop_fits("Component", {"relation": "no_such_relation", "direction": "in", "to_type": "X"})
