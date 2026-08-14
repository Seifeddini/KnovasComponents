# Section B Buildout — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development`
> or `superpowers:executing-plans` to implement this plan task-by-task.

**Goal:** Close Pflichtenheft requirements B1, B2, B3 and B5 — individual identity,
server-verified user→group mapping, ethical walls, and four-eyes control — plus the firm-side
administration console and ingestion management that make them operable.

**Spec:** Knovas Pflichtenheft §3 Section B (buyer-side requirements analysis, 14 August 2026).
Rendered plan: <https://claude.ai/code/artifact/6bbc763a-6a16-4afb-b07b-3386816393df>

**Repositories:** this plan spans two. `KnovasComponents` (customer-hosted) and
`KnowledgeBase` (Knovas backend). A copy of this file lives in both.

**Branch:** `feat/section-b-buildout` in each repo.

---

## The finding that shapes everything

Section B is not four independent gaps. It is one architectural fact with four consequences.

1. **KnovasComponents never sends `access_groups`.** A grep of
   `KnovasPlatform/components/docbridge_integration/src/` returns zero occurrences. Every call the
   shipped Platform makes to `/secured/query` arrives with the field omitted, which
   `PrincipalResolver` resolves to `asserted=False` — "unrestricted documents only"
   (GI-ACCESSROLES-06). The firm's RBAC tree exists and is never consulted.
2. **KnowledgeBase cannot verify it if we do send it.**
   `app/src/services/rbac/principal_resolver.py` states the boundary in its own docstring: tenant
   comes from the mTLS certificate and is unforgeable; `access_groups` comes from the request body
   and "we do not cryptographically bind them to an end user." A deliberate v1 decision.

There is no authenticated subject anywhere in the request path. Build the subject, make it
verifiable across the mTLS boundary, and B1/B2/B3/B5 become tractable in sequence.

## Division of labour

> **KnovasComponents holds the people. KnowledgeBase holds the enforcement.**

The firm's users, credentials, role and group grants, and the console that administers them live on
the firm's own hardware and never leave it. KnowledgeBase never learns a lawyer's name — it learns a
signed opaque subject id and a group list, and its job is to refuse anything that is not signed.

The identity database is a **new, local PostgreSQL in KnovasComponents**, not the existing
`postgres:15-alpine` in `KnowledgeBase/knovas-software/local_setup/docker-compose.yml`. That one is
Knovas-operated and holds every tenant's data; putting firm staff accounts there would move personal
data across the boundary the product sells.

---

## Foundation — the local identity store

| ID | Change | Files |
|----|--------|-------|
| KC-F1 | New `platform-db` service: `postgres:15-alpine`, database `knovas_platform`, **no published port**, password via secret file (mirrors the `POSTGRES_PASSWORD_FILE` pattern KnowledgeBase already uses). Volume `platform_db_data`. | `KnovasComponents/docker-compose.yml`, `KnovasPlatform/docker-compose.yml`, `knovas.env.example` |
| KC-F2 | Migration runner and schema; dated `.sql` files plus `schema_migrations`, deliberately copying the shape of `KnowledgeBase/app/src/CLI/manage_migrations.py`. | `src/identity/db.py`, `src/identity/migrations/*.sql`, `src/identity/migrate.py` |
| KC-F3 | First-boot admin from `PLATFORM_ADMIN_EMAIL`, one-time password to a 0600 file, `must_change_password=true`, MFA enrolment forced. Refuse to boot on a weak value (reuse the check at `app.py:701`). | `src/identity/bootstrap.py`, `src/web_interface/app.py:684-724` |

Two concepts share the schema and must never be conflated:

- **Platform roles** (`admin`, `approver`, `ingestion_manager`, `member`) govern what a user may
  *do inside the Platform*. Local.
- **Access groups** are Knovas-side RBAC group ids from `GET /secured/access_groups` and govern what
  a user may *see*. `user_access_groups` is the join that B2 signs.

Tables: `users`, `roles`, `user_roles`, `user_access_groups`, `access_group_cache`, `sessions`,
`approval_requests`, `audit_log`, `ingestion_profiles`, `settings`, `schema_migrations`.

---

## B1 — Individual user accounts with SSO/OIDC + MFA

**Demand:** Entra ID / Google Workspace federation; joiner-mover-leaver lifecycle. A shared firm
password is disqualifying on its own. **Today: MISSING.**

`app.py:684-724` reads a single `web.login.username` / `web.login.password` pair;
`require_company_login` (`:877`) sets one boolean `session['company_login_ok']` (`:978`). The
brute-force throttle at `:708` documents the reason it is per-IP: "the shared login is a single
credential."

