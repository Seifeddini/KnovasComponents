# What the Platform MVP needs from the Knovas Core

Written by the coordinator on 2026-11-21 for whoever takes the Core change (founder, 2026-11-21: "Someone else does
it"). The Platform side is built and waits on these two reads. Follow-ups FU-37 and FU-38.

## Why

The assistant now runs end to end on the demo tenant (tenant `817f2330-…`, production Core): the person's search
through `POST /secured/query`, Claude answering, the Platform's check. Both test questions came back "Dazu finde ich
nichts in Ihren Quellen", correctly: each hit carries only its chunk's own text, measured at 2 to 172 characters
(mostly about 50), and no answer can be quoted word for word from that. The dossier has the same gap: a fact names
its evidence chunk (`provenance_chunk_id`), but no route returns that chunk's text or page.

## CR-1: a few sentences around each hit, in the search answer

- **Where**: `POST /secured/query`, per entry of a hit's `top_chunks`.
- **What**: a field `context` (name to be chosen by the Core): the chunk's text together with the neighbouring
  sentences **of the same document**, up to 1,200 characters, cut at a word boundary, centred on the chunk; and the
  location of that text: `context_page_number`, `context_sentence_number`, `context_sentence_number_end`.
- **Opt-in**: only when the request asks for it (e.g. `"include_context": true`), so the search page's answers stay
  as small as today.
- **Rights**: the context comes only from the chunk's own document, which the caller may already read (the hit is
  there). Nothing from another document.
- **Logs**: no context text in any log line. The Core's INFO line that logs raw `top_chunks` (Platform follow-up FU-09
  found the Platform's side; check the Core's) must not log it.
- **Invariant**: GI-QUERY-04's containment rule needs the Core's own amendment for this.

**Update 2026-11-22:** the Core's document-text route (`GET /secured/document/<uuid>/text`, FEAT-2026-11-16 T12) is live in production. The founder decided the same day that the assistant may read it under limits (`work-orders/PMVP-20-context.md`): the Platform cuts the context around each hit itself, so **CR-1 is not needed for the pilot**. It stays useful later (one call instead of one per document).

## CR-1c: the document-text route reads as the person

- **Where**: `GET /secured/document/<uuid>/text`.
- **What**: under `brokered`, accept the Platform's signed claim on this GET (a header such as `X-Knovas-Principal-Assertion`, or a query parameter), verified like the body field of `POST /secured/query`; today the route resolves the principal without a claim, so it answers 401 under `brokered`.
- **Why**: with Ein the assistant's context reads must narrow exactly as the person's search does. Until then the Platform falls back to the search's snippet on any refusal.
- **Needed**: together with CR-R1.

## CR-2: one passage by its id

- **Where**: a per-thing read, e.g. `GET /secured/chunks/<chunk_id>`.
- **What**: the chunk's text (with the same context as CR-1 if asked), its document's pointer, `page_number`,
  `sentence_number`, `sentence_number_end`.
- **Rights**: as the caller (the signed actor, like search). A chunk of a document the caller may not read answers
  exactly like a chunk that does not exist (404, same body).
- **Use**: the dossier opens a fact's passage under the fact (B-04, BRAIN-VIEWS POST-02); later the evidence trail.

## How the Platform will use them

- CR-1: inside `keeping_snippets()` (`knovas_client.py`, the passage round of PMVP-20) the passage becomes the
  `context` and its location; everything else stays. The check (quote word for word, numbers in the quote) is
  unchanged.
- CR-2: the dossier (PMVP-30a) shows the passage text and page under a fact, and opens the document at that page.

## How to know it works

On the demo tenant, as a member: "Welche Kündigungsfrist gilt im Mietvertrag?" and a question about the Abmahnung in
matter 2023-041 get answers with a quote under each statement, through the local demo copy (the coordinator's
launcher). Nothing is ever written to the Knovas GmbH tenant for this.

## Added on 2026-11-21: what roles and "enforcement on" need from the Core (CR-R1 to CR-R6)

From the role builder's design (`design/role-builder.md`, § 2 and § 7, with the code lines that show each point). The
founder decided the same day that these go with CR-1 and CR-2 to whoever takes the Core work. **The most urgent is
CR-R1**: today, when a tenant is switched to `enforcing`, the Core ignores the Platform's signed identity
(`principal_resolver.py` reads the body field `access_groups`, which the Platform never sends), so every Platform
search would see open documents only. The Platform's claims are verified only in the posture `brokered`, and no route
can switch a tenant to it. Until CR-R1 and CR-R2 land, the pilot runs with enforcement off and roles' data do not act.

