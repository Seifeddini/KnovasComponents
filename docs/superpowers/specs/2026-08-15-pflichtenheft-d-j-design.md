# Pflichtenheft sections D–H and J — design

**Date:** 2026-08-15
**Status:** proposed design, awaiting owner guidance (see §12) — planned in
`docs/superpowers/plans/2026-08-15-pflichtenheft-d-j-*.md`
**Covers:** every MUST and SHOULD of Pflichtenheft §3 sections D, E, F, G, H, the
cheap NICEs (D5, F8-adjacent "similar matters"), and an offer for section J.
**Explicitly excludes:** section A (paper pack), B (identity — consumed as a
dependency, see §3), C (matters — consumed as a dependency, see §3), C4 / PMS
synchronisation, E2 (Swiss procedural computation — declared out of scope under
E1, §4.1), F10 (federated Swisslex/Weblaw search), F6 tier 2 (searching superseded
text), I (RAG), K, L.
**Repositories:** `KnowledgeBase` (Knovas backend) and `KnovasComponents`
(customer-hosted Platform, RemoteController, Office add-ins). A copy of this file
lives in both — `KnowledgeBase/docs/superpowers/specs/` and
`KnovasComponents/docs/superpowers/specs/`.
**Source:** Knovas Pflichtenheft, 14 August 2026
(<https://claude.ai/code/artifact/a7cc80a6-457f-4a45-81ee-4aad51a33c61>).

---

## 1 · Problem

The Pflichtenheft's own verdict is that "the hard science is done and the moat is
real — what stands between Knovas and the signature is a login screen, a paper
pack, a mailbox connector, and the screen where a lawyer clicks on a Mandat and
finally sees what their firm knows." Sections D–H and J are the middle of that
sentence: they are almost entirely *integration, product surface, and honest
declarations* over machinery that already exists in `KnowledgeBase`.

Reading both repositories on 2026-08-15 confirms the picture and sharpens it in
seven places that shape everything below:

1. **`/secured/query` accepts only `Input`, `scope` and `access_groups`.** There
   is no filter, offset, sort, or metadata on a hit; and the live ingest path
   never writes `title`, `author` or `current_path` — every production `Document`
   is titled `"Untitled Document"` and authored `"System"`
   (`information_object_manager.py:1866`). F3 is therefore not a UI task; it is
   an ingest-contract + Weaviate-schema + query-contract task, and the Platform's
   backlog says so already ("Erst die API, dann die UI").
2. **The relevance gate is off in every overlay and cannot be switched on** for
   hybrid traffic until the fusion-score/cosine mismatch documented at
   `relevance_gate.py:265` is resolved. Scoped search (C3) hard-requires the gate.
   Everything that narrows a search by matter or practice area inherits this
   blocker, so it is planned as a prerequisite, not assumed.
3. **There is no eventing spine of any kind** — no outbox, no webhook, no
   job-status row, no push transport. `RemoteController` is not reachable from
   the internet in most installs. E6 must be pull-first.
4. **The graph is further along than its labels**, but four-eyes is
   *unrepresentable*: fact creation does not record an actor, `PATCH /facts/<id>`
   writes no ledger event, and `confirm` has no precondition. E3 needs a small,
   precise backend change before any UI.
5. **Identity is being built on `feat/section-b-buildout`** in both repos
   (local users, sessions, broker-signed principal assertions,
   `PrincipalContext.subject`). D2, E3, J2 and H2 consume it; this design does
   not invent a second actor model.
6. **RemoteController extracts author, language, created and modified for every
   document and throws them away** at the upload boundary
   (`extract_content.py:50`). D5/F3/F5's ingest-side data exists at zero cost.
7. **The section-C design and plan (2026-08-14)** already establish the Platform's
   graph client, typed model, matter page, chronology, dossier and bootstrap.
   Every graph-facing screen here is built on that blueprint, not beside it.

## 2 · Scope

| ID | Priority | Knovas today | Delivered as | Repo |
| --- | --- | --- | --- | --- |
| D1 party register + dedup | MUST | PARTIAL | identifier kinds + folding + tenant-wide search, duplicate candidates, node merge (§5.8); "Parteien" screen (§6.5) | KB + KC |
| D2 conflicts check, logged as evidence | MUST | MISSING | conflict-check endpoint + immutable record + decisions (§5.9); "Konfliktprüfung" screen + protocol (§6.6) | KB + KC |
| D3 Zefix/UID enrichment | SHOULD | MISSING | Platform-side Zefix client → facts with a generated evidence document (§6.7) | KC |
| D4 lateral-hire conflict import | SHOULD | MISSING | CSV/XLSX batch over D2 (§6.6) | KC |
| D5 expertise location | NICE (cheap) | MISSING | author metadata at ingest + author facet + "Wer kennt sich aus?" (§6.2) | KB + KC |
| E1 declare deadline strategy | MUST | MISSING | declaration: integrate-first (§4.1) + `product-statements.md` | KC docs |
| E3 four-eyes with immutable trail | MUST | PARTIAL | per-attribute confirmation policy enforced server-side, `fact_updated`/`fact_adopted` ledger events (§5.6); Fristen screen (§6.8) | KB + KC |
| E4 AI reads the Verfügung, human confirms | SHOULD | PLANNED | deterministic Swiss-date extractor → `propose_fact` with passage offsets (§5.7); proposal inbox (§6.8) | KB + KC |
| E5 deadlines in Outlook with substitutes | MUST | MISSING | per-user ICS feed with responsible + deputy attendees, PMS via events (§6.8) | KC (+KB events) |
| E6 eventing spine | MUST | MISSING | PostgreSQL event outbox, `GET /secured/events`, webhooks, job status (§5.5); Platform poller + Posteingang (§6.9) | KB + KC |
| F1 OCR accuracy evidence DE/FR/IT | MUST (residual) | LIVE | Italian traineddata + OCR signal + synthetic DE/FR/IT benchmark + on-prem runbook (§7.4) | KC (RC) |
| F2 whole estate: mailbox, XLSX/PPTX, PST | MUST | PARTIAL | Graph mailbox mirror (§7.2), RC-local XLSX/PPTX extractors (§7.3), PST exploder (§7.5) | KC (RC) |
| F3 filters + pagination | MUST | MISSING | ingest `metadata` + Weaviate properties + `filters/limit/offset/sort/facets` (§5.2, §5.3); filter rail (§6.2) | KB + KC |
| F4 firm-scale throughput | MUST | PARTIAL | seat-sized Redis bucket, edge backstop, SLO statement + load evidence (§5.4) | KB (+KC docs) |
| F5 DE/FR/IT/EN retrieval evidenced | MUST | PARTIAL | language-aware ingest/query, FR/IT suites (§5.10) | KB (+RC language) |
| F6 version-aware retrieval | SHOULD | PARTIAL | tier 1: versions API + continuity (§5.3.3); version affordance (§6.3) | KB + KC |
| F7 jump to the hit | SHOULD | PLANNED | `chunk_uuid` + `snippet` on hits (§5.3.1); vendored pdf.js viewer (§6.4) | KB + KC |
| F8 similar documents / matters | SHOULD | MISSING | `POST /secured/documents/<uuid>/similar` (§5.3.4); "Ähnliche Dokumente/Akten" (§6.3) | KB + KC |
| F9 honest empty results | SHOULD | BUILT | gate rollout prerequisite (§5.1); empty state renders gate signals (§6.2) | KB + KC |
| G1 knowledge map on the live graph | MUST | DEMO | graph mode default + honesty badge (§6.10) | KC |
| G2 matter ego graph | MUST | MISSING | ego endpoint (§5.11); "Akten-Kompass" (§6.10) | KB + KC |
| G3 every node answers "why?" | MUST | BUILT | evidence enrichment (§5.11); "Warum?" panel → viewer (§6.10) | KB + KC |
| G4 trust made visible | MUST | BUILT | `trust_chip` macro with scope + signals (§6.10) | KC |
| G5 partner's Monday report | SHOULD | BUILT | report paging + date-precision fix (§5.11); "Berichte" screen (§6.10) | KB + KC |
| G6 week one is not an empty graph | MUST | PLANNED | bulk import endpoint (§5.11); PMS-export CSV wizard (§6.10) + C-plan bootstrap | KB + KC |
| G7 draw on the map | SHOULD | DEMO | type-level Vorgaben in graph mode (§6.10) | KC (needs C-plan A4/A5) |
| G8 tireless junior | SHOULD | BUILT | filters wired to live endpoints incl. 503 states + job polling (§6.10) | KC |
| G9 company-brain honesty | MUST | HYPOTHESIS | capability legend + statements + in-product badges (§4.6, §8) | KC docs |
| H1 fixed-price migration incl. PST | MUST | PARTIAL | PST exploder + migration runbook + index-status verification (§7.5) | KC (RC) |
| H2 Outlook and Word add-ins | MUST | MISSING | `knovas_office_addins` component + Platform filing endpoint (§6.11) | KC |
| H4 tables survive ingestion | SHOULD | LIVE | XLSX sheets as tables (§7.3); table rendering in preview (§6.3) | KC |
| H5 exit as easy as entry | MUST | PARTIAL | NDJSON graph + document exports with scope marker + round-trip test (§5.12); "Exit-Paket" doc (§8) | KB + KC docs |
| H6 Justitia 4.0 readiness | SHOULD | MISSING | declaration (§4.4) | KC docs |
| J1 time-capture strategy | MUST | MISSING | declaration "integrate + journal" (§4.5) | KC docs |
| J2 activity hints | SHOULD | PARTIAL | Platform-local, opt-in "Arbeitstag-Journal" (§6.12) | KC |
| J3 realization reporting | SHOULD | MISSING | CSV export of journal per matter/user for the PMS + dependency statement (§6.12) | KC |
| J4 Swiss invoicing out of scope | SHOULD | LIVE | declaration (§4.5) | KC docs |

Not addressed (stated so the buyer never discovers it): E2, F10, F6 tier 2, C4,
IMAP/EWS mailbox variants (Graph first, §7.2), legacy `.doc`, standalone scanned
images (TIFF/JPG), encrypted files with password supply.

## 3 · Dependencies this design consumes

| Dependency | What we use | State on 2026-08-15 |
| --- | --- | --- |
| Section-C plan `2026-08-14-matters-and-typed-nodes.md` | KB: `GET /secured/graph/nodes/<id>/trust`, `target_node_type_id` on schema attributes. KC: `graph_routes.py` blueprint, `graph_model.py` codecs, `matter_view.py`, `GraphError`, cassette tests, `/api/graph/*`, `/api/matters/*`, matter page, bootstrap | approved, not started |
| Section-B branch `feat/section-b-buildout` (both repos) | KC: `src/identity/` — `IdentityGate.current_user`, `require_authenticated_user`, `PrincipalBroker.assertion_for`, `ApprovalService`, `audit.record`, the `platform-db` PostgreSQL. KB: `PrincipalContext.subject`/`brokered`, `EnforcementMode.BROKERED`, KB-B2-4 "bind `actor_ref` to the verified subject" | partially implemented on branch (identity store, sessions, People tab, assertion verify); B3/B5/KB-B2-4 pending |
| `knovas-extract` (separate repo, out of scope for changes) | `ExtractionResult.metadata` (author/language/created/modified/extra), `content.tables[]`, `use_ocr`/`ocr_language`, the public `dispatch.MIME_REGISTRY` hook | v0.3.0, spec 1.3.0 |

**Actor rule used everywhere below.** The *actor* of an evidentiary record is
`principal.subject` when the request carried a verified broker assertion
(`brokered=True`), otherwise the client-supplied `actor_ref`. Records store
`actor_kind ∈ {subject, client_ref, tenant, system}` so a reader can tell a
verified subject from a caller-asserted reference. Until KB-B2-4 lands, every
"who" in D2/E3 is honestly labelled `client_ref`. Nothing in this design is
blocked on Section B; the identity-dependent *screens* are sequenced after it
(§10).

## 4 · Declarations (the cheap, load-bearing half)

These are product statements a buyer files, and they must be written before
code so the code has something to be honest against. They live in a new
`KnovasComponents/docs/product-statements.md` (§8) with the capability legend.

### 4.1 E1 — deadline strategy: integrate-first

The PMS keeps the calendar of record. Knovas **stores** deadlines as evidenced,
audit-trailed date facts; **proposes** deadlines it reads from incoming
documents, with the source passage attached and never auto-committed; **enforces**
independent second confirmation on the attributes a firm marks four-eyes; and
**hands** confirmed deadlines to the outside world through two channels — an
iCalendar feed each user subscribes to in Outlook, and `graph.fact.*` events an
integrator (or the PMS vendor) consumes. Knovas does **not** compute procedural
deadlines (ZPO/StPO/BGG/SchKG, Gerichtsferien, Zustellfiktion, cantonal
holidays). **E2 is out of scope** and stays with the PMS's Fristen module. Saying
this plainly costs nothing and removes the actuarial risk the Pflichtenheft
warns about.

### 4.2 F4 — the throughput statement

"Sustained 6 queries per minute per licensed seat, burst 18, cluster-wide;
p95 query latency ≤ 3.0 s at 20 concurrent seats on the reference deployment;
measured, published with the load-test artefact." The 12 q/min figure the
Pflichtenheft quotes is a per-tenant default that today lives in an in-process
bucket (multiplied by replicas × workers) and in an *archived* NGINX config —
§5.4 makes the number true before it is promised.

### 4.3 F6 — what "version-aware" means here

Tier 1: a document's version history is listable and every hit says whether it
is the current version and how many predecessors exist. Tier 2 (searching the
text of a superseded version) requires retaining old chunks and vectors — a
data-model change and an index-size multiple — and is **not** in scope. The
Pflichtenheft's "find the executed version, not draft 7 of 12" is met by
`document_status` (draft/final/executed) as a filter (§5.2), not by tier 2.

### 4.4 H6 — Justitia 4.0

Knovas is a knowledge layer, not an e-filing client. Documents received through
justitia.swiss (once the cantonal rollout reaches the firm) enter Knovas the same
way every other document does — through the share, the mailbox connector, or the
Outlook add-in — and carry `source_kind` and `document_type` so they are
findable and filterable. Knovas will not transmit to courts. Status: PLANNED
(statement only; no code).

### 4.5 J1 / J3 / J4 — time capture

**J1 declaration — "integrate + journal":** Knovas does not become a time-capture
product. It (a) keeps a per-user, opt-in, customer-hosted activity journal
(matters opened, documents opened, searches run, with timestamps) that the lawyer
can read back as "gestern: Akte Weber 14:00–16:30" and export; (b) emits the
same activity as CSV for import into the PMS's timesheet; (c) offers the event
spine to a capture partner. The business case is therefore anchored at 4–6 h;
the 15 h ceiling stays an engineering target that requires PMS billing data.
**J3:** realization/write-off reporting needs PMS billing data (C4) — the CSV
export is the substrate, the report is out of scope until C4 exists.
**J4:** QR-Rechnung, VAT, cantonal tariffs stay in the PMS. Stated in writing.

### 4.6 G9 — the capability legend

Customer-facing docs adopt the Pflichtenheft's eight labels (LIVE · BUILT ·
GATED · DEMO · PARTIAL · PLANNED · MISSING · HYPOTHESIS) with an explicit mapping
onto the internal three ([NOW] = LIVE/BUILT, [GATED] = GATED/DEMO/PARTIAL,
[ROADMAP] = PLANNED/MISSING/HYPOTHESIS). Every feature doc in §8 carries a label
per screen. In-product: the sidebar shows a badge when Cortex runs on fixture
data or search runs on test results; gap detection, composed-path conflicts and
drift alerts are **not shown at all** until their gates clear.

