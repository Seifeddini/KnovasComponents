"""Ansichten, Step 2: process and spine definitions from the tenant, validated.

The fixture writes definition nodes the way KnowledgeBase's loader does; the
loader here must put them back together, validate them against the schemas,
name what the tenant does not hold, and fall back to the tenant's definitions
bundle with a readable error when the tenant's definitions are broken.
"""
from __future__ import annotations

import copy
import uuid

import pytest
from flask import Flask

from graph_views import definitions as defs, service as views
from graph_views.settings import ViewSettings
from views_fixture import FakeViewsClient, FixtureGraph, KNOVAS_SOURCE, client_zero_graph, load_definitions_into
from web_interface.views_routes import create_views_blueprint


class _NoWait:
    def acquire(self):
        return None


def _ctx(graph):
    client = FakeViewsClient(graph)
    svc = views.ViewService(lambda: client, ViewSettings(config_source=KNOVAS_SOURCE), bucket=_NoWait(), markers=lambda: set())
    return svc, svc.context(), client


def _graph_with(processes=None, spines=None, **kw):
    g = FixtureGraph()
    for t in ("Process", "ProcessStep", "Spine", "SpineLevel"):
        g.type(t, meta_capable=True)
    load_definitions_into(g, processes, spines, **kw)
    return g


class TestTheBundle:
    def test_the_bundle_validates_with_seven_processes_and_four_spines(self):
        proc, spine = defs.from_bundle()
        assert defs.validate(proc, "process-definition.schema.json") == []
        assert defs.validate(spine, "spine-definition.schema.json") == []
        assert len(proc["processes"]) == 7 and len(spine["spines"]) == 4

    def test_report_rules_may_carry_their_checks_as_configuration(self):
        # KnowledgeBase KB-12: a bundle built since then carries checks and not_evaluable on each R-rule
        proc, _ = defs.from_bundle()
        doc = copy.deepcopy(proc)
        doc["report_rules"][0]["checks"] = [{"kind": "fact_present", "fact": "owner"}]
        doc["report_rules"][0]["not_evaluable"] = {"unless_types": ["TestRun"], "message": "No test runs yet."}
        assert defs.validate(doc, "process-definition.schema.json") == []
        doc["report_rules"][0]["not_evaluable"]["extra"] = 1
        assert len(defs.validate(doc, "process-definition.schema.json")) == 1

    def test_an_invalid_definition_reads_as_a_sentence_with_its_place(self):
        proc, _ = defs.from_bundle()
        broken = copy.deepcopy(proc)
        lifecycle = next(p for p in broken["processes"] if p["key"] == "PROC-feature-lifecycle")
        brief = next(s for s in lifecycle["steps"] if s["id"] == "brief")
        brief["exit_criteria"].append("rule one")
        brief["produces"][0]["path"][0]["direction"] = "sideways"
        errors = defs.validate(broken, "process-definition.schema.json")
        assert len(errors) == 2
        assert all("PROC-feature-lifecycle / STEP-feature-lifecycle-brief:" in e for e in errors)
        assert any("'rule one' does not match" in e and "exit_criteria" in e for e in errors)
        assert any("'sideways' is not one of ['out', 'in']" in e for e in errors)