| No. | What | Why |
|---|---|---|
| CR-R1 | The posture route accepts `brokered` once a key is registered and the backfill is complete (DEC-0077 point 5; bet item SA-03) | Under `enforcing` the Platform's claim is ignored, so neither roles' data nor "Ein" act for the Platform |
| CR-R1a | A **read of the tenant's posture** on the secured API (as the tenant's certificate, no admin key): `disabled` / `enforcing` / `brokered` (added 2026-11-21 from RB-5's draft) | The Platform shows «wirkt mit «Ein»» on every role until it can see that the tenant is `brokered`; without this read it always shows it |
| CR-R2 | Folder-rule routes take the caller's signed claim under `brokered` (today they build the caller from `acting_as` only, and `brokered` refuses a caller without a claim) | Without it, under "Ein" no folder rule can be set from the Platform |
| CR-R3 | A new folder rule reaches documents already read in (a backfill for one rule, started by the route) | Without it, a new folder covers only documents read in later |
| CR-R4 | Probe: evaluate without applying; count per day what would be hidden, no names | A-06's middle position |
| CR-R5 | **Rewritten 2026-11-21 ("Core gateway only")**: a pool of an agent's Platform-signed claims, `PUT` and `DELETE /secured/agents/<agent id>/claims`; and DEC-0077 amended so a claim may name a Platform-registered agent (details below) | External agents use the Core's MCP gateway, which today sends no groups and no claim; the Platform must hand it the agent's groups, signed |
| CR-R6 | **Rewritten 2026-11-21**: a gateway key bound to an agent id; per call the gateway sends one of that agent's pooled claims (details below) | Without it the gateway reads as a caller without groups, and under `brokered` it is refused |

Also from the design: **the Core has no deny** (its filter is a disjunction without negation), so A-04's "a denial
wins" is not "Exist" as the plan says; the founder decided on 2026-11-21: no deny for the pilot.

**Needed for the pilot (15 December):** CR-R1 and CR-R2; CR-R3 for folders over documents already read in. For
external agents also CR-R5 and CR-R6. Later: CR-R4.

## CR-R5 and CR-R6 in detail (founder, 2026-11-21: "Core gateway only", "only after «Ein»")

The Platform gets no MCP endpoint. External agents use the Core's MCP gateway (`mcp.knovas.ch`,
FEAT-2026-11-16-copilot-mcp-gateway, on KnowledgeBase `master`). Today the gateway authenticates a **company key per
tenant** (`mcp_gateway_keys`, `knovas_gw_…`, a SHA-256, at most 365 days, issued by Knovas staff with
`manage_mcp_keys`), and every call is `POST /secured/query` (or the `fetch` text route) over the tenant's certificate
with **no `access_groups` and no `principal_assertion`**, declaring the actor `mcp-gateway.<key id>`
(`knovas_mcp/gateway/search.py:1-12, 67-69`). It therefore reads everything under `disabled`, open documents under
`enforcing`, and is refused under `brokered`. The design (`design/role-builder.md` § 5) keeps the Core's verifier and
enforcement as they are: **the Platform signs the agent's claims and hands the Core a pool of them ahead of use; the
gateway sends one per call.** The agent is known to both by the Platform's agent id (a UUID).

### CR-R5: the agent's pool of claims (Secure API)

- **Routes:** `PUT /secured/agents/<agent_id>/claims` and `DELETE /secured/agents/<agent_id>/claims`. `agent_id`
  matches `request_actor.CLEAR_ID_RE`.
- **Auth:** the tenant's client certificate (mTLS) as on every `/secured/*` route; the tenant is the certificate's.
  The claims themselves carry the authority: each is verified with the tenant's registered broker key.
- **Request (`PUT`):** `{"claims": [<JWS>, …]}`, 1 to 20 claims and nothing else. Each must be `typ
  knovas-principal+jws`, `alg EdDSA`, signed by a key registered for this tenant, `tid` = the certificate's tenant,
  `sub` = `<agent_id>`, `exp` in the future, `exp - iat` at most 300 seconds, at most 32 groups, every group resolving
  in the tenant's tree (GI-ACCESSROLES-04). Checked **without burning `jti`** (the use burns it). One bad claim refuses
  the whole request: 400 `invalid_claims` (no detail of which), nothing stored. The tenant is not `brokered`: 409
  `not_brokered`, nothing stored ("only after «Ein»").
