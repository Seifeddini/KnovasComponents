# Redesign for a hosted Platform: server-side text, RC-served documents, federated identity

**Status:** design sketch / decision input — not approved, nothing implemented.
**Date:** 2026-09-02
**Follows:** `2026-09-02-hosted-knovas-platform.md` (the brainstorm this answers)
**Repos:** same file in `KnovasComponents` and `KnowledgeBase`.
**Touches:** `KnovasComponents/docs/superpowers/plans/2026-08-14-section-b-buildout.md` — Part 3 changes that plan's identity design; read that interaction before starting work.

---

## Why these three are one problem

Each of the three couplings is the same missing thing wearing a different hat: **a request has no
verifiable subject, so the system substitutes physical proximity.** Search context comes from a file
the Platform can read because it is on the same host. Document bytes come from a share the Platform
can mount because it is on the same network. Identity is a shared password because there is nobody
to be. Move the Platform into our cluster and all three fail at once — which is why they should be
redesigned together, not patched one at a time.

The design below establishes the subject once, at login, from the customer's own IdP, and then
carries it to two independent verifiers.

```mermaid
sequenceDiagram
    participant B as Browser (customer LAN)
    participant P as KnovasPlatform (our cluster)
    participant K as secure-api (our cluster)
    participant R as RemoteController (customer host)
    participant I as Customer IdP

    B->>I: OIDC auth-code + PKCE
    I-->>B: ID token (sub, groups)
    B->>P: session established (RP verifies ID token)
    P->>K: POST /secured/query + principal assertion (IdP-signed)
    K->>K: verify vs tenant's registered JWKS; tenant from mTLS CN
    K-->>P: hits + chunk text + context  (Part 1)
    P-->>B: result list + per-hit grant token (Knovas-signed, 120 s)
    B->>R: GET /files/<pointer> + grant + ID token   (Part 2)
    R->>R: verify grant (Knovas JWKS) AND ID token (IdP JWKS), sub must match
    R-->>B: document bytes — never leave the customer network
```

**The property that makes this worth building:** no single party can read a customer document.
Knovas cannot mint a subject — it does not hold the IdP signing key. The IdP cannot authorize a
pointer — it does not hold the Knovas signing key. Compare with today, where the Platform on the
customer's VM has the whole share mounted read-only and one shared password in front of it.

---

# Part 1 — `/secured/query` returns the text

## What is already true

| Fact | Evidence |
|---|---|
| Verbatim chunk text is already stored | `SentenceChunk.original_text`, written at `weaviate_service.py:215-217`, backfilled idempotently by `weaviate_manager.py:907` |
| It is already fetched server-side | `fetch_sentence_chunk_text_by_uuid` (`weaviate_service.py:602`), called on the cross-encoder path at `query_two_stage.py:618` |
| Text is already returned to callers | `ingested_summary.text` in every result row (`query_two_stage.py:1033`) |
| The response deliberately drops it | `top_chunks_payload` (`query_two_stage.py:955-967`) carries page/sentence numbers and scores, no text — as `knovas_client.py:738` records: *"top_chunks holds extra match locations (no chunk text)"* |
| Which is why the sidecar exists | RC writes it (`knovas_uploader.py:155`), Platform reads it (`app.py:548`, `:2438`) |

So this is **not** a storage change and **not** a new data-protection boundary. It is a response-shape
change to a field the pipeline already has in memory.

## KB-1 — `include_text` on `/secured/query`

```jsonc
// request
{ "Input": "...", "include_text": true }

// response, per result row
"top_chunks": [
  { "chunk_uuid": "…", "cosine_similarity": 0.71, "rerank_score": 8.4,
    "page_number": 3, "sentence_number": 118, "sentence_number_end": 121,
    "text": "Die Parteien vereinbaren …",
    "text_source": "verbatim",      // or "degraded" — see the migration note
    "text_truncated": false }
]
```

- **Default off.** Opt-in per request, with a per-tenant kill switch (`QUERY_INCLUDE_TEXT_ENABLED`),
  because response size is the one thing that changes for existing integrators.
