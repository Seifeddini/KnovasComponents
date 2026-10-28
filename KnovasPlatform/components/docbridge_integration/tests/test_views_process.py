"""Ansichten, Step 3: TRACE and BOARD of a process, from its definition.

Path resolution and traversal limits, missing-item flags computed the way
KnowledgeBase's process_completeness.py computes them, R-rules taken from its
report, fan-out grouping in lanes, and the board's columns, cards and filters.
"""
from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path

import pytest
from flask import Flask

from graph_views import completeness as comp, process_views as pv, service as views
from graph_views.settings import ViewSettings
from graph_views.topology import TopologyIndex
from graph_views.traverse import follow, path_text
from views_fixture import FakeViewsClient, client_zero_graph
from web_interface.views_routes import create_views_blueprint

TODAY = dt.date(2026, 10, 27)
PROC = "PROC-feature-lifecycle"


class _NoWait:
    def acquire(self):
        return None


@pytest.fixture
def graph():
    g = client_zero_graph()
    proc = g.by_key[PROC]
    # A brief with nothing behind it: the brief step's required artefacts are missing.
    bare = g.node("FeaturePlan", "Bare brief", key="FEAT-bare", status="draft",
                  current_step=("brief", "2026-10-20"))
    g.edge(bare, "instance_of", proc)
    g.edge(bare, "in_state", g.by_key["STEP-feature-lifecycle-brief"])
    # The fixture's main plan, implementing, gets a commit and a risk.
    plan = g.by_key["FEAT-2026-09-08-adaptive-rate-limits"]
    g.add_fact(plan, "current_step", "brief", valid_from="2026-09-08", valid_to="2026-10-01")
    g.add_fact(plan, "current_step", "approved", valid_from="2026-10-01", valid_to="2026-10-20")
    commit = g.node("Commit", "abc123 rate limits", key="COMMIT-abc123")
    g.edge(commit, "implements", plan)
    risk = g.node("Risk", "Rate limits too tight", key="RISK-09", status="owned")
    g.edge(risk, "affects", plan)
    closed = g.node("Risk", "Old risk", key="RISK-10", status="closed")
    g.edge(closed, "affects", plan)
    return g


def _svc(graph):
    client = FakeViewsClient(graph)
    svc = views.ViewService(lambda: client, ViewSettings(), bucket=_NoWait(), markers=lambda: set())
    return svc, client


def _ctx(graph):
    svc, client = _svc(graph)
    ctx = svc.context()
    return ctx, svc.definitions(ctx), client


# -- traversal --------------------------------------------------------------------


class TestFollow:
    def _index(self, g):
        return TopologyIndex([{"id": tid, "name": n} for n, tid in g.types.items()],
                             list(g.nodes.values()), g.edges)

    def test_two_hops_along_the_declared_directions(self, graph):
        index = self._index(graph)
        plan = graph.by_key["FEAT-2026-09-08-adaptive-rate-limits"]
        walk = follow(index, [plan], [{"relation": "affects", "direction": "out", "to_type": "Component"},
                                      {"relation": "verifies", "direction": "in", "to_type": "AlloyCheck"}])
        assert len(walk.nodes) == 22 and not walk.truncated

    def test_the_wrong_direction_finds_nothing(self, graph):
        index = self._index(graph)
        plan = graph.by_key["FEAT-2026-09-08-adaptive-rate-limits"]
        walk = follow(index, [plan], [{"relation": "affects", "direction": "in", "to_type": "Component"}])
        assert walk.nodes == []

    def test_the_type_filter_is_part_of_the_hop(self, graph):
        index = self._index(graph)
        comp_id = graph.by_key["L2-KNOWLEDGE-GRAPH"]
        walk = follow(index, [comp_id], [{"relation": "touches", "direction": "in", "to_type": "Decision"}])
        assert walk.nodes == []          # Events touch it, not Decisions

    def test_node_cap_per_hop(self, graph):
        index = self._index(graph)
        plan = graph.by_key["FEAT-2026-09-08-adaptive-rate-limits"]
        walk = follow(index, [plan], [{"relation": "affects", "direction": "out", "to_type": "Component"},
                                      {"relation": "verifies", "direction": "in", "to_type": "AlloyCheck"}],
                      max_nodes_per_hop=10)
        assert len(walk.nodes) == 10 and walk.truncated

    def test_hop_cap(self, graph):
        index = self._index(graph)
        hop = {"relation": "affects", "direction": "out", "to_type": "Component"}
        walk = follow(index, ["x"], [hop] * 5, max_hops=4)
        assert walk.nodes == [] and walk.truncated

    def test_path_text_is_the_reports_notation(self):
        assert path_text([{"relation": "affects", "direction": "out", "to_type": "Component"},
                          {"relation": "governs", "direction": "in", "to_type": "ComponentContract"}]) \
            == "→affects→ Component ←governs– ComponentContract"


