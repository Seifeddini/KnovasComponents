# Typed-node workbench — design

**Date:** 2026-09-02
**Status:** approved design — planned in
`docs/superpowers/plans/2026-09-02-typed-node-workbench-*.md`. Revised
2026-09-04: dependency state corrected (§2.3, §6.4), formal models added
(§5.1, §5.2, §6.5, §10), dangling references removed (§2.3, §12).
**Jira:** SS-315 *Platform Projekt und Mandantenmanagement* (Epic)
**Covers:** admin-defined node types and their field schemas; connection fields
between types; a schema-driven creation form; a single searchable list →
immediate-neighbourhood graph → field reader surface; per-user editor grants.
**Repositories:** `KnovasComponents` (customer-hosted Platform) and
`KnowledgeBase` (Knovas backend). A copy of this file lives in both.

---

## 1 · Problem

The request, in the owner's words:

> A admin should be able to define NodeTypes. Each NodeType can then be assigned
> a view, where users can create a new entity of this nodetype based on fields
> that the admin has defined for this nodetype. Certain fields can also be
> connections to other nodetypes. Users must then be able to create new such
> nodetypes, assign other users as editors (also nodetypes) and edit/work with
> it. For all these nodetypes there should be one view that is a list which is
> searchable, as soon as one node is selected, the immediate-neighborhood graph
> opens. It should also be able to view and read the content of this node
> (fields).

Two phrases were clarified with the owner before this design was written:

- *"create new such nodetypes"* means **nodes of those types**. Type definition
  is admin-only; entity creation is open to any user (§6.2).
- *"assign other users as editors (also nodetypes)"* — the editor relation is a
  Platform-side grant against a Platform user, not a graph node. The
  parenthetical was not carried forward; see §2.4 for why a graph-native editor
  relation cannot enforce anything.

And the constraint that shapes the whole design, given when the scope question
was put:

> The implementation should be independent of the node-type. It should be
> generally applicable.

That constraint is the design. Everything below follows from it.

## 2 · What already exists

Establishing this precisely matters, because most of the machinery is built and
the temptation is to rebuild rather than reach.

### 2.1 KnowledgeBase — the Knowledge Graph API is complete

`knovas-software/app/src/api/graph_api.py` serves ~36 routes under
`/secured/graph/*`, behind NGINX mTLS, gated by `KNOWLEDGE_GRAPH_ENABLED`.

| Capability | Where |
| --- | --- |
| Node types, CRUD | `POST/GET /secured/graph/node-types`, `PATCH/DELETE /…/<id>` |
| Field definitions | `kg_node_type_attribute`: `name`, `datatype ∈ {text, date, money, enum, entity_ref}`, `required`, `sort_order`, `enum_values`, `deprecated_at` |
| Field schema, CRUD | `GET/POST /…/node-types/<id>/schema`, `PATCH/DELETE /…/schema/<aid>` |
| Connection fields | `entity_ref` — materialises a typed edge (`kg_edges.edge_source='fact_derived'`) |
| Nodes, CRUD | `POST/GET /…/nodes`, `GET/PATCH/DELETE /…/nodes/<id>` |
| Field values | `kg_node_fact` + `GET/POST /…/nodes/<id>/facts`, `PATCH/DELETE /…/facts/<fid>` |
| List + search | `GET /…/nodes?node_type_id=&q=` — `q` is `ILIKE '%…%'` on `name` |
| Neighbourhood | `GET /…/nodes/<id>/neighbors?depth=1..3` (GI-GRAPH-04 cap) |
| Per-object ACL | `access_group_ids` / `acl_reader_ids` / `acl_epoch` on `kg_nodes`, `kg_edges`, `kg_node_fact`, `kg_category`, `kg_tag` (migration `20260804_kg_object_acl.sql`, GI-GRAPH-12) |

### 2.2 KnovasComponents — the Platform reaches a fraction of it

On `main`: the Cortex screen (`ontology.html`, `ontology.js` — 1654 lines,
cytoscape already vendored) renders a whole-graph-first view with an entity
drawer and a document drawer. `GraphOntologySource` (`src/ontology_graph.py`)
serves it from `/secured/graph` behind a TTL cache.
`knovas_client.py` carries 21 graph methods.

