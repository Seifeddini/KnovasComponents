# Typed-node workbench — backend slice implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Knowledge Graph API the two things the Platform workbench needs and does not have: edges in the neighbours response, and a target node type on `entity_ref` schema attributes — each pinned by an Alloy model that lands before its code, per the Feature Design Workflow.

**Architecture:** Both changes are additive and reuse existing enforcement. `include_edges` induces edges on the already-ACL-filtered neighbour set and runs them through `GraphAccessGuard.filter_edges`, so no new visibility *rule* is introduced — but the place that rule is easiest to lose (which node set the edges are induced on) is new, and gets its own model. `target_node_type_id` is a nullable column with a tenant-scoped lookup and a composite foreign key; its model pins tenant-locality and the no-existence-oracle answer.

**Tech Stack:** Python 3, Flask blueprints, psycopg2, PostgreSQL 15+, pytest, Alloy 6.2.0 (headless CLI, `models/alloy/ci/alloy_driver.py`).

**Spec:** `docs/superpowers/specs/2026-09-02-typed-node-workbench-design.md` (§5)

**Jira:** SS-315 *Platform Projekt und Mandantenmanagement*

**Repository:** `KnowledgeBase` only. The Platform slice is
`docs/superpowers/plans/2026-09-02-typed-node-workbench-components.md`; it
consumes Task A2 and Task A4 and can start before either lands.

**Branch:** `design/typed-node-workbench`

**Validation status of this plan (2026-09-04):** every Alloy file embedded below was run with the pinned Alloy 6.2.0 jar from `knovas-software/models/alloy/`: 8 checks hold, 5 witnesses are satisfiable, 4 mutants produce counterexamples. The manifest and obligation snippets were applied and both `scripts/check_alloy_coverage.py` and `scripts/check_alloy_obligations.py` exited 0. The pytest blocks follow the fixtures and response envelope actually used in `tests/test_graph_api.py` and `tests/test_kg_object_acl.py` (`client` + `env`, `acting_as()` / `url_as()`, payload keys at the top level of the JSON body — there is no `data` wrapper).

## Global Constraints

- **Model before code** (`docs/Docs/01_SYSTEM/Feature_Design_Workflow.md`, `docs/Docs/05_TESTS/Alloy_Unified_Model_Guide.md` §3). Tasks A0 and A3 land the `.als` files, mutants, manifest entries and obligation bindings **before** A1/A2 and A4 touch code. **No new Golden Invariant**: both models pin clauses of invariants that already exist — GI-GRAPH-12 ("an edge is only as visible as its least visible endpoint node") for the neighbourhood payload, GI-GRAPH-07 ("schema attribute rows are tenant-scoped") plus GI-GRAPH-11's 404-not-403 rule for the target type. If implementation reveals a rule not covered by those, stop and escalate — do not invent an invariant in passing.
- **Unified-model idiom** (`knovas-software/models/alloy/README.md`): sigs from `domain/` where they exist, mechanism preds in `mechanisms/` with `@code_under_check`, checks as `Mechanism implies Property`, `run` witnesses in every file that has checks, one open-based mutant per load-bearing conjunct, every mechanism pred covered or exempt in `ci/obligations.yaml`. The precondition step of the workflow (§3a) is discharged through those obligation bindings — the unified tree has no `fact` blocks to translate.
- **Traversal depth cap is 3** (GI-GRAPH-04). Never raise it.
- **Foreign or missing ids answer 404, never 403**, on reads and writes alike. No graph route may become an existence oracle.
- **The tenant comes from the mTLS certificate only** (GI-GRAPH-02). Never from a request body, query string or header.
- **Edges are induced on the post-filter node set**, never on the raw traversal.
- Every route already sits behind `@require_valid_client_certificate` and `KNOWLEDGE_GRAPH_ENABLED`. Do not add a second gate.
- **Response envelope:** `APIResponseService.create_success_response(message, data)` merges `data` into the top level of the JSON body next to `status` and `message`. Tests read `payload["neighbors"]`, `payload["attribute"]` — never `payload["data"][...]`.
- **Test idiom:** `tests/test_graph_api.py` runs with the RBAC posture `DISABLED` (fixtures `client`, `env` → `repo, *_ = env`, tenants `TENANT_A`/`TENANT_B`); `tests/test_kg_object_acl.py` runs `ENFORCING` (fixture `client`, helpers `acting_as(*groups)` for bodies and `url_as(path, *groups)` for query strings, groups `all` → `legal` → `legal-eu`, `all` → `hr`). New tests carry the registered markers already set at module level (`pytest.mark.api`, `pytest.mark.l2("L2-KNOWLEDGE-GRAPH")`) and `@pytest.mark.alloy_obligation("mechanisms/<file>.als::<Pred>")` where they discharge a mechanism.
- Run pytest from `knovas-software/app/` with `TESTING=true`; run Alloy from `knovas-software/models/alloy/` with the jar at `.cache/alloy.jar` (download URL on line 2 of `ci/alloy.version`).

---

## Part Overview

| Task | Deliverable | Blocks |
| --- | --- | --- |
| A0 | Alloy: `mechanisms/kg_neighborhood.als`, `data_plane/kg_neighborhood_edges.als`, two mutants, registries | A1, A2 |
| A1 | `neighbor_edges()` on the real and fake repositories | A2 |
| A2 | `include_edges=true` on the neighbours route | Platform B4, E2 |
| A3 | Alloy: `mechanisms/kg_schema_target.als`, `data_plane/kg_attribute_target_type.als`, two mutants, registries | A4 |
| A4 | `target_node_type_id` on schema attributes | Platform B3, E4 |

---

### Task A0: Alloy — induced neighbourhood edges

The neighbours route will return edges among the neighbours it returns. GI-GRAPH-12 already fixes the visibility rule; what the code can get wrong is the *induction set*. This task pins it before A2 writes the route.

**Files:**
- Create: `knovas-software/models/alloy/mechanisms/kg_neighborhood.als`
- Create: `knovas-software/models/alloy/data_plane/kg_neighborhood_edges.als`
- Create: `knovas-software/models/alloy/mutants/kg_neighborhood_edges__raw_walk.als`
- Create: `knovas-software/models/alloy/mutants/kg_neighborhood_edges__edge_acl_skipped.als`
- Modify: `docs/ModernDocs/alloy/component-manifest.yaml` (`L2-KNOWLEDGE-GRAPH`)
- Modify: `knovas-software/models/alloy/ci/obligations.yaml`
- Regenerate: `knovas-software/models/alloy/ci/expected_results.json`
- Modify: `docs/Docs/01_SYSTEM/Golden_Invariants.md` (GI-GRAPH-12 row), `docs/Docs/05_TESTS/alloy_component_coverage_matrix.md`, `docs/Docs/05_TESTS/alloy_pytest_matrix.md`, `docs/Docs/05_TESTS/traceability_map.md`
- Modify: `knovas-software/app/tests/alloy_invariants/test_kg_v1_alloy_pins.py`

**Interfaces:**
- Consumes: `domain/tenancy.als` (`Tenant`), `domain/graph.als` (`KnowledgeNode`, `KnowledgeEdge`, `EdgesTenantLocal`).
- Produces: preds `AnchorGateMechanism`, `NeighborFilterMechanism`, `InducedEdgeMechanism`, `NeighborhoodMechanism`, `endpointsIn`; checks `no_edge_names_a_hidden_node`, `every_edge_is_drawable`, `every_returned_edge_is_itself_visible`, `induction_is_complete`, `induced_edges_stay_in_tenant`.

- [ ] **Step 1: Write the mechanism module**

Create `knovas-software/models/alloy/mechanisms/kg_neighborhood.als`:

```alloy
/*
 * MECHANISM MODULE — the neighbours route's node filter and edge induction,
 * single definition.
 * Plan: docs/superpowers/plans/2026-09-02-typed-node-workbench-backend.md (A0)
 * (mechanism modules hold preds only — no facts, no commands).
 *
 * @code_under_check
 *   - app/src/api/graph_api.py (node_neighbors: filter_objects over the raw
 *     walk; include_edges induces on the FILTERED set plus the anchor)
 *   - app/src/services/knowledge_graph/graph_access.py (GraphAccessGuard
 *     .filter_objects, .filter_edges)
 *   - app/src/services/knowledge_graph/repository.py (neighbors,
 *     neighbor_edges — both endpoints in the given set)
 *
 * Visibility itself is not re-derived here. `VisibleNode` / `VisibleEdge`
 * stand for `GraphAccessGuard.object_is_visible(principal, ·)` for the one
 * caller of this request; the closure rule behind that verdict is proven in
 * data_plane/kg_object_acl_assignment (GI-GRAPH-12). This module fixes only
 * how the route USES the verdict when it assembles the payload.
 */
module semantix/mechanisms/kg_neighborhood

open semantix/domain/tenancy
open semantix/domain/graph

/* The caller's verdicts, as object_is_visible returns them. */
sig VisibleNode in KnowledgeNode {}
sig VisibleEdge in KnowledgeEdge {}

/* One GET /secured/graph/nodes/<anchor>/neighbors?include_edges=true. */
sig Neighborhood {
  anchor:   one KnowledgeNode,
  walked:   set KnowledgeNode,   // repo.neighbors(): the raw traversal
  returned: set KnowledgeNode,   // guard.filter_objects(walked): `neighbors`
  edges:    set KnowledgeEdge    // the `edges` array
}

/* Both endpoints of e lie in ns (repo.neighbor_edges: node_lo AND node_hi). */
pred endpointsIn[e: KnowledgeEdge, ns: set KnowledgeNode] {
  e.eFrom in ns and e.eTo in ns
}

/* The route answers 404 unless the anchor is visible; the walk excludes the
 * start node and never leaves its tenant (GI-GRAPH-04, traversal_same_tenant). */
pred AnchorGateMechanism {
  all n: Neighborhood {
    n.anchor in VisibleNode
    n.anchor not in n.walked
    n.walked.nTenant in n.anchor.nTenant
  }
}

/* filter_objects: `neighbors` is exactly the visible part of the walk. */
pred NeighborFilterMechanism {
  all n: Neighborhood | n.returned = n.walked & VisibleNode
}

/* neighbor_edges over (returned + anchor), then filter_edges: an edge is in
 * the payload iff both endpoints were returned (or are the anchor) AND the
 * edge's own assignment admits the caller. */
pred InducedEdgeMechanism {
  all n: Neighborhood |
    n.edges = { e: KnowledgeEdge |
      endpointsIn[e, n.returned + n.anchor] and e in VisibleEdge }
}

pred NeighborhoodMechanism {
  AnchorGateMechanism
  NeighborFilterMechanism
  InducedEdgeMechanism
}
```

- [ ] **Step 2: Write the component model**

Create `knovas-software/models/alloy/data_plane/kg_neighborhood_edges.als`:

```alloy
/*
 * @invariant_id    GI-GRAPH-12, GI-GRAPH-04
 * @golden_doc      docs/Docs/01_SYSTEM/Golden_Invariants.md
 * @plan            docs/superpowers/plans/2026-09-02-typed-node-workbench-backend.md (A0, A2)
 * @code_under_check
 *   - app/src/api/graph_api.py (node_neighbors, include_edges)
 *   - app/src/services/knowledge_graph/graph_access.py (filter_objects, filter_edges)
 *   - app/src/services/knowledge_graph/repository.py (neighbors, neighbor_edges)
 * @pytest_must_agree
 *   - app/tests/test_kg_object_acl.py (TestEdgeVisibility, TestNeighborhoodEdges)
 * @scope           6
 *
 * The neighbours route can return the edges among the neighbours it returns.
 * GI-GRAPH-12 already says an edge is only as visible as its least visible
 * endpoint; this model pins the one place that rule is easy to lose: WHICH
 * node set the edges are induced on. The route walks the graph, filters the
 * walk, and must induce on the filtered set. Induce on the walk and an edge
 * to a hidden node is returned — the caller learns the node exists and is
 * attached to something they can see, the graph shape leaking around the
 * node ACL.
 *
 * Why this is not a tautology: the mechanism fixes the induction set as a
 * parameter (returned + anchor) and the edge verdict as a conjunct; the
 * properties speak only about what a returned edge NAMES and whether it is
 * itself visible. Substitute `walked` for `returned` and
 * no_edge_names_a_hidden_node produces a counterexample
 * (mutants/kg_neighborhood_edges__raw_walk); drop the edge verdict and
 * every_returned_edge_is_itself_visible does
 * (mutants/kg_neighborhood_edges__edge_acl_skipped).
 */
module semantix/data_plane/kg_neighborhood_edges

open semantix/domain/tenancy
open semantix/domain/graph
open semantix/mechanisms/kg_neighborhood

/* ── the properties, stated apart from the mechanism ─────────────────────── */

/* GI-GRAPH-12: no edge in the payload names a node the caller may not see. */
pred NoEdgeNamesAHiddenNode {
  all n: Neighborhood | all e: n.edges |
    e.eFrom in VisibleNode and e.eTo in VisibleNode
}

/* Every edge is drawable: both endpoints are in the same payload. */
pred EveryEdgeIsDrawable {
  all n: Neighborhood | all e: n.edges | endpointsIn[e, n.returned + n.anchor]
}

/* The edge's own assignment is honoured, not only its endpoints'. */
pred EveryReturnedEdgeIsVisible {
  all n: Neighborhood | n.edges in VisibleEdge
}

/* Nothing visible is silently dropped: the payload is the full induced
 * subgraph, so an absent edge means "not connected", not "not shown". */
pred InductionIsComplete {
  all n: Neighborhood | all e: VisibleEdge |
    endpointsIn[e, n.returned + n.anchor] implies e in n.edges
}

/* The payload never leaves the anchor's tenant. */
pred EdgesStayInTenant {
  all n: Neighborhood | all e: n.edges |
    e.eFrom.nTenant = n.anchor.nTenant and e.eTo.nTenant = n.anchor.nTenant
}

/* ── checks: Mechanism implies Property ─────────────────────────────────── */

check no_edge_names_a_hidden_node {
  NeighborhoodMechanism implies NoEdgeNamesAHiddenNode
} for 6

check every_edge_is_drawable {
  NeighborhoodMechanism implies EveryEdgeIsDrawable
} for 6

check every_returned_edge_is_itself_visible {
  NeighborhoodMechanism implies EveryReturnedEdgeIsVisible
} for 6

check induction_is_complete {
  NeighborhoodMechanism implies InductionIsComplete
} for 6

/* Tenancy composes with the domain's write-guard shape: with tenant-local
 * edges, the induced payload stays inside the anchor's tenant. */
check induced_edges_stay_in_tenant {
  (NeighborhoodMechanism and EdgesTenantLocal) implies EdgesStayInTenant
} for 6

/* ── witnesses (non-vacuity) ─────────────────────────────────────────────── */

/* The mechanism live: a hidden node on the walk whose edge to the anchor is
 * withheld, beside a visible neighbour whose edge is returned. */
run witness_mechanism_live {
  NeighborhoodMechanism
  some n: Neighborhood | some hidden: n.walked - VisibleNode, shown: n.returned {
    some e: VisibleEdge | e.eFrom = n.anchor and e.eTo = hidden and e not in n.edges
    some e: n.edges | e.eFrom = n.anchor and e.eTo = shown
  }
} for 6

/* The forbidden state is representable absent the mechanism: an edge in the
 * payload naming a node the caller cannot see. */
run witness_breach_expressible {
  some n: Neighborhood | some e: n.edges | e.eTo not in VisibleNode
} for 4

/* An edge between two visible nodes withheld by its OWN assignment. */
run witness_edge_hidden_by_its_own_acl {
  NeighborhoodMechanism
  some n: Neighborhood | some e: KnowledgeEdge - VisibleEdge |
    endpointsIn[e, n.returned + n.anchor] and e not in n.edges
} for 6
```

- [ ] **Step 3: Write the two mutants**

Create `knovas-software/models/alloy/mutants/kg_neighborhood_edges__raw_walk.als`:

```alloy
/*
 * MUTANT — expected outcome: counterexample.
 *
 * Shadows: data_plane/kg_neighborhood_edges.als :: no_edge_names_a_hidden_node
 * (GI-GRAPH-12)
 * Simulated bug: node_neighbors builds `visible_ids` from the RAW walk
 * (`repo.neighbors(...)`) instead of from the filtered rows — the one-line
 * slip the route comment "filter the result, not the walk" warns about. An
 * edge from the anchor to a node the caller cannot see is then induced, and
 * filter_edges cannot save it because the endpoint set it is handed already
 * contains the hidden node.
 *
 * Open-based: imports the real modules; only the induction set is restated.
 */
module semantix/mutants/kg_neighborhood_edges__raw_walk

open semantix/data_plane/kg_neighborhood_edges

pred InducedOnRawWalk {
  all n: Neighborhood |
    n.edges = { e: KnowledgeEdge |
      endpointsIn[e, n.walked + n.anchor] and e in VisibleEdge }
}

check hidden_node_named_under_raw_walk {
  (AnchorGateMechanism and NeighborFilterMechanism and InducedOnRawWalk)
    implies NoEdgeNamesAHiddenNode
} for 6
```

Create `knovas-software/models/alloy/mutants/kg_neighborhood_edges__edge_acl_skipped.als`:

```alloy
/*
 * MUTANT — expected outcome: counterexample.
 *
 * Shadows: data_plane/kg_neighborhood_edges.als :: every_returned_edge_is_itself_visible
 * (GI-GRAPH-12)
 * Simulated bug: the route serialises `repo.neighbor_edges(...)` directly and
 * never passes it through `guard.filter_edges` — endpoints are checked, the
 * edge's own `access_group_ids` are not. An edge carrying the `legal` closure
 * between two open nodes would be shown to `hr`.
 */
module semantix/mutants/kg_neighborhood_edges__edge_acl_skipped

open semantix/data_plane/kg_neighborhood_edges

pred InducedWithoutEdgeVerdict {
  all n: Neighborhood |
    n.edges = { e: KnowledgeEdge | endpointsIn[e, n.returned + n.anchor] }
}

check restricted_edge_shown_without_its_verdict {
  (AnchorGateMechanism and NeighborFilterMechanism and InducedWithoutEdgeVerdict)
    implies EveryReturnedEdgeIsVisible
} for 6
```

- [ ] **Step 4: Run the commands and confirm the outcomes**

From `knovas-software/models/alloy/` (jar per `ci/alloy.version`; `mkdir -p .cache && curl -fsSL -o .cache/alloy.jar "$(sed -n 2p ci/alloy.version)"` if missing):

```bash
for c in no_edge_names_a_hidden_node every_edge_is_drawable every_returned_edge_is_itself_visible induction_is_complete induced_edges_stay_in_tenant; do
  rm -rf /tmp/als && java -jar .cache/alloy.jar exec -c $c -o /tmp/als -f -t json -q data_plane/kg_neighborhood_edges.als && ls /tmp/als
done
for c in witness_mechanism_live witness_breach_expressible witness_edge_hidden_by_its_own_acl; do
  rm -rf /tmp/als && java -jar .cache/alloy.jar exec -c $c -o /tmp/als -f -t json -q data_plane/kg_neighborhood_edges.als && ls /tmp/als
done
rm -rf /tmp/als && java -jar .cache/alloy.jar exec -c hidden_node_named_under_raw_walk -o /tmp/als -f -t json -q mutants/kg_neighborhood_edges__raw_walk.als && ls /tmp/als
rm -rf /tmp/als && java -jar .cache/alloy.jar exec -c restricted_edge_shown_without_its_verdict -o /tmp/als -f -t json -q mutants/kg_neighborhood_edges__edge_acl_skipped.als && ls /tmp/als
```

Expected: the five checks list only `receipt.json` (no counterexample); the three witnesses and both mutant checks additionally list a `*-solution-0.json` (satisfiable / counterexample). Each command finishes in ~1–2 s. If a check finds a counterexample, the model is wrong, not the plan's claim — stop and read the instance before touching anything else.

- [ ] **Step 5: Register the files in the ModernDocs manifest**

In `docs/ModernDocs/alloy/component-manifest.yaml`, under `L2-KNOWLEDGE-GRAPH: alloy_models:`, directly after the `data_plane/kg_fact_visibility.als` block (the one ending in `visibility_is_monotone_in_terms`), add:

```yaml
      # SS-315 typed-node workbench: the neighbours route's `include_edges`
      # induces edges on the FILTERED neighbour set, so no returned edge names
      # a node the caller cannot see (GI-GRAPH-12 applied to a payload).
      - path: mechanisms/kg_neighborhood.als
      - path: data_plane/kg_neighborhood_edges.als
        checks:
          - no_edge_names_a_hidden_node
          - every_edge_is_drawable
          - every_returned_edge_is_itself_visible
          - induction_is_complete
          - induced_edges_stay_in_tenant
      - path: mutants/kg_neighborhood_edges__raw_walk.als
      - path: mutants/kg_neighborhood_edges__edge_acl_skipped.als
```

- [ ] **Step 6: Bind the mechanism preds in the obligations manifest**

In `knovas-software/models/alloy/ci/obligations.yaml`, append to the `obligations:` list (before the `exempt:` key) — every test id below exists today and resolves under the checker's AST rule:

```yaml
  # ── kg_neighborhood (SS-315, plan A0/A2) ─────────────────────────────────
  - pred: mechanisms/kg_neighborhood.als::AnchorGateMechanism
    claim: a hidden anchor answers 404; the walk excludes the start node and stays in its tenant
    code: [app/src/api/graph_api.py, app/src/services/knowledge_graph/repository.py]
    tests:
      - test_kg_object_acl.py::TestNodeRequiredGroups::test_a_restricted_node_reads_as_404_not_403
      - test_graph_api.py::TestNodesEdgesTraversal::test_neighbors_cap_and_truncation_indicator
    mutants: []
    mutant_waiver: the anchor gate is the pre-existing object_is_visible 404; its refutation surface is kg_object_acl_assignment
  - pred: mechanisms/kg_neighborhood.als::NeighborFilterMechanism
    claim: filter_objects keeps exactly the visible part of the raw walk
    code: [app/src/services/knowledge_graph/graph_access.py, app/src/api/graph_api.py]
    tests:
      - test_kg_object_acl.py::TestEdgeVisibility::test_neighbors_omit_hidden_nodes
    mutants: [mutants/kg_neighborhood_edges__raw_walk.als]
  - pred: mechanisms/kg_neighborhood.als::InducedEdgeMechanism
    claim: edges are induced on the filtered set plus the anchor and pass filter_edges (endpoints AND the edge's own verdict)
    code: [app/src/api/graph_api.py, app/src/services/knowledge_graph/graph_access.py, app/src/services/knowledge_graph/repository.py]
    tests:
      - test_kg_object_acl.py::TestEdgeVisibility::test_an_edge_to_a_hidden_node_is_not_listed
      - test_kg_object_acl.py::TestEdgeVisibility::test_an_edge_carries_its_own_required_group
    mutants: [mutants/kg_neighborhood_edges__raw_walk.als, mutants/kg_neighborhood_edges__edge_acl_skipped.als]
    note: A2 adds test_kg_object_acl.py::TestNeighborhoodEdges::* (the route itself) to this binding
```

and to the `exempt:` list:

```yaml
  - {pred: mechanisms/kg_neighborhood.als::endpointsIn, reason: helper over an edge and a node set; bound through InducedEdgeMechanism and A1's TestNeighborEdges}
  - {pred: mechanisms/kg_neighborhood.als::NeighborhoodMechanism, reason: composite of AnchorGate + NeighborFilter + InducedEdge}
```

- [ ] **Step 7: Regenerate the lockfile and run the whole suite**

```bash
cd knovas-software/models/alloy
python3 ci/alloy_driver.py --emit-expected > ci/expected_results.json
python3 ci/alloy_driver.py -j 4
```

Expected: the driver exits 0 (no failing check, no unsatisfiable witness, no mutant that stopped refuting, no file with checks but no witness). `git diff ci/expected_results.json` shows exactly the 10 new entries: 5 `no_counterexample` checks and 3 `satisfiable` runs under `models/alloy/data_plane/kg_neighborhood_edges.als`, 2 `counterexample` checks under the two mutant files. Nothing else changes.

- [ ] **Step 8: Run the two lints**

From the repo root:

```bash
python3 scripts/check_alloy_coverage.py
python3 scripts/check_alloy_obligations.py
```

Expected: both exit 0. The coverage lint prints `OK: … alloy file(s) covered by ModernDocs component-manifest.yaml` and a `WARN` block about the legacy Docs V2 matrix — that warning is pre-existing and non-fatal; Step 9 adds the rows anyway. The obligations lint prints `OK: mechanisms/kg_neighborhood.als: 3/5 preds covered (2 exempt)`.

- [ ] **Step 9: Update the catalog, matrices and pins**

1. `docs/Docs/01_SYSTEM/Golden_Invariants.md`, row **GI-GRAPH-12**: in the *Enforced by* cell append `, `knovas-software/app/src/api/graph_api.py` (`node_neighbors`, `include_edges`)`; in the *Tests / Alloy* cell, directly after `` `a_permitted_assignment_stays_readable`) ``, append:
   ``, [`data_plane/kg_neighborhood_edges.als`](../../../knovas-software/models/alloy/data_plane/kg_neighborhood_edges.als) (`no_edge_names_a_hidden_node`, `every_returned_edge_is_itself_visible`, `induction_is_complete`); `test_kg_object_acl.py::TestNeighborhoodEdges` ``.
   This is a "change an implementation without changing the invariant" edit per that file's procedure section — no new id, no renumbering.
2. `docs/Docs/05_TESTS/alloy_component_coverage_matrix.md`: after the `KG access guard (graph reads)` row add
   `| KG neighbourhood payload (induced edges) | `knovas-software/app/src/api/graph_api.py` (`node_neighbors`), `knovas-software/app/src/services/knowledge_graph/graph_access.py` (`filter_edges`) | `mechanisms/kg_neighborhood.als`, `data_plane/kg_neighborhood_edges.als` | GI-GRAPH-12, GI-GRAPH-04 | Covered |`
3. `docs/Docs/05_TESTS/alloy_pytest_matrix.md`: add a row
   `| `data_plane/kg_neighborhood_edges.als` | `no_edge_names_a_hidden_node`, `every_returned_edge_is_itself_visible`, `induction_is_complete`, `every_edge_is_drawable`, `induced_edges_stay_in_tenant` | `app/tests/test_kg_object_acl.py::TestEdgeVisibility`, `::TestNeighborhoodEdges` (A2) |`
4. `docs/Docs/05_TESTS/traceability_map.md`: add a row
   `| KG neighbourhood with edges (SS-315) | `docs/superpowers/specs/2026-09-02-typed-node-workbench-design.md` §5.1 | `knovas-software/app/src/api/graph_api.py`, `knovas-software/app/src/services/knowledge_graph/graph_access.py` | `knovas-software/app/tests/test_kg_object_acl.py` | [`kg_neighborhood_edges.als`](../../../knovas-software/models/alloy/data_plane/kg_neighborhood_edges.als) |`
5. `knovas-software/app/tests/alloy_invariants/test_kg_v1_alloy_pins.py`: add to `V1_COMMANDS`
   ```python
       # --- SS-315 typed-node workbench ---
       "models/alloy/data_plane/kg_neighborhood_edges.als": {
           "no_edge_names_a_hidden_node": "check",
           "every_edge_is_drawable": "check",
           "every_returned_edge_is_itself_visible": "check",
           "induction_is_complete": "check",
           "induced_edges_stay_in_tenant": "check",
           "witness_mechanism_live": "run",
           "witness_breach_expressible": "run",
           "witness_edge_hidden_by_its_own_acl": "run",
       },
   ```
   and to `V1_HEADER_GIS`: `"models/alloy/data_plane/kg_neighborhood_edges.als": ["GI-GRAPH-12", "GI-GRAPH-04"],`. The pin test `test_no_v1_check_is_unpinned_on_disk` compares the file's commands against this dict, so every `check` and `run` in the file must be listed.

- [ ] **Step 10: Run the pins**

Run: `cd knovas-software/app && TESTING=true python3 -m pytest tests/alloy_invariants/test_kg_v1_alloy_pins.py -q`
Expected: all pass.

- [ ] **Step 11: Commit**

```bash
git add knovas-software/models/alloy/mechanisms/kg_neighborhood.als \
        knovas-software/models/alloy/data_plane/kg_neighborhood_edges.als \
        knovas-software/models/alloy/mutants/kg_neighborhood_edges__raw_walk.als \
        knovas-software/models/alloy/mutants/kg_neighborhood_edges__edge_acl_skipped.als \
        knovas-software/models/alloy/ci/expected_results.json \
        knovas-software/models/alloy/ci/obligations.yaml \
        docs/ModernDocs/alloy/component-manifest.yaml \
        docs/Docs/01_SYSTEM/Golden_Invariants.md \
        docs/Docs/05_TESTS/alloy_component_coverage_matrix.md \
        docs/Docs/05_TESTS/alloy_pytest_matrix.md \
        docs/Docs/05_TESTS/traceability_map.md \
        knovas-software/app/tests/alloy_invariants/test_kg_v1_alloy_pins.py
git commit -m "alloy(graph): induced neighbourhood edges never name a hidden node (SS-315)

Pins the induction set of the neighbours route's include_edges payload
under GI-GRAPH-12: edges are induced on the filtered neighbour set plus
the anchor and pass filter_edges. Two mutants (raw-walk induction, edge
verdict skipped) refute. No new invariant."
```

---

### Task A1: `neighbor_edges()` on both repositories

The neighbours route returns nodes and a hop count with no edges, so a caller
cannot draw the neighbourhood. This task adds the query only; the route change
is A2.

**Files:**
- Modify: `knovas-software/app/src/services/knowledge_graph/repository.py` (after `list_edges`, ~line 310)
- Modify: `knovas-software/app/src/interfaces/IKnowledgeGraphRepository.py` (beside `list_edges`, ~line 98)
- Modify: `knovas-software/app/tests/fixtures/fake_kg_repository.py` (after `list_edges`, ~line 242)
- Test: `knovas-software/app/tests/test_graph_api.py`
- Modify: `knovas-software/models/alloy/ci/obligations.yaml` (move `endpointsIn` from exempt to covered)

**Interfaces:**
- Consumes: nothing.
- Produces: `KnowledgeGraphRepository.neighbor_edges(client_id, node_ids) -> list[dict]` — every `kg_edges` row of this tenant whose `node_lo` **and** `node_hi` are both in `node_ids`. `node_ids` is any iterable of uuid strings. An empty or single-element `node_ids` returns `[]`. `FakeKnowledgeGraphRepository` gets the same signature.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_graph_api.py` (module level; it uses the module's `env` fixture and `TENANT_A`):

```python
class TestNeighborEdges:
    """Edges induced on a node set — the query behind include_edges.

    Alloy: mechanisms/kg_neighborhood.als::endpointsIn (both endpoints, not either).
    """

    @pytest.mark.alloy_obligation("mechanisms/kg_neighborhood.als::endpointsIn")
    def test_returns_only_edges_with_both_endpoints_in_the_set(self, env):
        repo, *_ = env
        a = repo.create_node(TENANT_A, name="A")["id"]
        b = repo.create_node(TENANT_A, name="B")["id"]
        c = repo.create_node(TENANT_A, name="C")["id"]
        repo.create_edge(TENANT_A, str(a), str(b), relation="knows")
        repo.create_edge(TENANT_A, str(b), str(c), relation="knows")

        rows = repo.neighbor_edges(TENANT_A, {str(a), str(b)})

        assert len(rows) == 1
        assert rows[0]["relation"] == "knows"
        assert {str(rows[0]["node_lo"]), str(rows[0]["node_hi"])} == {str(a), str(b)}

    def test_a_single_node_has_no_induced_edges(self, env):
        repo, *_ = env
        a = repo.create_node(TENANT_A, name="A")["id"]
        b = repo.create_node(TENANT_A, name="B")["id"]
        repo.create_edge(TENANT_A, str(a), str(b), relation="knows")

        assert repo.neighbor_edges(TENANT_A, {str(a)}) == []

    def test_an_empty_set_is_not_a_full_table_scan(self, env):
        repo, *_ = env
        a = repo.create_node(TENANT_A, name="A")["id"]
        b = repo.create_node(TENANT_A, name="B")["id"]
        repo.create_edge(TENANT_A, str(a), str(b), relation="knows")

        assert repo.neighbor_edges(TENANT_A, set()) == []

    def test_a_foreign_tenants_edge_is_never_induced(self, env):
        """Same ids, other tenant: the query is tenant-scoped like every kg_* read."""
        repo, *_ = env
        a = repo.create_node(TENANT_B, name="A")["id"]
        b = repo.create_node(TENANT_B, name="B")["id"]
        repo.create_edge(TENANT_B, str(a), str(b), relation="knows")

        assert repo.neighbor_edges(TENANT_A, {str(a), str(b)}) == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `TESTING=true python3 -m pytest tests/test_graph_api.py::TestNeighborEdges -v`