# -- completeness -------------------------------------------------------------------


class TestMissing:
    def test_a_bare_brief_misses_what_the_report_lists(self, graph):
        ctx, d, _ = _ctx(graph)
        brief = next(s for s in d.process(PROC)["steps"] if s["id"] == "brief")
        missing = comp.missing_artefacts(ctx.index, graph.by_key["FEAT-bare"], brief)
        assert [(m["type"], m["path"]) for m in missing] == [
            ("Record", "←describes– Record"),
            ("Component", "→affects→ Component"),
            ("Capability", "→serves→ Capability"),
            ("Goal", "→motivated_by→ Goal"),
            ("Invariant", "→keeps→ Invariant"),            # what the plan declares itself (DEC-0023)
            ("AlloyCheck", "←verifies– AlloyCheck"),
        ]
        goal = next(m for m in missing if m["type"] == "Goal")
        assert goal["alternative"] == "Hypothesis →justified_by→ Hypothesis"
        assert next(m for m in missing if m["type"] == "Invariant")["note"].startswith("contracts-and-invariants step")

    def test_the_alternative_satisfies_the_artefact(self, graph):
        ctx, d, _ = _ctx(graph)
        bare = graph.by_key["FEAT-bare"]
        hyp = graph.node("Hypothesis", "Users want it", key="H-1")
        graph.edge(bare, "justified_by", hyp)
        ctx, d, _ = _ctx(graph)
        brief = next(s for s in d.process(PROC)["steps"] if s["id"] == "brief")
        assert "Goal" not in [m["type"] for m in comp.missing_artefacts(ctx.index, bare, brief)]

    def test_p_rules(self):
        assert comp.p_rule_met({"check": "fact_present", "fact": "owner"}, {"owner": "x"}, TODAY)
        assert not comp.p_rule_met({"check": "fact_present", "fact": "owner"}, {}, TODAY)
        assert not comp.p_rule_met({"check": "facts_present", "facts": ["a", "b"]}, {"a": "1", "b": "not set"}, TODAY)
        assert comp.p_rule_met({"check": "date_not_past", "fact": "d"}, {"d": "2026-10-27"}, TODAY)
        assert not comp.p_rule_met({"check": "date_not_past", "fact": "d"}, {"d": "2026-10-26"}, TODAY)

    def test_r_rules_come_from_the_kb_report_with_its_time(self, monkeypatch):
        monkeypatch.setattr(comp, "_report", lambda: {"generated_at": "2026-10-27 20:12", "instances": [
            {"instance": "FEAT-x", "step": "brief", "unmet": [{"id": "R1", "why": "rules report: R1 violation on FEAT-x"}]}]})
        step = {"id": "brief", "exit_criteria": ["R1", "R7", "P-owner"]}
        rules = [{"id": "P-owner", "name": "owner", "check": "fact_present", "fact": "owner", "description": "has owner"}]
        out = {c["id"]: c for c in comp.exit_criteria(step, rules, {"owner": "x"}, "FEAT-x", "brief", TODAY)}
        assert out["R1"] == {"id": "R1", "source": "kb-report", "as_of": "2026-10-27 20:12", "met": False,
                             "why": "rules report: R1 violation on FEAT-x"}
        assert out["R7"]["met"] is True and out["R7"]["source"] == "kb-report"
        assert out["P-owner"] == {"id": "P-owner", "met": True, "source": "platform", "why": None}

    def test_a_report_at_another_step_is_not_trusted_for_this_one(self, monkeypatch):
        monkeypatch.setattr(comp, "_report", lambda: {"generated_at": "t", "instances": [
            {"instance": "FEAT-x", "step": "approved", "unmet": []}]})
        out = comp.exit_criteria({"exit_criteria": ["R1"]}, [], {}, "FEAT-x", "brief", TODAY)
        assert out[0]["met"] is None and "approved" in out[0]["why"]


# -- trace ------------------------------------------------------------------------------