A client can create a node type and a node — **by name only**. There is no way
to declare that a type has fields, no way to record a field value, and no way
to read one back.

### 2.3 Adjacent work in flight

| Branch | State | Relevance |
| --- | --- | --- |
| `design/matters-and-typed-nodes` | local only — **not on `origin`** of either repository | Its backend additions and client/codec layers were the starting point for §5 and §7.0; everything reused is restated in full in the two plans, so nothing here depends on reading it. Its Mandat page, intake, chronology and argument dossier are **superseded** — see §3.2. |
| `feat/section-b-buildout` | **partially merged**: PR #7 landed `identity/passwords.py` and the ingestion compiler; the identity stack (`users`, `roles`, `sessions`, `principal`, `IdentityGate`, admin People console, `platform-db`) sits in 24 unmerged commits | **Hard dependency** of the editor half (§6). |
| `feat/admin-document-rbac` | design + plan, unmerged; carries the same identity stack in its own 20 commits, diverged from section-b | Document-level ACL console. Adjacent, not a dependency — but one of the two branches must merge and the other rebase before §6 starts. |

### 2.4 Two corrections to the record

Both were discovered by reading past module docstrings into the code, and both
change the design. Recording them so the next reader does not repeat the error.

1. **Graph topology *is* access-controlled.** The module docstring of
   `services/knowledge_graph/graph_access.py` states that nodes, edges and
   schema attributes "are tenant-level organisational metadata and are not
   access-controlled", and that controlling them "would need a second, parallel
   permission model … one nobody has asked for". That text predates
   `20260804_kg_object_acl.sql`, which gave exactly those objects the same
   `(access_group_ids, acl_reader_ids, acl_epoch)` triple documents carry.
   `GraphAccessGuard.object_is_visible`, `filter_objects` and `filter_edges`
   enforce it; `PATCH /nodes/<id>` accepts `required_groups`. It is catalogued
   as **GI-GRAPH-12**, Alloy-modelled and CI-gated, "code landed".

   `Golden_Invariants.md` is internally inconsistent on this point:
   **GI-GRAPH-11** still describes topology as deliberately uncontrolled while
   **GI-GRAPH-12** gives it an ACL. GI-GRAPH-12 is the later entry and matches
   the code. Reconciling the two entries is out of scope here but should be
   raised with the backend owners.

2. **That ACL is the wrong axis for "editors".** It is *group-based read*
   control. The backend has no user concept whatsoever — a principal is an mTLS
   tenant plus a list of asserted group ids, and `principal_resolver.py` is
   explicit that those groups are not cryptographically bound to an end user.
   Further, a caller who may *see* a node may *edit* it: there is no
   reader/editor split anywhere in `graph_api.py`.

   So per-user editor grants cannot come from the backend as it stands, and the
   two systems compose on different axes rather than competing:

   > **KnowledgeBase decides who may *see* a node, by group.
   > KnovasComponents decides who may *write* it, by user.**

## 3 · Approach

### 3.1 One surface, generated from the schema

There is exactly one node screen and exactly one creation form, and neither
contains the name of any node type. Both are generated at runtime from
`GET /secured/graph/node-types/<id>/schema`. Adding a "Mandat" type is
data entry by an administrator, not a deployment.

The test for every change proposed against this design: **if it would need a
code change to support a new node type, it is wrong.**

### 3.2 What this supersedes

`design/matters-and-typed-nodes` §7.3–§7.7 specify a Mandat page, an intake
flow, a chronology and an argument dossier as distinct, type-aware surfaces.
Under the constraint in §1 those cannot be built as written: each one hardcodes
a type name and a field vocabulary.

They are not lost. A "Mandat page" under this design is the generic node page
displaying a node whose type happens to be Mandat, with the fields an admin
defined on it. Chronology, dossiers and trust rollups become *rendering
capabilities of the generic page* — a later slice adds "render `date` fields as
a timeline" once, and every type with date fields gets a timeline.

§5 (backend additions) and §6 (client, codec and composer layers) of that
design are reused as written, with the amendments in §5.1 below.

### 3.3 Graph-mode only