## 5 · KnowledgeBase slice

All security-relevant additions follow
`docs/Docs/01_SYSTEM/Feature_Design_Workflow.md`: Alloy model + mutant + a
`GI-*` entry in `Golden_Invariants.md` + a mapped pytest **before**
implementation. Every new `/secured/*` route is mTLS-gated by
`@require_valid_client_certificate`, resolves a `PrincipalContext`, keeps
404-not-403, and is documented in the Developer Kit.

### 5.1 Prerequisite — make the relevance gate enable-able (F9, unlocks C3/F3 scope)

- Every stage-1 hit gains `score_mode ∈ {vector, hybrid, bm25}`; hybrid hits
  keep their fusion score in a new `fusion_score` key and `cosine_similarity` is
  only set when a cosine distance exists.
- `config/relevance_calibration.json` gains per-mode floors under
  `models.<vector_name>.modes.{vector,hybrid,bm25}` (`floor` / `min_score`),
  with the model-level `floor` as fallback; `scripts/relevance/calibrate_gate.py`
  samples backgrounds per mode; the gate picks the floor by `score_mode`.
- `no_strong_matches` is always present in the response (`false` when the gate
  did not run) so SDKs get a stable contract.
- Rollout: dev overlay on (already), prod after per-mode calibration; the
  QualityTests negative-control suite is the acceptance artefact.