**All B1 work is in KnovasComponents. KnowledgeBase is unchanged for B1.**

| ID | Change | Why it closes B1 |
|----|--------|------------------|
| KC-B1-1 | Per-user local auth: argon2id, password policy, per-*user* lockout beside the existing per-IP throttle, forced rotation on `must_change_password`. | Removes the shared firm password |
| KC-B1-2 | Server-side sessions. `session['company_login_ok']` → `session['sid']`, looked up in `sessions` on every request; `require_company_login` → `require_authenticated_user`. | Makes **leaver** real: disabling an account ends access on the next request, not at cookie expiry |
| KC-B1-3 | TOTP MFA with recovery codes; mandatory for `admin`. Federated users delegate to the IdP. | The "+ MFA" clause |
| KC-B1-4 | OIDC: authorization-code + PKCE against Entra ID / Google Workspace, `id_token` validated with discovery JWKS, JIT provisioning, optional group-claim → access-group mapping. | The SSO clause and the automatic half of joiner-mover-leaver |
| KC-B1-5 | Admin console **People** tab at `/admin/people`. Create/disable/re-enable, assign roles, reset password, force MFA re-enrol, revoke sessions. All audit-logged. | The requested administration page; manual joiner-mover-leaver |
| KC-B1-6 | Refuse the old mode: with `identity.enabled` true, fail to start if shared-login config is present. | Prevents the disqualifying configuration surviving an upgrade |

---

## B2 — Server-verified user→group mapping

**Demand:** The RBAC groups a request asserts must come from an authenticated identity, not from
whatever the client claims; group hierarchy manageable by the firm. **Today: PARTIAL.**

B2 has two halves and both must ship. Platform-side group resolution is the *useful* half; backend
verification is the half that turns PARTIAL into a control the firm can attest to — otherwise the
same `curl` with the tenant certificate still reads everything.

### KnovasComponents — become the broker

| ID | Change |
|----|--------|
| KC-B2-1 | `src/identity/principal.py`: resolve the caller's group list **server-side** from `user_access_groups`. A group list supplied by the browser is rejected with 400 — never merged, never ignored. |
| KC-B2-2 | `src/identity/assertion.py`: Ed25519 JWS carrying `sub` (opaque user uuid), `tid`, `grp`, `rol`, `iat`/`exp` (120 s), `jti`. Attached to every outbound Knovas call (`src/knovas_client.py:946, :1757`). |
| KC-B2-3 | Admin console **Access groups** tab. Sync the tenant tree from `GET /secured/access_groups` (hierarchy + `epoch`) into `access_group_cache`; create/rename/delete through the existing endpoints; assign to users; block saving when `epoch` moved. |

### KnowledgeBase — become able to verify it

| ID | Change |
|----|--------|
| KB-B2-1 | `PUT/GET /admin/clients/<client_id>/principal_broker_key`, following the per-client admin PUTs at `internal_api.py:1525-1727`; key on `clients` with a rotation column + migration. |
| KB-B2-2 | `services/rbac/assertion.py` + an assertion path in `PrincipalResolver.from_request`. `alg` pinned to `EdDSA` server-side and never read from the token header; `tid` must equal the certificate tenant; `jti` burned in Redis. |
| KB-B2-3 | New posture `BROKERED` beside `DISABLED`/`ENABLED`. Under it, a request with body-asserted groups and no valid assertion fails closed with 401 — it does not degrade to "unrestricted only". Existing integrators keep `ENABLED`. |
| KB-B2-4 | Bind `actor_ref` to the verified subject. `graph_api.py:1082, 1221, 1244, 1266` overwrite it from `sub`; the docstring at `:1172` is updated. |
| KB-B2-5 | Update the recorded decisions: `principal_resolver.py` docstring, `GI-ACCESSROLES-*`, the Alloy model and `app/tests/alloy_invariants/`. |

**Residual risk (state it to the buyer):** the assertion narrows the trust boundary from "anything
holding the tenant certificate" to "the Platform's broker process on the firm's own host." It does
not eliminate it — that host holds both the certificate and the signing key. Full elimination needs
per-user client certificates or an IdP-signed token verified directly by secure-api.

---

## B3 — Ethical walls enforced everywhere

**Demand:** A walled lawyer gets no trace of the matter — not in search, previews, the graph,
reports, or AI answers. Screening changes logged. **Today: PARTIAL.**

Three gaps; only the middle one is new engineering.