class TestFromTenant:
    def test_all_definitions_load_from_the_tenant_and_validate(self):
        svc, ctx, _ = _ctx(_graph_with())
        d = defs.load(ctx)
        assert d.source == "tenant"
        assert d.errors == []
        assert len(d.processes) == 7 and len(d.spines) == 4
        lifecycle = d.process("PROC-feature-lifecycle")
        assert [s["id"] for s in lifecycle["steps"]][:3] == ["idea", "brief", "contracts-and-invariants"]
        assert lifecycle["final"] == ["released", "killed"]
        assert d.spine("SPINE-market")["branches"][0]["key"] == "sales"

    def test_the_tenant_matches_the_copy_when_the_loader_wrote_it(self):
        _, ctx, _ = _ctx(_graph_with())
        assert defs.load(ctx).differences == []

    def test_a_tenant_loaded_with_the_current_loader_has_no_gaps(self):
        _, ctx, _ = _ctx(_graph_with())
        d = defs.load(ctx)
        assert d.source == "tenant" and d.gaps == [], d.gaps
        assert d.process("PROC-feature-lifecycle")["change_classes"]["on_substantive"] == {"from": "approved", "to": "brief"}
        proc, _ = defs.from_bundle()
        want = {s["key"]: s.get("description") for p in proc["processes"] for s in p["steps"]}
        assert {s["key"]: s.get("description") for p in d.processes for s in p["steps"]} == want

    def test_the_step_description_and_change_classes_come_from_the_tenant(self):
        proc, _ = defs.from_bundle()
        changed = copy.deepcopy(proc["processes"])
        release = next(p for p in changed if p["key"] == "PROC-release")
        release["change_classes"]["minor"] = ["typo"]
        release["steps"][0]["description"] = "Auf dem Mandanten geschrieben."
        _, ctx, _ = _ctx(_graph_with(processes=changed))
        d = defs.load(ctx)
        assert d.process("PROC-release")["change_classes"]["minor"] == ["typo"]
        assert d.process("PROC-release")["steps"][0]["description"] == "Auf dem Mandanten geschrieben."

    def test_what_an_older_tenant_does_not_hold_is_named_as_a_gap(self):
        _, ctx, _ = _ctx(_graph_with(change_facts=False))
        d = defs.load(ctx)
        # since Phase P8 the loader writes status_map, transitions, governing_fact and the used P-rules;
        # a tenant loaded before the change-class and step-description facts takes those two from the bundle
        assert not any(g.startswith(("P-Regeln", "governing_fact", "status_map", "rework")) for g in d.gaps), d.gaps
        line = next(g for g in d.gaps if g.startswith("change_classes"))
        assert "PROC-feature-lifecycle" in line and "PROC-decision" in line
        assert any(g.startswith("Schrittbeschreibungen") for g in d.gaps), d.gaps
        assert d.process("PROC-feature-lifecycle")["change_classes"]["on_substantive"] == {"from": "approved", "to": "brief"}

    def test_a_broken_definition_on_the_tenant_falls_back_with_a_readable_error(self):
        proc, _ = defs.from_bundle()
        broken = copy.deepcopy(proc["processes"])
        step = next(s for p in broken if p["key"] == "PROC-decision" for s in p["steps"] if s["id"] == "decided")
        step["exit_criteria"] = ["R9", "not a rule"]
        _, ctx, _ = _ctx(_graph_with(processes=broken))
        d = defs.load(ctx)
        assert d.source == "bundle"
        assert any(e.startswith("Mandant: PROC-decision / STEP-decision-decided:") and "'not a rule'" in e for e in d.errors)
        assert len(d.processes) == 7            # the bundle still draws every view

    def test_unparseable_json_on_a_step_is_named(self):
        g = _graph_with()
        step = g.by_key["STEP-feature-lifecycle-brief"]
        for f in g.facts[step]:
            if f["label"] == "produces":
                f["value"] = "[{broken"
        _, ctx, _ = _ctx(g)
        d = defs.load(ctx)
        assert d.source == "bundle"
        assert any("STEP-feature-lifecycle-brief.produces: kein gültiges JSON" in e for e in d.errors)

    def test_a_tenant_change_shows_as_a_difference(self):
        proc, _ = defs.from_bundle()
        changed = copy.deepcopy(proc["processes"])
        next(p for p in changed if p["key"] == "PROC-release")["owner_role"] = "ROLE-SOMEONE-ELSE"
        _, ctx, _ = _ctx(_graph_with(processes=changed))
        d = defs.load(ctx)
        assert d.source == "tenant"
        assert "PROC-release: owner_role weicht ab" in d.differences

    def test_no_definition_nodes_means_the_bundle(self):
        g = FixtureGraph()
        g.node("Component", "Lonely")
        _, ctx, _ = _ctx(g)
        d = defs.load(ctx)
        assert d.source == "bundle" and len(d.processes) == 7

    def test_definitions_are_cached_per_subject(self):
        svc, ctx, client = _ctx(_graph_with())
        svc.definitions(ctx)
        reads = len(client.calls)
        svc.definitions(svc.context())
        assert len(client.calls) == reads


class _Gate:
    def __init__(self, roles):
        self.user = type("U", (), {"id": uuid.uuid4(), "roles": frozenset(roles)})()

    def current_user(self):
        return self.user


def _app(graph, roles):
    from pathlib import Path

    client = FakeViewsClient(graph)
    svc = views.ViewService(lambda: client, ViewSettings(config_source=KNOVAS_SOURCE), bucket=_NoWait(), markers=lambda: set())
    app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / "src" / "web_interface" / "templates"))
    app.register_blueprint(create_views_blueprint(_Gate(roles), svc, graph_mode=lambda: True))
    return app.test_client()


class TestRoute:
    def test_everyone_sees_the_definitions(self):
        body = _app(client_zero_graph(), ()).get("/api/views/definitions").get_json()
        assert len(body["processes"]) == 7 and len(body["spines"]) == 4
        assert "errors" not in body and "gaps" not in body

    def test_admins_see_errors_gaps_and_differences(self):
        body = _app(client_zero_graph(), ("admin",)).get("/api/views/definitions").get_json()
        assert body["errors"] == [] and body["gaps"] == [] and "differences" in body
        assert body["pinned"]["label"] == "KNOVAS" and body["pinned"]["source"] == KNOVAS_SOURCE
        assert body["pinned"]["content_hash"].startswith("sha256:") and len(body["pinned"]["source_commit"]) == 40


def test_page_offers_the_definitions_kind():
    from pathlib import Path

    page = (Path(__file__).resolve().parents[1] / "src" / "web_interface" / "templates" / "views.html").read_text(encoding="utf-8")
    assert 'data-kind="definitions"' in page and "js/views_definitions.js" in page
