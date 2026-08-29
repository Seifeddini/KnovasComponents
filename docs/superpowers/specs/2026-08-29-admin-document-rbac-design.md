# Admin document inventory and folder RBAC — design

**Date:** 2026-08-29
**Status:** approved design (six owner decisions taken, see §2) — planned in
`docs/superpowers/plans/2026-08-29-admin-document-rbac-*.md`
**Covers:** the firm administrator's console view of every uploaded document in
their tenant, per-document ACL editing, folder-level access rules that survive
re-ingest, and the operational switch that makes RBAC enforcing at all.
**Explicitly excludes:** user→group mapping (section B2), ethical walls per
*matter* (B3), the approvals queue (B5), and the ingestion profile console
(KC-IN-*). This design consumes those, it does not rebuild them.
**Repositories:** `KnowledgeBase` (Knovas backend) and `KnovasComponents`
(customer-hosted Platform, RemoteController). A copy of this file lives in both —
`KnowledgeBase/docs/superpowers/specs/` and
`KnovasComponents/docs/superpowers/specs/`.

---

## 1 · Problem

The request: *"make sure the architecture exists so that the admin in
KnovasComponents can view all the uploaded docs and change the RBAC stuff for the
docs or the folders (if included in the doc pointer)."*

Half of it exists and half of it does not, and the halves are not the ones you
would guess.

### 1.1 What exists

`KnowledgeBase` carries a complete, carefully built RBAC engine in
`knovas-software/app/src/services/rbac/`: a group forest with depth and count
limits (`group_tree.py`, `models.py`), a policy evaluator with reader-closure and
domination rules (`access_policy.py`), per-document ACL writes with an explicit
narrow-before-widen ordering (`document_acl_service.py:133`), Weaviate storage
(`weaviate_acl_store.py`), and a stage-1/stage-2 filter builder
(`acl_filter_builder.py`). Four endpoints are live:

| Endpoint | Anchor | Purpose |
|---|---|---|
| `GET/POST /secured/access_groups` | `secure_api.py:3571` | Group tree, create |
| `GET/PATCH/DELETE /secured/access_groups/<id>` | `secure_api.py:3611` | Subtree, rename, delete |
| `GET/PUT /secured/document_access` | `secure_api.py:3645` | Read / replace one document's ACL |
| `GET /secured/documents_by_access` | `secure_api.py:3709` | Pointers readable by one group |

### 1.2 What does not exist

1. **No admin surface anywhere.** `knovas_client.py` in
   `KnovasPlatform/components/docbridge_integration/src/` — 2400+ lines — makes
   **zero** calls to any of those four endpoints. The engine is unreachable from
   the product.

2. **The console shell is not on the mainline.**
   `src/web_interface/admin.py` exists only in the `feat/section-b-buildout`
   worktree, not on `main` and not on `feat/pflichtenheft-d-j`. Its own docstring
   states the intent: *"One tab so far — People. The others (Access groups,
   Walls, Approvals, Ingestion) attach to the same blueprint."* None of those
   four were built.

3. **There is no "list every document" capability.** `documents_by_access`
   answers "which pointers can group X read", returning bare strings — no title,
   no date, no current ACL, no cursor. Worse, documents with *no* group carry the
   `ACL_UNRESTRICTED` sentinel, which is deliberately not a resolvable group
   identifier, so `tree.resolve()` (`document_acl_service.py:265`) rejects it.
   **Unrestricted documents are currently unlistable by any API.** That is the
   majority of every corpus.

4. **No folder-level RBAC.** `set_access_by_pointer` takes exactly one pointer
   and requires the document to already exist. Pointers *are* folder-structured —
   `sync_executor.py:296` builds `{identifier_prefix}/{relative/path}` with
   forward slashes — so the prefix semantics the request asks for are available,
   but nothing consumes them.