### 5.2 Ingest metadata (F3, D5, F5, F6, H2)

`POST /secured/init_document_transmission` accepts an optional `metadata` object:

```jsonc
"metadata": {
  "author": "Dr. A. Muster",              // ≤ 500
  "document_type": "Verfügung",           // ≤ 128, client vocabulary, stored verbatim (NFC, trimmed)
  "language": "de",                       // ISO-639-1/2, ≤ 3 letters, lower-cased
  "document_date": "2026-03-01",          // YYYY-MM-DD (the document's own date, not the ingest date)
  "document_status": "final",             // draft | final | executed | unknown
  "source_kind": "share",                 // share | onedrive | mailbox | pst | upload | addin
  "extra": {"eml:message_id": "<…>"}      // ≤ 16 namespaced keys, values ≤ 256 chars
}
```

Persisted as `transmission_keys.document_metadata JSONB`, threaded through
`IOReceiver.submit_snippet` → `_process_complete_transmission` → the remote-worker
job dict → `IOManager.process_information_object` → `document_data`, and written
to Weaviate: `Document` gains `document_type`, `language`, `document_date`
(DATE), `document_status`, `source_kind`, `extra_json`; `title`, `author`,
`current_path` are finally populated from `title`/`metadata.author`/`path`.
The filterable subset (`author`, `document_type`, `language`, `document_date`,
`document_status`, `source_kind`) is **denormalised onto `SentenceChunk`**
(`Tokenization.FIELD`, `index_filterable=True`, `index_searchable=False`) —
exactly the `kg_*` precedent — because stage 1 searches chunks and a
post-filter would spend the 2 000-chunk budget before filtering. Additive
`_ensure_document_metadata_properties` migrator + a `manage_weaviate.py
backfill-metadata --tenant` CLI (new properties index only newly written
objects). Content-identical re-ingest with new metadata takes a *metadata-update*
branch instead of `noop`. `PATCH /secured/documents/<uuid>/metadata` updates a
visible document's metadata (Document + its chunks) without re-upload.
Matter and practice area are **not** metadata properties: they are the graph
(`scope`) — the same document may be filed under two matters through two
pointers.

### 5.3 Query contract (F3, F7, F8, F6)

#### 5.3.1 Filters, paging, sort, facets, richer hits

```jsonc
POST /secured/query
{
  "Input": "Kündigungsfrist",
  "scope": {...},                                    // matter / practice area, unchanged
  "filters": {"author": ["Muster"], "document_type": ["Vertrag"], "language": ["de","fr"],
              "document_status": ["final","executed"], "source_kind": ["share"],
              "date_from": "2024-01-01", "date_to": "2026-08-15",
              "pointer_prefix": ["winjur/2024-"]},   // all optional; conjunctive; never widen ACL/scope
  "limit": 20,                                       // 1..QUERY_COLBERT_STAGE2_TOP_DOCUMENTS
  "offset": 0,                                       // window over ONE ranked, gated set — not a corpus offset
  "sort": "relevance",                               // relevance | date_desc | date_asc
  "facets": ["author","document_type","language","document_status"]
}
```

Response additions: `total_ranked`, `has_more`, `offset`, `limit`, `sort`,
`facets: {author: [{value, count}], …}` (counted over the ranked, ACL-filtered
pool — cheap and leak-free; documented as such), per hit `title`, `author`,
`document_type`, `language`, `document_date`, `document_status`, `source_kind`,
`has_versions`, `version_count`, `is_current`; per `top_chunks[]` entry
`chunk_uuid`, `chunk_kind` (so an `auto_summary` chunk is never shown as a
passage), `snippet` (`original_text` ≤ 300 chars — the text a viewer highlights).
A `MetadataFilterBuilder` module owns the Filter DSL (mirroring
`AclFilterBuilder`) and is ANDed as the third operand of `_and_filters` at
`query_two_stage.py:308`; a source-inspection test in the style of
`test_rbac_query_filter.py` guards every stage-1 branch. Paging slices **after**
dedupe and after the gate; the ceiling is the reranked pool
(`QUERY_COLBERT_STAGE2_TOP_DOCUMENTS`), stated in the docs. Date sort re-orders
the gated set in-process.

#### 5.3.2 Invariants

- **GI-QUERY-F3-01** filters only narrow: `results(filters) ⊆ results(∅)` for the
  same principal and scope. **-02** facets count only hits the principal may
  read. **-03** paging is a window over one ranked set; `offset+limit ≤ pool`.
  Alloy `data_plane/query_metadata_filters.als` + mutant
  `query__filter_widens.als`.

#### 5.3.3 Versions (F6 tier 1)

`GET /secured/document/<uuid>/versions` → `{current: {...}, versions: [{version_number,
content_hash_raw, pointer_at_version, path, timestamp, changed_by, changed_by_kind}]}`;
`DocumentVersion` gains `version_number` (monotonic) and `changed_by`; the
detach-and-attach and multi-pointer-divergence dedup branches write a version row
*before* re-homing (history no longer lost); `content_hash_raw` stops duplicating
`last_hash`. Query hits carry `has_versions`/`version_count`.

#### 5.3.4 Similar documents (F8)

`POST /secured/documents/<uuid>/similar {limit?, filters?, scope?}` → hits in the
query hit shape. Implementation composes existing primitives: seed chunk vectors
(new `fetch_sentence_chunk_vectors_by_uuid` with `include_vector`), up to
`SIMILAR_SEED_CHUNKS=8` evenly-spaced seed chunks, one ACL/filter-scoped
`near_vector` each, document aggregation through `combine_stage1_dense_scores`
(one scale for "search" and "similar"), seed excluded, `RelevanceGate.apply` for
tiers and honest empties. Hits additionally carry `kg_node_ids` **filtered to
nodes the caller may see**, so the Platform can render "ähnliche Akten" by
grouping. Shares the query rate bucket.

### 5.4 Throughput and the per-seat SLO (F4)

- `clients.seat_count` (admin `PUT /admin/clients/<id>/seats`, JWT) — the
  contractual seat number.
- Query bucket moves to **Redis** (cluster-wide) keyed `secure-query:{tenant}`,
  capacity `seat_count × SECURE_API_QUERY_PER_SEAT_BURST` (default 18), refill
  `seat_count × SECURE_API_QUERY_PER_SEAT_PER_MIN / 60` (default 6/min);
  fallback to the in-process bucket when Redis is unavailable (logged, metric).