- **(a) The UI never asserts.** Closed for free by KC-B2-1.
- **(b) Three surfaces bypass the object ACL that already exists** — see the correction below.
- **(c) Screening changes are not logged** as an evidentiary record.

### Correction: the graph is further along than its label

An earlier revision of this plan proposed building node-level visibility. **It already exists.**
`DB/migrations/20260804_kg_object_acl.sql` gives nodes, edges, categories and tags the same
`(access_group_ids, acl_reader_ids, acl_epoch)` triple documents carry;
`GraphAccessGuard.object_is_visible` / `filter_objects` enforce it on `GET /secured/graph/nodes` and
`/nodes/<id>`; `filter_edges` already implements "an edge is only as visible as its least visible
endpoint", with a docstring naming exactly the leak it prevents. **GI-GRAPH-12 states all of this
and is marked Covered (Alloy + pytest), code landed.**

The catalog therefore contradicts itself: **GI-GRAPH-11** says topology "(node types, nodes, edges,
sections, categories, tags, filters) is deliberately not access-controlled"; **GI-GRAPH-12**, two
rows below it in the same file, says otherwise. The code agrees with GI-GRAPH-12.

What actually remains:

1. **The export.** `GET /secured/graph` (`graph_api.py:1808`) calls `_tenant()` instead of
   `_caller()` and dumps node_types, nodes, edges, categories and tags with no principal at all.
   Every neighbouring node route filters; this one does not. One call, the whole matter list.
2. **Four node-attached routes reach a node by id without checking it:**
   `POST /nodes/<id>/sections` (`:835`), `PATCH|DELETE /sections/<sid>` (`:858`),
   `PATCH|DELETE /filters/<fid>` (`:1319`), `PATCH|DELETE /identifiers/<iid>` (`:1522`). Each takes
   `_tenant()` only, so each answers differently for "hidden node" and "no such node" — existence
   oracles, which GI-GRAPH-11's own 404-not-403 clause forbids — and each permits a write into a
   node the caller cannot see.
3. **Node types** — the one genuine open decision. Recommendation: leave them tenant-level. A type
   is schema ("Mandate", "Client", "Opposing Party"), not an instance, so it names no matter. The
   export filters regardless of this choice.

Three places still describe the superseded decision and must be corrected with the code:
`graph_access.py:22-38`, the `_caller()` docstring at `graph_api.py:145-148`, and GI-GRAPH-11.

### KnovasComponents

| ID | Change |
|----|--------|
| KC-B3-1 | Principal on every content route — search, document, preview, thumbnail, preview-content, download, client-path, ontology, and the LLM generate/summarize calls (`app.py:1065-1460`). |
| KC-B3-2 | Preserve 404-not-403 end to end. No route reveals that an id exists. |
| KC-B3-3 | Bind the companion open-token to the minting user's principal (`app.py:1212-1319`). The redeem path is exempt from session CSRF (`:900-930`) and is therefore the natural bypass. |
| KC-B3-4 | Admin console **Walls** tab. Per matter: which groups reach it, who changed it and when. Changes go through the B5 approval queue. |

### KnowledgeBase

| ID | Change |
|----|--------|
| ~~KB-B3-1~~ | ~~Node-level visibility: `kg_node_access` + a `node_visible()` rule.~~ **Withdrawn — already built** (`20260804_kg_object_acl.sql`, `GraphAccessGuard`, GI-GRAPH-12). Building it again would create a second, competing permission model over the same objects. Kept as a struck row so the reasoning stays auditable. |
| KB-B3-2 | **The export.** `GET /secured/graph` (`graph_api.py:1808`) switches from `_tenant()` to `_caller()` and runs its five collections through the guard every other node route already uses — `filter_objects` for nodes, categories and tags, `filter_edges` with the visible node ids for edges. |
| KB-B3-2b | The four node-attached routes (sections ×2, filters, identifiers) switch to `_caller()` and gate on `object_is_visible` before touching the node. |
| KB-B3-2c | Node types stay tenant-level unless decided otherwise — the one genuine open decision in B3. |
| KB-B3-3 | Screening ledger: append-only `kg_access_events` / `access_group_events` with verified actor, following `fact_event_ledger.py`; readable via `GET /secured/access_events`. |
| KB-B3-4 | Invariants and tests: revise GI-GRAPH-11, extend the Alloy model, assert every graph read path resolves a principal. |

**Open verification item:** retrieval is filtered, but tenant-wide derived statistics (the German
BM25 corpus model, the two learned identifier channels) have not been measured for score drift on a
walled corpus. Until that test runs, describe B3 as "enforced on every read path", not "no trace".