- **Caps:** `QUERY_TEXT_MAX_CHARS_PER_CHUNK` (2000 — the value `context_store.py:16` already uses),
  `QUERY_TEXT_MAX_CHUNKS_PER_DOC`. Truncation is flagged, never silent.
- **Implementation:** one batched `fetch_sentence_chunk_text_by_uuid` **after**
  `dedupe_then_truncate` (`query_two_stage.py:1006`), so the fetch is bounded by
  `stage2_top_documents × max_chunks_per_doc`, not by the Stage-1 candidate set. `chunk_uuid` is
  already in `top_items` (`cid`) and simply stops being discarded.
- **Cost:** one extra tenant-scoped Weaviate fetch per query when the flag is on; zero when off. On
  the cross-encoder path the text is already loaded — reuse `text_by_chunk` instead of refetching.

## KB-2 — `POST /secured/document_context`

The sidecar's real value is not the hit sentence, it is the ±N sentences around it and the
first-page snippet. That is a windowed read of chunks we already store.

```jsonc
// request
{ "pointer": "tenant/Akte4711/Brief.docx", "sentence_number": 118,
  "radius": 10, "include_first_page": true }

// response
{ "sentences": [ { "sentence_number": 108, "page_number": 3, "text": "…" }, … ],
  "first_page": { "text": "…", "truncated": true },
  "truncated": false }
```

- **Implementation:** a windowed variant of `fetch_document_text_for_summarization`
  (`weaviate_service.py:1380`) — it already filters `belongs_to_document` and returns
  `page_number` / `sentence_number` / `original_text`. Slice around the requested sentence instead
  of concatenating everything.
- **Access control:** `_document_visible_to_caller` (`secure_api.py:306`), **404 not 403**, exactly
  as the other non-query document routes do (GI-ACCESSROLES-09). A text-returning endpoint is a
  document read.
- **Logging:** the returned text is never logged (GI-DATA-02); only counts and byte totals.

## What this deletes

| Repo | Gone |
|---|---|
| KnovasComponents (RC) | `src/sync/context_sidecar.py`, the `write_context_sidecar` call at `knovas_uploader.py:155`, `/var/rc-state/search_context`, `scripts/build_context_sidecars.py` |
| KnovasComponents (Platform) | `src/context_store.py`, `SEARCH_CONTEXT_STORE_PATH`, `_DEFAULT_CONTEXT_STORE_PATH` (`app.py:548`), the `rc-state:/var/rc-state:ro` mount (`docker-compose.yml:39`) |
| Both | The class of support tickets that reads "the preview is empty on this host" |

It also closes a quiet access-control hole: today `/api/document/<id>/preview-content` reads a file
by path off the mount with no group check, so document text reaches the browser outside the ACL.
Server-side text goes through `_document_visible_to_caller` by construction. (This is what KC-B3-1
was going to have to fix route by route.)

## Migration note — the one that will bite

`fetch_sentence_chunk_text_by_uuid` prefers `original_text` and falls back to `text`, which is
*BM25 preprocessing output — lowercased, compound-split, deduplicated, digits stripped*
(`weaviate_service.py:609-614`). Returning that as "the sentence" would look broken to a user and
would be worse than showing nothing. Therefore:

1. Return `text_source: "verbatim" | "degraded"` and let the caller suppress degraded text; or omit
   the field entirely for degraded chunks — decide once, in the API contract.
2. Add a per-tenant coverage check (`% of SentenceChunk with original_text`) to
   `manage_weaviate.py`, and require ≥ some threshold before enabling `include_text` for a tenant.
3. Tenants below the threshold get a reindex, not a flag.

---

# Part 2 — documents opened and previewed via the RC

## The mapping already exists

The RC derives the Knovas pointer as `f"{identifier_prefix}/{relative_path}"`
(`knovas_uploader.py:139-141`). It is reversible: strip the prefix, and you have the path relative
to the documents root. The sync-state table (`sync_state_db.py:15-21`, keyed on `relative_path`)
gives an allow-list for free — **the RC serves only files it actually ingested**, never an arbitrary
path under the mount.