class TestTrace:
    def test_lanes_are_the_steps_in_order_with_states_and_dates(self, graph):
        ctx, d, _ = _ctx(graph)
        t = pv.trace(ctx, d, PROC, graph.by_key["FEAT-2026-09-08-adaptive-rate-limits"], today=TODAY)
        assert [l["id"] for l in t["lanes"]] == [s["id"] for s in d.process(PROC)["steps"]]
        states = {l["id"]: l["state"] for l in t["lanes"]}
        assert states["brief"] == "done" and states["implementing"] == "current" and states["verified"] == "future"
        entered = {l["id"]: l["entered"] for l in t["lanes"]}
        assert entered["brief"] == "2026-09-08" and entered["approved"] == "2026-10-01"
        assert t["current"] == {"step": "implementing", "since": "2026-10-20", "days": 7}

    def test_lane_artefacts_follow_the_paths_with_fan_out_grouping(self, graph):
        ctx, d, _ = _ctx(graph)
        t = pv.trace(ctx, d, PROC, graph.by_key["FEAT-2026-09-08-adaptive-rate-limits"], today=TODAY)
        brief = next(l for l in t["lanes"] if l["id"] == "brief")
        checks = next(a for a in brief["artefacts"] if a["type"] == "AlloyCheck")
        assert checks["count"] == 22 and checks["items"] == []
        assert checks["chip"]["breakdown"] == [{"status": "passed", "count": 20}, {"status": "failed", "count": 2}]
        comps = next(a for a in brief["artefacts"] if a["type"] == "Component")
        assert [i["name"] for i in comps["items"]] == ["L2-KNOWLEDGE-GRAPH — Knowledge Graph"]

    def test_current_step_missing_items_and_context(self, graph):
        ctx, d, _ = _ctx(graph)
        t = pv.trace(ctx, d, PROC, graph.by_key["FEAT-2026-09-08-adaptive-rate-limits"], today=TODAY)
        assert t["missing"] == []                       # a commit implements it
        risks = [a for a in t["context"] if a["type"] == "Risk" and a["path"] == "←affects– Risk"][0]
        assert {i["name"]: i["status"] for i in risks["items"]} == {
            "Rate limits too tight": "owned", "Old risk": "closed"}

    def test_a_bare_brief_is_flagged(self, graph):
        ctx, d, _ = _ctx(graph)
        t = pv.trace(ctx, d, PROC, graph.by_key["FEAT-bare"], today=TODAY)
        assert len(t["missing"]) == 6
        brief = next(l for l in t["lanes"] if l["id"] == "brief")
        assert all(a["missing"] for a in brief["artefacts"] if a["required"])
        owner = next(c for c in brief["exit_criteria"] if c["id"] == "P-owner")
        assert owner["met"] is False and owner["source"] == "platform"

    def test_meta_events_stay_out_of_the_context_lane(self, graph):
        plan = graph.by_key["FEAT-2026-09-08-adaptive-rate-limits"]
        for i in (2, 3):
            graph.edge(graph.node("RunEvent", f"Loader run {i}", key=f"EVT-run-{i}", meta="yes"), "touches", plan)
        ctx, d, _ = _ctx(graph)
        t = pv.trace(ctx, d, PROC, plan, today=TODAY)
        runs = next(a for a in t["context"] if a["type"] == "RunEvent")
        assert runs["count"] == 0 and runs["items"] == []
        # the business event is not a declared context path any more (RunEvent split, DEC-0019)
        assert not any(a["type"] == "Event" for a in t["context"])

    def test_an_instance_of_another_type_is_refused(self, graph):
        ctx, d, _ = _ctx(graph)
        with pytest.raises(pv.NotPlaceable):
            pv.trace(ctx, d, PROC, graph.by_key["L2-KNOWLEDGE-GRAPH"], today=TODAY)

    def test_the_fact_budget_caps_reads(self, graph):
        ctx, d, client = _ctx(graph)
        before = len([c for c in client.calls if c.endswith("/facts")])
        t = pv.trace(ctx, d, PROC, graph.by_key["FEAT-2026-09-08-adaptive-rate-limits"], today=TODAY, fact_budget=5)
        assert t["fact_reads"] <= 5
        brief = next(l for l in t["lanes"] if l["id"] == "brief")
        assert next(a for a in brief["artefacts"] if a["type"] == "AlloyCheck")["chip"]["breakdown"] is None
        assert len([c for c in client.calls if c.endswith("/facts")]) - before <= 5 + 2

    def test_the_current_step_and_the_context_are_read_first(self, graph):
        ctx, d, _ = _ctx(graph)
        t = pv.trace(ctx, d, PROC, graph.by_key["FEAT-2026-09-08-adaptive-rate-limits"], today=TODAY, fact_budget=3)
        risks = next(a for a in t["context"] if a["type"] == "Risk" and a["path"] == "←affects– Risk")
        assert all("status" in i for i in risks["items"])           # context: read
        commit = next(a for l in t["lanes"] if l["state"] == "current" for a in l["artefacts"] if a["type"] == "Commit")
        assert commit["items"] and commit["status_attribute"] is None  # nothing to read on a Commit
        brief = next(l for l in t["lanes"] if l["id"] == "brief")
        assert next(a for a in brief["artefacts"] if a["type"] == "AlloyCheck")["chip"]["breakdown"] is None

    def test_items_page(self, graph):
        ctx, d, _ = _ctx(graph)
        brief = next(s for s in d.process(PROC)["steps"] if s["id"] == "brief")
        position = [a["type"] for a in brief["requires"] + brief["produces"]].index("AlloyCheck")
        page = pv.trace_items(ctx, d, PROC, graph.by_key["FEAT-2026-09-08-adaptive-rate-limits"], "brief", position)
        assert page["count"] == 22 and {m["status"] for m in page["members"]} == {"passed", "failed"}