Expected: FAIL — `AttributeError: 'FakeKnowledgeGraphRepository' object has no attribute 'neighbor_edges'`

- [ ] **Step 3: Implement on the real repository**

In `src/services/knowledge_graph/repository.py`, directly after `list_edges`:

```python
    def neighbor_edges(self, client_id, node_ids):
        """Edges whose BOTH endpoints are in ``node_ids`` (the induced subgraph).

        Both endpoints, not either: an edge with one endpoint outside the set
        names a node the caller was not given, which is the graph shape leaking
        around the node ACL. The route passes the already-filtered neighbour
        set for exactly this reason (GI-GRAPH-12;
        models/alloy/mechanisms/kg_neighborhood.als::endpointsIn).
        """
        ids = [str(n) for n in node_ids]
        if len(ids) < 2:
            return []
        return self._query(
            "SELECT * FROM kg_edges "
            "WHERE client_id = %s AND node_lo = ANY(%s::uuid[]) AND node_hi = ANY(%s::uuid[])",
            (client_id, ids, ids),
        )
```

- [ ] **Step 4: Implement on the fake repository**

In `tests/fixtures/fake_kg_repository.py`, directly after `list_edges`:

```python
    def neighbor_edges(self, client_id, node_ids):
        ids = {str(n) for n in node_ids}
        if len(ids) < 2:
            return []
        return [
            dict(r) for r in self.tables["kg_edges"].values()
            if str(r["client_id"]) == str(client_id)
            and str(r["node_lo"]) in ids and str(r["node_hi"]) in ids
        ]
```

- [ ] **Step 5: Declare it on the interface**

In `src/interfaces/IKnowledgeGraphRepository.py`, directly after the `list_edges` declaration:

```python
    @abstractmethod
    def neighbor_edges(self, client_id: str, node_ids: Iterable[str]) -> List[Dict[str, Any]]:
        """Edges whose both endpoints are in node_ids (the induced subgraph)."""
```

Add `Iterable` to the module's `typing` import if it is not already there.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `TESTING=true python3 -m pytest tests/test_graph_api.py::TestNeighborEdges -v`
Expected: 4 passed

- [ ] **Step 7: Run the surrounding suite for regressions**

Run: `TESTING=true python3 -m pytest tests/test_graph_api.py tests/test_kg_object_acl.py -q`
Expected: all pass — this task adds a method and changes no existing path.

- [ ] **Step 8: Bind the helper pred**

In `ci/obligations.yaml`, delete the `endpointsIn` line from `exempt:` and add to `obligations:`:

```yaml
  - pred: mechanisms/kg_neighborhood.als::endpointsIn
    claim: neighbor_edges returns an edge only when node_lo AND node_hi are in the given set
    code: [app/src/services/knowledge_graph/repository.py]
    tests:
      - test_graph_api.py::TestNeighborEdges::test_returns_only_edges_with_both_endpoints_in_the_set
      - test_graph_api.py::TestNeighborEdges::test_a_single_node_has_no_induced_edges
    mutants: [mutants/kg_neighborhood_edges__raw_walk.als]
```

Run: `python3 scripts/check_alloy_obligations.py` (from the repo root)
Expected: exit 0, `mechanisms/kg_neighborhood.als: 4/5 preds covered (1 exempt)`.

- [ ] **Step 9: Commit**

```bash
git add src/services/knowledge_graph/repository.py \
        src/interfaces/IKnowledgeGraphRepository.py \
        tests/fixtures/fake_kg_repository.py \
        tests/test_graph_api.py \
        ../models/alloy/ci/obligations.yaml
git commit -m "feat(graph): neighbor_edges returns the induced subgraph (SS-315)"
```

---

### Task A2: `include_edges=true` on the neighbours route

**Files:**
- Modify: `knovas-software/app/src/api/graph_api.py` (`node_neighbors`, ~line 797)
- Test: `knovas-software/app/tests/test_kg_object_acl.py`
- Modify: `knovas-software/models/alloy/ci/obligations.yaml`
- Modify: `docs/Knovas_Developer_Kit/api/Knowledge_Graph_API.md`

**Interfaces:**
- Consumes: `repo.neighbor_edges(client_id, node_ids)` from A1; `guard.filter_edges(principal, edges, visible_node_ids)` which already exists in `services/knowledge_graph/graph_access.py` (~line 237).
- Produces: `GET /secured/graph/nodes/<node_id>/neighbors?depth=N&include_edges=true` returning `{"status": "success", "message": "Neighbors", "neighbors": [...], "edges": [...], "depth_applied": int, "depth_cap": 3, "truncated": bool}`. The `edges` key is **absent** unless `include_edges` is truthy — not present-and-empty, so a caller can tell "not requested" from "none found".

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_kg_object_acl.py`, directly after `class TestEdgeVisibility` (same `client` fixture, same `acting_as` / `url_as` helpers; groups: `all` is the root, `legal` and `hr` are siblings under it):

```python
class TestNeighborhoodEdges:
    """include_edges must never disclose a node the caller cannot see.

    Alloy: data_plane/kg_neighborhood_edges.als (no_edge_names_a_hidden_node,
    every_returned_edge_is_itself_visible, induction_is_complete,
    every_edge_is_drawable).
    """

    def _node(self, client, name, groups=None):
        body = {"name": name, **acting_as("all")}
        if groups:
            body["required_groups"] = groups
        return client.post("/secured/graph/nodes", json=body).get_json()["node"]["id"]

    def _edge(self, client, lo, hi, groups=None):
        body = {"node_lo": lo, "node_hi": hi, "relation": "knows", **acting_as("all")}
        if groups:
            body["required_groups"] = groups
        response = client.post("/secured/graph/edges", json=body)
        assert response.status_code == 201
        return response.get_json()["edge"]["id"]

    def _neighbors(self, client, anchor, group, include_edges=True):
        path = f"/secured/graph/nodes/{anchor}/neighbors?depth=1"
        if include_edges:
            path += "&include_edges=true"
        response = client.get(url_as(path, group))
        assert response.status_code == 200
        return response.get_json()

    def test_edges_are_absent_unless_requested(self, client):
        anchor, other = self._node(client, "Anchor"), self._node(client, "Other")
        self._edge(client, anchor, other)
        payload = self._neighbors(client, anchor, "all", include_edges=False)
        assert "edges" not in payload
        assert [n["id"] for n in payload["neighbors"]] == [other]

    def test_edges_are_returned_when_requested(self, client):
        anchor, other = self._node(client, "Anchor"), self._node(client, "Other")
        edge = self._edge(client, anchor, other)
        payload = self._neighbors(client, anchor, "all")
        assert [e["id"] for e in payload["edges"]] == [edge]
        assert payload["edges"][0]["relation"] == "knows"
        assert {str(payload["edges"][0]["node_lo"]),
                str(payload["edges"][0]["node_hi"])} == {anchor, other}

    @pytest.mark.alloy_obligation("mechanisms/kg_neighborhood.als::InducedEdgeMechanism")
    def test_an_edge_to_a_restricted_neighbour_is_withheld(self, client):
        """The node is filtered out of `neighbors`; its edge must go with it.
        Returning the edge would disclose that the node exists and that it is
        attached to something the caller can see (GI-GRAPH-12)."""
        anchor = self._node(client, "Anchor")
        hidden = self._node(client, "Hidden", ["hr"])
        self._edge(client, anchor, hidden)
        payload = self._neighbors(client, anchor, "legal")
        assert payload["neighbors"] == []
        assert payload["edges"] == []

    @pytest.mark.alloy_obligation("mechanisms/kg_neighborhood.als::InducedEdgeMechanism")
    def test_a_restricted_edge_between_two_visible_nodes_is_withheld(self, client):
        """filter_edges applies the edge's OWN assignment too, not only its endpoints'."""
        anchor, other = self._node(client, "Anchor"), self._node(client, "Other")
        self._edge(client, anchor, other, ["legal"])
        payload = self._neighbors(client, anchor, "hr")
        assert [n["id"] for n in payload["neighbors"]] == [other]
        assert payload["edges"] == []

    @pytest.mark.alloy_obligation("mechanisms/kg_neighborhood.als::InducedEdgeMechanism")
    def test_every_returned_edge_is_drawable_and_nothing_visible_is_dropped(self, client):
        """Both endpoints of every edge are in the payload, and every visible
        edge among the payload's nodes is returned (induction_is_complete)."""
        anchor = self._node(client, "Anchor")
        a, b = self._node(client, "A"), self._node(client, "B")
        hidden = self._node(client, "Hidden", ["hr"])
        expected = {self._edge(client, anchor, a), self._edge(client, anchor, b),
                    self._edge(client, a, b)}
        self._edge(client, a, hidden)
        payload = self._neighbors(client, anchor, "legal")
        present = {anchor} | {n["id"] for n in payload["neighbors"]}
        assert {e["id"] for e in payload["edges"]} == expected
        for edge in payload["edges"]:
            assert {str(edge["node_lo"]), str(edge["node_hi"])} <= present
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `TESTING=true python3 -m pytest tests/test_kg_object_acl.py::TestNeighborhoodEdges -v`
Expected: `test_edges_are_absent_unless_requested` passes (the key is not there today); the other four FAIL with `KeyError: 'edges'`.

- [ ] **Step 3: Implement the route change**

In `src/api/graph_api.py`, inside `node_neighbors`, replace the final block

```python
    rows = guard.filter_objects(principal, repo.neighbors(client_id, node_id,
                                                          depth=applied_depth))
    _bill("graph_read", client_id)
    return response_service.create_success_response("Neighbors", {
        "neighbors": _serialize(rows),
        "depth_applied": applied_depth,
        "depth_cap": 3,
        "truncated": requested_depth > 3,
    })
```

with

```python
    rows = guard.filter_objects(principal, repo.neighbors(client_id, node_id,
                                                          depth=applied_depth))
    payload = {
        "neighbors": _serialize(rows),
        "depth_applied": applied_depth,
        "depth_cap": 3,
        "truncated": requested_depth > 3,
    }
    if request.args.get("include_edges", "false").lower() == "true":
        # Induce on the FILTERED set plus the anchor. Building this from the
        # raw walk would name endpoints the caller was not given — the graph
        # shape leaking around the node ACL (GI-GRAPH-12;
        # models/alloy/data_plane/kg_neighborhood_edges.als). filter_edges
        # then applies each edge's own assignment on top.
        visible_ids = {str(r["id"]) for r in rows} | {str(node_id)}
        payload["edges"] = _serialize(guard.filter_edges(
            principal, repo.neighbor_edges(client_id, visible_ids), visible_ids))
    _bill("graph_read", client_id)
    return response_service.create_success_response("Neighbors", payload)
```