---

## B5 — Four-eyes on destructive and sensitive actions

**Demand:** Matter deletion, wall changes, bulk exports require a second confirmer.
**Today: PARTIAL.** `/secured/delete_all_documents` (`secure_api.py:2697`) requires
`confirm_client_id` to echo the caller's own tenant — and its docstring is explicit that this "is a
typo guard, not authentication."

### KnovasComponents

| ID | Change |
|----|--------|
| KC-B5-1 | `src/identity/approvals.py` over `approval_requests`. Requester ≠ approver enforced both in the service and by a SQL check constraint; requests expire. |
| KC-B5-2 | Guarded actions: matter (graph node) deletion, wall/ACL change, bulk export, tenant purge, ingestion-profile changes that widen or halt coverage. |
| KC-B5-3 | Dual-control token: single-use, action + target + requester + approver, 15 min, signed with the same Ed25519 key as KC-B2-2. |
| KC-B5-4 | Admin console **Approvals** tab: pending queue with a readable diff, approve/reject with a reason. |

### KnowledgeBase

| ID | Change |
|----|--------|
| KB-B5-1 | `services/rbac/dual_control.py`, reusing the broker key from KB-B2-1. Reject on requester == approver, target mismatch, expiry, or burned `jti`. |
| KB-B5-2 | Guard `/secured/delete_all_documents`, `/secured/delete_information_object`, graph node `DELETE`, `PUT /secured/document_access` behind `clients.require_dual_control`. The existing `confirm_client_id` typo guard stays — it is orthogonal. |
| KB-B5-3 | Persist the approval on the Knovas side (`tenant_purge_service.py`, `deployment_event_service.py`) so the trail survives loss of the firm's Platform host. |

---

## Ingestion administration — and making sync config easy

**Today:** RemoteController already exposes everything a console needs — `/discover`, `/sync`,
`/sync/start`, `/sync/stop`, `/sync/status`, `/sync/config`, `/metrics` — but every route is gated
by `require_internal_access` (`src/routes/sync_control.py:19-24`), which verifies a **Knovas
employee** JWT. The firm's own administrator cannot configure their own ingestion.

`RemoteController/docs/configuration.md` presents the difficulty as a feature: **"Two configuration
layers."** *What* to sync is a `POST /sync` body (`sync_request.schema.json`); *when and how fast* is
a separate file (`remote_controller_sync_config.schema.json`). To change one thing an administrator
must today hand-write two JSON documents against two schemas, know that `max_document_age_seconds`
exists in **both** with a precedence rule, hold a Knovas employee JWT, set mode `0600` on four files,
and know that `save_last_sync_body` silently persists the last body. Six kinds of knowledge for one
decision: "index this folder, nightly, and wall it to the litigation group."

### One profile. One form. One write.

A single versioned `ingestion_profiles` row is the only artifact a human edits. The Platform
*compiles* it into the two RemoteController documents and pushes them together. Nobody opens
`remote_controller_sync.json` again.

| In the form | What the administrator does | What the Platform compiles |
|-------------|-----------------------------|----------------------------|
| **Folders** | Picks folders from a browser backed by `/discover` — never types a path. Per folder: recursive or not, and **which access group its documents get**. | `sources[].path`, `recursive`, `access_groups` → sync body |
| **What to index** | Named file kinds as chips — *Documents*, *E-mail* — not glob patterns. Profile-level, because `sync_request.schema.json` puts `include_globs`/`exclude_globs` under `filters`, not under a source. Common junk (`~$*`, `Thumbs.db`, `.git/`) is excluded without being asked for. | `filters.include_globs`, `filters.exclude_globs` → sync body |
| **When** | *Continuously* · *Nightly, outside office hours* · *Only when I start it*. "Advanced" discloses window and interval. | `mode`, `window.start_local`/`end_local`, `scan_interval_seconds` → sync config |
| **How fast** | *Gentle* · *Normal* · *Fast*, each stated as a consequence ("Gentle: about 300 documents an hour, no noticeable load"). "Advanced" discloses the numbers. | `rate_limit.*`, `max_files_per_cycle` → sync config |
| **Age cut-off** | One control, one place: "Ignore documents older than …". | `filters.max_document_age_seconds` → sync body **only**; the config-file default is never written, so the precedence rule stops existing |

- **Validated before it is sent.** The Platform holds both JSON Schemas and validates in the form.
- **Dry run before saving.** "Preview" calls `/discover` — *≈ 12'400 files · 3.2 GB · 41 unsupported
  · 118 older than your cut-off*.
