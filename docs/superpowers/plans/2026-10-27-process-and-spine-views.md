# Process and spine views — Platform implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan step by step. Steps use checkbox (`- [ ]`) syntax for tracking. One PR per step.

**Goal:** Replace the unusable raw force graph with views drawn **from the ontology's own definitions**: a decluttered ego graph, TRACE and BOARD per process, TREE and MATRIX per spine, and saved views as Platform configuration.

**Architecture:** A read-only `graph_views` package walks only the paths that KnowledgeBase's process and spine definitions declare, over a per-subject topology index (D1). It reads facts only for nodes that are drawn. A `/api/views/*` blueprint serves JSON. One page, `/views` ("Ansichten"), renders the five render kinds with vanilla JS (Cytoscape for the ego graph, DOM for lanes, columns, trees and grids). Saved views are rows in the Platform database; every change to a shared view lands in `audit_log`.

**Tech Stack:** Python 3.11, Flask, psycopg 3, PostgreSQL 15 (`platform-db`), jsonschema 4.26, pytest, vanilla JS, Cytoscape (vendored).

**Inputs (KnowledgeBase, branch `client-zero/phase-p`, commit `06f2974ee7467a3c13843c11315c7eeaa2653a3c`):** `docs/ModernDocs/client-zero/ontology/generated/{process,spine}-definitions.json` and their README, `ontology/schema/*.schema.json`, `ontology/relations.yaml`, `ontology/descriptions.yaml`, `reports/process-completeness.json`.

**Repository:** `KnovasComponents`. **Base branch:** `feat/cortex-star-view`. **Working directory for all commands:** `KnovasPlatform/components/docbridge_integration/`.

## Problem

The Cortex page draws the raw graph with a force layout. Definition and bookkeeping nodes (GlossaryTerm, Process, ProcessStep, Spine, SpineLevel, Phase, Gate, loader-run Events) look like instances. Hubs drown everything: one component has 980 AlloyChecks attached, and one process node has 175 `instance_of` edges. Nobody can read a feature's progress, a board of plans, or the product structure from it.

## Design

### Principle

Semantics live in the ontology; presentation lives in the Platform. The Platform renders **from** the process and spine definitions, stores only presentation settings, never invents a traversal path, never stores a view in the brain, and never writes to the brain.

### Decisions

**D1 — Topology index from the edge list (decided 2026-10-27).** The API has no edge query bounded to one node (`GET /secured/graph/edges?node_id=` is ignored). The bounded routes `neighbors?include_edges` and `dossier` take ~1.4 s per node. A 2-hop TRACE would then take ~3 s and a BOARD of 24 plans ~30 s, both over budget.

`GET /secured/graph/edges` takes 0.8 s for 6,717 edges and carries relation names. It is **topology only** (ids and relation names, no facts), and the brain filters it per caller (an edge is only as visible as its least visible endpoint). So the Platform:
- reads it once per (tenant, subject), with a 60 s TTL, in one parallel round with the node list (headers: id, name, type, description; no facts) and the node types;
- keeps it in process memory only, never in the browser, never shared between subjects;
- drops it on logout, on certificate change and on refresh.

Every view shows "Stand HH:MM:SS" and a refresh button that bypasses the cache. Every edge read logs edge count, payload size and duration. Above `VIEWS_EDGE_LIMIT` (100,000) edges or `VIEWS_EDGE_BUDGET_SECONDS` (2 s), the request falls back to per-node traversal over `neighbors?include_edges` and the view says so.

A6 (server-side bounded traversal) removes the full edge read; the final report names it.

**D2 — Meta detection (decided 2026-10-27).** A type-level marker in the definitions or ontology files wins and costs no fact read. Phase P is adding this marker and splitting Event into a meta run-event type and a business Event type. Until it lands:
- only types whose schema declares a `meta` attribute can be meta;
- for such types with ≤ `VIEWS_META_EXACT_MAX` (50) nodes, the per-node `meta` fact decides, read under the rate limit;
- larger types are hidden as a whole and labelled "nach Typ ausgeblendet".

Decisions are cached with the index under the same key and TTL. The final report lists the types hidden by type.