## KC-1 — a file blueprint on the RC

```
GET /files/<path:pointer>              → bytes (inline | attachment)
GET /files/<path:pointer>/thumbnail    → PDF page 1 as PNG (optional)
HEAD /files/<path:pointer>             → existence + size + mtime, for can_open
```

Guards, all of them:

- **Own blueprint, own auth.** Do *not* reuse `_RC_DECORATORS` (`sync_control.py:18-24`):
  `require_same_origin` is a CSRF defense for the employee control plane and would reject the
  cross-origin `GET` this route exists to serve. The file blueprint gets token auth plus an explicit
  CORS allow-list containing exactly the tenant's Platform origin.
- **Safe-join** under the documents root; reject any traversal, symlink escape, or absolute pointer.
- **Allow-list** from sync state — an un-ingested path is 404, not 403.
- **Rate limit** per IP and per subject (`util/rate_limiter.py` is already there).
- **Local audit line** per served file: timestamp, subject, pointer, bytes, outcome. The customer
  can see every file Knovas's UI caused to be read, on their own host.
- `X-Content-Type-Options: nosniff`, explicit `Content-Disposition`, no directory listing, no
  range-serving of anything not in the allow-list.

## KC-2 / KB-3 — the two-party grant

```
grant  (EdDSA, signed by Knovas, published JWKS)
  iss  https://api.knovas.ch
  aud  rc:<tenant-uuid>
  tid  <tenant-uuid>          must equal the RC's own tenant
  sub  <IdP subject>          the user the Platform authenticated
  ptr  <pointer>              exactly one document
  ops  ["read"] | ["read","thumbnail"]
  exp  iat + 120 s            jti single-use
```

The browser presents **two** credentials to the RC:

| Header | Signed by | Proves |
|---|---|---|
| `Authorization: Bearer <grant>` | Knovas | *this document* is authorized (ACL, RBAC, walls all evaluated server-side) |
| `X-Knovas-Id-Token: <ID token>` | the customer's IdP | *this person* is who the grant names |

The RC verifies both, against two independent JWKS, and requires `grant.sub == idtoken.sub`,
`grant.aud == rc:<own tenant>`, unexpired, `jti` unseen. Knovas's JWKS is fetched and **pinned** at
enrollment; the IdP's comes from the tenant's own OIDC discovery document.

- **120 s and single-use** matches what the Platform's `open_tokens.py` already does
  (`URLSafeTimedSerializer`, `max_age=120`, `jti` burned in SQLite) — the mechanism moves, the
  semantics don't.
- **Single-token mode** for tenants with no IdP: grant only. Document it as the weaker mode,
  because it is the one where Knovas alone can cause any ingested file to be read.

## KC-3 / KB-4 — TLS and DNS with no customer work

The browser has to reach the RC over HTTPS it already trusts; asking for an internal CA re-imports
the burden hosting is meant to remove. Instead:

1. `rc-<tenant>.knovas.ch` — a **public A record pointing at the customer's private IP**.
2. Certificate from Let's Encrypt via **DNS-01**, the machinery already built for `api.knovas.ch`
   (`2026-07-28-letsencrypt-api-knovas-ch.md`) precisely because that host could not do HTTP-01.
3. Delivered and renewed over the channel that already delivers and renews the tenant client
   certificate.

Customer does no DNS work and no PKI work. **Two things to verify before committing to this:**
some corporate resolvers apply DNS-rebinding protection and will drop a public name that resolves
into RFC1918 (mitigation: a documented resolver exception, or split-horizon DNS where the customer
already runs internal DNS); and TLS-terminating proxies may need the origin allow-listed.

## The fallback ladder

Per tenant, per capability, degrading per hit rather than per deployment:

| | Path | Needs |
|---|---|---|
| 1 | **Öffnen via UNC / local mount** | Nothing but the path mapping — works today, must never regress |
| 2 | **RC fetch** — preview, thumbnail, download | Browser can reach `rc-<tenant>.knovas.ch` |
| 3 | **Text only** — Part 1's chunk text and context | Always available |

A roaming user off the VPN silently lands on 3. A user in the office gets 2. Neither needs a
different deployment.