Leave everything above it — the uuid check, the `object_is_visible` gate, the
depth parsing and the GI-GRAPH-04 clamp — untouched.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `TESTING=true python3 -m pytest tests/test_kg_object_acl.py::TestNeighborhoodEdges -v`
Expected: 5 passed

- [ ] **Step 5: Run the graph and RBAC suites for regressions**

Run: `TESTING=true python3 -m pytest tests/test_graph_api.py tests/test_kg_object_acl.py tests/test_kg_rbac_routes.py tests/test_kg_rbac_composition.py -q`
Expected: all pass. The default response shape is unchanged, so nothing that
ignores `include_edges` can break.

- [ ] **Step 6: Extend the obligation binding and re-lint**

In `ci/obligations.yaml`, under `mechanisms/kg_neighborhood.als::InducedEdgeMechanism`, add the three route tests to `tests:` and delete the `note:` line:

```yaml
      - test_kg_object_acl.py::TestNeighborhoodEdges::test_an_edge_to_a_restricted_neighbour_is_withheld
      - test_kg_object_acl.py::TestNeighborhoodEdges::test_a_restricted_edge_between_two_visible_nodes_is_withheld
      - test_kg_object_acl.py::TestNeighborhoodEdges::test_every_returned_edge_is_drawable_and_nothing_visible_is_dropped
```

Run (repo root): `python3 scripts/check_alloy_obligations.py` — Expected: exit 0, no dangling markers.
Then run the obligation suite as CI does:

```bash
ARGS=$(python3 scripts/check_alloy_obligations.py --emit-pytest-args | tr '\n' ' ')
cd knovas-software/app && TESTING=true python3 -m pytest $ARGS -q
```

Expected: all pass.

- [ ] **Step 7: Document the parameter**

In `docs/Knovas_Developer_Kit/api/Knowledge_Graph_API.md`, in the neighbours route section (the table row `/secured/graph/nodes/<id>/neighbors?depth=N`), add:

```markdown
`include_edges` (optional, default `false`) — when `true`, the response also
carries an `edges` array: the edges induced on the returned neighbour set plus
the anchor node. Edges are filtered by the same rule as nodes, and an edge with
an endpoint the caller may not see is never returned (GI-GRAPH-12). The key is
absent when the parameter is not sent, so "not requested" is distinguishable
from "none found".
```

- [ ] **Step 8: Commit**

```bash
git add src/api/graph_api.py tests/test_kg_object_acl.py \
        ../models/alloy/ci/obligations.yaml \
        ../../docs/Knovas_Developer_Kit/api/Knowledge_Graph_API.md
git commit -m "feat(graph): include_edges on the neighbours route (SS-315)

Edges are induced on the post-filter neighbour set and run through
GraphAccessGuard.filter_edges, so an edge is never returned whose endpoint
the caller cannot see. Pinned by data_plane/kg_neighborhood_edges.als
under GI-GRAPH-12; no new invariant."
```

---

### Task A3: Alloy — target node type on `entity_ref` attributes

`target_node_type_id` is a client-supplied uuid that becomes a stored
reference. The model pins that it is tenant-local and that the refusal for a
foreign id is indistinguishable from the refusal for an unknown one.

**Files:**
- Create: `knovas-software/models/alloy/mechanisms/kg_schema_target.als`
- Create: `knovas-software/models/alloy/data_plane/kg_attribute_target_type.als`
- Create: `knovas-software/models/alloy/mutants/kg_attribute_target_type__cross_tenant_target.als`
- Create: `knovas-software/models/alloy/mutants/kg_attribute_target_type__foreign_is_forbidden.als`
- Modify: `docs/ModernDocs/alloy/component-manifest.yaml`, `knovas-software/models/alloy/ci/obligations.yaml`, `knovas-software/models/alloy/ci/expected_results.json` (regenerate)
- Modify: `docs/Docs/01_SYSTEM/Golden_Invariants.md` (GI-GRAPH-07 row), the three matrices, `test_kg_v1_alloy_pins.py`

**Interfaces:**
- Consumes: `domain/tenancy.als` (`Tenant`).
- Produces: pred `TargetValidationMechanism`, fun `denoted`; checks `a_stored_target_is_tenant_local`, `only_entity_ref_stores_a_target`, `foreign_and_missing_answer_alike`.

- [ ] **Step 1: Write the mechanism module**

Create `knovas-software/models/alloy/mechanisms/kg_schema_target.als`:

```alloy
/*
 * MECHANISM MODULE — target node type on entity_ref schema attributes,
 * single definition.
 * Plan: docs/superpowers/plans/2026-09-02-typed-node-workbench-backend.md (A3, A4)
 * (mechanism modules hold preds only — no facts, no commands).
 *
 * @code_under_check
 *   - app/src/api/graph_api.py (_validated_target_type; node_type_schema POST
 *     and modify_schema_attribute PATCH call it before the repository)
 *   - app/src/services/knowledge_graph/repository.py (get_node_type —
 *     `WHERE client_id = %s AND id = %s`, so a foreign id resolves to nothing)
 *   - DB/migrations/20260902_kg_attribute_target_type.sql
 *     (fk_kg_attribute_target_node_type, ck_kg_attribute_target_is_entity_ref —
 *      the database-level second line, pinned by the DDL precondition test)
 *
 * The write is modelled at the validation layer: what the service answers,
 * and what it lets through to the repository. The composite foreign key is
 * defence in depth and is pinned by pytest against the migration DDL, not
 * re-modelled here.
 */
module semantix/mechanisms/kg_schema_target

open semantix/domain/tenancy

sig NodeType { ntTenant: one Tenant }

abstract sig Datatype {}
one sig EntityRef, TextKind, DateKind, MoneyKind, EnumKind extends Datatype {}

/* The submitted target_node_type_id. `resolves` is the row that id names
 * anywhere in the database; none means the id is unknown. */
sig TargetRef { resolves: lone NodeType }

abstract sig Outcome {}
one sig Stored, NotFound, Unprocessable extends Outcome {}

/* One POST .../schema or PATCH .../schema/<aid> as _validated_target_type
 * sees it: the caller's tenant, the attribute's datatype (the stored one on
 * PATCH), whether the key was sent, and the answer. */
sig TargetWrite {
  twTenant:  one Tenant,
  twKind:    one Datatype,
  twRef:     lone TargetRef,
  twOutcome: one Outcome
}

/* repo.get_node_type(client_id, target): the row, only inside the tenant. */
fun denoted[w: TargetWrite]: set NodeType {
  { t: w.twRef.resolves | t.ntTenant = w.twTenant }
}

/* _validated_target_type, conjunct per branch, in the code's order:
 *   absent key            -> nothing to validate, the write proceeds
 *   key on a non-entity_ref -> 422 target_type_requires_entity_ref
 *   entity_ref, unresolved -> 404 (unknown and foreign answer alike)
 *   entity_ref, resolved   -> stored with that target */
pred TargetValidationMechanism {
  all w: TargetWrite {
    no w.twRef implies w.twOutcome = Stored
    (some w.twRef and w.twKind != EntityRef) implies w.twOutcome = Unprocessable
    (some w.twRef and w.twKind = EntityRef and no denoted[w])
      implies w.twOutcome = NotFound
    (some w.twRef and w.twKind = EntityRef and some denoted[w])
      implies w.twOutcome = Stored
  }
}
```

- [ ] **Step 2: Write the component model**

Create `knovas-software/models/alloy/data_plane/kg_attribute_target_type.als`:

```alloy
/*
 * @invariant_id    GI-GRAPH-07, GI-GRAPH-11
 * @golden_doc      docs/Docs/01_SYSTEM/Golden_Invariants.md
 * @plan            docs/superpowers/plans/2026-09-02-typed-node-workbench-backend.md (A3, A4)
 * @code_under_check
 *   - app/src/api/graph_api.py (_validated_target_type)
 *   - app/src/services/knowledge_graph/repository.py (get_node_type, add_attribute, update_attribute)
 *   - DB/migrations/20260902_kg_attribute_target_type.sql
 * @pytest_must_agree
 *   - app/tests/test_graph_api.py (TestSchemaAttributeTargetType)
 *   - app/tests/preconditions/test_kg_attribute_target_type_preconditions.py
 * @scope           5
 *
 * An entity_ref attribute may declare WHICH node type it points at. That id
 * is a reference across the tenant wall's most tempting seam: a client can
 * submit any uuid, and the only thing between that uuid and a stored row
 * pointing into another tenant is the lookup being tenant-scoped.
 *
 * Two rules meet: the reference is tenant-local (GI-GRAPH-07 — schema
 * attribute rows are tenant-scoped), and the refusal must not become an
 * existence oracle (GI-GRAPH-11 — a target the caller may not reach answers
 * 404, never a distinct "exists but not yours").
 *
 * Why this is not a tautology: the mechanism describes the code's branches
 * over `denoted`, a lookup with the tenant folded into it; the properties
 * speak about the stored row's tenant and about two callers' answers being
 * indistinguishable. Unfold the tenant from the lookup and
 * a_stored_target_is_tenant_local fails
 * (mutants/kg_attribute_target_type__cross_tenant_target); give the foreign
 * case its own answer and foreign_and_missing_answer_alike fails
 * (mutants/kg_attribute_target_type__foreign_is_forbidden).
 */
module semantix/data_plane/kg_attribute_target_type

open semantix/domain/tenancy
open semantix/mechanisms/kg_schema_target

/* ── properties ─────────────────────────────────────────────────────────── */

/* Whatever is stored with a target points inside the caller's tenant. */
pred StoredTargetIsTenantLocal {
  all w: TargetWrite | (w.twOutcome = Stored and some w.twRef) implies
    (some w.twRef.resolves and w.twRef.resolves.ntTenant = w.twTenant)
}

/* Only an entity_ref attribute ever stores a target. */
pred OnlyEntityRefStoresATarget {
  all w: TargetWrite | (w.twOutcome = Stored and some w.twRef) implies
    w.twKind = EntityRef
}

/* A foreign id and an unknown id get the same answer. */
pred foreign[w: TargetWrite] {
  some w.twRef.resolves and w.twRef.resolves.ntTenant != w.twTenant
}
pred missing[w: TargetWrite] {
  some w.twRef and no w.twRef.resolves
}
pred ForeignAndMissingAnswerAlike {
  all disj a, b: TargetWrite |
    (a.twKind = EntityRef and b.twKind = EntityRef and foreign[a] and missing[b])
      implies a.twOutcome = b.twOutcome
}

/* ── checks ─────────────────────────────────────────────────────────────── */

check a_stored_target_is_tenant_local {
  TargetValidationMechanism implies StoredTargetIsTenantLocal
} for 5

check only_entity_ref_stores_a_target {
  TargetValidationMechanism implies OnlyEntityRefStoresATarget
} for 5

check foreign_and_missing_answer_alike {
  TargetValidationMechanism implies ForeignAndMissingAnswerAlike
} for 5

/* ── witnesses (non-vacuity) ─────────────────────────────────────────────── */

/* All four branches live in one universe. */
run witness_mechanism_live {
  TargetValidationMechanism
  some w: TargetWrite | w.twOutcome = Stored and some denoted[w]
  some w: TargetWrite | w.twOutcome = NotFound and foreign[w]
  some w: TargetWrite | w.twOutcome = NotFound and missing[w]
  some w: TargetWrite | w.twOutcome = Unprocessable
} for 5

/* The breach is representable absent the mechanism: a stored row whose
 * target lives in another tenant. */
run witness_breach_expressible {
  some w: TargetWrite | w.twOutcome = Stored and foreign[w]
} for 3
```