5. **The folder-inheritance contract is declared and unimplemented.**
   `RemoteController/contracts/sync_request.schema.json:18` defines
   `sources[].access_groups` with a careful rationale ("documents from a walled
   folder are born walled rather than repaired afterwards"). `grep -rn
   access_groups RemoteController/src/` returns nothing. Every re-sync of a
   walled folder re-ingests its documents unrestricted.

6. **Nothing can switch a tenant to enforcing.** `clients.rbac_enforcement`
   exists (`DB/migrations/20260728_access_groups.sql:48`, `NOT NULL DEFAULT
   'disabled'`) and `principal_resolver.py:135` reads it, but no code path in
   either repository writes it. **RBAC is inert in every deployment today.**

So the engine is real; the architecture around it is not.

---

## 2 · Owner decisions taken

Recorded because each eliminates a materially different design.

| # | Decision | Consequence |
|---|---|---|
| D1 | **Walls bind the admin too.** | The inventory is ACL-filtered by the admin's own principal. "All documents" means all documents that admin may see. Consistent with B3 and with the 404-not-403 rule. |
| D2 | **Folder RBAC = persisted rule + backfill.** | A rule is stored per pointer-prefix, applies at ingest, and optionally backfills the existing corpus. A one-shot bulk apply was rejected because re-sync would reopen every wall. |
| D3 | **A new `GET /secured/documents`.** | One authoritative, document-centric listing rather than extending the group-centric `documents_by_access`. This is also what makes unrestricted documents listable. |
| D4 | **Backend first, console after section-b.** | The `KnowledgeBase` work has no identity dependency and lands immediately. The console tabs land once `feat/section-b-buildout` merges. |
| D5 | **Most restrictive wins** on deduplicated documents. | See §5.4, including the one degenerate case the rule does not itself decide. |
| D6 | **Design for 10M+ documents per tenant.** | This is the decision that rewrote the design. See §4. |

---

## 3 · Dependencies this design consumes

- **`feat/section-b-buildout`** (unmerged) supplies `web_interface/admin.py` and
  its `require_admin` gate, `identity/users.py` with `access_groups_of` /
  `set_access_groups`, and the `user_access_groups` table. The console half
  attaches to that blueprint and duplicates none of it. Per D4 the backend half
  does not wait for it.
- **The shipped RBAC engine** (§1.1). No second permission model is introduced.
  Every new surface resolves a `PrincipalContext` through the existing
  `principal_resolver` and evaluates through the existing
  `AccessPolicyEvaluator`.
- **The ingest path.** `/secured/init_document_transmission` already
  materialises `access_groups` when the body carries them (`secure_api.py:990`).
  Folder rules feed that existing code; they do not replace it.

---

## 4 · The scale decision (D6) — folder ACLs are indirection, not materialisation

This section exists because the obvious design does not survive 10M documents,
and the difference is not a constant factor.

### 4.1 Why the obvious design fails

Retrieval filters on the chunk. `query_two_stage.py:308` composes stage 1 as:

```python
stage1_filter = _and_filters(acl_filter, scope_filter)
```

and `acl_filter_builder.py:66` builds that filter as a `contains_any` over
`SentenceChunk.acl_reader_ids`. Therefore **every ACL change must reach every
chunk of every affected document.** The write path that does this,
`weaviate_acl_store.py:308`, issues one update per chunk:

```python
for obj in objs:
    chunk_collection.data.update(
        uuid=str(obj.uuid),
        properties={PROP_ACL_READER_IDS: list(reader_closure)},
    )
```

There is no batch-update primitive available: `weaviate_batch_writer.py` wraps
`data.insert_many()` and is insert-only. At 10M documents and ~200 chunks each,
re-classifying one large folder is ~2×10⁹ single-object round trips. At a
sustained 2,000 writes/second that is roughly eleven days. A "Save" button cannot
be built on that.

### 4.2 The indirection

**A chunk stores the identity of the folder rule that governed it at ingest, not
that rule's expanded group closure.**

Add one property to `SentenceChunk` and `Document`:

```
acl_folder_id : TEXT     # folder-rule UUID, or ACL_UNRESTRICTED
```

- **Changing a folder's groups becomes one PostgreSQL row write.** Zero Weaviate
  writes. The rule id stamped on the chunks never changes; only the mapping from
  rule id → group set does, and that mapping lives in `folder_acl_rules`.
- **The query filter keeps its shape.** `principal_terms(principal)` gains the
  folder-rule ids whose group set intersects the principal's closure. The stage-1
  filter becomes a third conjunct in the same `_and_filters` call — still
  `contains_any`, still an allow-list, still fail-closed on a missing value
  because the sentinel, never NULL, is what marks "unrestricted".
- **Per-document overrides stay materialised** on `acl_reader_ids` exactly as
  today. Those are single-document operations, the existing path handles them,
  and their cost is already acceptable.

Only two operations still pay per-chunk writes: an explicit per-document
override, and a document physically moving between folders. Both are
single-document. Both already work.

### 4.3 Bounding the principal's term list

The term list grows by the number of folder rules a principal may read. For a
firm with hundreds of walls where a given lawyer sits inside fifty, that is fifty
extra terms — comparable to the group closure already being sent. The design caps
it explicitly, mirroring the structural limits already in `models.py`:

```
FOLDER_RULE_MAX_COUNT      = 10_000   # per tenant
PRINCIPAL_MAX_FILTER_TERMS = 4_096    # groups + folder rules, per query
```

Exceeding `PRINCIPAL_MAX_FILTER_TERMS` is a 400 naming the limit, never a
silently truncated filter — a truncated allow-list is an authorization bypass.

### 4.4 The migration is nearly free *now* and expensive later

`principal_resolver.py:151` defaults every tenant to `disabled`, the column
default is `'disabled'`, and §1.2.6 established that nothing writes `'enforcing'`.
**No wall is live in any deployment.** Adding `acl_folder_id` today costs a schema
addition plus a sentinel backfill, with no correctness risk, because every legacy
row is genuinely unrestricted — which is exactly what `AclBackfillCommand`
already writes.

After the first large tenant goes enforcing, this same change becomes the
two-billion-write migration §4.1 describes. **This ordering is the single
strongest argument for doing the work now rather than after the first enforcing
customer.**

---

## 5 · KnowledgeBase slice

### 5.1 `services/rbac/document_catalog_service.py` (new) — keyset inventory

`GET /secured/documents`, with keyset pagination on `pointer`.

`weaviate_manager.py:561` defines `pointer` as a scalar TEXT property, unique per
Document object (a read-only mirror of `pointers[0]`). It is a total order, so it
is a valid cursor key.

```
GET /secured/documents
    ?after=<pointer>        # exclusive cursor; omit for the first page
    &limit=<n>              # default 100, max 1000
    &prefix=<folder>        # range scan: pointer >= prefix AND pointer < prefix+￿
    &group=<identifier>     # narrow to one group's closure
    &unrestricted=true      # documents carrying the sentinel
    &conflicts=true         # documents parked by §5.4
    &status=active|deleted|archived

→ { "documents": [ { pointer, pointers[], title, current_path, status,
                     access_group_ids[], acl_folder_id, acl_epoch,
                     update_date } ],
    "next_after": "<pointer>|null",
    "total_count": <int>,          # aggregate.over_all, not a page walk
    "truncated": false }
```

Three properties of this shape matter:

- **`offset` is permanently 0.** Sorting by `pointer` and filtering `pointer >
  after` never accumulates offset, so the `QUERY_MAXIMUM_RESULTS: "100000"`
  ceiling (`infra/kubernetes/base/weaviate/weaviate-deployment.yaml:62`) is never
  approached and per-page cost is constant. This is the whole reason the endpoint
  is new rather than an extension of `documents_by_access`, whose
  `list_pointers_by_reader_term` (`weaviate_acl_store.py:251`) issues a single
  unpaged `fetch_objects(limit=limit)`.
- **Folder listing is the same index walk**, expressed as a range on the cursor
  key. No second access path to keep correct.
- **The ACL filter is ANDed in** from
  `acl_filter_builder.build_document_filter(principal)`, per D1. Unrestricted
  documents become listable because the endpoint matches on the reader *term*
  rather than resolving a group identifier through `tree.resolve()`.

### 5.2 `services/rbac/folder_rule_service.py` + `folder_acl_rules` (new)

```sql
CREATE TABLE folder_acl_rules (
    rule_id          UUID PRIMARY KEY,
    tenant_id        UUID NOT NULL,
    pointer_prefix   TEXT NOT NULL,
    access_group_ids UUID[] NOT NULL DEFAULT '{}',
    version          INTEGER NOT NULL DEFAULT 1,
    created_by       TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, pointer_prefix)
);
```

Resolution is **longest-matching prefix**, so `/matters/A/privileged/` overrides
`/matters/`. Versioned and attributed, matching the `ingestion_profiles` pattern
the section-b plan establishes for anything a human edits.

`GET/POST/PATCH/DELETE /secured/folder_rules`, with the same `can_assign`
domination check `document_access` applies (`secure_api.py:3680-3690`), so an
administrator cannot classify a folder into a group they do not dominate.

### 5.3 Ingest wiring

In `secure_api.py:990`, when the init body carries no explicit `access_groups`,
resolve the longest-matching folder rule for the document's pointer and stamp
both `acl_folder_id` and the materialised closure. An explicit `access_groups` in
the body still wins — RemoteController's per-source value (§6.1) is explicit.

### 5.4 Deduplication (D5) — most restrictive wins, with one refusal

Dedup means one Weaviate object can carry several pointers: `pointers` is a
`TEXT_ARRAY` (`weaviate_manager.py:562`) and `weaviate_service.py:787` notes that
"multiple pointers may share one" document under copy-on-write. Identical content
filed in `/matters/A/` and `/matters/B/` is **one** row with **one** ACL, so two
folder rules can collide on it.

Per D5:

1. Collect the rules of every folder the document is filed in.
2. Discard rules with an empty (unrestricted) group set — any non-empty rule
   beats unrestricted.
3. Intersect the reader closures of the rest.

**The degenerate case the rule does not decide:** if that intersection comes out
empty while the individual rules were non-empty, the document would be readable
by nobody, including whoever filed it. Writing that is silent data loss dressed as
security. This design **refuses the write** in that case and parks the document
in a conflicts list surfaced by `GET /secured/documents?conflicts=true` for an
explicit human decision. That preserves the fail-closed intent of D5 without
orphaning documents.

### 5.5 `acl_backfill_jobs` + worker (new)

Backfill is never synchronous. Per §4.2 a folder *rule change* needs no backfill
at all; backfill exists for the narrower cases — adopting rules over a
pre-existing corpus, and per-document override sweeps.

```sql
CREATE TABLE acl_backfill_jobs (
    job_id          UUID PRIMARY KEY,
    tenant_id       UUID NOT NULL,
    pointer_prefix  TEXT,
    last_pointer    TEXT,              -- the resumable keyset cursor
    documents_done  BIGINT NOT NULL DEFAULT 0,
    documents_total BIGINT,
    chunks_written  BIGINT NOT NULL DEFAULT 0,
    state           TEXT NOT NULL DEFAULT 'queued',
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

The worker **streams** through §5.1's cursor rather than materialising a list —
`rbac_commands.py:70` accumulates the whole corpus into `missing_docs` before
writing, which is the second thing that breaks at scale after its offset paging.
It skips documents whose closure is already correct, preserves
`set_access_by_pointer`'s narrow-before-widen ordering per document, and persists
`last_pointer` after each page so a restart resumes rather than restarts.

### 5.6 `PUT /admin/clients/<client_id>/rbac-enforcement` (internal API)

Following the existing shape at `internal_api.py:1525`
(`/admin/clients/<client_id>/query-prefix`). Body
`{"enforcement": "enforcing"|"disabled"}`. Knovas-staff-authenticated, not
customer-facing: turning enforcement on is an onboarding step with a
prerequisite, not a self-serve toggle.

**Precondition, enforced by the endpoint:** switching to `enforcing` is refused
unless an ACL backfill has completed for the tenant. Without it every pre-RBAC
document lacks `acl_reader_ids` and the corpus goes dark — the exact failure
`AclBackfillCommand`'s docstring warns about.

### 5.7 `AclBackfillCommand` — off offset paging

`rbac_commands.py:70-92` is the only tenant-wide walk that exists and it has both
scale defects (offset paging past the 100k ceiling; whole corpus in RAM). Move it
onto the §5.1 cursor and make it stream. It keeps its dry-run default and its
`--apply --confirm-tenant` guard.

---

## 6 · KnovasComponents slice

### 6.1 RemoteController — implement `sources[].access_groups`

The contract exists (`contracts/sync_request.schema.json:18`); the code does not.

- `_WalkTarget` (`sync_executor.py:330-353`) gains `access_groups`, carried from
  the matching `sources[]` entry.
- The `upload_queue` tuple (`sync_executor.py:290`) carries it to the uploader.
- `knovas_uploader.upload_file` adds it to `init_body`
  (`knovas_uploader.py:177-181`) before the
  `POST /secured/init_document_transmission` at `knovas_uploader.py:191`.

Note the interaction with `sequential_subfolders`: `sync_executor.py:355` warns
and uses `sources[0]` only when that mode is on. Per-source groups are therefore
honoured in the normal multi-source path and degrade to the first source's value
in sequential mode — which the plan documents rather than silently changing.

### 6.2 `knovas_client.py` — the missing client methods

None of these exist today: `access_groups()`, `create_access_group()`,
`rename_access_group()`, `delete_access_group()`, `document_access(pointer)`,
`set_document_access(pointer, groups, acting_as)`, `documents(after=, prefix=,
group=, unrestricted=, conflicts=)`, `folder_rules()` plus mutators, and
`backfill_job(job_id)`.

### 6.3 Console — **Documents** tab

Attaches to the `admin.py` blueprint behind `require_admin`. Virtualised list fed
by §5.1's cursor — the screen never holds the corpus, it holds a window.

- Columns: pointer, title, folder, current groups, status, last update.
- Filters: folder prefix, group, unrestricted-only, conflicts-only.
- Row action: edit ACL (per-document override, materialised).
- Multi-select: apply groups to the selection.
- Counts come from `total_count` (a Weaviate aggregate), never from a page walk.

### 6.4 Console — **Access groups** tab

Group tree CRUD over the already-shipped endpoints, plus folder rules: create a
rule on a prefix, see how many documents it governs, and start an optional
backfill with live progress from §5.5. Conflicts from §5.4 surface here as a
"Needs a decision" panel.

---

## 7 · Testing

- **Keyset correctness:** a fixture corpus larger than one page proves no
  document is skipped or repeated across cursor pages, including when a document
  is written mid-walk.
- **The scale claim is asserted, not assumed:** a test pins that the inventory
  path never passes a non-zero `offset` to Weaviate. That is the property keeping
  the 100k ceiling irrelevant, and it is easy to regress.
- **Filter completeness:** extend the existing pytest mirror that asserts the ACL
  filter appears in every stage-1 branch (`acl_filter_builder.py:68-76` documents
  it) to cover the new `acl_folder_id` conjunct.
- **Dedup:** §5.4's three cases — restrictive wins, unrestricted discarded, and
  the empty-intersection refusal — each get a test.
- **Enforcement precondition:** §5.6 refuses `enforcing` without a completed
  backfill.
- **Alloy:** the folder-rule indirection changes what "visible" means, so
  `data_plane/document_acl_filter.als` gains `acl_folder_id` and the
  corresponding `GI-ACCESSROLES-*` invariant is revised. This repository does not
  accept a stray `.als`; the plan carries the coverage-script obligations.

---

## 8 · Open decisions (with stated defaults — the plans are executable as-is)

1. **Folder-rule inheritance depth.** Default: longest-matching prefix only, no
   union with ancestors. A document inherits exactly one rule.
2. **Who may create folder rules.** Default: `admin` role only, subject to the
   existing `can_assign` domination check. Delegating to `ingestion_manager` is a
   one-line change if wanted.
3. **Conflicts-list ownership.** Default: surfaced in the Access groups tab; not
   routed through the B5 approvals queue. Routing it there is strictly additive.
4. **`PRINCIPAL_MAX_FILTER_TERMS = 4096`.** Chosen to sit well under gRPC message
   limits; not measured against a real Weaviate deployment. The plan includes the
   measurement task.