---

# Part 3 — identity

## KB-5 — register the tenant's IdP, not a broker key

Per client, on the Knovas side:

| Field | Purpose |
|---|---|
| `idp_issuer` | exact `iss` match |
| `idp_jwks_uri` | key discovery, cached with rotation |
| `idp_audience` | the Platform's client id for that tenant |
| `idp_subject_claim` | default `sub` |
| `idp_group_claim` | default `groups` |
| `idp_group_map` | IdP group GUID → Knovas access-group id |
| posture | `DISABLED` / `ENABLED` / `BROKERED` (unchanged semantics) |

Administered through the existing internal-API per-client PUT pattern and the admin console.

## KB-6 — verify it in `PrincipalResolver`

`principal_resolver.py` gains an assertion path beside the body-groups path:

- signature verified against the tenant's registered JWKS; **`alg` pinned server-side**, never read
  from the token header;
- `iss` and `aud` exact-match the registration;
- **`tid` — i.e. the tenant the assertion claims — must equal the tenant from the mTLS CN.** The
  certificate stays the tenant authority (GI-TENANT-03 untouched); the assertion only adds a
  subject.
- `exp` within the configured TTL; `jti` burned in Redis;
- mapped groups resolved **inside the certificate tenant** — an unmapped IdP group is ignored (never
  widens), no mapped groups means `asserted=False`, which is still "unrestricted documents only"
  (GI-ACCESSROLES-06 untouched);
- under `BROKERED`, a request with body-asserted groups and no valid assertion **fails closed**, it
  does not degrade to the unrestricted path.

That list is Section B's KB-B2-1…3 with one substitution: **the registered key belongs to the
customer's IdP instead of to a broker process.** Everything downstream — `actor_ref` binding, the
ledger, the Alloy models, GI-BROKER-01…04 — keeps its shape.

## KC-4 — the Platform becomes an OIDC relying party

Authorization-code + PKCE, discovery-based JWKS validation, server-side sessions (so disabling an
account ends access on the next request, not at cookie expiry), JIT provisioning of the local
profile row. This is Section B's **KC-B1-4, which that plan deliberately scheduled last**; hosting
makes it first, and in exchange **KC-F1…F3 and KC-B1-1…3 — the local user database, argon2id
passwords, TOTP enrolment — stop being needed for federated tenants.** We store an opaque subject
id and a group mapping. No credential, no MFA secret, no password-reset path, no breach surface.

## Tenants with no IdP

Two honest options, in preference order:

1. **Split-plane** — identity stays on the customer's host, in the RC, and the hosted UI redirects
   login there. Preserves the Section B boundary exactly. Costs the RC a user-facing surface — but
   note it needs *the same* browser-trusted RC endpoint that Part 2 already builds, so the marginal
   infrastructure is zero.
2. **Knovas-hosted local accounts** — argon2id + TOTP, i.e. Section B's KC-B1-1…3 running on our
   infrastructure. Fastest, and the only design here that puts firm staff PII in Knovas-operated
   PostgreSQL. If we offer it, offer it as a migration ramp with an end date, not as a tier.

## What this does to the Section B plan

| Section B item | Under a hosted Platform |
|---|---|
| KC-F1…F3 (local identity DB, migrations, bootstrap admin) | **Not needed** for federated tenants; needed only for the local-accounts fallback |
| KC-B1-1…3 (argon2id, sessions, TOTP) | Sessions stay (server-side); passwords and TOTP move to the IdP |
| KC-B1-4 (OIDC, scheduled last) | **Becomes the foundation, scheduled first** |
| KC-B2-2 (Ed25519 broker JWS) | **Dropped** — the IdP token is the assertion |
| KB-B2-1…3 (broker key registration + verification + BROKERED) | **Re-pointed** to IdP issuer/JWKS/audience; logic unchanged |
| KC-B2-3, KC-B3-*, KC-B5-*, KC-IN-* (groups tab, walls, approvals, ingestion) | **Unchanged** — they move to a hosted console, they don't change shape |
| GI-BROKER-01…04 | Restated with "registered tenant IdP key" for "registered broker key" |
| The residual-risk paragraph | **Deleted, not relocated.** The plan says full elimination needs "an IdP-signed token verified directly by secure-api" — this is that |

