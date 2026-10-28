# Ansichten (process and spine views): final report

Plan: `docs/superpowers/plans/2026-10-27-process-and-spine-views.md`. Tenant: Client Zero, af639810. Measured 2026-10-27 with a local Platform against the production brain, read-only. Screenshots are in `docs/superpowers/screenshots/2026-10-27-views/`.

## What was built

One page, `/views` ("Ansichten"), linked from the Cortex dock. It has five render kinds plus the definitions page. All of them read only, and only along the paths the definitions declare.

| Step | What | Branch | Live result |
|---|---|---|---|
| 1 | Decluttered ego graph: meta layer off by default, type legend with descriptions, fan-out chips with status breakdown (members paged), dossier panel | `feat/views-step1-ego-graph` | 84 nodes checked (top-40 hubs + one per type): no meta node drawn, never more than 5 of a kind; warm 0.1 s; dossier 1.3 s (the brain's dossier route) |
| 2 | Definition loader: tenant definition nodes → JSON shape → schema validation; pinned fallback; definitions page | `feat/views-step2-definitions` | 7 processes, 4 spines from the tenant, valid; cold 4.1 s (60 reads, cached 10 min), warm 2 ms |
| 3 | TRACE and BOARD from process definitions; missing items and P-rules computed, R-rules from the KB report | `feat/views-step3-trace-board` | BOARD 3.9 s cold / 4 ms warm; TRACE 0.9 s cold / 25 ms warm; board vs `process-completeness.json`: 24 instances, **0 mismatches** |
| 4 | TREE and MATRIX from spine definitions | `feat/views-step4-tree-matrix` | level expansion 2–143 ms; Capability × Component 13 × 52 in 2.7 s cold / 3 ms warm |
| 5 | Saved views: personal and shared, admin-only sharing, audit log | `feat/views-step5-saved-views` | save, reopen, share, update live; the audit log shows created / shared / updated with the actor |
| 6 | Five default shared views for the Knovas tenant | `feat/views-step6-default-views` | fresh session: first view 1.7 s (pays topology + definitions), the other four 128–221 ms, warm 24–50 ms |

### Budgets

| View | Budget | Measured |
|---|---|---|
| TRACE (one instance) | < 2 s | 0.9–1.7 s on first open; 25–50 ms warm |
| BOARD (all feature plans) | < 5 s | 3.9 s with identity off; 0.14 s in a session where the instance facts are already cached |
| TREE level expansion | < 1 s | 2–143 ms |

Three exceptions, stated plainly:

- **First view after a cache expiry:** it pays the topology read, 1.2–1.5 s.
- **Many cold opens in a row exceed the budgets.** Example: 24 traces back to back, of which 6 took over 2 s (max 9.4 s). The views spend at most 300 of the tenant's 600 calls a minute, and a state (status, outcome, key) costs one call per node. The burst of 120 covers a normal session; a sweep drains it, and reads then go at 5 per second. Bulk reads (A7, below) remove this.
- **Identity on:** each call carries its own principal assertion, minted from the database. The first board in a fresh identity session took 5.2 s including the definitions load.

### Decisions recorded in the plan

- **D1, edge index:** one `/edges` and one `/nodes` read per (tenant, subject), held 60 s in process memory. It is never shared and never sent to the browser, and is dropped on logout and certificate change. Every view shows "Stand HH:MM:SS" and a refresh button. Above 100,000 edges or 2 s, a request walks per node instead (configurable).
- **D2, meta detection:** a type-level marker wins. Until Phase P adds one, a type whose schema declares `meta` is decided per node when it has ≤ 50 nodes, and hidden as a whole above that.
- **D3, facts only for drawn nodes:** schemas are read lazily. Chip breakdowns are read only for types with a state attribute, and a TRACE spends at most 48 state reads, current step and context first.
- **D4, rules:** R1–R8 are not re-implemented. Their results come from the KB report, labelled with its time.
- **D5, definitions:** tenant first, pinned copy as fallback. A test fails if a copy diverges from its pin.

## For KnowledgeBase to fix (not patched in the Platform)

### Definitions and the loader

1. **Rework transitions are missing on the tenant.** These pairs exist in `process-definitions.json` but have no `transitions_to` edge:
   - feature lifecycle: approved → brief, brief → idea, verified → implementing
   - decision: reviewed → proposed
   - release: verified → candidate
   - risk review: mitigating → owned

   Each is the reverse of an existing forward edge, so the loader probably deduplicates `transitions_to` by node pair.
2. **The definition nodes do not hold the whole definition.** Missing:
   - the P-rules
   - `governing_fact`
   - step `owner_role`
   - the prose step descriptions (the node says "Step 2 of 10…")
   - the `rework` flag on transitions

   The Platform takes them from the pinned copy and lists them as gaps.
3. **The ProcessStep description is off by one:** it says "Step n of 10" for the feature lifecycle, which has 11 steps.
4. **R-rules have no machine-readable definition.** Exit criteria name R1, R2, R5 and R7, but only P-rules are defined in `process-definitions.json`. The Platform can show their results only from the report.
5. **`process-completeness.json` is not committed** (untracked at 85cb865a), so the Platform's copy cannot be pinned to a commit. Please commit the reports, or name the commit they were generated from.
6. **The FeaturePlan `status` enum differs from the step ids.** The enum is draft/review/approved/…; the steps are idea/brief/…. The mapping lives in the loader (`terms.step_for`), not in the definitions.

### Meta marking (D2)

7. **There is no type-level meta marker yet.** GlossaryTerm (678 nodes) is the only type hidden by type in the live check. Event is mixed (4 of 11 nodes meta), so the planned split into a run-event type and a business Event type is needed. Please confirm the marker will cover Process, ProcessStep, Spine, SpineLevel, GlossaryTerm, Phase, Gate and the run events.

### Data on the tenant

8. **Every `current_step` fact is dated 2026-10-27,** so every plan shows 0 days in its step. The dated history looks reset by the loader's re-run.
9. **Owner values are inconsistent:** `unknown`, `Platform`, `siran`, `Siran`, `ROLE-ENGINE-OWNER`. `unknown` satisfies P-owner.
10. **FEAT-2026-10-05-graph-native-brain is in "approved"** although two commits implement it.
11. **7 of 13 capabilities (CAP-05 … CAP-11) have no software area realising them,** so the product spine is empty below them. This covers all of Cortex and Nexus.
12. **Component has no state attribute** (only `alloy_status`, `docs_status`), so components cannot be coloured by status. Either declare which attribute is a type's state, or add `status`.
13. **Risk nodes carry duplicate open facts** (`likelihood`, `impact`, `status` twice).
14. **Segment ICP-03 is named "Law firms (CH"**: the name is cut off.
15. **`relations.yaml`, `member_of`:** the range lists Function twice.
16. **Unverified spine levels and paths** (market L5, sales B2/B3, onboarding paths) are drawn and stay empty as expected.

## Brain API features that would remove the remaining cost

| Feature | What it removes |
|---|---|
| **A6, server-side bounded traversal:** walk a declared path from a node with hop and node caps | The full `/edges` read (0.8 s, 3.1 MB) per subject per minute. This is the read D1 accepted for now. |
| **`GET /secured/graph/edges?node_id=`** (today the parameter is silently ignored): a defect or a missing filter | The same, for one-hop views |
| **A7, bulk reads:** facts (or chosen fact labels) for many nodes in one call | One call per node for states, keys and owners. This is what drives cold board/trace times and the rate pacing (600 calls/min per tenant, shared by every client). |
| **Aggregation:** counts and state breakdown per (relation, direction, type) around a node | Member reads for fan-out chips, e.g. 980 AlloyChecks on L2-BRAIN-GRAPH-STORE |
| **Selected facts on the node list route** (`?facts=key,status`) | Key reads for the key-prefix filter (L2-/L3- components) and the instance search |
| **BUG-03:** the node detail route omits `description` | The list route as a workaround |
| **Dossier latency** (1.3 s) | Slow side panel |

Observed on 2026-10-27: the export (`GET /secured/graph`) now carries relation names, so BUG-02 looks fixed there. `neighbors?include_edges=true` works (A2 is live), although `knovas_client.graph_neighbors` still says it is not rolled out.

## Step 6b — discovery and navigation (2026-10-28)

**The sparse ego graph of FEAT-2026-10-05-graph-native-brain.** It was not the edge index (6,718 edges, complete), a stale cache, grouping, the meta filter or direction handling. The server returned all neighbours: 10 drawn plus chips for 51 decisions, 24 risks, 22 test runs and 11 components.

The cause was navigation state. The Graph tab's centre (`node`) and the trace's instance (`instance`) were two selections, and `node` stayed in the URL across tabs. Opening the plan in Ablauf and then the Graph tab re-centred on the last centred node, for example RISK-22 from the risk chip's member list. RISK-22's ego graph is the plan alone (the meta nodes are hidden), which is what was seen.

Fixed by one shared selection for every tab, remembered per user in the Platform's `settings` table. Regression tests in `tests/test_views_discovery.py`:
- one chip per (type, relation, direction) above the threshold;
- every neighbour reachable through drawn nodes or paged chips;
- RISK-22 alone shows only the plan.

**Built:**
- **Start panel** ("Startpunkt wählen", also the Graph tab without a selection): tiles per type in spine order, meta types only with "Meta-Ebene zeigen", plus four suggestions: Meine offenen Pläne, Zuletzt geöffnet, Ziele dieses Quartals, Am stärksten vernetzt.
- **"Liste" tab:** one paged table per type (key, name, status, owner, step, last confirmed, edges) with search, sort, filters and the row actions Ablauf / Graph / Baum.
- **One typeahead** over names, keys and descriptions, with suggestions while empty.
- The pinned copies moved to KnowledgeBase master (1a58e068), since Phase P has merged (gate GP). `terms.yaml` was added for the declared key patterns.

**Measured (live, fresh server):**

| Action | Time | Reads |
|---|---|---|
| First search (FEAT-2026-10-05), including topology | 1.6 s | 61 |
| Later searches | 5–324 ms | |
| Start page, first time per user | up to 7 s | ~150, cached 10 min |
| Start page, after that | 2–3 ms | |
| List page of a large type | 1.6 s | 50 |
| List of a small type | 2 ms | |
| Locating a node for Ablauf / Baum | 21–181 ms | |

The start page's first-time cost is a schema per type plus the meta fact of the ~100 nodes of small meta-capable types.

**Limits, stated in the UI:**
- Types with more than 60 nodes whose names do not carry their keys (AlloyCheck, Commit, ComponentContract, Hypothesis, Record, SourceFile) are searched by name only.
- Lists of such types read status and owner per page, so those columns sort and filter within the page.

**For KnowledgeBase (data):**
- Plan-table decisions are named `D-10: …`, but their keys (`FEAT-…/D-10`) are not in their names.
- A plan's Author row reads "Drafted by Claude Code for owner review", which becomes its owner.
- The `curation_status` of facts is never `confirmed` on the tenant, so "Zuletzt bestätigt" stays empty everywhere.
