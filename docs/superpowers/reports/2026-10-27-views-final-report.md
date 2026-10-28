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
- Every fact read in these checks had `curation_status: manual`, none `confirmed`, so "Zuletzt bestätigt" stays empty. Confirmation is not part of the loader's flow yet.

## Step 7 — fix and change from the Platform (2026-10-28)

**What it does.** Users fix missing or wrong links and change plans from the views. KnowledgeBase stays the source of truth, and nothing writes the brain. The Platform reads three KnowledgeBase files:
- the edit map (DEC-0022): where each relation and fact is edited;
- the suggestions (DEC-0025): ranked candidates for each gap;
- the change classes in the process definitions (DEC-0024).

All three are vendored and pinned to KnowledgeBase master 45fc86b5, which merged `client-zero/edit-map`.

**Where a user acts:**
- **TRACE.** Every one-hop artefact is one of three things:
  - fixable here: "+ hinzufügen" opens a picker with the suggestions (rule, score and reason) on top, then a search; every shown link has "falsch / entfernen" (one-line reason required) and "ersetzen";
  - locked, with the edit map's reason and its "fix at source" text;
  - indirect: the path runs through another node, so it is changed there.
- **"Bearbeiten"** opens exactly the template's fields, each with its class. Fields without a route say why.
- **"Lücken"** lists one row per missing artefact or failed KB rule across all instances:
  - the top suggestion is preselected and can be changed (including "andere…");
  - rows are accepted one at a time or many at once;
  - filters by process, step, type and owner.

**The change set:**
- **Collecting.** All fixes and edits collect in a change set, one per user, kept in the Platform database (badge with count).
- **Operations.** Each is one change ("add CAP-02 to what FEAT-… serves"), not a text patch. It is checked when added:
  - routed by the edit map;
  - classified by the instance's own process (taken from its `instance_of` edge, never from the request);
  - permitted;
  - a key of the right form.
- **"Änderungen" (review)** applies every operation to the owning files at one base commit. It shows:
  - each file's diff, grouped by owning file;
  - each operation's class, reason and suggestion;
  - for a substantive change on an instance at or past `on_substantive.from`: the status rollback (approved → `draft` per `status_map`), a decision record with the user's reason, and the rules to pass again;
  - the impact from the edge index.
- **Conflicts.** A file that moved on master since the base shows its diff; "Neu aufsetzen" re-applies the operations on the new head and names any that no longer apply.
- **Submit** opens ONE pull request:
  - a branch at the base, each file written over its base blob;
  - title and body from the change set;
  - an audit entry `plan.changeset.submitted` with who, files, classes, instances and the PR link.
  The affected items then show "Änderung ausstehend: PR #n" until the tenant shows the merged links.
- **Permissions.** `node_grants` as before: minor changes for owner, editor or admin; substantive changes for the instance's owner or an admin.

**Writers and how they were checked.** One writer per edit-map id. Each records the source and key path it was written for. When the pinned edit map names another place, the writer refuses; a test fails on re-vendoring.

Every edit is read back with a port of KnowledgeBase's plan parser and refused unless exactly the target changed. Against all 32 plans, with KnowledgeBase's own parser as the judge: 815 add/remove round trips, 0 differences.

**Measured** (live tenant, preview against a read-only KnowledgeBase checkout; times as cold → warm):

| Action | Time |
|---|---|
| Definitions (first load per user, 76 paced fact reads) | 3.9–6.2 s, then cached 10 min |
| TRACE right after that | 3.2–4.1 s → 25–33 ms |
| Lücken, feature lifecycle (72 rows) | 0.34 s → 25 ms |
| Lücken, all seven processes (81 rows) | 0.17 s → 22 ms (was 32.7 s when it reused the board) |
| Picker (suggestions and search) | 0.19 s → 20 ms |
| Adding an operation | 27–102 ms |
| "Bearbeiten" form | 120–142 ms |
| Review (4 operations, 4 files, rollback, DEC) | 0.2 s |

The cold TRACE is over the 2 s budget only right after the definitions load, because that load drains the rate-limit bucket. This is the bulk-read limit already listed (A7).

**Live run** (tenant af639810). Four operations:
- add a Goal suggestion to the roadmap plan (in brief: substantive, but before the rework point);
- add CAP-02 to graph-native-brain;
- remove one Alloy model (picked as a check);
- set the owner to `PERS-siran`.