- [ ] **Step 3: Write the two mutants**

Create `knovas-software/models/alloy/mutants/kg_attribute_target_type__cross_tenant_target.als`:

```alloy
/*
 * MUTANT — expected outcome: counterexample.
 *
 * Shadows: data_plane/kg_attribute_target_type.als :: a_stored_target_is_tenant_local
 * (GI-GRAPH-07)
 * Simulated bug: _validated_target_type resolves the target with a lookup
 * that is not tenant-scoped (a `get_node_type_any(id)` or a bare
 * `SELECT ... WHERE id = %s`). A uuid from another tenant then resolves,
 * the write is stored, and the schema of tenant A names a type of tenant B.
 * The composite FK would catch it at the database — which is exactly why the
 * DDL precondition test exists as the second line — but the service must
 * refuse it first.
 */
module semantix/mutants/kg_attribute_target_type__cross_tenant_target

open semantix/data_plane/kg_attribute_target_type

/* The lookup with the tenant unfolded from it. */
fun denotedAnywhere[w: TargetWrite]: set NodeType { w.twRef.resolves }

pred ValidationWithUnscopedLookup {
  all w: TargetWrite {
    no w.twRef implies w.twOutcome = Stored
    (some w.twRef and w.twKind != EntityRef) implies w.twOutcome = Unprocessable
    (some w.twRef and w.twKind = EntityRef and no denotedAnywhere[w])
      implies w.twOutcome = NotFound
    (some w.twRef and w.twKind = EntityRef and some denotedAnywhere[w])
      implies w.twOutcome = Stored
  }
}

check stored_target_crosses_tenant_under_unscoped_lookup {
  ValidationWithUnscopedLookup implies StoredTargetIsTenantLocal
} for 5
```

Create `knovas-software/models/alloy/mutants/kg_attribute_target_type__foreign_is_forbidden.als`:

```alloy
/*
 * MUTANT — expected outcome: counterexample.
 *
 * Shadows: data_plane/kg_attribute_target_type.als :: foreign_and_missing_answer_alike
 * (GI-GRAPH-11)
 * Simulated bug: the helper distinguishes "exists but belongs to another
 * tenant" (403) from "unknown" (404) — the well-meant, wrong message that
 * turns the schema route into an existence oracle for other tenants' type
 * ids.
 */
module semantix/mutants/kg_attribute_target_type__foreign_is_forbidden

open semantix/data_plane/kg_attribute_target_type

one sig Forbidden extends Outcome {}

pred ValidationWithDistinctForeignAnswer {
  all w: TargetWrite {
    no w.twRef implies w.twOutcome = Stored
    (some w.twRef and w.twKind != EntityRef) implies w.twOutcome = Unprocessable
    (some w.twRef and w.twKind = EntityRef and missing[w]) implies w.twOutcome = NotFound
    (some w.twRef and w.twKind = EntityRef and foreign[w]) implies w.twOutcome = Forbidden
    (some w.twRef and w.twKind = EntityRef and some denoted[w])
      implies w.twOutcome = Stored
  }
}

check oracle_under_distinct_foreign_answer {
  ValidationWithDistinctForeignAnswer implies ForeignAndMissingAnswerAlike
} for 5
```

- [ ] **Step 4: Run the commands and confirm the outcomes**

From `knovas-software/models/alloy/`, the same loop as A0 Step 4 over `data_plane/kg_attribute_target_type.als` (checks `a_stored_target_is_tenant_local`, `only_entity_ref_stores_a_target`, `foreign_and_missing_answer_alike`; runs `witness_mechanism_live`, `witness_breach_expressible`) and the two mutant checks `stored_target_crosses_tenant_under_unscoped_lookup`, `oracle_under_distinct_foreign_answer`.
Expected: three checks with no solution file, two witnesses and both mutants with one. About 1 s each.

- [ ] **Step 5: Register, bind, regenerate, lint**

1. Manifest, under `L2-KNOWLEDGE-GRAPH: alloy_models:` after the A0 block:

   ```yaml
         # SS-315: target_node_type_id on entity_ref schema attributes is
         # tenant-local and a foreign id answers like an unknown one.
         - path: mechanisms/kg_schema_target.als
         - path: data_plane/kg_attribute_target_type.als
           checks:
             - a_stored_target_is_tenant_local
             - only_entity_ref_stores_a_target
             - foreign_and_missing_answer_alike
         - path: mutants/kg_attribute_target_type__cross_tenant_target.als
         - path: mutants/kg_attribute_target_type__foreign_is_forbidden.als
   ```

2. `ci/obligations.yaml`, `exempt:` list — the code this pred mirrors does not exist until A4, so the binding is deferred honestly rather than pointed at an unrelated test:

   ```yaml
     - {pred: mechanisms/kg_schema_target.als::TargetValidationMechanism, reason: bound in plan A4 to test_graph_api.py::TestSchemaAttributeTargetType once _validated_target_type exists}
   ```

3. Regenerate and run: `python3 ci/alloy_driver.py --emit-expected > ci/expected_results.json && python3 ci/alloy_driver.py -j 4` — Expected: exit 0; the lockfile diff is exactly 3 + 2 entries for the component file and 2 for the mutants.
4. Lints from the repo root: `python3 scripts/check_alloy_coverage.py && python3 scripts/check_alloy_obligations.py` — Expected: both exit 0 (`mechanisms/kg_schema_target.als: 0/1 preds covered (1 exempt)`).

- [ ] **Step 6: Catalog, matrices, pins**

1. `Golden_Invariants.md`, row **GI-GRAPH-07**: *Enforced by* append `, `knovas-software/app/src/api/graph_api.py` (`_validated_target_type`), `knovas-software/DB/migrations/20260902_kg_attribute_target_type.sql``; *Tests / Alloy* — after the existing `knowledge_graph_facts.als` link append
   ``, [`data_plane/kg_attribute_target_type.als`](../../../knovas-software/models/alloy/data_plane/kg_attribute_target_type.als) (`a_stored_target_is_tenant_local`, `only_entity_ref_stores_a_target`, `foreign_and_missing_answer_alike`); `test_graph_api.py::TestSchemaAttributeTargetType`, `preconditions/test_kg_attribute_target_type_preconditions.py` ``.
2. `alloy_component_coverage_matrix.md`, after the A0 row:
   `| KG schema attribute target type | `knovas-software/app/src/api/graph_api.py` (`_validated_target_type`), `knovas-software/DB/migrations/20260902_kg_attribute_target_type.sql` | `mechanisms/kg_schema_target.als`, `data_plane/kg_attribute_target_type.als` | GI-GRAPH-07, GI-GRAPH-11 | Covered |`
3. `alloy_pytest_matrix.md`:
   `| `data_plane/kg_attribute_target_type.als` | `a_stored_target_is_tenant_local`, `only_entity_ref_stores_a_target`, `foreign_and_missing_answer_alike` | `app/tests/test_graph_api.py::TestSchemaAttributeTargetType`, `app/tests/preconditions/test_kg_attribute_target_type_preconditions.py` (A4) |`
4. `traceability_map.md`:
   `| KG entity_ref target type (SS-315) | `docs/superpowers/specs/2026-09-02-typed-node-workbench-design.md` §5.2 | `knovas-software/app/src/api/graph_api.py`, `knovas-software/DB/migrations/20260902_kg_attribute_target_type.sql` | `knovas-software/app/tests/test_graph_api.py`, `knovas-software/app/tests/preconditions/` | [`kg_attribute_target_type.als`](../../../knovas-software/models/alloy/data_plane/kg_attribute_target_type.als) |`
5. `test_kg_v1_alloy_pins.py`, `V1_COMMANDS`:
   ```python
       "models/alloy/data_plane/kg_attribute_target_type.als": {
           "a_stored_target_is_tenant_local": "check",
           "only_entity_ref_stores_a_target": "check",
           "foreign_and_missing_answer_alike": "check",
           "witness_mechanism_live": "run",
           "witness_breach_expressible": "run",
       },
   ```
   and `V1_HEADER_GIS`: `"models/alloy/data_plane/kg_attribute_target_type.als": ["GI-GRAPH-07", "GI-GRAPH-11"],`.

Run: `TESTING=true python3 -m pytest tests/alloy_invariants/test_kg_v1_alloy_pins.py -q` — Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add knovas-software/models/alloy/mechanisms/kg_schema_target.als \
        knovas-software/models/alloy/data_plane/kg_attribute_target_type.als \
        knovas-software/models/alloy/mutants/kg_attribute_target_type__cross_tenant_target.als \
        knovas-software/models/alloy/mutants/kg_attribute_target_type__foreign_is_forbidden.als \
        knovas-software/models/alloy/ci/expected_results.json \
        knovas-software/models/alloy/ci/obligations.yaml \
        docs/ModernDocs/alloy/component-manifest.yaml \
        docs/Docs/01_SYSTEM/Golden_Invariants.md \
        docs/Docs/05_TESTS/alloy_component_coverage_matrix.md \
        docs/Docs/05_TESTS/alloy_pytest_matrix.md \
        docs/Docs/05_TESTS/traceability_map.md \
        knovas-software/app/tests/alloy_invariants/test_kg_v1_alloy_pins.py