- **Effect:** `PUT` replaces the agent's whole pool (old unused claims are dropped); `DELETE` drops it at once (204, also
  when empty). Answers 204.
- **Data:** a new table, e.g. `agent_claims(id, client_id → clients ON DELETE CASCADE, agent_id TEXT, claim TEXT, jti,
  expires_at)`, an index on `(client_id, agent_id, expires_at)`; expired rows purged; at most 20 per agent. Readable
  and deletable only by the gateway's role (`mcp_gateway_reader` gains `SELECT, DELETE` on it) and the app. Per
  agent also `last_pool_expires_at` (the latest claim's `exp` of the last `PUT`; cleared by `DELETE`), which CR-R6
  reads to tell a used-up pool from none.
- **Logs and audit:** one line per call with the tenant, the agent id, the count, the outcome and an error code; never a
  claim, a group name or a `jti` beyond its first eight characters (GI-DATA-04). An audit event `agent.claims.replaced`
  or `agent.claims.dropped` with the agent id and the count.
- **Rate:** the class `metadata` (10 per second, burst 20) is enough: the Platform pushes once a minute per agent.
- **The limit this sets, plainly:** the Platform pushes 20 claims, the maximum, and each upstream call burns one, so
  **an agent makes at most 20 upstream calls a minute; raise the Core's maximum if pilots need more.**
- **Decision text:** DEC-0077 amended: "a signed claim names a person **or an agent registered on the Platform**".
- **Depends on:** CR-R1 (a tenant can be `brokered`). **Used by:** RB-7 (the Platform pushes) and CR-R6.

- **Order of PUT and DELETE** (added 2026-11-21 from RB-7's security read): the Core refuses (409) a `PUT` whose
  claims were all issued (`iat`) before the agent's last `DELETE`, so a delayed push can never bring back a pool the
  Platform has already withdrawn (a revoked agent, an inactive person).

### CR-R6: a gateway key bound to an agent (the MCP gateway)

- **Key:** `mcp_gateway_keys.agent_id TEXT NULL` (a non-destructive migration; mirrored in `DB/init.sql`; the reader role
  gains `SELECT (agent_id)`). `manage_mcp_keys issue --client <tenant> --agent <agent id> --print-once` binds a key to
  the agent and **refuses unless the tenant is `brokered`** ("only after «Ein»"); `list` shows the agent id. A key
  without `agent_id` behaves exactly as today.
- **Per call** with an agent-bound key (every upstream request of `search` and `fetch`): take one unused, unexpired
  claim of (the key's tenant, `agent_id`) atomically (`DELETE … WHERE id = (SELECT … ORDER BY expires_at LIMIT 1 FOR
  UPDATE SKIP LOCKED) RETURNING claim`), send it as the body field `principal_assertion`, and declare the actor
  `{"kind": "agent", "id": "<agent_id>"}`. A retry after `429`/`503` takes a fresh claim (the Secure API burns each
  `jti`). **No unused claim left**, nothing is sent upstream, and the answer depends on whether the agent has a pool:
  - it had one within the last 5 minutes (`last_pool_expires_at` later than now minus 300 seconds) and it is used
    up: the gateway's busy text with a retry hint ("Knovas is busy, try again in a few seconds."), outcome
    `pool_empty`;
  - it has none (never pushed, dropped by `DELETE`, or expired more than 5 minutes ago): "No current rights for this
    agent; nothing was read.", outcome `no_claim`.
- **Unchanged:** the key check, the tenant from the key's row, the certificate per tenant, the Secure API's verifier
  and enforcement (a claim from the pool is verified like any Platform claim).
- **Logs:** the existing `mcp_call` line gains `agent_id` and the outcomes `pool_empty` and `no_claim`; never a claim.
- **Models and sign-off:** `gateway_key.als` / `gateway_tools.als` gain the bound key and "a bound key's call carries
  exactly one pooled claim of its agent"; GI-GW-01 to GI-GW-05 as amended need a new sign-off (Siran), as the plan
  requires for any change to them.
- **Depends on:** CR-R5 and CR-R1. **Used by:** every external agent on a client tenant (RB-7), and Claude Code on the
  Knovas tenant once the internal MCP server stops at `brokered`.

**How to know it works:** on the demo tenant, `brokered`: an agent in a role with a walled group finds a walled
document through the gateway; taken out of the role on the Platform, it no longer does within a minute; revoked, its
next call reads nothing; with the Platform stopped, it reads nothing after 300 seconds.

## Added on 2026-11-22: what the enforcement switch needs (PMVP-42's draft)

From the work order `work-orders/PMVP-42.md`. The Platform never switches a tenant: Knovas staff do, through the
internal admin API (`PUT /admin/clients/<id>/rbac-enforcement`, an employee login). The Platform shows the position,
records the firm's request and its approval by a second administrator, and refuses to ask for «Ein» before two green
end-to-end rights runs are on record (plan § 8.1, DEC-0082). For that it needs two things from the Core besides CR-R1.

### CR-R1a in detail: the tenant reads its own posture

- **Route:** `GET /secured/rbac/posture`, on the tenant's client certificate (mTLS) like every `/secured/*` route; the
  tenant is the certificate's. No admin key, no principal assertion (the answer names no person). Admission class
  `metadata`.
- **Answer:** `{"mode": "disabled" | "enforcing" | "brokered", "backfill_complete": true | false,
  "broker_key_registered": true | false}` in the Secure API's usual envelope. Nothing else: no group, no document, no
  other tenant. A failure to read the posture is 503, never a guess (as `EnforcementLookupError`).
- **Logs:** the tenant id and the mode.
- **Used by:** `KnovasAPIClient.rbac_posture()` (PMVP-42 adds it; a 404, the Core without this route, answers None):
  the position sentence on «Zugriff» (PMVP-42), RB-5's «wirkt mit «Ein»», PMVP-41's switch from computed answers to
  Knovas's own, PMVP-42's refusal of «Ein» before a complete backfill.
- **How to know it works:** on the demo tenant the page says «Aus»; after Knovas switches it to `brokered` it says
  «Ein» on the next page view.

### CR-R7: the end-to-end rights test under `brokered`, and what counts for § 8.1

- **Why:** for the Platform «Ein» is `brokered` (CR-R1). The job `rbac-wall` (`scripts/ci/rbac_wall_stack.py`; run
  record `docs/ModernDocs/reports/2026-11-rbac-phase-1-run-record.md`) runs the journey under `enforcing` with groups in
  the request body (first green run 18 Nov 2026), a posture the Platform never uses. Two green runs of that journey do
  not show that the Platform's «Ein» keeps the walls.
- **What:** the same journey under `brokered`: a broker key registered for the test tenant; every search, document
  read and `document_readable` carries a claim signed as the Platform signs it (`typ knovas-principal+jws`, EdDSA,
  `sub`, `tid`, `grp`, `rol`, at most 300 seconds, `jti` burned). Steps at least: a walled document is invisible to a
  claim without its group and visible to one with it or a group above it; an open document is visible to every claim;
  a request without a claim, an expired, replayed or foreign-tenant claim is refused; the control run without
  enforcement reports the leak. Each run's record line names the posture, the commit and the run id.
- **What counts for § 8.1:** two green runs of this journey, of the Core version deployed for the pilot, each on the
  run record with run id, date, commit, posture `brokered` and result. Knovas copies the two lines into the
  installation's configuration (`enforcement.rights_runs`, PMVP-62's release note); the Platform refuses to ask for
  «Ein» without two.
- **Recommended:** Knovas's switch step (`scripts/ops/rbac_switch.py`, or CR-R1's route) refuses `brokered` unless it
  is given two such runs, so the gate also holds where the switch happens.
- **Depends on:** CR-R1. **Used by:** PMVP-42, PMVP-43 (the pilot's truth table runs on top of it), § 11's measure
  "documents shown to a person without the right: 0".

**Needed for the pilot's «Ein» (15 December):** CR-R1, CR-R1a, CR-R2 and CR-R7 with two green runs. A route for the
firm's second administrator to carry the switch out itself (CR-R8, a dual-control token the Platform signs) is
**not** requested: it waits for the founder's decision 1 in `work-orders/PMVP-42.md`.