# -- board ----------------------------------------------------------------------------


class TestBoard:
    def test_columns_are_steps_and_cards_carry_the_numbers(self, graph):
        ctx, d, _ = _ctx(graph)
        b = pv.board(ctx, d, PROC, today=TODAY)
        assert [c["id"] for c in b["columns"]] == [s["id"] for s in d.process(PROC)["steps"]]
        cards = {c["key"]: c for col in b["columns"] for c in col["cards"]}
        bare = cards["FEAT-bare"]
        assert bare["step"] == "brief" and bare["missing"] == 6 and bare["days_in_step"] == 7
        plan = cards["FEAT-2026-09-08-adaptive-rate-limits"]
        assert plan["step"] == "implementing" and plan["open_risks"] == 1 and plan["owner"] == "ROLE-ENGINE-OWNER"

    def test_filters(self, graph):
        ctx, d, _ = _ctx(graph)
        assert pv.board(ctx, d, PROC, owner="ROLE-ENGINE-OWNER", today=TODAY)["total"] == 1
        assert pv.board(ctx, d, PROC, status="draft", today=TODAY)["total"] == 1
        b = pv.board(ctx, d, PROC, today=TODAY)
        assert b["filters"]["owners"] == ["ROLE-ENGINE-OWNER"]
        assert set(b["filters"]["statuses"]) == {"draft", "implementing"}

    def test_capability_filter_follows_the_declared_path(self, graph):
        plan = graph.by_key["FEAT-2026-09-08-adaptive-rate-limits"]
        cap = graph.node("Capability", "Ask the brain", key="CAP-01")
        graph.edge(plan, "serves", cap)
        ctx, d, _ = _ctx(graph)
        b = pv.board(ctx, d, PROC, capability=cap, today=TODAY)
        assert b["total"] == 1 and b["filters"]["capabilities"] == [{"id": cap, "name": "Ask the brain"}]

    def test_search_by_key_or_name(self, graph):
        ctx, d, _ = _ctx(graph)
        assert [r["key"] for r in pv.search_instances(ctx, d, PROC, "bare")] == ["FEAT-bare"]
        assert [r["key"] for r in pv.search_instances(ctx, d, PROC, "FEAT-2026")] == ["FEAT-2026-09-08-adaptive-rate-limits"]


# -- routes -----------------------------------------------------------------------------


def _app(graph):
    svc, _ = _svc(graph)
    gate = type("G", (), {"current_user": lambda self: type("U", (), {"id": uuid.uuid4(), "roles": frozenset()})()})()
    app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / "src" / "web_interface" / "templates"))
    app.register_blueprint(create_views_blueprint(gate, svc, graph_mode=lambda: True))
    return app.test_client()


class TestRoutes:
    def test_trace_and_board(self, graph):
        app = _app(graph)
        t = app.get(f"/api/views/trace?process={PROC}&instance={graph.by_key['FEAT-bare']}")
        assert t.status_code == 200 and len(t.get_json()["missing"]) == 6
        b = app.get(f"/api/views/board?process={PROC}")
        assert b.status_code == 200 and b.get_json()["total"] == 2

    def test_unknown_process_is_404_with_a_reason(self, graph):
        res = _app(graph).get("/api/views/board?process=PROC-nope")
        assert res.status_code == 404 and "nicht definiert" in res.get_json()["error"]

    def test_instances(self, graph):
        body = _app(graph).get(f"/api/views/instances?process={PROC}&q=bare").get_json()
        assert [r["key"] for r in body["results"]] == ["FEAT-bare"]