- **Versioned, attributed, reversible.** Every save is a new version; restore is one action.
- **No JWT, no chmod, no file editing.** Authentication is the admin's own Platform session.
- **One status line**, from `/sync/status` and `/metrics`, with Start and Pause beside it.
- **"Copy for support"** emits the profile as redacted JSON.

| ID | Change |
|----|--------|
| KC-IN-1 | `require_tenant_admin` on RemoteController, verifying a broker-signed assertion with `ingestion_manager` in `rol`. Sits **beside** the employee path; each route declares which principals it accepts. |
| KC-IN-2 | `ingestion_profiles` as source of truth: versioned row holding the sync body and the sync config, with author, approver, timestamp. |
| KC-IN-3 | Admin console **Ingestion** tab. |
| KC-IN-4 | **Per-source default access group.** Extend `sync_request.schema.json` so each `sources[]` entry carries optional `access_groups`; `knovas_uploader.py:191` passes it to `/secured/init_document_transmission`, which already materialises the ACL. Without this, every new ingest reopens the wall it just closed. |
| KC-IN-5 | RC stays unpublished — `knovas-internal` only; the console is the sole firm-facing surface. |
| KC-IN-6 | **The profile compiler.** One function turns a profile row into the two RC documents, validates each against its shipped schema before any network call, and pushes them as a unit with rollback. Presets are named constants in one table. |
| KC-IN-7 | **Preview and rollback.** `/discover` dry-run before save; every save a new version; restore re-compiles and re-pushes. |

KnowledgeBase needs no change for ingestion administration:
`/secured/init_document_transmission` already accepts `access_groups`, and
`/remote_controller/verify_operator` keeps working unchanged for Knovas staff.

---

## Formal model — the Alloy work (KnowledgeBase)

This repository does not accept a stray `.als`. A new model must carry `@code_under_check` and
`@invariant_id` headers; every cited `GI-*` must exist in `docs/Docs/01_SYSTEM/Golden_Invariants.md`
(`scripts/check_alloy_coverage.py`); every pred added to `mechanisms/*.als` must appear in
`models/alloy/ci/obligations.yaml` bound to ≥1 statically-resolvable pytest test **and** ≥1 mutant
(`scripts/check_alloy_obligations.py`); every check and mutant needs an entry in
`ci/expected_results.json`; the component must be listed in
`docs/ModernDocs/alloy/component-manifest.yaml`.

| ID | Model | What it states | Serves |
|----|-------|----------------|--------|
| AL-1 | **Modify** `domain/principals.als` — `sig Subject`, `sig Assertion`; `Principal` gains `prSubject`, `prAssertion` | Today `Principal` is `prTenant + prGroups` with no provenance — the model cannot express "where did these groups come from." Kept as preds, never facts, exactly as `ResolvedPrincipal` is, so the analyzer can still construct the forged states the code must reject. | B2 |
| AL-2 | **Create** `mechanisms/principal_brokering.als` — `signedByTenantBroker`, `algPinned`, `assertionTenantBound`, `assertionFresh`, `assertionUnreplayed`, `BrokeredIdentityMechanism` | One pred per code step, in the style of `mechanisms/identity_resolution.als`. `algPinned` is its own conjunct precisely so a mutant can drop it. | B2 |
| AL-3 | **Create** `data_plane/principal_assertion.als` — `groups_bound_to_subject`, `assertion_cannot_cross_tenant`, `expired_assertion_never_grants`, `replayed_jti_never_grants`, `brokered_never_falls_back_to_body` | The last check is the important one: under `BROKERED`, body-asserted groups with no valid assertion must fail closed, not degrade to GI-ACCESSROLES-06's "unrestricted only" — which would look like correct behaviour while disabling the control. | B2 |
| AL-4 | **Add** `check staleness_bounded_by_ttl` | The formal statement of why the TTL is 120 s: a subject disabled at the Platform keeps access for at most one assertion lifetime. The only part of B1's leaver property KnowledgeBase can enforce. | B1, B2 |
| AL-5 | **Extend** `data_plane/kg_object_acl_assignment.als` — not a new model — with `edge_visible_implies_both_endpoints_visible` and `every_topology_read_resolves_a_principal` | The object ACL is already modelled, with seven checks on assignment and dominance. Two properties GI-GRAPH-12 *states* are not modelled: the endpoint rule (the file contains no reference to `node_lo` or an endpoint, and no mutant exercises it) and that a read path resolves a principal at all — exactly the conjunct `GET /secured/graph` drops. | B3 |
| AL-6 | **Create** `lifecycles/dual_control.als` — `four_eyes_requires_two_distinct`, `token_single_use`, `token_target_bound`, `expired_token_never_executes`, `approval_precedes_execution` (temporal) | A lifecycle because it is inherently temporal. `token_target_bound` stops an approval for one matter authorising another. | B5 |
| AL-7 | **Modify** `lifecycles/tenant_purge.als`, `lifecycles/acl_mutation.als` | Purge and ACL mutation gain the dual-control precondition when the tenant flag is set. | B5 |
| AL-8 | **Modify** `core/remote_controller_guard.als`, `entities/remote_controller_requests.als` | The paths are *alternatives*, not a widening: a tenant-admin assertion authorises only its own client's RC, and neither path relaxes GI-RC-01's conjunctive employee checks. | Ingestion |
| AL-9 | **Modify** `system.als` — `e2e_brokered_read_is_subject_bound`, `e2e_topology_never_oracles`, `e2e_destructive_needs_two_people` | Each composes mechanisms from ≥2 subsystems, as the composition root requires. Plus a liveness witness — a check that passes vacuously is worse than no check. | B2, B3, B5 |