- Optional **fairness** sub-bucket keyed on the seat: `principal.subject` when
  brokered, else a request-body `seat_ref` (client-asserted; fairness only,
  never security). LRU eviction ported from RC's hardened limiter (`max_buckets`).
- `429` carries `Retry-After`; edge backstop: the per-tenant NGINX
  `limit_req_zone` is restored to the live gateway config at a generous ceiling
  (`600r/m`, burst 60) and the dead precondition test path is fixed so its
  absence fails CI again.
- Evidence: `QualityTests/load/` (locust) — 20 concurrent seats, 30 minutes,
  reference dev deployment; results committed with the server config profile.
  Alloy `core/rate_limiting.als` gains "tenant capacity ≥ seats × per-seat".

### 5.5 Eventing spine (E6)

**Pull-first, push optional, PostgreSQL-durable, ids-only.**

Tables: `event_outbox(seq BIGSERIAL, id UUID, client_id, event_type, subject_type,
subject_id, payload JSONB, occurred_at, idempotency_key, UNIQUE(client_id,
idempotency_key))`; `webhook_subscriptions(id, client_id, url, secret_hash,
event_types TEXT[], active, created_by, created_at, last_success_at,
failure_count)`; `webhook_deliveries(id, subscription_id, event_seq, status
pending|delivered|failed, attempts, next_attempt_at, last_status_code,
last_error, leased_until)`; `ingest_jobs(transmission_key_id, client_id, pointer,
status queued|running|indexed|failed|dead_lettered, attempts, error, updated_at)`
(durable — `transmission_keys` rows are deleted on success);
`kg_jobs(id, client_id, kind, target_id, status, total, done_count,
failed_count, error, …)` for filter-apply and imports.

