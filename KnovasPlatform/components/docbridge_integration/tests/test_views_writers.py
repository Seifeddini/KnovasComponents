"""Step 7: edit-map routing and the writers that put one change into the file that owns it.

Each writer is judged by reading its result back the way KnowledgeBase's loader reads plans
(writers.declared, a port of providers/plan_declarations.py). The fixture texts reproduce the
forms found in the real plans: numbered headings, invariant tables under "Security & Golden
Invariants", inline and flow-mapping sidecar lists, several Alloy models on one row.
"""
from __future__ import annotations

import pytest
import yaml

from graph_views import edit_map, writers as w

KEY = "FEAT-2026-10-05-graph-native-brain"
PLAN = w.Plan(KEY, "docs/ModernDocs/plans/2026-10-05-graph-native-brain.md")

PLAN_MD = """---
doc_type: feature_plan
status: approved
---

# Feature Plan: Graph-native company brain

| Field | Value |
|-------|-------|
| **Author** | Siran |
| **Status** | approved |
| **Affected L2** | L2-KNOWLEDGE-GRAPH, L2-COMPANY-BRAIN |

## Problem

The brain stores its graph in PostgreSQL tables.

## 7. Component contract deltas

### L2-KNOWLEDGE-GRAPH

| ID | Condition | GI |
|----|-----------|-----|
| PRE-KG-01 | caller is authenticated | GI-GRAPH-01 |

- RESP-KG-02: owns the scope index

## 15 · Golden Invariants

| GI ID | Impact |
|-------|--------|
| GI-GRAPH-01 | preserved |
| **GI-GRAPH-02** | extended |

## Alloy

| Model path | Action | Checks |
|------------|--------|--------|
| `data_plane/kg_walk.als` | extend | `walk_bounded` |
| `data_plane/a.als`, `data_plane/b.als` | new | `x` |

## Risks

| ID | Risk | Mitigation |
|----|------|------------|
| RISK-01 | engine young | probes |
| RISK-02 | fork | plugins |

## References

- none
"""

SIDECAR = """feature:
  title: Graph-native company brain
  status: approved
  author: Siran
hierarchy:
  affected_L2:
    - L2-KNOWLEDGE-GRAPH
    - L2-COMPANY-BRAIN
golden_invariants:
  preserved: [GI-GRAPH-01, GI-TENANT-03]
  extended:
    - {id: GI-GRAPH-04, gate: P10, change: "budget"}
  proposed:
    - id: GI-GSTORE-13
      owner_L2: L2-K8S-GITOPS
      statement: "Signed images"
estimate: {}
"""

LINKS = """version: 1
nodes: []
edges:
  # graph-native brain
  - {from: FEAT-2026-10-05-graph-native-brain, relation: serves, to: CAP-11}
  - {from: FEAT-2026-10-05-graph-native-brain, relation: motivated_by, to: GOAL-2026-Q4-brain-on-graph-store}
  - {from: FEAT-2026-07-27-other, relation: serves, to: CAP-03}
"""

SAMPLE = """version: 1
nodes:
  - {type: PreMortem, key: PM-2026-10-01-brain, name: Pre-mortem}
edges:
  - {from: GOAL-2026-Q4-cortex-default, relation: has_key_result, to: KR-1}
  - {from: PM-2026-10-01-brain, relation: examines,       to: FEAT-2026-07-30-company-brain-foundation}
other: {a: 1}
"""


def texts(**over):
    t = {PLAN.path: PLAN_MD, PLAN.sidecar: SIDECAR, w.PLAN_LINKS: LINKS, w.SAMPLE: SAMPLE}
    t.update(over)
    return t


def W(edit_id):
    writer, why = w.writer_for(edit_id)
    assert writer is not None, why
    return writer


# -- routing ------------------------------------------------------------------------------------