git commit -m "alloy(graph): entity_ref target type is tenant-local and never an oracle (SS-315)"
```

---

### Task A4: `target_node_type_id` on schema attributes

An `entity_ref` attribute today records that a node references *some* node, not
which kind. The Platform's node picker cannot be filtered without this, and a
type-level expectation ("a Mandat has a responsible Person") cannot be re-read
after it is written.

**Files:**
- Create: `knovas-software/DB/migrations/20260902_kg_attribute_target_type.sql`
- Modify: `knovas-software/DB/init.sql` (the `kg_node_type_attribute` block, ~line 530)
- Create: `knovas-software/app/tests/preconditions/test_kg_attribute_target_type_preconditions.py`
- Modify: `knovas-software/app/src/services/knowledge_graph/repository.py` (`add_attribute` ~line 165, `update_attribute` ~line 198)
- Modify: `knovas-software/app/src/interfaces/IKnowledgeGraphRepository.py` (`add_attribute`, ~line 49)
- Modify: `knovas-software/app/src/api/graph_api.py` (`node_type_schema` ~line 537, `modify_schema_attribute` ~line 582)
- Modify: `knovas-software/app/tests/fixtures/fake_kg_repository.py` (`add_attribute`, ~line 122)
- Test: `knovas-software/app/tests/test_graph_api.py`
- Modify: `knovas-software/models/alloy/ci/obligations.yaml`
- Modify: `docs/Knovas_Developer_Kit/api/Knowledge_Graph_API.md`

**Interfaces:**
- Consumes: nothing from A1/A2.
- Produces:
  - `POST /secured/graph/node-types/<id>/schema` accepts `target_node_type_id` (uuid string or null).
  - `PATCH /…/schema/<aid>` accepts the same; `null` clears it.
  - `GET /…/schema` returns `target_node_type_id` on every attribute.
  - Rejections: `target_node_type_id` with a non-`entity_ref` datatype → **422**, error code `target_type_requires_entity_ref`. A target node type that does not exist in the caller's tenant → **404** with the same body as for an unknown id (never 403, never a distinct "foreign" message).
  - `KnowledgeGraphRepository.add_attribute(..., target_node_type_id=None)` and `update_attribute(client_id, attribute_id, target_node_type_id=...)`.

- [ ] **Step 1: Write the migration**

Create `DB/migrations/20260902_kg_attribute_target_type.sql`:

```sql
-- Target node type for entity_ref schema attributes (SS-315).
--
-- An entity_ref attribute materialises a typed edge, but until now it did not
-- record WHICH type it points at. Without that a client cannot offer a filtered
-- picker, and a type-level expectation ("a Mandat has a responsible Person")
-- cannot be re-read after it is written.
--
-- Nullable on purpose: every existing attribute keeps a null target and behaves
-- exactly as it does today. The composite FK carries client_id so a
-- cross-tenant target is impossible at the DATABASE, matching every other
-- kg_* reference (GI-GRAPH-01/02/07). ON DELETE SET NULL names the one column
-- to null: the plain form nulls every referencing column, and client_id is
-- NOT NULL (PostgreSQL 15+ syntax; local compose runs 15, Azure runs 17).
--
-- Model: models/alloy/data_plane/kg_attribute_target_type.als
-- Design: docs/superpowers/specs/2026-09-02-typed-node-workbench-design.md (5.2)

ALTER TABLE kg_node_type_attribute
    ADD COLUMN IF NOT EXISTS target_node_type_id UUID NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'fk_kg_attribute_target_node_type') THEN
        ALTER TABLE kg_node_type_attribute
            ADD CONSTRAINT fk_kg_attribute_target_node_type
            FOREIGN KEY (target_node_type_id, client_id)
            REFERENCES kg_node_type (id, client_id)
            ON DELETE SET NULL (target_node_type_id);
    END IF;
END $$;

-- Only entity_ref may carry a target. Enforced here as well as in the service
-- so a direct write cannot produce a row the API would have refused.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'ck_kg_attribute_target_is_entity_ref') THEN
        ALTER TABLE kg_node_type_attribute
            ADD CONSTRAINT ck_kg_attribute_target_is_entity_ref
            CHECK (target_node_type_id IS NULL OR datatype = 'entity_ref');
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_kg_attribute_target
    ON kg_node_type_attribute (client_id, target_node_type_id)
    WHERE target_node_type_id IS NOT NULL;
```

Mirror it into the `kg_node_type_attribute` block of `DB/init.sql` so a fresh
database and a migrated one agree: add the column line
`    target_node_type_id UUID NULL,                    -- entity_ref only; SS-315`
after `enum_values`, and inside the same `CREATE TABLE` add the two table
constraints after the existing `FOREIGN KEY (node_type_id, client_id) …` line:

```sql
    CONSTRAINT ck_kg_attribute_target_is_entity_ref
        CHECK (target_node_type_id IS NULL OR datatype = 'entity_ref'),
    CONSTRAINT fk_kg_attribute_target_node_type
        FOREIGN KEY (target_node_type_id, client_id) REFERENCES kg_node_type (id, client_id)
        ON DELETE SET NULL (target_node_type_id)