Every capability here exists only when `ONTOLOGY_SOURCE=graph`. The fixture
source (`ontology_store.py`) freezes at its current feature set so the shipped
Cortex demo keeps working, and new surfaces render an explicit
"Wissensnetz-Modus erforderlich" state in fixture mode — never a 500, never
invented data. This continues the decision taken in the matters design §4.

## 4 · Scope

**In scope**

| # | Requirement | Delivered as |
| --- | --- | --- |
| 1 | Admin defines node types and their fields | Typ-Werkstatt (§7.1) |
| 2 | Users create entities from those fields | Schema-driven form (§7.2) |
| 3 | Fields that are connections to other types | `entity_ref` + `target_node_type_id` (§5.2, §7.2) |
| 4 | Assign other users as editors | `node_grants` + route guard (§6) |
| 5 | One searchable list of all nodes | Workbench list pane (§7.3) |
| 6 | Immediate-neighbourhood graph on selection | `include_edges` + graph pane (§5.1, §7.4) |
| 7 | Read a node's field content | Field reader pane (§7.5) |

**Out of scope, deliberately**

- Trust tiers, evidence, contradictions and completeness reports. All exist in
  the API; all render onto the generic node page in a later slice.
- Matter-specific surfaces (chronology, dossiers, intake) — see §3.2.
- Full-text search across *field values*. `q` is `ILIKE` on node `name` only
  (`repository.py:258`). Searching inside facts needs a backend change and is
  named here rather than faked.
- PMS synchronisation (C4), unchanged from the matters design.

## 5 · KnowledgeBase slice

Two changes. Both are small; one is new to this design.

### 5.1 `include_edges` on the neighbours route — **new**

**Changed route:** `GET /secured/graph/nodes/<id>/neighbors?depth=N&include_edges=true`

`repo.neighbors()` returns neighbour *nodes* carrying a hop count and **no
edges** (`repository.py:345`). A neighbourhood graph cannot be drawn from that:
the client would know which nodes are nearby but not what connects them, nor
with which relation. The alternative — having the Platform derive edges from its
cached topology export — was rejected: it does not scale past a tenant whose
full export is large, and it would put a second, Platform-side implementation of
edge visibility next to the authoritative one.

Response gains an `edges` array. Implementation shape:

```python
rows = guard.filter_objects(principal, repo.neighbors(client_id, node_id,
                                                      depth=applied_depth))
payload = {"neighbors": _serialize(rows), "depth_applied": applied_depth,
           "depth_cap": 3, "truncated": requested_depth > 3}
if request.args.get("include_edges", "false").lower() == "true":
    visible_ids = {str(r["id"]) for r in rows} | {str(node_id)}
    payload["edges"] = _serialize(guard.filter_edges(
        principal, repo.neighbor_edges(client_id, visible_ids), visible_ids))
```

plus `KnowledgeGraphRepository.neighbor_edges(client_id, node_ids)` — one
indexed select over `kg_edges` where **both** endpoints are in the set.