class TestRouting:
    def test_every_writer_matches_the_pinned_edit_map(self):
        """Re-vendoring an edit map that moved a source turns this red, not a write to the old place."""
        for edit_id, writer in w.WRITERS.items():
            assert writer.routing_problem() is None, edit_id
        for edit_id in w.FIELD_ROUTES:
            assert w.field_routing_problem(edit_id) is None, edit_id

    def test_a_moved_source_is_refused(self, monkeypatch):
        moved = dict(edit_map.entry("FeaturePlan serves Capability"), source="client-zero/ontology/elsewhere.yaml")
        real = edit_map.entry
        monkeypatch.setattr(edit_map, "entry", lambda i: moved if i == "FeaturePlan serves Capability" else real(i))
        writer, why = w.writer_for("FeaturePlan serves Capability")
        assert writer is None and "elsewhere.yaml" in why

    def test_a_locked_item_says_where_to_fix_it(self):
        writer, why = w.writer_for("Commit implements FeaturePlan")
        assert writer is None and "from git" in why and "commit message" in why

    def test_what_the_platform_cannot_route_is_listed(self):
        ids = {r["id"] for r in w.unroutable_report()}
        assert "Decision affects FeaturePlan" in ids          # plan D- rows define decisions; no writer
        assert not ids & set(w.WRITERS)                       # every writer routes

    def test_relation_ids_follow_the_stored_edge(self):
        assert edit_map.relation_id("FeaturePlan", {"relation": "serves", "direction": "out", "to_type": "Capability"}) \
            == "FeaturePlan serves Capability"
        assert edit_map.relation_id("FeaturePlan", {"relation": "verifies", "direction": "in", "to_type": "AlloyCheck"}) \
            == "AlloyCheck verifies FeaturePlan"

    def test_sources_are_repository_paths(self):
        assert edit_map.sources("FeaturePlan serves Capability") == ["docs/ModernDocs/client-zero/ontology/plan-links.yaml"]


class TestSuggestions:
    def test_a_missing_item_has_ranked_candidates_with_reasons(self):
        items = [i for i in edit_map.suggestion_items() if i["candidates"]]
        item = items[0]
        found = edit_map.suggestions_for(item["instance"], item["path"])
        assert found is item
        c = found["candidates"][0]
        assert {"target", "score", "rule", "reason"} <= set(c)
        scores = [c["score"] for c in found["candidates"]]
        assert scores == sorted(scores, reverse=True)

    def test_an_item_without_a_rule_says_so(self):
        assert any(i.get("no_rule") and not i["candidates"] for i in edit_map.suggestion_items())


# -- YAML edge lists -------------------------------------------------------------------------------


class TestYamlEdges:
    def test_add_appends_after_the_last_edge_with_a_note(self):
        out = w.apply(W("FeaturePlan serves Capability"), texts(), PLAN, "add", "CAP-02", "change set 2026-10-28")
        new = out[w.PLAN_LINKS]
        assert "  # change set 2026-10-28\n  - {from: FEAT-2026-10-05-graph-native-brain, relation: serves, to: CAP-02}\n" in new
        assert {"from": KEY, "relation": "serves", "to": "CAP-02"} in yaml.safe_load(new)["edges"]
        assert new.startswith(LINKS.rstrip("\n").rsplit("\n", 0)[0][:40])       # the rest stays byte for byte

    def test_remove_drops_only_that_line(self):
        out = w.apply(W("FeaturePlan serves Capability"), texts(), PLAN, "remove", "CAP-11", "")
        assert "to: CAP-11" not in out[w.PLAN_LINKS] and "to: CAP-03" in out[w.PLAN_LINKS]

    def test_the_instance_can_be_the_to_end_and_spacing_is_kept(self):
        plan = w.Plan("FEAT-2026-07-30-company-brain-foundation", "docs/ModernDocs/plans/2026-07-30-company-brain-foundation.md")
        writer = W("PreMortem examines FeaturePlan")
        assert writer.present(texts(), plan) == ["PM-2026-10-01-brain"]
        out = w.apply(writer, texts(), plan, "remove", "PM-2026-10-01-brain", "")
        assert "examines" not in out[w.SAMPLE] and yaml.safe_load(out[w.SAMPLE])["other"] == {"a": 1}

    def test_an_edge_from_another_source_cannot_be_removed_here(self):
        with pytest.raises(w.WriteError, match="nicht eingetragen"):
            w.apply(W("FeaturePlan serves Capability"), texts(), PLAN, "remove", "CAP-06", "")

    def test_a_target_of_the_wrong_type_is_refused(self):
        with pytest.raises(w.WriteError, match="Capability"):
            w.apply(W("FeaturePlan serves Capability"), texts(), PLAN, "add", "GOAL-2026-Q4-x", "")

    def test_a_present_target_is_not_added_twice(self):
        with pytest.raises(w.WriteError, match="bereits"):
            w.apply(W("FeaturePlan serves Capability"), texts(), PLAN, "add", "CAP-11", "")