The review gave:
- the rollback to `draft`;
- `DEC-0026-2026-10-05-graph-native-brain-change.md`;
- rules R1, R7, P-owner and P-source to pass again;
- impact: 11 components, 49 invariants, 24 risks and 51 decisions;
- four files: the DEC, `plan-links.yaml`, the plan and its `.ai.yaml`.

Submit answered "nur Vorschau", since no token is configured. No pull request was created.

Screenshots: `step7-trace-fixes.png`, `step7-trace-locks.png`, `step7-picker.png`, `step7-gaps.png`, `step7-review.png`, `step7-review-diff.png`, `step7-plan-form.png`.

**Security review findings, fixed:**
- a revision from the browser could reach `git show` as an option;
- the process (and with it the class and the permission) was taken from the request.

### What could not be routed, and edit-map gaps for KnowledgeBase

| Item | What happens in the Platform | What KnowledgeBase could do |
|---|---|---|
| `Decision affects FeaturePlan` | Locked. The edit map routes it to the plan's D-/OD- rows, which define the plan's own decisions, so an existing DEC cannot be linked there. The DEC loader still ignores `affects:` and links every DEC record to FEAT-2026-10-25-client-zero-phase-0. | Read `affects:` from DEC front matter and add it to the edit map as the route for linking a DEC to a plan. |
| `PreMortem examines FeaturePlan`, `Risk mitigated_by FeaturePlan` | Routable (sample.yaml), but PROC-feature-lifecycle gives them no change class, so they are not offered. PreMortem is a brief artefact. | Add both to `change_classes` (or say they are fixed elsewhere). |
| References section, target date | No edit-map entry, no template field. Shown locked with the reason. | Add References to the edit map and a change class; add a target date to the template if plans should have one. |
| Plan edges in `sample.yaml` | FEAT-2026-07-30-company-brain-foundation's serves, motivated_by, justified_by and affects, and FEAT-2026-10-25-client-zero-phase-0's affects and motivated_by, live in sample.yaml. The edit map routes these relations to `plan-links.yaml` and the Affected L2 row, so "entfernen" refuses: the edge is not where the edit map says. | Move them to their routed sources, or list sample.yaml as a second source. |
| `change_classes` on the tenant | The loader writes status_map, transitions, governing_fact and the P-rules as Process facts, but not change_classes, and not the step descriptions. Both come from the pinned copy, listed as gaps. | Write `change_classes` (and the descriptions) as Process and ProcessStep facts. |
| Rules to pass again | Not declared. The Platform takes the exit criteria from the rework step up to the instance's step. | Declare them in `on_substantive`, if they differ. |
| Edit-map sources | Written two ways: `client-zero/…` and `docs/ModernDocs/…`. `key_path` is prose, so the Platform pins its writers to the exact strings. | One path form; a machine-readable key path next to the prose. |
| Alloy model paths | Matched exactly after stripping a `models/alloy/` prefix. Markdown links (`../../KnowledgeBase/…/x.als`) and bare names (`doc_field_commit.als` in graph-native-brain) never link to a check. | Normalise `../` prefixes and resolve bare names in `providers/alloy.py`. |
| Invalid sidecar | `2026-09-06-billing-non-repudiation.ai.yaml` is not valid YAML (`{dev,prod}` in a flow mapping), so the loader silently skips its golden_invariants. | Quote the value; fail the loader on an unreadable sidecar. |
| Owner keys vs accounts | Owners are now `ROLE-*` / `PERS-*` keys (DEC-0020), but no Platform account carries one. "Instance owner" in the permissions therefore means the `node_grants` owner. | A person key on the Platform user, or role holders, so the brain's owner can be the one who may make substantive changes. |
| DEC numbers | The next number is computed from master at the base (DEC-0026 now), so a KnowledgeBase branch open at the same time can take the same number. | Allocate at merge, or check for duplicates in CI. |
| Other processes | Writers exist for the feature lifecycle only: plan file, sidecar, plan-links.yaml and sample.yaml edges. seed.yaml, experiments.yaml, campaigns, sales, releases and DEC/ADR front matter are listed by `writers.unroutable_report()` (59 entries) and shown locked. | (Platform: next writers, feature plans first as asked.) |

**For the user:**
- **Token.** A real pull request needs a least-privilege token (contents and pull requests on KnowledgeBase only), set as `KB_GITHUB_TOKEN` or `views.change_requests.github_token_file`.
- **Accounts.** The views need accounts (identity on).