### Mutants

| New mutant | The bug it embodies | Kills |
|------------|---------------------|-------|
| `mutants/broker__alg_from_header.als` | Algorithm read from the token header — algorithm confusion | AL-2 |
| `mutants/broker__no_tenant_binding.als` | Assertion tenant not compared with certificate tenant | AL-2 |
| `mutants/broker__body_groups_fallback.als` | Under BROKERED, missing assertion falls back to body groups. The failure that looks like it works. | AL-3 |
| `mutants/broker__jti_reused.als` | Replay cache not consulted | AL-2 |
| `mutants/kg_object_acl__read_without_principal.als` | A topology read that resolves no principal — this mutant **is** `GET /secured/graph` today, and it must fail | AL-5 |
| `mutants/kg_object_acl__edge_near_endpoint_only.als` | Edges filtered on the near endpoint only. The code is already correct here; the mutant proves the model would catch a regression, which today it would not. | AL-5 |
| `mutants/dual__self_approval.als` | Requester may approve their own request | AL-6 |
| `mutants/dual__token_replay.als` | A token executes more than once | AL-6 |
| `mutants/dual__target_swap.als` | An approval for one target authorises another | AL-6 |
| `mutants/rc__tenant_admin_widens_employee_path.als` | The new path relaxes the employee checks instead of standing beside them | AL-8 |

### Golden Invariants

| ID | Statement | Status |
|----|-----------|--------|
| GI-BROKER-01 | Asserted groups are accepted only from an assertion signed by the tenant's registered broker key, algorithm pinned server-side, asserted tenant equal to the certificate tenant, within lifetime, single-use identifier. | New |
| GI-BROKER-02 | Under BROKERED a request without a valid assertion fails closed. It never degrades to the GI-ACCESSROLES-06 path. | New |
| GI-BROKER-03 | The actor recorded in the fact-event ledger is the assertion subject. No body field can set it. | New |
| GI-BROKER-04 | A subject revoked at the broker loses access within one assertion lifetime. The bound is the TTL, stated not assumed. | New |
| GI-GRAPH-11 | **Amended — a contradiction fix, not a reversal.** The sentence "Graph topology … is deliberately not access-controlled" already contradicts GI-GRAPH-12 two rows below it in the same file, which gives nodes, edges, categories and tags an ACL and is marked Covered. Narrowed to node types, which is what it is still true of. | Amend |
| GI-GRAPH-14 | Every read path that returns graph objects resolves the caller's principal and filters through GI-GRAPH-12's guard — the `GET /secured/graph` export included. No route reaches a node by id without first deciding whether the caller may see it. *(Renumbered: first proposed as GI-GRAPH-12, which is taken; 13 is taken too.)* | New |
| GI-DUAL-01 | A guarded destructive operation executes only against a valid, single-use dual-control token whose requester and approver differ and whose target equals the request's target. | New |
| GI-DUAL-02 | Dual control is per-tenant opt-in via `clients.require_dual_control`. When set, omitting the token is a rejection, never a bypass. | New |
| GI-RC-07 | RC admin routes are handled for either an allowlisted Knovas employee (GI-RC-01) or a tenant-admin assertion for that RC's own client. Disjoint alternatives; neither relaxes the other. | New |

### CI and pinning artifacts

- `models/alloy/ci/expected_results.json` — one entry per new check (`no_counterexample`) and per
  mutant (`counterexample_found`).