# -- the plan file ------------------------------------------------------------------------------------


class TestPlanFile:
    def test_affected_row_and_the_sidecar(self):
        out = w.apply(W("FeaturePlan affects Component"), texts(), PLAN, "add", "L2-SECURE-API", "x")
        assert "| **Affected L2** | L2-KNOWLEDGE-GRAPH, L2-COMPANY-BRAIN, L2-SECURE-API |" in out[PLAN.path]
        assert "    - L2-SECURE-API\n" in out[PLAN.sidecar]
        out = w.apply(W("FeaturePlan affects Component"), texts(), PLAN, "remove", "L2-COMPANY-BRAIN", "")
        assert "| **Affected L2** | L2-KNOWLEDGE-GRAPH |" in out[PLAN.path]
        assert yaml.safe_load(out[PLAN.sidecar])["hierarchy"]["affected_L2"] == ["L2-KNOWLEDGE-GRAPH"]

    def test_an_invariant_is_added_as_a_table_row_under_a_numbered_heading(self):
        out = w.apply(W("FeaturePlan keeps Invariant"), texts(), PLAN, "add", "GI-GRAPH-09", "x")
        assert "| **GI-GRAPH-02** | extended |\n| GI-GRAPH-09 | preserved |\n" in out[PLAN.path]
        assert "GI-GRAPH-09" in w.declared(out[PLAN.path], SIDECAR).invariants

    @pytest.mark.parametrize("gid", ["GI-GRAPH-01", "GI-TENANT-03", "GI-GRAPH-04", "GI-GSTORE-13", "GI-GRAPH-02"])
    def test_an_invariant_is_removed_from_the_table_and_every_sidecar_form(self, gid):
        out = w.apply(W("FeaturePlan keeps Invariant"), texts(), PLAN, "remove", gid, "")
        merged = {**texts(), **out}
        left = w.declared(merged[PLAN.path], merged[PLAN.sidecar]).invariants
        assert gid not in left
        assert set(left) == set(w.declared(PLAN_MD, SIDECAR).invariants) - {gid}
        yaml.safe_load(merged[PLAN.sidecar])                  # the sidecar stays valid YAML

    def test_a_plan_without_the_section_gets_the_template_one(self):
        bare = "# Feature Plan: X\n\n| Field | Value |\n|---|---|\n| **Author** | A |\n\n## Alloy\n\nWaiver\n"
        out = w.apply(W("FeaturePlan keeps Invariant"), texts(**{PLAN.path: bare, PLAN.sidecar: None}), PLAN, "add", "GI-X-01", "x")
        assert out[PLAN.path].index("## Golden Invariants") < out[PLAN.path].index("## Alloy")

    def test_a_contract_goes_under_its_component(self):
        writer = W("FeaturePlan keeps ComponentContract")
        out = w.apply(writer, texts(), PLAN, "add", "L2-KNOWLEDGE-GRAPH/POST-KG-03", "x")
        assert "L2-KNOWLEDGE-GRAPH/POST-KG-03" in w.declared(out[PLAN.path], None).contracts
        out = w.apply(writer, texts(), PLAN, "add", "L2-SECURE-API/PRE-API-01", "x")
        assert "### L2-SECURE-API" in out[PLAN.path]
        assert "L2-SECURE-API/PRE-API-01" in w.declared(out[PLAN.path], None).contracts

    @pytest.mark.parametrize("cid", ["L2-KNOWLEDGE-GRAPH/PRE-KG-01", "L2-KNOWLEDGE-GRAPH/RESP-KG-02"])
    def test_a_contract_row_or_bullet_is_removed(self, cid):
        out = w.apply(W("FeaturePlan keeps ComponentContract"), texts(), PLAN, "remove", cid, "")
        assert cid not in w.declared(out[PLAN.path], None).contracts

    def test_an_alloy_check_stands_for_its_model(self):
        writer = W("AlloyCheck verifies FeaturePlan")
        out = w.apply(writer, texts(), PLAN, "add", "ALLOY-data_plane/relevance_gate.als#gate_holds", "x")
        assert "| `data_plane/relevance_gate.als` | extend |" in out[PLAN.path]
        assert "data_plane/relevance_gate.als" in w.declared(out[PLAN.path], None).alloy_models

    def test_removing_one_model_keeps_the_others_on_its_row(self):
        out = w.apply(W("AlloyCheck verifies FeaturePlan"), texts(), PLAN, "remove", "data_plane/a.als", "")
        models = w.declared(out[PLAN.path], None).alloy_models
        assert "data_plane/a.als" not in models and "data_plane/b.als" in models
        assert "| `data_plane/b.als` | new | `x` |" in out[PLAN.path]

    def test_a_risk_row_is_removed_but_a_new_risk_needs_text(self):
        writer = W("Risk affects FeaturePlan")
        out = w.apply(writer, texts(), PLAN, "remove", f"{KEY}/RISK-02", "")
        assert "RISK-02" not in out[PLAN.path] and "RISK-01" in out[PLAN.path]
        assert writer.can_add is False
        with pytest.raises(w.WriteError, match="Bearbeiten"):
            w.apply(writer, texts(), PLAN, "add", f"{KEY}/RISK-03", "")

    def test_an_edit_the_loader_would_read_differently_is_refused(self, monkeypatch):
        writer = W("AlloyCheck verifies FeaturePlan")
        monkeypatch.setattr(writer, "remove", lambda t, p, target: {PLAN.path: PLAN_MD.replace("`data_plane/b.als`", "")
                                                                     .replace("`data_plane/a.als`, ", "")})
        with pytest.raises(w.WriteError, match="verloren: data_plane/b.als"):
            w.apply(writer, texts(), PLAN, "remove", "data_plane/a.als", "")


class TestFields:
    def test_owner_title_and_description_with_the_sidecar(self):
        out = w.apply_field(texts(), PLAN, "owner", "PERS-siran")
        assert "| **Author** | PERS-siran |" in out[PLAN.path] and "author: PERS-siran" in out[PLAN.sidecar]
        out = w.apply_field(texts(), PLAN, "title", "Brain on a graph store")
        assert "# Feature Plan: Brain on a graph store" in out[PLAN.path]

    @pytest.mark.parametrize("fld", ["sources", "target_date"])
    def test_fields_without_a_route_say_why(self, fld):
        with pytest.raises(w.WriteError, match="Edit-Map|Zieldatum"):
            w.apply_field(texts(), PLAN, fld, "x")

    def test_the_rollback_sets_front_matter_row_and_sidecar(self):
        out = w.set_status(texts(), PLAN, "draft")
        assert "\nstatus: draft\n" in out[PLAN.path] and "| **Status** | draft |" in out[PLAN.path]
        assert yaml.safe_load(out[PLAN.sidecar])["feature"]["status"] == "draft"