**Sequencing consequence:** Section B steps 01–03 are exactly the work this supersedes. They are the
first thing to pause if this direction is taken.

---

# Build list

## KnowledgeBase

| ID | Change | Files |
|---|---|---|
| KB-1 | `include_text` on `/secured/query`; batched text fetch after truncate; caps; `text_source` | `services/query_two_stage.py:955-1006`, `api/secure_api.py:1173`, `config/defaults.toml` |
| KB-2 | `POST /secured/document_context` — windowed sentence read, ACL-gated, 404-not-403 | `api/secure_api.py`, `services/weaviate_service.py:1380` |
| KB-3 | Grant minting service + `/.well-known/knovas-jwks.json`; EdDSA key in Key Vault with rotation | `services/document_grant_service.py` (new), internal API, `infra/` |
| KB-4 | `rc-<tenant>.knovas.ch` issuance + delivery + renewal on the existing cert channel | `infra/kubernetes/…`, cert-manager DNS-01 |
| KB-5 | Per-client IdP registration (issuer, JWKS, audience, claim names, group map) + admin console tab | `api/internal_api.py`, `DB/migrations/`, `website/admin/` |
| KB-6 | Assertion path in `PrincipalResolver`; alg pinned; `tid` == cert tenant; `jti` in Redis; BROKERED fail-closed | `services/rbac/principal_resolver.py`, `services/rbac/assertion.py` (new) |
| KB-7 | `original_text` coverage report + reindex path per tenant | `CLI/manage_weaviate.py` |
| KB-8 | Golden Invariants, Alloy models and mutants (below) | `docs/Docs/01_SYSTEM/`, `models/alloy/` |

## KnovasComponents

| ID | Change | Files |
|---|---|---|
| KC-1 | RC file blueprint: `/files/<pointer>` (+ thumbnail, HEAD), safe-join, sync-state allow-list, rate limit, audit line, CORS allow-list | `RemoteController/src/routes/files.py` (new), `src/app.py:33-38` |
| KC-2 | Grant + ID-token verification on the RC; pinned Knovas JWKS; tenant IdP JWKS; `jti` store | `RemoteController/src/auth/document_grant.py` (new) |
| KC-3 | RC TLS listener for `rc-<tenant>.knovas.ch`, cert install + renew | `RemoteController/`, compose, docs |
| KC-4 | Platform as OIDC RP; server-side sessions; principal on every content route | `docbridge_integration/src/web_interface/app.py:684-724, :877` |
| KC-5 | Platform consumes `include_text` + `document_context`; delete `context_store.py` and the sidecar reader | `src/knovas_client.py`, `app.py:548, :2438`, `src/context_store.py` |
| KC-6 | RC stops writing sidecars | `src/sync/knovas_uploader.py:155`, `src/sync/context_sidecar.py` |
| KC-7 | Platform mints nothing itself — grants come from Knovas; open-token minting retires or moves | `src/open_tokens.py`, `app.py:1478-1560` |
| KC-8 | OneDrive `web_url` as ingest metadata instead of a JSONL on the share | `src/sync/`, `app.py:2317` |
| KC-9 | Docs: certificates, hosting requirements, setup — one component, one bundle | `docs/*` |

---

# Invariants

| ID | Statement |
|---|---|
| **GI-QUERY-04** | Text returned by any retrieval path is verbatim chunk text from the caller's own tenant, capped and flagged when truncated, filtered by the caller's principal, and never written to logs or metric labels. |
| **GI-DOCGRANT-01** | A document grant authorizes exactly one pointer for one subject in one tenant, is single-use within its lifetime, and its audience names exactly one RC. A grant minted for one tenant never verifies at another tenant's RC. |
| **GI-DOCGRANT-02** | The RC serves bytes only when **both** the Knovas grant and the tenant IdP's token verify and name the same subject. Neither credential alone is sufficient. *(Relaxed only in explicitly configured single-token mode, which fails closed if the tenant has an IdP registered.)* |
| **GI-RC-08** | The RC serves only paths recorded in its sync state, resolved under the configured documents root. Traversal, symlink escape, and un-ingested paths answer 404. |
| **GI-HOSTED-01** | The client certificate used for an outbound `/secured/*` call is the one bound to the session's tenant; an unresolvable tenant fails closed. |
| **GI-BROKER-01…04** | Amended: "registered broker key" → "registered tenant IdP key". `alg` pinned server-side, `tid` equal to the certificate tenant, single-use `jti`, revocation bounded by TTL. |