- `models/alloy/ci/obligations.yaml` — an obligation per new mechanism pred, naming code paths,
  resolvable pytest tests, and mutant.
- `app/tests/alloy_invariants/` — `test_principal_assertion.py`, `test_dual_control.py`, and
  additions to the existing `test_kg_v1_alloy_pins.py` for AL-5's two new commands, plus
  `@pytest.mark.alloy_obligation` markers.
- `docs/ModernDocs/alloy/component-manifest.yaml` — new `L2-PRINCIPAL-BROKER`; updated lists for
  `L2-SECURE-API`, `L2-INTERNAL-API`, the knowledge-graph components.
- `docs/Docs/05_TESTS/alloy_component_coverage_matrix.md`.

> **Sequencing note the model imposes:** `mutants/kg_object_acl__read_without_principal.als` encodes
> *today's* behaviour as a failure. It cannot be committed before KB-B3-2 lands, or CI goes red
> against shipped code. Model changes ride in the same commit as the behaviour they describe.

---

## Documentation

> **Found while surveying — fix this first.** The Secure API reference exists in **three** copies
> and they have **already drifted**: `KnowledgeBase/docs/Knovas_Developer_Kit/api/Secure_API.md`,
> `KnovasComponents/docs/KnovasAPI/Secure_API.md`, and
> `KnovasComponents/KnovasPlatform/knovas-docs/…/03_API/Secure_API.md` have three different
> checksums. The Client Integration Guide too. This plan changes the Secure API contract, so it will
> drift three ways unless one copy becomes canonical and the others become a CI sync step.

### KnowledgeBase

| Document | Change |
|----------|--------|
| `Docs/01_SYSTEM/Golden_Invariants.md` | Eight new GIs, one amendment. Lints against the models. |
| `Docs/01_SYSTEM/Trust_Boundaries_and_Security_Model.md` | The broker is a new trust boundary. One line becomes two: certificate for tenant, assertion for subject. |
| `Docs/01_SYSTEM/Decisions/ADR-0003-platform-as-identity-broker.md` | **New ADR.** Why the subject is asserted by the customer's Platform rather than per-user certificates or a direct IdP token, and what stays trusted. |
| `Docs/01_SYSTEM/Decisions/ADR-0004-graph-topology-access-control.md` | **New ADR, reframed.** Not a reversal — GI-GRAPH-12 already reversed it for nodes, edges, categories and tags. Records that the reversal is finished (export + four node-attached routes), that node types stay tenant-level, and why three places still describe the superseded decision: `graph_access.py:22-38`, the `_caller()` docstring at `graph_api.py:145-148`, and GI-GRAPH-11. | B3 |
| `Docs/01_SYSTEM/Multi_Tenant_Data_Model.md`, `Data_Flows.md` | New `clients` columns, the screening ledger, and the assertion's path through the request flow. (No new graph ACL table — `20260804_kg_object_acl.sql` already documents that one.) |
| `Docs/01_SYSTEM/IOAccessControl-System_Client_Documents.md`, `Security_and_Compliance_Overview.md` | From "the client asserts groups" to "the client's broker proves them". The passage a Datenschutzbeauftragte reads. |
| `Knovas_Developer_Kit/api/{Secure_API,Knowledge_Graph_API,Client_Integration_Guide}.md` | `principal_assertion`, the BROKERED 401, the dual-control token; graph topology reads principal-scoped and 404. |
| `Knovas_Employee_Kit/` | Register a tenant broker key, rotate it, set the posture, enable `require_dual_control`. Without these the feature ships unprovisionable. |
| `Docs/08_RUNBOOKS/` | Broker key rotation and compromise; dual-control break-glass. |
| `Docs/05_TESTS/Alloy_Unified_Model_Guide.md`, `alloy_component_coverage_matrix.md`, `Docs/01_SYSTEM/GI_RC_07_Tenant_Admin_Sync_Path.md` | §7 obligations, the matrix, and a GI-RC-07 note following the GI-RC-06 pattern. |

### KnovasComponents