**The non-negotiable constraint.** Edges are induced on the **post-filter** node
set, never on the raw walk. An edge built from the walk would name an endpoint
the caller cannot see, disclosing both that the node exists and that it is
connected to something visible — the graph shape leaking around the node ACL.
This is the same reasoning as the existing comment on that route ("filter the
result, not the walk") and it is already the stated rule of
`GraphAccessGuard.filter_edges`.

**No new Golden Invariant, one new Alloy model.** GI-GRAPH-12 already reads
"an edge is only as visible as its least visible endpoint node" and is CI-gated
through `test_kg_object_acl.py`. What no existing model pins is the part this
route adds: *which node set the edges are induced on*. `kg_object_acl_assignment.als`
proves the visibility closure and assignment dominance; it says nothing about
induction. So the backend plan lands `mechanisms/kg_neighborhood.als` +
`data_plane/kg_neighborhood_edges.als` (checks `no_edge_names_a_hidden_node`,
`every_returned_edge_is_itself_visible`, `induction_is_complete`, …) with two
mutants — one that induces on the raw walk, one that skips the edge's own
verdict — under GI-GRAPH-12's existing row, before the route is written
(Task A0). Tests extend `test_kg_object_acl.py` (`TestNeighborhoodEdges`) and
carry `@pytest.mark.alloy_obligation` markers bound in `ci/obligations.yaml`.

**Opt-in, not always-on.** The flag defaults to `false` so callers that only
need neighbour identity do not pay for the second query. `graph_neighbors` in
`knovas_client.py` currently has **zero callers**, so widening its return from a
bare list to `{neighbors, edges}` breaks nothing.

### 5.2 `target_node_type_id` on schema attributes — **carried over**

Unchanged from the matters design §5.2. `POST|PATCH
/secured/graph/node-types/<id>/schema` accepts `target_node_type_id`, valid only
when `datatype == "entity_ref"`, validated same-tenant and returned by the
schema `GET`. Requires a migration; existing attributes keep a null target and
behave exactly as today.

Without it, a connection field records that "this node references some node",
not *which kind* — so the node picker in the creation form cannot be filtered
and a type-level expectation cannot be re-read. Requirement 3 in §4 is only
half-delivered until this lands.

The id is a client-supplied reference across the tenant wall, so it gets a
model before the code (backend Task A3): `data_plane/kg_attribute_target_type.als`
under GI-GRAPH-07 (schema attribute rows are tenant-scoped) and GI-GRAPH-11
(a foreign target answers 404 exactly like an unknown one — never a distinct
"exists but not yours"). Mutants: an unscoped lookup, and a 403 for the
foreign case. The composite foreign key is the database's second line and is
pinned by a DDL precondition test.

## 6 · Editor grants — KnovasComponents

### 6.1 Storage

New migration `src/identity/migrations/0002_node_grants.sql`:

```sql
CREATE TABLE IF NOT EXISTS node_grants (
    node_id    UUID        NOT NULL,          -- a KnowledgeBase kg_nodes id
    user_id    UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role       VARCHAR(8)  NOT NULL CHECK (role IN ('owner', 'editor')),
    granted_by UUID        NULL REFERENCES users(id) ON DELETE SET NULL,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (node_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_node_grants_user ON node_grants (user_id);
```

`node_id` is deliberately **not** a foreign key: it names a row in a different
database, on the other side of the mTLS boundary. The Platform therefore cannot
guarantee referential integrity and must not pretend to — a grant whose node has
been deleted is dead data, cleaned by a periodic reconciliation, never by a
constraint that cannot exist.

### 6.2 Rules

| Action | Who |
| --- | --- |
| Define a node type or edit its schema | platform role `admin` only |
| Create a node | any authenticated user; the creator is written as `owner` |
| Edit a node's name, facts, edges | `owner` or `editor` of that node, or `admin` |
| Grant or revoke `editor` | `owner` of that node, or `admin` |
| Transfer `owner` | `admin` only |
| Read | governed by the **backend** ACL (§2.4), not by `node_grants` |

Read is deliberately absent from this table's first five rows. The Platform does
not implement a second read model; it surfaces the node's `required_groups` and
lets `GraphAccessGuard` decide. Two permission systems answering the same
question is the failure mode this design is avoiding.

### 6.3 Honest limits

`node_grants` is enforced by the Platform's own routes. Anything holding the
tenant certificate and calling `/secured/graph/*` directly bypasses it entirely.
That is not a new weakness introduced here — it is already true of every
Platform permission, and `principal_resolver.py` states the same boundary for
RBAC itself: the model "does not protect against a compromised client backend,
which already holds the tenant certificate and can read everything anyway."

It must be described to buyers as what it is: a control over who may edit
through the product, not a cryptographic guarantee.

### 6.4 Dependency

The identity stack must be on the branch before any of §6 ships. On `main`
the Platform still authenticates with one shared company login
(`web.login.username` / `web.login.password` in `src/web_interface/app.py`,
`create_app`) and therefore has no "other users" to grant anything to. The
stack is written and lives, unmerged, on both `feat/section-b-buildout` and
`feat/admin-document-rbac` (§2.3); merging one and rebasing the other is a
review task, not new construction, but it gates the editor half. The Platform
plan's C0 (the formal model) and B1–B4 do not wait for it.

### 6.5 Formal model

`node_grants` is a new permission model, so it is modelled before the store is
written (Platform Task C0), in the same idiom as
`KnowledgeBase/knovas-software/models/alloy/` and with the same headless
driver and pinned lockfile — the Platform repository's first Alloy tree.
`models/alloy/node_grants.als` pins the table of §6.2 as mechanisms and checks
what erodes under delivery pressure: an editor never delegates
(`an_editor_cannot_delegate`), "who may grant?" has one answer per node
(`who_may_delegate_is_unambiguous`), a reader without any grant is still served
a backend-visible node (`grants_never_narrow_reads` — rule 4 of §8), and an
admin can always repair a grant-less node. `node_grants_lifecycle.als` pins
that a revoke removes editor rows only, so the owner survives it, and that the
creator owns the new node. Four mutants — editor delegates, two owners, reads
narrowed by grants, revoke ignoring the role — each produce a counterexample.

## 7 · KnovasComponents slice — layers and surfaces

### 7.0 Layers

| Layer | File | Job |
| --- | --- | --- |
| Codecs | `src/graph_model.py` *(new)* | The five datatypes, encode and decode: `text→str`, `date→{value, precision}`, `money→{amount, currency}` (ISO 4217), `enum→member of enum_values`, `entity_ref→{node_id}`. Pure, no I/O. |
| Client | `src/knovas_client.py` *(extend)* | `graph_schema`, `graph_update_schema_attribute`, `graph_update_node_type`, `graph_update_node`, facts list/create/update/delete, `graph_neighbors(…, include_edges)`. Switch `graph_nodes()` to the server-side `node_type_id`/`q` filters instead of filtering the whole export in Python. Rename `graph_delete_schema_attribute` → `graph_deprecate_schema_attribute`: the server soft-deprecates, and the present name describes an operation the API does not perform. |
| Errors | `src/knovas_client.py` | `GraphError(status, error_code, message)`, keeping the existing 404-to-`None` discipline. The documented 503s (`filter_embedding_model_stale`, `relevance_calibration_missing`) mean *retry once the operator finishes*; rendering them as failures teaches users to distrust a feature that is working. |
| Composer | `src/graph_workbench.py` *(new)* | One payload per screen: node detail, facts joined to their attribute definitions, the depth-1 neighbourhood, the node's `required_groups`, and its grants. Not one request per pane — the Secure API is rate-limited at roughly 1 req/s. |
| Grants | `src/identity/node_grants.py` *(new)* | §6. Read/write `node_grants`; one `may_write(user, node_id)` predicate every mutating route calls. |

### 7.1 Typ-Werkstatt — the schema editor

Admin-only. Create a node type; then add attributes: name, datatype picker,
`required` toggle, sort order, an enum-value editor when
`datatype == "enum"`, and a target-type picker when `datatype == "entity_ref"`
(§5.2). Reorder by `sort_order`. Rename in place.

Removal is presented as **deprecation**, in those words: "Attribut wird
stillgelegt — bestehende Fakten bleiben erhalten." The API soft-deprecates and
facts keep their `attribute_id`; a UI offering "löschen" would describe an
operation that does not happen.

### 7.2 Schema-driven creation form

Pick a type; the form is generated from its schema. One control per attribute,
chosen by datatype: text input, date picker with a precision selector, amount +
ISO-4217 currency, a select over `enum_values`, and a node picker for
`entity_ref` — filtered to `target_node_type_id` once §5.2 lands, unfiltered
before that.

Save is `POST /nodes` followed by one `POST /nodes/<id>/facts` per filled
attribute. The creator is written into `node_grants` as `owner` in the same
request.

**The form never blocks a save on a missing required attribute.** Schemas are
overlays that make absence visible; they do not gate writes. Blocking would
contradict the API's own model and would empty the completeness report of its
purpose — that report exists to answer "14 of 60 mandates lack a
power-of-attorney date", which requires those 14 to have been creatable.

### 7.3 List pane — searchable, all types

Type filter chips built from `GET /node-types`, plus a search box, hitting
`GET /api/graph/nodes?type=&q=`. Server-side filtering, not client-side over a
full export. Selecting a row drives the other two panes and pushes a URL so a
node is linkable.

Search is `ILIKE` on `name`. The empty state says so rather than implying that
field contents were searched.

### 7.4 Graph pane — immediate neighbourhood

On selection, cytoscape renders the selected node, its depth-1 neighbours, and
the edges among them with relation labels, from the §5.1 response. Reuses the
cytoscape instance, styling and zoom toolbar already built for Cortex.

Clicking a neighbour makes it the selection, so the graph is navigated by
walking it. Depth stays at 1 by default; the API caps at 3 (GI-GRAPH-04) and the
control offers 1–3.

### 7.5 Field reader

The node's facts joined to its type's schema, ordered by `sort_order`. Each row
is a label and a value rendered per datatype — `date` honours `precision`
(`day` as a date, `month` as "März 2026", `year` as "2026"), because a
month-precision fact drawn on a specific day is a fabricated detail in a
document a court may see.

An attribute marked `required` with no fact renders as a **visible gap**, not an
error. `entity_ref` values render as links that select the target node,
which is the same navigation the graph pane offers, from the other direction.

A "Sichtbarkeit" row shows the node's assigned groups (§2.4), editable by an
admin through `required_groups` on `PATCH /nodes/<id>`.

### 7.6 Editors panel

On the node page: the owner, the current editors, a user search over the
Platform's `users` table, grant, and revoke. Visible to all; actionable by the
owner and by `admin` (§6.2).

### 7.7 Routes

New `/api/graph/*` namespace, leaving `/api/ontology/*` and the shipped Cortex
screen untouched. All mutating routes carry CSRF enforcement per the existing
pattern (`tests/test_csrf_enforcement.py`).

```
GET    /api/graph/node-types                      any user
POST   /api/graph/node-types                      admin
GET    /api/graph/node-types/<id>/schema          any user
POST   /api/graph/node-types/<id>/schema          admin
PATCH  /api/graph/node-types/<id>/schema/<aid>    admin
DELETE /api/graph/node-types/<id>/schema/<aid>    admin — deprecate
GET    /api/graph/nodes?type=&q=                  any user
POST   /api/graph/nodes                           any user; creator → owner
GET    /api/graph/nodes/<id>                      composed page payload
PATCH  /api/graph/nodes/<id>                      owner | editor | admin
GET    /api/graph/nodes/<id>/facts                any user
POST   /api/graph/nodes/<id>/facts                owner | editor | admin
PATCH  /api/graph/facts/<fid>                     owner | editor | admin
DELETE /api/graph/facts/<fid>                     owner | editor | admin
GET    /api/graph/nodes/<id>/grants               any user
POST   /api/graph/nodes/<id>/grants               owner | admin
DELETE /api/graph/nodes/<id>/grants/<user_id>     owner | admin
```

"any user" means an authenticated Platform session whose principal the backend
ACL then narrows. It never means unauthenticated.

### 7.8 Screen placement

The workbench is a **new screen**, not a modification of Cortex. Cortex remains
the whole-graph exploration view and the fixture-mode demo.

This is a deliberate deferral, not a permanent split: the two overlap, and once
the workbench carries the field reader and the neighbourhood graph, Cortex is
the likelier one to be absorbed. Merging them now would put the shipped demo at
risk for no gain in this slice.

## 8 · Normative design rules

These hold across every surface above and are the ones most likely to erode
under delivery pressure.

1. **No node type appears in code.** Every form, column and label is generated
   from the schema. A change that needs code to support a new type is wrong.
2. **Schemas never block writes.** Required attributes produce visible gaps and
   completeness entries, never a blocked save.
3. **Deprecate is not delete.** The API soft-deprecates attributes; the UI uses
   that word and explains that existing facts survive.
4. **One read model.** Read visibility is the backend ACL. The Platform adds
   write control only, and never a second answer to "may I see this?".
5. **Edges are only as visible as their least visible endpoint** (GI-GRAPH-12),
   including in the neighbourhood response.

## 9 · Data flow — selecting a node

```
browser                Platform                         KnowledgeBase
   │  GET /api/graph/nodes/<id>
   ├──────────────────────▶ graph_workbench.compose()
   │                          ├─ graph_node(id) ──────────▶ GET /secured/graph/nodes/<id>
   │                          │                             (node + facts, ACL-filtered)
   │                          ├─ graph_schema(type_id) ───▶ GET /…/node-types/<t>/schema
   │                          ├─ graph_neighbors(id,        GET /…/nodes/<id>/neighbors
   │                          │    depth=1,                     ?depth=1&include_edges=true
   │                          │    include_edges=True) ───▶     (nodes + induced edges)
   │                          └─ node_grants.for_node(id)   [local platform-db]
   │  {node, fields[], neighbourhood{nodes,edges}, grants, visibility}
   ◀──────────────────────┤
```

Three backend calls per selection, two of them cacheable by TTL (the schema
changes rarely; the node detail does not change between renders). The composer
joins facts to attribute definitions server-side so the browser never needs the
schema separately.

## 10 · Testing

- **Codec unit tests** for all five datatypes including malformed input
  (`graph_model.py`), following `tests/test_ontology_api.py` conventions.
- **Contract cassettes** recorded once from the dev tenant for every client
  method, per the matters design §9. Backend drift then fails a test instead of
  quietly producing a wrong screen.
- **Grant enforcement**: non-editor write → 403; owner grants then revokes;
  admin override; creator is owner; grant on a deleted node is inert. Each test
  names the Alloy mechanism it discharges (`mayWrite`, `mayGrant`,
  `RevokeMechanism`, `CreateMechanism`), and `tests/test_node_grants_alloy.py`
  pins every model command and outcome.
- **Neighbourhood visibility** (KnowledgeBase): a restricted node adjacent to the
  anchor appears in neither `neighbors` nor `edges`; an edge with one restricted
  endpoint is absent; an edge carrying its own restriction is absent between two
  visible nodes; every visible edge among the returned nodes is present. Extends
  `test_kg_object_acl.py`, bound to `mechanisms/kg_neighborhood.als` through
  `ci/obligations.yaml`.
- **Alloy** (both repositories): checks hold at the recorded scope, every
  witness is satisfiable, every mutant produces a counterexample, and the
  lockfile (`ci/expected_results.json`) matches — `run_all.sh` prints
  `alloy-checks: ok`. KnowledgeBase additionally runs
  `scripts/check_alloy_coverage.py` and `scripts/check_alloy_obligations.py`.
- **CSRF** coverage for every new mutating route.
- **Fixture-mode fallback**: every new surface renders the explicit
  "Wissensnetz-Modus erforderlich" state, never a 500.

## 11 · Risks

| Risk | Mitigation |
| --- | --- |
| The identity stack does not merge (two diverged branches carry it), stranding §6 | Merge one, rebase the other, first; C0 and B1–B4 are independent of it; the §7 surfaces except 7.6 ship regardless |
| Secure API rate limit (~1 req/s) versus a three-call screen | TTL cache in `graph_workbench.py`; schema and node-type reads cached hardest |
| Graph mode has never run against a live instance | Phase 1 verifies against the dev tenant and records the cassettes before any UI work |
| Search is name-only and users expect field search | Named in the empty state and in §4; a backend change is scoped separately |
| `node_grants.node_id` has no referential integrity | Accepted and documented (§6.1); periodic reconciliation, never a constraint |
| GI-GRAPH-11 / GI-GRAPH-12 contradiction confuses a later reader | Raised with backend owners as a docs fix; §2.4 records which one matches the code |

## 12 · Related

- `design/matters-and-typed-nodes` — the design this grew out of; not on
  `origin` in either repository, so nothing here cites it as a source (§2.3)
- `KnowledgeBase/knovas-software/models/alloy/{mechanisms/kg_neighborhood.als,
  data_plane/kg_neighborhood_edges.als, mechanisms/kg_schema_target.als,
  data_plane/kg_attribute_target_type.als}` — the backend models (plan A0, A3)
- `KnovasPlatform/components/docbridge_integration/models/alloy/{node_grants.als,
  node_grants_lifecycle.als}` — the Platform models (plan C0)
- `docs/superpowers/specs/2026-08-04-wissensnetz-ontology-mvp-design.md` — Cortex MVP
- `docs/superpowers/plans/2026-08-14-section-b-buildout.md` — the identity dependency
- `KnowledgeBase/knovas-software/app/src/api/graph_api.py` — the API
- `KnowledgeBase/docs/Docs/01_SYSTEM/Golden_Invariants.md` — GI-GRAPH-04, -11, -12