Alloy work rides the same commits, per the house rule: modify `mechanisms/principal_brokering.als`
to take its key from an IdP registration; add `mechanisms/document_grant.als`
(`grantSingleUse`, `grantAudienceBound`, `grantSubjectMatchesIdToken`, `grantPointerBound`) with
mutants `grant__subject_unchecked` (accepts a grant without the ID token — the failure that looks
like it works), `grant__audience_ignored`, `grant__replayed_jti`, `rc__serves_unindexed_path`.

---

# Rollout

Every step leaves a working system, and every step is per-tenant flagged.

| # | Step | Ships |
|---|---|---|
| 1 | KB-1, KB-2, KB-7 — text and context on the API, coverage check | Works for *today's* on-prem Platform too: the sidecar becomes redundant before anything moves |
| 2 | KC-5, KC-6 — Platform reads text from the API, RC stops writing sidecars | One coupling gone, still fully on-prem |
| 3 | KB-5, KB-6, KC-4 — IdP registration, assertion verification, RP | Shared password gone; RBAC becomes subject-bound. Valuable on-prem as well |
| 4 | KB-3, KB-4, KC-2, KC-3 — grants, JWKS, RC TLS | Bytes reachable from a browser that isn't on the Platform's host |
| 5 | KC-1 — RC file routes; Platform preview/download switch to the RC | The last file dependency leaves the Platform |
| 6 | Move the Platform into our cluster (per-tenant deployment) | Hosting, with nothing left to break |

Steps 1–3 are worth doing **whether or not we host**: they delete the sidecar, close the
preview ACL hole, and remove the shared password. Only steps 4–6 are hosting-specific. That
ordering is deliberate — it means the decision to host can be deferred until after step 3 without
wasting any of the work.

---

# Risks worth naming now

- **`original_text` coverage.** If a pilot tenant's corpus predates the property, step 1 shows
  degraded text or nothing. Measure before promising previews.
- **DNS rebinding protection** may drop `rc-<tenant>.knovas.ch` → private IP on some corporate
  resolvers. Verify with the pilot's actual resolver before designing the UI around it.
- **The RC becomes a user-facing service.** Today it listens on `127.0.0.1` and nothing points at
  it. A browser-reachable TLS listener needs a patch cadence, request limits, and a security review
  it has never had.
- **Response size and latency** on `include_text` — bounded by the caps, but measure against the
  query SLO before defaulting it on for any tenant.
- **Two-party grants raise a support question**: "the preview is broken" now has two verifiers and
  two clock skews. The RC's audit line and a `GET /files/_diagnose` that reports which check failed
  (without leaking why) will pay for themselves.
- **Section B rework.** Steps 01–03 of that plan are in flight and this supersedes them. The cost of
  deciding late is higher than the cost of deciding now.

---

# Open questions

1. **Single-token mode** — do we ship it at all, or is an IdP a hard prerequisite for hosted
   tenants? It is the only mode where Knovas alone can cause a customer file to be read.
2. **Does `include_text` default on** for new tenants once coverage is verified, or stay opt-in
   forever?
3. **Who owns the grant signing key rotation** — same cadence and runbook as the tenant CA, or its
   own?
4. **Does the RC serve thumbnails** (moving `render_first_page_png` and its PDF dependency into the
   RC image), or does the browser render PDF page 1 itself from the streamed bytes?
5. **Split-plane identity** — do we commit to building it, or is "bring an IdP" the answer to every
   tenant that objects?