| Document | Change |
|----------|--------|
| `README.md` | The quickstart says "fill 4 values". It becomes five — the administrator's email — and first run hands back a one-time password. |
| `docs/certificates.md` | This file opens by warning that mismatched cert filenames "are the most common setup failure." The broker signing key is a third key artifact and belongs here, or it becomes the *next* one. |
| `docs/hosting-requirements.md` | A hosting partner now runs a database holding the firm's user accounts. Disk, memory, and a backup obligation. |
| `KnovasPlatform/README.md`, `docs/setup.md` | Replace `COMPANY_LOGIN_NAME`/`COMPANY_LOGIN_PASSWORD` with bootstrap admin, MFA, optional OIDC. |
| `KnovasPlatform/docs/administration/{users-and-roles,access-groups-and-walls,approvals,ingestion,identity-database}.md` | **Five new documents**, one per console tab, for a firm's IT contact. `identity-database.md` is the one nobody would think to write and matters most: if they never back it up they lose every account and grant. |
| `KnovasPlatform/docs/integration/open-tokens-api.md` | Tokens become principal-bound. |
| `KnovasPlatform/docs/deployment/*`, `docs/platforms/{windows,debian,ubuntu}.md` | New container, volume, secret file; data-directory permissions per platform. |
| `RemoteController/docs/configuration.md` | The **"Two configuration layers"** section is demoted to a troubleshooting appendix; the document opens by pointing at the Platform's Ingestion tab. |
| `RemoteController/docs/{SETUP,operations,onboarding-checklist,local-setup}.md`, `README.md` | Tenant-admin path; start/stop from the console; the checklist loses its hand-edit steps. |
| `docs/KnovasAPI/*`, `KnovasPlatform/knovas-docs/…/03_API/*` | The two drifted mirrors. Resynchronise from canonical in CI, or replace with a pointer. Do not hand-edit a third variant. |
| `RELEASE_NOTES.md`, `RemoteController/CHANGELOG.md`, `docs/specifications.md` | A breaking upgrade — the shared login stops working. Needs an upgrade note with a migration path. |

---

## Order of work

Each step leaves the system working and shippable.

| # | Repo | Step |
|---|------|------|
| 01 | KC | PostgreSQL, migrations, bootstrap admin — KC-F1…F3 |
| 02 | KC | Per-user auth, server-side sessions, MFA — KC-B1-1…3, B1-6 |
| 03 | KC | Admin console shell and People tab — KC-B1-5 |
| 04 | KB | Broker key registration, assertion verification, BROKERED — KB-B2-1…3 + AL-1…4 + the four broker mutants, same commits |
| 05 | KC | Broker minting and principal on every call — KC-B2-1, B2-2, B3-1. This single step makes search, previews and AI answers wall-aware |
| 06 | KC | Access-groups tab, tree sync and assignment — KC-B2-3. **B2 complete** |
| 07 | KB | Finish topology filtering — KB-B3-2, B3-2b, B3-4 + AL-5 + ADR-0004 (KB-B3-1 withdrawn: already built) |
| 08 | KC | 404-not-403, open-token binding, Walls tab — KC-B3-2…4 |
| 09 | KB | Screening ledger and actor_ref binding — KB-B2-4, B3-3. **B3 complete** |
| 10 | KC | Approvals workflow and queue — KC-B5-1…4 |
| 11 | KB | Dual-control enforcement — KB-B5-1…3 + AL-6, AL-7 + the three dual mutants. **B5 complete** |
| 12 | KC | Ingestion: profiles, compiler, RC firm-admin path, per-source ACLs — KC-IN-1…7 (+ AL-8 in KB) |
| 13 | KB | Compose the new mechanisms in `system.als` — AL-9 |
| 14 | KC | OIDC federation — KC-B1-4. Deliberately last: additive, most IdP-variable, and local accounts plus MFA already remove the disqualifying shared password |

**Documentation is not at the end.** The Golden Invariants rows, model headers and manifest entries
are lint inputs — they ship in the same commit as their model or CI fails. The two ADRs land with
the decision they record. Everything else is written in the step whose feature it describes, with
one exception: **deduplicate the three Secure API copies before step 4.**

---

## Honest limits

- **B4 (per-user attributable audit) is not built.** The `audit_log` table exists after KC-F2 and
  receives the events B1/B2/B3/B5 generate, and KB-B2-4 makes the Knovas-side fact ledger
  attributable. Missing: reinstated engagement telemetry, read/search/download/export coverage,
  retention policy, works-council consent design. Data plumbing after this plan, not architecture.
- **B6 (role-appropriate defaults) is not built.** `roles`/`user_roles` and the group-assignment UI
  are the substrate; the role-shaped default views are a separate product exercise.
- **The Platform host remains a trusted component.** It holds both the tenant certificate and the
  broker signing key.
- **Ranking-signal leakage across walls is unverified.**
- **Nothing here touches C4 (PMS sync), mailbox coverage, or time capture** — the largest missing
  hour levers in §2 of the Pflichtenheft. Section B is the permission hurdle, not the payoff hurdle.