**D3 — Facts only for drawn nodes.**
- The node list route (`/nodes?node_type_id=`, ~40 ms) supplies names and descriptions. BUG-03: the single-node route omits the description.
- Facts come from `/nodes/<id>/facts` (~33 ms). They are read only for nodes on screen, in parallel (4 workers), paced under `VIEWS_FACT_RATE_PER_MINUTE` (300, half the tenant's 600/min), and honouring 429 `Retry-After`.
- Fan-out chips beyond the cap load their members **paged** on click (page of 50), never all at once.
- A chip's status breakdown is read only when the type's schema declares a state attribute (`status`, `stage`, `onboarding_state`, `outcome`, `result`) and the chip has at most `VIEWS_CHIP_STATUS_MAX` (60) members.
- Schemas are read lazily, only for the types a view meets, and cached per subject for 10 min. Reading all 59 up front drained the bucket and put the first graph at 6.4 s; read lazily it takes 0.1 s warm.
- The bucket bursts to `VIEWS_FACT_BURST` (120) and refills at the configured rate.
- The dossier panel loads on click.

**D4 — Rules.** The Platform computes missing artefacts (`requires` and required `produces`, followed along their declared paths, alternative counted, `min_count` respected) and P-rules (`fact_present`, `facts_present`, `date_not_past`) the way `scripts/client_zero/process_completeness.py` does. It does **not** re-implement R1–R8. Their results are read from KnowledgeBase's `process-completeness.json` and shown as "aus KB-Regelbericht, Stand <generated_at>". KnowledgeBase stays the single source of truth for rules.

**D5 — Definitions source.**
- **Primary:** the tenant's definition nodes.
  - `Process` facts: `key`, `instance_types`, `owner_role`, `context` (JSON), `pack`, `exists_today`.
  - `ProcessStep` facts: `key`, `order`, `resting`, `requires`/`produces` (JSON), `exit_criteria` (CSV).
  - `has_step` and `transitions_to` edges.
  - `Spine` facts: `levels`, `branches` (JSON).
- The loader reassembles the JSON shape and validates it against the schemas. Definitions are cached 10 min per subject, since 76 fact reads must not run per request.
- **Fallback:** a vendored copy under `src/graph_views/definitions/`, pinned to the KnowledgeBase commit in `SOURCE.json` (path, commit, sha256 per file). A test fails when a file's hash and the recorded hash diverge. Once Phase P merges to KnowledgeBase master (gate GP), the pin moves to master.

**D6 — Fix and change through KnowledgeBase (Step 7, 2026-10-28).**
- **Never a brain write.** A fix or edit becomes an operation in the user's change set (Platform database); one submit opens ONE pull request on KnowledgeBase; the loader updates the tenant after the merge.
- **Routing** comes only from KnowledgeBase's edit map (DEC-0022). Each Platform writer records the source and key path it implements and refuses when the pinned edit map names another place; locked entries show the edit map's reason and fix-at text.
- **Classes** come from the process definition (DEC-0024), for the instance's own process (its `instance_of` edge), never the request's. A substantive change at or past `on_substantive.from` adds the rollback (first status that `status_map` maps to the rework step) and a DEC record with the user's reason.
- **Suggestions** come from `suggestions.json` (DEC-0025), shown with rule, score and reason; the Platform does not invent candidates.
- **Permissions** reuse `node_grants`: minor for owner, editor or admin; substantive for the owner or an admin.
- **Conflicts.** Operations are semantic, so a moved master is a rebase: re-apply on the new head and name what no longer applies.

### Components

| File | Responsibility |
|---|---|
| `src/graph_views/topology.py` | `TopologyIndex` (adjacency by relation and direction, type of node, name, description) and `TopologyCache` (per tenant+subject, TTL, refresh, scale guard, logging) |
| `src/graph_views/facts.py` | `FactReader`: paced, parallel, 429-aware fact reads; per-subject cache |
| `src/graph_views/meta.py` | `MetaClassifier` (D2) |
| `src/graph_views/definitions.py` | load from tenant / vendored JSON, validate against schemas, readable errors |
| `src/graph_views/traverse.py` | `follow(index, start, path, caps)`: declared hops only; depth ≤ `VIEWS_MAX_HOPS` (4), ≤ `VIEWS_MAX_NODES_PER_HOP` (2,000) |
| `src/graph_views/grouping.py` | fan-out grouping: more than N (default 5) neighbours of one type over one relation and direction → one chip with count and status breakdown |
| `src/graph_views/completeness.py` | missing artefacts, P-rules, R-rule results from the KB report |
| `src/graph_views/process_views.py` | TRACE and BOARD |
| `src/graph_views/spine_views.py` | TREE and MATRIX |
| `src/graph_views/ego.py` | decluttered ego graph and dossier |
| `src/graph_views/descriptions.py` | display names and descriptions from the vendored `descriptions.yaml` |
| `src/graph_views/definitions/` | vendored JSON, schemas, `descriptions.yaml`, `process-completeness.json`, `SOURCE.json` |
| `src/web_interface/views_routes.py` | `/api/views/*` blueprint (blueprint-factory shape of `graph_routes.py`) |
| `src/identity/migrations/0005_saved_views.sql`, `src/identity/saved_views.py` | saved views |
| `src/web_interface/templates/views.html`, `static/js/views*.js`, `static/css/views.css` | the page |

### Routes (all GET unless noted; all require a signed-in user and graph mode)

- `/views` — the page.
- `/api/views/graph?node=<id>&meta=0|1&n=5` — ego graph.
- `/api/views/graph/group?node=&relation=&direction=&type=&page=` — paged chip members.
- `/api/views/node/<id>` — dossier.
- `/api/views/definitions` — processes, spines and validation errors. Errors are shown to admins only.
- `/api/views/instances?process=&q=` — instance search by key or name.
- `/api/views/trace?process=&instance=` and `/api/views/board?process=&owner=&capability=&status=`.
- `/api/views/tree?spine=&parent=` and `/api/views/matrix?spine=&rows=<level>&cols=<level>`.
- `/api/views/saved` — GET list, POST create; `/api/views/saved/<id>` — PATCH, DELETE.

Every response carries `as_of` and `timings_ms`, plus `fallback` when D1's guard fired.

### Saved views

A view stores **only**: name, description, audience (`personal` | `shared`), source (`{process: key}` | `{spine: key}`), render kind (`graph` | `trace` | `board` | `tree` | `matrix`), optional default root, grouping threshold, hidden types, filters, sorting, colours.

Personal views belong to one user. Shared views are readable by every signed-in user and editable by admins. Every create, update and delete of a shared view, and every share of a personal view, writes `audit.record(action="view.shared.created|updated|deleted", target_type="saved_view", detail={name, changed fields})`.

Views carry no access rights: what a view shows is still filtered by the brain per caller. That is why no Alloy model accompanies this plan; the grant model it would check lives in the brain.

## Out of scope

- Any change to the Knovas engine or the Brain API; any write to the brain.
- The Cortex star field and the hidden Cortex Cytoscape layer (only a dock link is added).
- Re-implementing R1–R8; editing definitions (they are KnowledgeBase's).
- Persisting graph data in the browser.
- Patching wrong data or directions in the Platform. Those go to the final report for KnowledgeBase.

## Test plan

- **Schema validation:** vendored JSON validates; a broken definition produces a readable error naming the path; the vendored-copy pin test.
- **Path resolution:** `follow` on a fixture graph for out/in hops, multi-hop, type filter, alternative path.
- **Traversal limits:** hop cap, node cap with a `truncated` flag, an undeclared relation is never followed.
- **Topology cache:** keyed by tenant and subject; no cross-subject hit; TTL; refresh bypass; scale guard triggers the fallback.
- **Fan-out grouping:** ≤ N drawn as nodes, > N one chip; status breakdown; paged members.
- **Meta hiding:** marker wins; exact for small meta-capable types; whole-type hiding above the threshold; default off.
- **Missing-item flags:** the fixture reproduces `process-completeness.json` rows for feature-lifecycle instances, `missing` equal per instance; P-rules; R-rule results labelled from the KB report.
- **Saved views:** personal views invisible to others; shared views editable by admins only (403 otherwise); every shared change in `audit_log`; CSRF on writes.
- **Default views:** each of the five default views rendered against the fixture graph.
- **Frontend:** `node --check` on the new scripts; Node `vm` tests for lane, column, chip and matrix rendering (pattern of `tests/test_galaxy_search.py`).
- **Live (measured, not in CI):** `scripts/measure_views.py` renders TRACE, BOARD, TREE and MATRIX against the tenant and prints the times recorded in each PR.

## Steps (one PR each)

- [x] **Step 1 — Ego graph, decluttered.** Topology index, fact reader, meta classifier, grouping, ego and dossier routes, `/views` page with the graph render kind, meta toggle (off), legend with descriptions, chips, dossier panel, Cortex dock link. Done when the Knovas tenant shows no meta node by default and no node draws more than N same-type neighbours.
- [x] **Step 2 — Definition loader.** Tenant definition nodes → JSON shape → schema validation; vendored fallback with pin test; definitions page (read-only; errors for admins). Done when 7 processes and 4 spines load and validate, and an invalid one shows a readable error.
- [x] **Step 3 — TRACE and BOARD.** Done when both render for the feature lifecycle on the tenant within budget (TRACE < 2 s, BOARD < 5 s) and the missing items match `process-completeness.json`.
- [x] **Step 4 — TREE and MATRIX.** Done when the product spine renders as a tree (level expansion < 1 s) and Capability × Component renders within budget.
- [x] **Step 5 — Saved views.** Done when a user can save, reopen and share a view, and the audit log shows the change.
- [x] **Step 6 — Default shared views.** Development cycle, Development board, Management trace, Decisions & risks, Market. Done when all five open from the picker on the tenant within budget.
- [x] **Step 6b — Discovery.** Start tiles in spine order, "Liste", typeahead, one selection for every tab (fixes the sparse ego graph).
- [x] **Step 7 — Fix and change (D6).** TRACE fixes with locks, picker with suggestions, "Lücken", "Bearbeiten", change-set review with diffs, rollback and DEC, conflicts and rebase, one PR, audit. Done when a change set builds against the real plan files and every writer round-trips all plans under KnowledgeBase's parser (815 round trips, 0 differences); a real PR needs the token.

## Related

- KnowledgeBase `docs/ModernDocs/client-zero/ontology/generated/README.md` (how to read a path)
- KnowledgeBase `scripts/client_zero/process_completeness.py` (reference for D4)
- `docs/superpowers/plans/2026-09-02-typed-node-workbench-components.md` (blueprint, store and test patterns)