```

(then the index after the block, identical to the migration).

- [ ] **Step 2: Write the DDL precondition test**

Create `tests/preconditions/test_kg_attribute_target_type_preconditions.py`, following `test_knowledge_graph_preconditions.py`'s DDL pattern:

```python
"""Precondition tests — entity_ref target type (SS-315).

The Alloy mechanism TargetValidationMechanism (mechanisms/kg_schema_target.als)
models the SERVICE. Its second line is the schema: a composite FK that makes a
cross-tenant target impossible at the database, and a CHECK that only
entity_ref carries one. Under TESTING=true there is no live PostgreSQL, so
these are proven against the migration DDL and init.sql themselves.

Model file: data_plane/kg_attribute_target_type.als (GI-GRAPH-07, GI-GRAPH-11)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.precondition,
    pytest.mark.l2("L2-KNOWLEDGE-GRAPH"),
]


def _db_file(*parts: str) -> str:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent.joinpath("DB", *parts)
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    pytest.skip(f"{'/'.join(parts)} not found in this checkout")


def _attribute_ddl(sql: str) -> str:
    match = re.search(
        r"CREATE TABLE IF NOT EXISTS kg_node_type_attribute \((.*?)\n\);", sql, re.DOTALL
    )
    assert match, "no DDL for kg_node_type_attribute"
    return match.group(1)


class TestTargetTypeIsTenantLocalAtTheDatabase:
    """Mechanism: kg_schema_target.als::TargetValidationMechanism, second line.
    The composite FK (target_node_type_id, client_id) → kg_node_type (id,
    client_id) makes a cross-tenant target impossible at the DATABASE."""

    def test_migration_declares_the_composite_fk_nulling_only_the_target(self):
        sql = _db_file("migrations", "20260902_kg_attribute_target_type.sql")
        assert "FOREIGN KEY (target_node_type_id, client_id)" in sql
        assert "REFERENCES kg_node_type (id, client_id)" in sql
        assert "ON DELETE SET NULL (target_node_type_id)" in sql

    def test_init_sql_mirrors_the_composite_fk(self):
        ddl = _attribute_ddl(_db_file("init.sql"))
        assert "FOREIGN KEY (target_node_type_id, client_id) REFERENCES kg_node_type (id, client_id)" in ddl
        assert "ON DELETE SET NULL (target_node_type_id)" in ddl


class TestOnlyEntityRefCarriesATarget:
    """Mechanism: the Unprocessable branch, mirrored as a CHECK so a direct
    write cannot produce a row the API would have refused."""

    def test_migration_declares_the_check(self):
        sql = _db_file("migrations", "20260902_kg_attribute_target_type.sql")
        assert "CHECK (target_node_type_id IS NULL OR datatype = 'entity_ref')" in sql

    def test_init_sql_mirrors_the_check(self):
        ddl = _attribute_ddl(_db_file("init.sql"))
        assert "CHECK (target_node_type_id IS NULL OR datatype = 'entity_ref')" in ddl
```

Run: `TESTING=true python3 -m pytest tests/preconditions/test_kg_attribute_target_type_preconditions.py -q`
Expected: 4 passed (they read the files written in Step 1).

- [ ] **Step 3: Write the failing route tests**

Append to `tests/test_graph_api.py`:

```python
class TestSchemaAttributeTargetType:
    """entity_ref attributes may name the type they point at.

    Alloy: data_plane/kg_attribute_target_type.als (a_stored_target_is_tenant_local,
    only_entity_ref_stores_a_target, foreign_and_missing_answer_alike).
    """

    def _type(self, client, name):
        return client.post("/secured/graph/node-types",
                           json={"name": name}).get_json()["node_type"]["id"]

    def _attribute(self, client, type_id, **body):
        return client.post(f"/secured/graph/node-types/{type_id}/schema",
                           json={"name": "Zustaendig", "datatype": "entity_ref", **body})

    @pytest.mark.alloy_obligation("mechanisms/kg_schema_target.als::TargetValidationMechanism")
    def test_entity_ref_attribute_stores_and_returns_its_target(self, client):
        mandate, person = self._type(client, "Mandate"), self._type(client, "Person")
        created = self._attribute(client, mandate, target_node_type_id=person)
        assert created.status_code == 201
        assert created.get_json()["attribute"]["target_node_type_id"] == person

        listed = client.get(f"/secured/graph/node-types/{mandate}/schema").get_json()
        assert listed["attributes"][0]["target_node_type_id"] == person

    @pytest.mark.alloy_obligation("mechanisms/kg_schema_target.als::TargetValidationMechanism")
    def test_a_target_on_a_text_attribute_is_422(self, client):
        mandate, person = self._type(client, "Mandate"), self._type(client, "Person")
        response = self._attribute(client, mandate, name="Notiz", datatype="text",
                                   target_node_type_id=person)
        assert response.status_code == 422
        assert response.get_json()["error_code"] == "target_type_requires_entity_ref"

    def test_an_unknown_target_is_404(self, client):
        mandate = self._type(client, "Mandate")
        response = self._attribute(client, mandate, target_node_type_id=str(uuid.uuid4()))
        assert response.status_code == 404

    @pytest.mark.alloy_obligation("mechanisms/kg_schema_target.als::TargetValidationMechanism")
    def test_a_foreign_target_answers_exactly_like_an_unknown_one(self, client, env):
        """A distinct message for 'exists but not yours' would be an oracle."""
        repo, *_ = env
        mandate = self._type(client, "Mandate")
        theirs = repo.create_node_type(TENANT_B, name="Theirs")["id"]

        foreign = self._attribute(client, mandate, target_node_type_id=str(theirs))
        unknown = self._attribute(client, mandate, target_node_type_id=str(uuid.uuid4()))

        assert foreign.status_code == unknown.status_code == 404
        assert foreign.get_json() == unknown.get_json()

    def test_a_target_may_be_added_later_by_patch(self, client):
        mandate, person = self._type(client, "Mandate"), self._type(client, "Person")
        attribute = self._attribute(client, mandate).get_json()["attribute"]

        patched = client.patch(
            f"/secured/graph/node-types/{mandate}/schema/{attribute['id']}",
            json={"target_node_type_id": person})

        assert patched.status_code == 200
        assert patched.get_json()["attribute"]["target_node_type_id"] == person

    def test_a_null_target_clears_it_by_patch(self, client):
        mandate, person = self._type(client, "Mandate"), self._type(client, "Person")
        attribute = self._attribute(client, mandate, target_node_type_id=person).get_json()["attribute"]

        patched = client.patch(
            f"/secured/graph/node-types/{mandate}/schema/{attribute['id']}",
            json={"target_node_type_id": None})

        assert patched.get_json()["attribute"]["target_node_type_id"] is None

    def test_an_existing_attribute_without_a_target_still_works(self, client):
        mandate = self._type(client, "Mandate")
        attribute = self._attribute(client, mandate).get_json()["attribute"]
        assert attribute["target_node_type_id"] is None
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `TESTING=true python3 -m pytest tests/test_graph_api.py::TestSchemaAttributeTargetType -v`
Expected: FAIL — `KeyError: 'target_node_type_id'` on the store-and-return, patch-adds, null-clears and existing-attribute tests; the 422 test fails with `assert 201 == 422`; the two 404 tests fail with `assert 201 == 404`. Any other failure means a fixture assumption is off — stop and fix the test, not the route.

- [ ] **Step 5: Widen the repository**

In `src/services/knowledge_graph/repository.py`, change `add_attribute` to
accept and persist the column:

```python
    def add_attribute(self, client_id, node_type_id, name, datatype, required=False,
                      description=None, sort_order=0, enum_values=None,
                      target_node_type_id=None):
        rows = self._query(
            """
            INSERT INTO kg_node_type_attribute
                (client_id, node_type_id, name, datatype, required, description,
                 sort_order, enum_values, target_node_type_id)
            SELECT %s, id, %s, %s, %s, %s, %s, %s, %s
              FROM kg_node_type WHERE client_id = %s AND id = %s
            RETURNING *
            """,
            (client_id, name, datatype, required, description, sort_order,
             Json(enum_values) if enum_values is not None else None,
             target_node_type_id, client_id, node_type_id),
        )
        return self._one(rows)
```

and add `"target_node_type_id"` to the `allowed` set inside `update_attribute`.

In `src/interfaces/IKnowledgeGraphRepository.py`, add `target_node_type_id: Optional[str] = None` to the abstract `add_attribute` signature.

- [ ] **Step 6: Mirror it on the fake repository**

In `tests/fixtures/fake_kg_repository.py`, give `add_attribute` the keyword
`target_node_type_id=None` and store it on the row (`"target_node_type_id": target_node_type_id`), so schema reads in tests carry the key whether or not it was set. The fake's `update_attribute` already applies any field.

- [ ] **Step 7: Validate in the route**

In `src/api/graph_api.py`, add a module-level helper above `node_type_schema`:

```python
def _validated_target_type(repo, client_id, datatype, body, response_service):
    """(target_node_type_id, error_response). Absent or null key -> (None, None).

    Mirrors models/alloy/mechanisms/kg_schema_target.als::TargetValidationMechanism
    branch for branch. A target on a non-entity_ref attribute is 422: the
    caller sent a coherent request that asks for something the model does not
    have, and telling them which field is wrong costs nothing. An unknown
    target is 404, like every other unreachable id on this blueprint — and the
    tenant-scoped get_node_type is what makes a foreign id answer exactly like
    an unknown one (no existence oracle, GI-GRAPH-11).
    """
    if "target_node_type_id" not in body:
        return None, None
    target = body.get("target_node_type_id")
    if target in (None, ""):
        return None, None
    if datatype != "entity_ref":
        return None, response_service.create_error_response(
            "target_node_type_id is only valid on an entity_ref attribute",
            status_code=422, error_code="target_type_requires_entity_ref")
    if not _valid_uuid(str(target)) or repo.get_node_type(client_id, target) is None:
        return None, response_service.create_not_found_response("Node type")
    return str(target), None
```

In the `POST` branch of `node_type_schema`, after the `enum_values` check and
before the `try:` that calls `repo.add_attribute`:

```python
    target, target_err = _validated_target_type(
        repo, client_id, datatype, body, response_service)
    if target_err:
        return target_err
```

and pass `target_node_type_id=target` into `repo.add_attribute(...)`.

In `modify_schema_attribute`'s `PATCH` branch, after `body = _json_body()`, resolve the datatype from the stored attribute — a PATCH does not carry it — and apply the same check; then include the key in `fields` only when it was sent:

```python
    target, target_err = _validated_target_type(
        repo, client_id, attribute.get("datatype"), body, response_service)
    if target_err:
        return target_err
    fields = {k: body[k] for k in ("name", "description", "required", "sort_order",
                                  "enum_values") if k in body}
    if "target_node_type_id" in body:
        fields["target_node_type_id"] = target
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `TESTING=true python3 -m pytest tests/test_graph_api.py::TestSchemaAttributeTargetType -v`
Expected: 7 passed

- [ ] **Step 9: Run the migration against a local database**

From `knovas-software/app/` against the compose PostgreSQL (`MIGRATIONS_DIR` may be set if the layout differs; the CLI reads `DB/migrations/` relative to the repo root by default):

```bash
python3 src/CLI/manage_migrations.py status
python3 src/CLI/manage_migrations.py apply
python3 src/CLI/manage_migrations.py verify
```

Expected: `20260902_kg_attribute_target_type.sql` applies cleanly, `verify` exits 0, and running `apply` a second time is a no-op — every statement is `IF NOT EXISTS` guarded.

- [ ] **Step 10: Run the full graph suite and the preconditions**

Run: `TESTING=true python3 -m pytest tests/test_graph_api.py tests/test_kg_object_acl.py tests/preconditions/test_kg_attribute_target_type_preconditions.py -q`
Expected: all pass.

- [ ] **Step 11: Bind the mechanism and re-lint**

In `ci/obligations.yaml`, delete the `TargetValidationMechanism` line from `exempt:` and add to `obligations:`:

```yaml
  # ── kg_schema_target (SS-315, plan A3/A4) ────────────────────────────────
  - pred: mechanisms/kg_schema_target.als::TargetValidationMechanism
    claim: absent key proceeds; target on a non-entity_ref is 422; unknown OR foreign target is 404 with an identical body; resolved same-tenant target is stored
    code: [app/src/api/graph_api.py, app/src/services/knowledge_graph/repository.py]
    tests:
      - test_graph_api.py::TestSchemaAttributeTargetType::test_entity_ref_attribute_stores_and_returns_its_target
      - test_graph_api.py::TestSchemaAttributeTargetType::test_a_target_on_a_text_attribute_is_422
      - test_graph_api.py::TestSchemaAttributeTargetType::test_a_foreign_target_answers_exactly_like_an_unknown_one
    mutants: [mutants/kg_attribute_target_type__cross_tenant_target.als, mutants/kg_attribute_target_type__foreign_is_forbidden.als]
```

Run (repo root): `python3 scripts/check_alloy_obligations.py` — Expected: exit 0, `mechanisms/kg_schema_target.als: 1/1 preds covered (0 exempt)`, no dangling markers. Then the obligation suite as in A2 Step 6 — Expected: all pass.

- [ ] **Step 12: Document the field**

In `docs/Knovas_Developer_Kit/api/Knowledge_Graph_API.md`, in the schema
attribute section, add `target_node_type_id` to the body and response tables,
stating: valid only when `datatype` is `entity_ref`; `422
target_type_requires_entity_ref` otherwise; `404` for an unknown or foreign
target; `null` clears it on PATCH; null on every attribute created before this
field existed.

- [ ] **Step 13: Commit**

```bash
git add ../../DB/migrations/20260902_kg_attribute_target_type.sql ../../DB/init.sql \
        tests/preconditions/test_kg_attribute_target_type_preconditions.py \
        src/services/knowledge_graph/repository.py src/interfaces/IKnowledgeGraphRepository.py \
        src/api/graph_api.py tests/fixtures/fake_kg_repository.py tests/test_graph_api.py \
        ../models/alloy/ci/obligations.yaml \
        ../../docs/Knovas_Developer_Kit/api/Knowledge_Graph_API.md
git commit -m "feat(graph): target_node_type_id on entity_ref attributes (SS-315)

Tenant-scoped lookup plus a composite FK; a foreign id answers exactly
like an unknown one. Pinned by data_plane/kg_attribute_target_type.als
under GI-GRAPH-07 / GI-GRAPH-11."
```

---

## Verification

From the repo root, the Alloy side exactly as CI runs it:

```bash
./scripts/docker-scripts/run-alloy-ci.sh      # or: bash knovas-software/models/alloy/ci/run_all.sh
python3 scripts/check_alloy_coverage.py
python3 scripts/check_alloy_obligations.py
```

From `knovas-software/app/`:

```bash
TESTING=true python3 -m pytest tests/test_graph_api.py tests/test_kg_object_acl.py \
       tests/test_kg_rbac_routes.py tests/test_kg_rbac_composition.py \
       tests/preconditions/test_kg_attribute_target_type_preconditions.py \
       tests/alloy_invariants/test_kg_v1_alloy_pins.py -q
ARGS=$(python3 ../../scripts/check_alloy_obligations.py --emit-pytest-args | tr '\n' ' ')
TESTING=true python3 -m pytest $ARGS -q
```

Then confirm by hand against the dev tenant, because neither change has run
against a live instance:

1. `GET /secured/graph/nodes/<id>/neighbors?depth=1` — response has **no**
   `edges` key.
2. `…&include_edges=true` — response has `edges`, and every edge's `node_lo`
   and `node_hi` appear in `neighbors` or are the anchor itself.
3. `POST …/schema` with `datatype=text` and a `target_node_type_id` — 422,
   `target_type_requires_entity_ref`.
4. `POST …/schema` with `datatype=entity_ref` and a random uuid — 404.
5. `GET …/schema` — every attribute carries `target_node_type_id`, null for
   pre-existing ones.

## Requirement traceability

| Spec § | Requirement | Task |
| --- | --- | --- |
| §5.1 | Edges in the neighbourhood response | A1, A2 |
| §5.1 | Edges induced on the post-filter set (GI-GRAPH-12) — modelled | A0 (`no_edge_names_a_hidden_node`), A2 step 3 |
| §5.1 | Opt-in flag; `edges` absent when not requested | A2 steps 1, 3 |
| §5.2 | `target_node_type_id`, entity_ref only — modelled | A3 (`only_entity_ref_stores_a_target`), A4 |
| §5.2 | Target is tenant-local; foreign answers like unknown — modelled | A3 (`a_stored_target_is_tenant_local`, `foreign_and_missing_answer_alike`), A4 steps 2, 3, 7 |
| §5.2 | Existing attributes keep a null target | A4 steps 1, 3 |
| §8.5 | Edges only as visible as their least visible endpoint | A0, A2 |
| §10 | Alloy models land before code; obligations bound to pytest | A0, A1 step 8, A2 step 6, A3, A4 step 11 |

## Related

- Design: `docs/superpowers/specs/2026-09-02-typed-node-workbench-design.md`
- Platform plan: `docs/superpowers/plans/2026-09-02-typed-node-workbench-components.md`
- `docs/Docs/01_SYSTEM/Feature_Design_Workflow.md`, `docs/Docs/05_TESTS/Alloy_Unified_Model_Guide.md` §3, §6–§7 — the procedure A0/A3 follow
- `docs/Docs/01_SYSTEM/Golden_Invariants.md` — GI-GRAPH-04, GI-GRAPH-07, GI-GRAPH-11, GI-GRAPH-12
- **Docs inconsistency to raise separately:** GI-GRAPH-11 describes graph topology as deliberately not access-controlled while GI-GRAPH-12 gives nodes and edges a minimum required group, and `graph_access.py`'s module docstring still carries the pre-`20260804_kg_object_acl.sql` text. GI-GRAPH-12 matches the code. Not fixed by this plan; it needs a backend owner's decision.
- **Observation, out of scope:** `kg_nodes.node_type_id` (`DB/init.sql` ~line 563) uses a composite FK with plain `ON DELETE SET NULL`, which nulls `client_id` too; deleting a node type that still has nodes will fail on the NOT NULL. The new FK in A4 uses the column-list form for that reason.