Endpoints: `GET /secured/events?after=<seq>&limit=&types=` (cursor pull;
the Platform's baseline transport), `POST|GET /secured/webhooks`,
`DELETE /secured/webhooks/<id>`, `POST /secured/webhooks/<id>/test`,
`GET /secured/transmissions/<key>/status`, `GET /secured/graph/jobs/<id>`,
`GET /secured/graph/jobs?status=`.

Delivery: a standalone worker process (`run_event_delivery_worker.py`, own
Deployment + egress NetworkPolicy, modelled on `run_ingestion_worker.py`) leases
rows with `SELECT … FOR UPDATE SKIP LOCKED`, signs `X-Knovas-Signature:
t=<unix>,v1=<hmac-sha256>` with the per-subscription secret, retries with
backoff (1 m, 5 m, 30 m, 2 h, 12 h → `failed`), and never sends document text —
payloads carry `event_type`, `subject_type`, `subject_id`, `occurred_at` and
ids only; the receiver fetches details over mTLS. Webhook URLs must be `https`,
resolve to public addresses only (no RFC-1918/link-local/loopback; re-resolved
at delivery), no embedded credentials — hardened beyond
`validate_remote_controller_base_url`.

Event catalogue (v1): `document.indexed`, `document.index_failed`,
`document.deleted`, `document.metadata_updated`, `graph.sort_proposal.created`,
`graph.fact.proposed`, `graph.fact.pending_confirmation`, `graph.fact.confirmed`,
`graph.fact.updated`, `graph.fact.disputed`, `graph.fact.confirmation_overdue`,
`graph.node.merged`, `graph.job.completed`, `conflict_check.completed`,
`export.ready`.

Invariants: **GI-EVENT-01** an event is delivered only to subscriptions of its
own tenant; **-02** payloads carry identifiers, never content; **-03** at-least-
once with a monotonic per-tenant `seq` and an idempotency key; **-04** a
subscription URL is public https. Alloy `data_plane/event_outbox.als` + mutant
`events__cross_tenant_delivery.als`; tables registered in
`tenant_purge_service.py`; Prometheus depth/latency/failure metrics.

### 5.6 Four-eyes on facts (E3)

- `kg_node_type_attribute` gains `confirmation_policy VARCHAR(16) NOT NULL DEFAULT
  'single'` (`single|four_eyes`) and `semantic_role VARCHAR(32) NULL`
  (`deadline | responsible | deputy | client | opposing_party | practice_area |
  status | matter_number | email`) — the small controlled vocabulary that lets
  the Platform's matter page, the deadline extractor and the ICS feed be
  schema-agnostic. Both settable on `POST|PATCH …/schema` and returned by `GET`.
- `POST /nodes/<id>/facts` accepts `actor_ref`; `GraphService.create_fact`
  records it (`fact_created`, `actor_kind`).
- `PATCH /facts/<id>` writes `fact_updated {prior_value, new_value, actor}` and,
  when the attribute is four-eyes, resets `curation_status` from `confirmed`
  to `manual` (re-confirmation required) and emits
  `graph.fact.pending_confirmation`.
- `POST /facts/<id>/confirm` under four-eyes: requires an actor (`409
  actor_required`) and requires it to differ from the actor of the last human
  `fact_created | fact_updated | fact_adopted` event (`409 four_eyes_required`);
  records the confirming principal's `group_ids` in the ledger event.
- New `POST /facts/<id>/adopt {actor_ref}`: turns an `extracted` (system-
  proposed) fact into a human-entered `manual` fact attributed to the adopting
  actor (`fact_adopted`); the *second* human then confirms. This is the "AI
  suggests, human A enters, human B confirms" chain.
- New tenant-wide listing `GET /secured/graph/facts?curation_status=&
  confirmation=pending|confirmed&semantic_role=&node_type_id=&older_than=&
  limit=&offset=` (visible facts only) — the review queue and the deadline list.
- Escalation: a scheduled sweep (Prefect flow beside `daily_usage_stats`) emits
  `graph.fact.confirmation_overdue` for four-eyes facts pending longer than
  `KG_FOUR_EYES_ESCALATION_HOURS` (default 24).
- **GI-FACT-09** a four-eyes fact is `confirmed` only if the confirming actor
  differs from the last human entering actor; **-10** any value change on a
  four-eyes fact clears `confirmed` and leaves a `fact_updated` event.
  Alloy `lifecycles/kg_four_eyes.als` + mutant `foureyes__self_confirm.als`.
  Honesty: enforcement is over the verified subject when brokered and over the
  client-supplied `actor_ref` otherwise — the record says which
  (`actor_kind`).

### 5.7 Deterministic deadline extraction (E4)

- `classes/extractors/swiss_dates.py`: pure, dependency-free parser for DE/FR/IT/EN
  absolute dates (`31. März 2026`, `31.03.2026`, `1er mars 2026`, `1° marzo
  2026`), relative deadlines (`innert 30 Tagen`, `dans les 30 jours`, `entro 30
  giorni`, `binnen 10 Tagen`) anchored on a detected notification/decision date
  or `document_date`, trigger vocabularies (`Frist`, `Rechtsmittelfrist`,
  `délai`, `termine`, `bis spätestens`, `au plus tard`), returning candidates
  `(kind, iso_date, precision, char_start, char_end, quote, trigger,
  confidence)`. Never guesses: no trigger, no candidate.
- `services/knowledge_graph/deadline_extractor.py`:
  `on_ingest_complete(client_id, pointer)` mirroring `DocumentSorter` (never
  raises; failures to `kg_extract_outbox`), also invoked on `POST
  /nodes/<id>/knowledge` and on sort-proposal accept. For every node the pointer
  is assigned to whose type has a `semantic_role='deadline'` date attribute:
  fetch the document's chunks (`original_text`, page/sentence), run the parser,
  `FactEvidenceStore.propose_fact(value={"value": iso, "precision": …},
  evidence_chunk_ids=[chunk], confidence, char_start, char_end, quote)`.
  Flag `KG_DEADLINE_EXTRACTION_ENABLED` (default false; on in dev overlay).
- `kg_fact_evidence` gains nullable `char_start`, `char_end`, `quote (≤ 300)`;
  `POST /facts/<id>/evidence` and evidence reads carry them (also G3/F7).
- `POST /secured/graph/facts/propose` — the same `propose_fact` for client-side
  extractors (RemoteController, add-in): body `{node_id, attribute_id|label,
  value, evidence:[{chunk_id, char_start?, char_end?, quote?}], confidence?}`.
- No LLM in this path (deterministic-first per the roadmap; the LLM service has
  no structured-output mode today). Existing GI-FACT-02/03/05 apply; add
  `data_plane/kg_deadline_extraction.als` pinning "the extractor never
  confirms; every proposal cites a chunk with in-bounds offsets".

### 5.8 Party register substrate (D1)

- `kg_node_identifier` gains `kind VARCHAR(24) NOT NULL DEFAULT 'name'`
  (`name | alias | legal_name | uid | matter_number | email | iban | other`) and
  `identifier_normalized TEXT` (NFC, casefold, diacritics stripped **and** a
  German digraph variant `ü→ue` etc. so "Müller" ≡ "Mueller" ≡ "Muller"), a
  `pg_trgm` GIN index and `UNIQUE (client_id, node_id, identifier_normalized)`;
  `lexical_matcher.normalize_text` learns the same folding. Cap raised
  16 → 32 (`KG_MAX_IDENTIFIERS_PER_NODE`; a Swiss party needs legal name, trade
  name, FR/IT variants, former names, UID).
- `GET /secured/graph/identifiers/search?q=&kind=&node_type_id=&threshold=&limit=`
  — tenant-wide fuzzy match over visible nodes: trigram prefilter → lexical
  matcher (and the learned token channel when available) →
  `[{node_id, node_name, node_type_id, identifier_id, identifier_text, kind,
  score, channel}]`. Fails loud (`degraded=true`) instead of silently
  returning nothing when a channel errors.
- `GET /secured/graph/nodes/duplicates?node_type_id=&threshold=&limit=` —
  candidate pairs by normalised-identifier similarity, each with the matching
  identifiers and score.
- `POST /secured/graph/nodes/<target>/merge {source_node_id, actor_ref?}` —
  moves identifiers (deduplicated), facts + evidence, edges (re-pointed,
  self-loops and duplicates dropped), knowledge assignments, sections and
  filter bindings to the target; the source becomes `status='merged'` with
  `merged_into` (readable tombstone: `GET /nodes/<source>` answers 200 with
  `merged_into`, links never break); an append-only `kg_node_events` ledger
  records `node_merged`; requires both nodes visible; facts keep their own
  `required_groups`. **GI-GRAPH-14** after a merge no assignment/fact/edge/
  identifier references the source, the source is tombstoned not deleted, and
  no visibility widens. Alloy `lifecycles/kg_node_merge.als` + mutant
  `merge__drops_reference.als`.

### 5.9 Conflicts check (D2, D4)

- `POST /secured/graph/conflict-checks {queries: [{name, role?}] (1..50), context?,
  actor_ref?}` runs, per name, (a) the identifier search of §5.8 and (b) a
  corpus name probe (the `query_name_prefilter` BM25 span probe → documents →
  their pointers' assigned nodes) and returns
  `{check_id, executed_at, hits: [{query_index, kind: party|document, node_id?,
  pointer?, matched_text, score, channel, matter_node_ids}], hit_count,
  withheld_count, degraded, principal_scoped}`.
- Persistence: append-only `kg_conflict_check` (id, client_id, actor,
  actor_kind, actor_ref, group_ids, executed_at, queries JSONB, context,
  hit_count, withheld_count, degraded, principal_scoped, result_hash,
  engine_version) + `kg_conflict_check_hit` + append-only
  `kg_conflict_check_decision (check_id, decision clear|conflict|
  waived_with_consent|needs_review, note, actor, actor_kind, decided_at)`.
  No UPDATE/DELETE path — a follow-up check is a new row; registered in the
  purge order.
- Reads: `GET /secured/graph/conflict-checks?limit=&offset=&since=`,
  `GET /secured/graph/conflict-checks/<id>`, `POST …/<id>/decisions`.
- **Wall policy (default, see §12):** hits the caller may not read are **not
  disclosed** but are **counted** (`withheld_count`) so a conflicts officer with
  narrow groups can never mistake "withheld" for "clean". `degraded=true` when
  any channel failed — an evidentiary check must fail loudly.
- **GI-CONFLICT-01** check records and decisions are append-only; **-02** a hit
  references only objects visible to the recording principal, all others are
  counted; **-03** tenant-scoped. Alloy `lifecycles/conflict_check_ledger.als`
  + mutant `conflict__silent_withhold.als`. Event `conflict_check.completed`.

### 5.10 Language-aware retrieval (F5)

- `language` on `Document` + `SentenceChunk` from `metadata.language`
  (`und` when absent); query-side language detection
  (`services/language_detect.py`, stopword-ratio for DE/FR/IT/EN, no new
  dependency) selects the instruction prefix from a per-language map
  (`QUERY_INSTRUCTION_PREFIX_DE|FR|IT|EN`, replacing the boolean);
  `preprocess_bm25_text` applies German compound splitting only for `de|und`;
  FR/IT stopword lists are unioned into the shared inverted index (a test lists
  collisions with domain terms); the three copies of the sentence-boundary regex
  collapse into one shared module and gain accented capitals and `«`; the
  legal-section regexes gain `Art. 12`, `art. 3 al. 2`, `Art. 5 cpv. 2 lett. b`;
  auto-summary language follows the document's language.
- Evidence: `QualityTests/testset/fr/` and `/it/` — 30 public Federal Supreme
  Court decisions each (bger.ch, FR/IT), 20 queries each incl. cross-language
  (`query DE → find FR memo`) and 10 negative controls; the runner reports nDCG@10,
  Recall@10, clean-negative rate per language; per-language shards feed the
  gate calibration (`models.<vector>.languages.<lang>`, fallback model-level).
  Cross-repo: the external SemantixBenchmark FR/IT sets are run once and cited;
  `calibration_corpus.py`'s hard-coded dataset path becomes an env var.

### 5.11 Graph surfaces the G-section needs (G2, G3, G5, G6)

- `GET /secured/graph/nodes/<id>/ego?depth=1..3&limit=` → `{nodes: [+hop],
  edges, truncated, depth_applied}` in one guarded call (visible nodes,
  `filter_edges`), hard node/edge cap.
- `GET /facts/<id>/evidence` rows carry `pointer`, `page_number`,
  `sentence_number`, `sentence_number_end`, `char_start`, `char_end`, `quote`
  (resolved through the existing chunk resolver; hidden chunks are dropped, as
  today).
- Reports: `limit/offset/node_type_id` on completeness and contradictions; the
  date-precision comparison bug (`reports.py:71`) fixed by comparing precision
  ranks; the parametrised `TestIncompatibilityRules` table extended.
- `POST /secured/graph/imports {dry_run: true, nodes: [{name, node_type_id,
  identifiers: [{text, kind}], facts: [{attribute_id, value}]}], edges: [{…}]}`
  (≤ 500 nodes per call) → a diff preview; `dry_run:false` applies in one
  transaction, records `kg_node_events.import`, returns a `kg_jobs` id for
  large imports. Explicit admin import of a PMS export is human-initiated
  bulk creation (with preview) — distinct from the automatic bootstrap, which
  stays proposal-only.

### 5.12 Export (H5)

- `GET /secured/export/graph?include=schema,nodes,edges,facts,evidence,history,
  identifiers,proposals,assignments,conflict_checks&cursor=&limit=` and
  `GET /secured/export/documents?cursor=&limit=` stream **NDJSON**; the first
  line is a manifest `{tenant, generated_at, scope: principal|tenant, counts,
  spec_version, api_version}`; principal-scoped by default through the same
  guards as the read routes, with the scope stated — an export that silently
  narrows is worse than none. Documents carry pointers, title, metadata (§5.2),
  versions, ratings and relevance-feedback aggregates, and dedup lifecycle
  events. An `e2e_journey` round-trip asserts exported counts equal repository
  counts. Event `export.ready` for large exports (async job).

### 5.13 Documentation and invariants (KB)

Developer Kit: `Secure_API.md` (metadata, filters/paging/sort/facets, versions,
similar, transmissions status, per-seat limits, export), `Knowledge_Graph_API.md`
(identifier kinds/search/duplicates/merge, conflict checks, facts listing/
propose/adopt, four-eyes, ego, evidence offsets, imports, jobs), new
`Events_API.md`, new `Export_and_Exit.md`, `Client_Integration_Guide.md`
(metadata best practices, language), `components/Remote_Controller.md` (OCR
statement corrected — OCR shipped; formats; connectors). `Golden_Invariants.md`
+ `alloy_component_coverage_matrix.md` for every new GI; runbooks for the
delivery worker, the metadata backfill, seat quotas, per-mode gate calibration;
`QualityTests/README.md` for FR/IT and load suites.

## 6 · KnovasComponents slice — KnovasPlatform

Built on the section-C blueprint (`graph_routes.py`, `graph_model.py`,
`matter_view.py`, cassettes) and the section-B identity gate. New screens are
German-labelled, mount in `_sidebar.html`, and follow the existing CSRF/login
patterns. Anything that needs graph mode renders an explicit
"Wissensnetz-Modus erforderlich" state on the fixture, never a 500.

### 6.1 Client and model additions
`knovas_client.py`: `search_documents(query, *, filters, limit, offset, sort,
facets, scope)`, `document_versions`, `similar_documents`,
`update_document_metadata`, `identifiers_search`, `node_duplicates`,
`merge_nodes`, `conflict_check_run/list/get/decide`, `facts_list`,
`fact_adopt`, `fact_propose`, `node_ego`, `graph_import`, `events_poll`,
`transmission_status`, `graph_job`, `export_graph/documents` (streaming). Typed
dataclasses in `graph_model.py` for `Identifier(kind)`, `ConflictCheck`,
`ConflictHit`, `Decision`, `EgoGraph`, `Event`. Cassettes recorded once from the
dev tenant per the C-plan convention.

### 6.2 Search — filters, paging, honesty (F3, F9, D5)
Filter rail on `/`: Akte (matter picker → `scope.node_ids`), Praxisgebiet
(matters whose `semantic_role=practice_area` fact matches → `scope`),
Dokumenttyp, Autor, Zeitraum, Sprache, Status, Quelle; sort selector; real
"Weitere Treffer" via `offset`; facet chips from the response; hit metaline shows
type · date · author · language · version badge; `auto_summary` chunk hits are
labelled "KI-Zusammenfassung"; the empty state renders `no_strong_matches` and
`semantix.status`; a persistent "Beispieldaten" banner whenever
`SEARCH_USE_TEST_RESULTS` is on. "Wer kennt sich aus?" = the author facet of a
query, one click from the rail (D5). Filters that the API rejects
(`400 validation_error`) are surfaced, never silently dropped; UI-only keys
(`exact_match`) are no longer forwarded.

### 6.3 Document dialog — versions, similar, tables, metadata (F6, F8, H4)
Version list with `changed_by`, "aktuelle Version" badge; "Ähnliche Dokumente"
(F8) and, on the matter page, "Ähnliche Akten" (grouped by visible
`kg_node_ids`); table rendering in `markdown.js` for extracted tables (H4);
metadata edit (type/status/date/language) through `PATCH …/metadata`.

### 6.4 Viewer — jump to the hit (F7)
Vendored pdf.js (`static/js/vendor/pdfjs/pdf.mjs`, `pdf.worker.mjs`, CSP
`worker-src 'self'`), route `/viewer?doc=&path=&page=&snippet=`; opens at the
hit page and highlights the snippet through the text layer (`find` controller);
falls back to `preview-content` for non-PDF. One `openEvidence(docId, path,
page, snippet)` helper used by search hits, Cortex evidence, fact evidence and
conflict hits. The backlog's measurement precondition (PDF vs DOCX open ratio)
is satisfied by the J2 journal counting opens by format for the first weeks.

### 6.5 Parteien (D1, D3)
`/parteien`: register list with kind-aware search (identifier search),
party detail (facts, matters via ego, corporate relations as edges), identifier
editor with kinds, "Dubletten" queue from `nodes/duplicates` with a merge
sheet (source/target preview, what moves, "Quelle bleibt als Verweis
erhalten"); merge is a B5 guarded action (`ApprovalService` kind
`party_merge`, admin bypass recorded). Zefix (§6.7) button on organisations.

### 6.6 Konfliktprüfung (D2, D4)
`/konfliktpruefung`: form (names + role + context), result page grouped by
parties / matters / documents with `withheld_count` and `degraded` rendered
prominently, decision with note, history list, printable
"Konfliktprüfungsprotokoll" (HTML→print CSS; contains check id, actor, time,
queries, hits, decision, hash). Lateral-hire import (D4): CSV/XLSX upload
(columns client / counterparty / matter / period) → one check per row under a
bundle `context` → summary table with per-row status → the same protocol.

### 6.7 Zefix / UID enrichment (D3)
`src/zefix_client.py` calls the Zefix public REST API **from the customer's
network** (never from Knovas) with firm-supplied credentials
(`ZEFIX_USERNAME/PASSWORD`, disabled when absent); result → facts on the
organisation node (UID, seat, legal form, status, cantonal register link)
plus a generated "Zefix-Auszug <UID> <date>" text document uploaded through
the Platform's upload path and linked as evidence, so the facts inherit trust
tiers honestly. Signatories/group structure are stated as **not** available
from Zefix (cantonal extract only).

### 6.8 Fristen (E3, E4, E5)
`/fristen`: three tabs — *Vorschläge* (extracted, `semantic_role=deadline`)
with quote, page and a viewer link → "Übernehmen" (`adopt`) or "Ablehnen"
(permanent, said so); *Zur Bestätigung* (four-eyes pending) → "Bestätigen"
disabled for the entering user with the reason; *Bestätigt*; overdue banner
from `confirmation_overdue` events; per-matter widget on the matter page.
Per-user ICS feed `GET /feeds/deadlines.ics?token=` (feed token signed with the
identity key, revocable in settings): one VEVENT per confirmed deadline with
`ORGANIZER`/`ATTENDEE` from the matter's `responsible`/`deputy` entity_ref
facts (Person nodes with an `email` identifier), matter name in SUMMARY,
`VALARM` 7 d/1 d, `X-KNOVAS-FACT-ID`; Outlook subscription instructions in the
docs. Person nodes and the two roles are created in the Typ-Werkstatt (C-plan
§7.1) with the new `semantic_role`.

### 6.9 Posteingang and jobs (E6 consumer)
Background poller (one leader via `platform-db` advisory lock; safe under two
gunicorn workers) pulls `GET /secured/events` into a local `events` table
with a per-tenant cursor; `/posteingang` renders sort proposals, deadline
proposals, pending confirmations, contradictions, job completions and
conflict-check completions with deep links; sidebar badge; ingestion/upload
screens poll `transmissions/<key>/status`. Integrators are pointed at the
API's webhooks; the Platform offers an "Ereignisprotokoll" CSV export.

### 6.10 Cortex on the live graph (G1–G8)
G1: `ONTOLOGY_SOURCE=graph` becomes the deploy-bundle default once the C-plan
cassettes exist; sidebar badge "Demo-Daten" when fixture. G2: `/matters/<id>/graph`
"Akten-Kompass" from the ego endpoint (Cytoscape reuse), one hop out, click →
matter page or party. G3: node "Warum?" panel — facts with tier chips, evidence
rows with quote/page → viewer. G4: `trust_chip` Jinja macro (tier + scope tag +
signals popover) reused by chronology/dossier. G5: `/berichte` — contradictions
and completeness with links, node-type filter, CSV. G6: `/import` wizard — CSV
mapping (matter number, client, counterparties, responsible lawyer, practice
area, status), dry-run diff via `POST /secured/graph/imports`, apply, plus the
C-plan file-structure bootstrap. G7: `create_type_relation` in graph mode once
`target_node_type_id` exists (C-plan A4/A5); declared relations render as
dashed Vorgaben. G8: Cortex filters call `filters/evaluate|apply|placements` in
graph mode with `503 filter_embedding_model_stale|relevance_calibration_missing`
rendered as "kann gerade nicht bewerten — bitte später" and apply progress via
`graph/jobs/<id>`.

### 6.11 Office add-ins and the filing endpoint (H2, E5)
New component `KnovasPlatform/components/knovas_office_addins/`: two manifests
(`manifest.outlook.xml`, `manifest.word.xml`) and one static taskpane app served
by the Platform at `/addins/*` over HTTPS (Office requires it). **Outlook:**
ribbon button "In Knovas ablegen" → taskpane (Platform session login inside the
webview) → matter picker (recent + suggestions from `identifiers/search` on
sender/recipients/subject) → one click "Ablegen": `Office.context.mailbox.item`
MIME via `makeEwsRequestAsync` (`GetItem` with `IncludeMimeContent`) →
`POST /api/filing/email {mime_base64, node_id, include_attachments}` → toast
"abgelegt" / "bereits abgelegt". Platform side: parse with stdlib `email`, body
through `knovas_extract_upload` with `metadata` (author=From,
document_date=Date, document_type "E-Mail", source_kind `addin`, `extra`
message-id) and `graph_assign`; each attachment its own document
(`<prefix>/mail/<message-id>/att/<name>`, assigned to the same matter); dedup
by `eml:message_id` in a local `filed_emails` table (platform-db). **Word:**
taskpane search (`/api/search` with the F3 filters), "Öffnen" through
`client-path` UNC or the companion token, "Zitat einfügen" via `insertText`
with pointer + page. Docs cover manifest hosting, permissions
(`ReadWriteMailbox` for EWS), central deployment vs sideload, and HTTPS.

### 6.12 Arbeitstag-Journal (J2, J3)
Opt-in per user (settings toggle + consent text); `activity_journal(user_id,
occurred_at, kind search|open_document|open_matter|open_viewer, matter_node_id,
pointer, page, format, query_hash)` in `platform-db`; hooks in `search()`,
`openPreview/openDocument`, matter/viewer routes; `/mein-tag`: per-day matter
blocks (gap > 20 min splits a block), duration, documents; CSV export
(user, day, matter, start, end, minutes, documents) for the PMS timesheet;
retention `JOURNAL_RETENTION_DAYS` (default 90); the user sees only their own
journal, admins see nothing per person (works-council-friendly by
construction); nothing leaves the firm's host. Also counts opens by format
for the F7 precondition.

## 7 · KnovasComponents slice — RemoteController

### 7.1 Metadata at ingest (F3, D5, F5, F6)
`ExtractedDocument`/`ExtractionPayload` gain `author`, `language`, `created`,
`modified`, `document_type`, `document_status`, `document_date`, `extra`;
`payload_from_extraction_result` reads them from `knovas_extract.Metadata`;
language falls back to `py3langid` over the first 20 KB when the extractor has
none; `document_type` from a per-source rule table (extension → type, filename
pattern `_Dokumenttyp` → type, sidecar `.knovas-meta.json` per folder);
`document_status` from filename heuristics (`Entwurf|draft` → draft,
`unterzeichnet|signed|final` → final/executed); `init_document_transmission`
sends `metadata` behind `RC_SEND_DOCUMENT_METADATA` (default on; documented as
requiring the API contract of §5.2). Optional `RC_MATTER_PATH_RULE` (regex with a
named group over the relative path) resolves a matter node through
`identifiers/search?kind=matter_number` (cached) and passes `graph_assign` —
day-one filing for WinJur-style folder trees.

### 7.2 Mailbox connector (F2)
`src/mailbox_mirror/` following the OneDrive mirror precedent: Microsoft Graph
application permission `Mail.Read`, mailbox allow-list, folder include/exclude,
per-folder delta queries with full-walk fallback, each message materialised as
`.eml` (`/messages/{id}/$value`) under `<mailbox>/<folder>/<stable-key>.eml`
(stable key = sha1 of `internetMessageId`; mtime pinned to `receivedDateTime`),
attachments saved beside the message (`<key>.att/<name>`) so they become their
own documents, the two OneDrive invariants copied (no cursor advance while
downloads fail; no prune on incomplete enumeration), env `MAILBOX_*`, sidecar
provenance (`mailbox://<upn>/<folder>/<id>`) in `path`. IMAP/EWS are stated
as later options.

### 7.3 XLSX / PPTX (F2, H4)
`src/sync/office_extractors.py`: `XlsxExtractor` (openpyxl `read_only`,
`data_only`; one `Table` per worksheet block ≤ 64 cols / 5 000 rows, ragged
rows padded, hidden sheets skipped, `title` = sheet name, `client_table_hint`
= `xlsx_s{i}_t{j}`, plus a flattened text rendering) and `PptxExtractor`
(python-pptx; one page per slide, slide title as a section, notes included)
registered into `knovas_extract.dispatch.MIME_REGISTRY` at RC import (the
documented public hook) — inside the two folders, no upstream wait; the five
extension allow-lists collapse into one `SYNCABLE_EXTENSIONS` source of truth
from which the globs derive; provenance is stamped honestly (`extractor.name`
recorded as `remote-controller-office` in the sidecar). Upstreaming to
knovas-extract is named as the follow-up.

### 7.4 OCR evidence (F1)
Dockerfile installs `tesseract-ocr-ita`; default `RC_TESSERACT_LANG=deu+fra+ita+eng`;
`_extract_bytes` keeps `result.warnings` and an `ocr_used` flag per document,
exported as `knovas_rc_documents_extracted_total{ext,ocr}` and
`knovas_rc_extract_errors_total{reason}`; `benchmarks/ocr/`: 30 ground-truth
legal texts per language rendered to page images at 200/300 dpi with skew/noise
(Pillow) → PDF → OCR through knovas-extract → CER/WER per language and dpi →
`results/<ts>/report.md`; plus a runbook to run the same harness against a
firm's own scans on-premise ("Nachweis auf eigenen Scans") since real court
scans cannot be published. Skipped scanned PDFs (`skip:unconvertible`) get a
documented re-queue command.

### 7.5 PST and migration (H1)
`scripts/explode_pst.py` (libpst `readpst -e -j N -o <staging>` in the image;
GPL tool invoked as a separate process; folder hierarchy preserved as
directories; `Message-ID` recorded for dedup) + `sync/pst_queue.py` (one PST
per cycle, resumable, state rows) + writable `RC_PST_INBOX`/`RC_PST_STAGING`
volumes; state DB gains `content_sha256` and `index_status/indexed_at` (RC
polls `transmissions/<key>/status` lazily so a migration can be verified:
"N Dokumente eingereicht, N indexiert"); `sync_response.schema.json` gains
`rate_limit` and `subfolder_progress` (already computed, never serialised);
`docs/migration.md` — inventory, PST step, throughput settings against the API
ceiling, dedup expectations, verification, rollback, and the fixed-price rule of
thumb.

## 8 · Documentation (KnovasComponents)

New: `docs/README.md` (index by audience), `docs/product-statements.md` (legend
+ E1/E2, F4 SLO, F6 tiers, G9, H6, J1/J3/J4), `KnovasPlatform/docs/features/`
(`search-filters-and-versions.md`, `viewer.md`, `matters-and-parties.md`,
`conflicts-check.md`, `deadlines.md`, `reports-and-inbox.md`,
`activity-journal.md`, `import-and-bootstrap.md`),
`KnovasPlatform/docs/integration/office-add-ins.md`, `…/graph-api.md`
(Platform route reference), `…/events.md`, `RemoteController/docs/connectors.md`,
`RemoteController/docs/migration.md`, `KnovasPlatform/CHANGELOG.md`.
Updated: `docs/KnovasAPI/*` re-mirrored from the Developer Kit (+
`Knowledge_Graph_API.md`, `Events_API.md`, `Export_and_Exit.md`) with a mirror
policy line and a `scripts/check_devkit_mirror.py` drift check;
`docs/specifications.md` (§1.3 formats, §1.6 connectors, §2.3 endpoints, §2.5
`ONTOLOGY_*`, §2.8 add-ins, §4 go-live rows, §7 index); `docs/hosting-requirements.md`
(mailbox/PST options, Graph egress, per-seat throughput); `docs/search-ui-backlog.md`
(F3/F6/F7/F8 resolved, dated); `RemoteController/docs/configuration.md`
(formats, OCR languages, metadata keys); `KnovasPlatform/docs/README.md`;
`RELEASE_NOTES.md`; `RemoteController/CHANGELOG.md`; `docs/certificates.md`
if the add-in host needs the bundle.

## 9 · Normative design rules

1. **The API first, then the UI.** No screen post-filters what the API should
   filter; no screen invents metadata the API does not return.
2. **Proposals never commit.** Extracted deadlines, sort proposals, imports in
   dry-run, Zefix results — a human accepts. Rejection is permanent and says so.
3. **Trust and scope travel together.** No tier, count, or export without its
   `scope`; no conflicts result without `withheld_count` and `degraded`.
4. **Ids leave, content stays.** Event payloads and webhook bodies carry
   identifiers only.
5. **Honest actor labels.** `actor_kind` states whether a "who" was a verified
   subject or a client-supplied reference.
6. **Additive schema, backfilled.** Every new Weaviate property ships with a
   backfill CLI or it silently hides the existing corpus.
7. **Language never blocks.** Unknown language degrades to today's behaviour;
   nothing 503s because a document is Italian.

## 10 · Sequencing

| Phase | KnowledgeBase | KnovasComponents | Unlocks |
| --- | --- | --- | --- |
| 0 | §5.1 gate prerequisite; §5.2 metadata; §5.3.1 filters/paging; §5.5 events (pull) | RC §7.1 metadata; docs §8 statements | everything F, D5, C3 scope |
| 1 | §5.3.3 versions; §5.3.4 similar; §5.4 throughput; §5.10 language | Platform §6.2–6.4, §6.9; RC §7.3 XLSX/PPTX, §7.4 OCR | F1–F9 |
| 2 | §5.6 four-eyes; §5.7 extractor; §5.8 register; §5.9 conflicts; §5.11 graph surfaces | Platform §6.5–6.8, §6.10 (after C-plan and section-B) | D, E, G |
| 3 | §5.5 webhooks + worker; §5.12 export | RC §7.2 mailbox, §7.5 PST; add-ins §6.11; journal §6.12 | H, J, E5 |

Phases 0 and 1 do not depend on section B. Identity-dependent screens (D2 actor,
E3 confirm buttons, J2, H2 login) are scheduled after `feat/section-b-buildout`
merges; their backend halves ship earlier with `actor_kind=client_ref`.

## 11 · Risks

| Risk | Mitigation |
| --- | --- |
| Gate rollout (§5.1) slips → scope-based matter/practice filters 503 | §5.1 is phase 0; the filter rail degrades to metadata filters only and says so |
| Per-pointer vs per-Document metadata under content-addressed dedup | matter lives in the graph per pointer; per-file metadata on the Document; documented; `PATCH …/metadata` for corrections |
| New Weaviate properties invisible for existing documents | backfill CLI is part of the same task; verified by `manage_weaviate.py verify` |
| Delivery worker needs cluster egress | own Deployment + explicit egress NetworkPolicy; pull path works without it |
| Four-eyes over client-supplied `actor_ref` before BROKERED | `actor_kind` recorded and rendered; docs say "verified once brokered" |
| RC-local XLSX/PPTX misattributed to the certified extractor | provenance stamped `remote-controller-office`; upstream follow-up named |
| PST volumes / GPL tool | separate process, writable volumes documented in SETUP; licence note |
| Office add-in auth inside the taskpane webview | Platform session cookie on the same HTTPS origin; documented fallback: login inside the taskpane |
| Cortex graph mode at firm scale (whole-topology fetch) | ego endpoint + server-side node filters (C-plan B4) replace topology scans |
| Facets computed over the ranked pool, not the corpus | stated in docs and UI ("Verteilung in den Treffern") |

## 12 · Guidance requested from the owner

Answers change the plan materially; defaults are stated.

1. **Section B sequencing** — accept scheduling identity-dependent *screens*
   after `feat/section-b-buildout` merges (backends earlier with
   `actor_kind=client_ref`)? *Default: yes.*
2. **Deadline model** — date facts on the Mandat with `semantic_role`
   attributes and Person nodes for responsible/deputy (vs. a dedicated Frist
   node type)? *Default: facts on the matter.*
3. **Four-eyes semantics** — enforce at the API over the verified subject when
   brokered, over `actor_ref` otherwise, with `adopt` for extracted facts?
   *Default: yes.*
4. **XLSX/PPTX in RemoteController** via the public `MIME_REGISTRY` hook now,
   upstream to knovas-extract later? *Default: yes.*
5. **Mailbox protocol** — Microsoft Graph first; IMAP/EWS declared later?
   *Default: Graph.*
6. **E6 push** — build webhooks + the delivery worker (needs an egress
   NetworkPolicy in the KB infra) in phase 3, pull in phase 0? *Default: yes.*
7. **F4 numbers** — 6 q/min/seat sustained, burst 18, p95 ≤ 3 s at 20 seats;
   `seat_count` set by Knovas ops. Was the removal of the NGINX `limit_req`
   zones from the live gateway intentional? *Default: restore as backstop.*
8. **D2 wall policy** — count withheld hits (recommended) vs. hard 403 vs.
   silent narrowing? *Default: count.*
9. **D3 Zefix** from the customer's network with firm credentials?
   *Default: yes.*
10. **F1 evidence** — synthetic DE/FR/IT scan benchmark + on-prem runbook (no
    real court scans published)? *Default: yes.*
11. **F5 evidence** — public bger.ch decisions FR/IT in QualityTests +
    external SemantixBenchmark citation? *Default: yes.*
12. **G9 vocabulary** — adopt the eight Pflichtenheft labels in customer docs
    with the mapping to [NOW]/[GATED]/[ROADMAP]? *Default: yes.*
13. **J1** — declare "integrate + journal"? *Default: yes.*
14. **Plan placement** — Part A in `KnowledgeBase/docs/superpowers/plans/`,
    Part B in `KnovasComponents/docs/superpowers/plans/`, this design in both.
    *Default: yes.*

## 13 · Related

- `docs/superpowers/specs/2026-08-14-matters-and-typed-nodes-design.md` and its plan
- `.worktrees/section-b-buildout/docs/superpowers/plans/2026-08-14-section-b-buildout.md`
- `KnowledgeBase/docs/Knovas_Developer_Kit/api/*.md` — the contracts this extends
- `KnowledgeBase/docs/Docs/01_SYSTEM/Golden_Invariants.md`, `Feature_Design_Workflow.md`
- `docs/search-ui-backlog.md` — the Platform's own "API first" ordering
