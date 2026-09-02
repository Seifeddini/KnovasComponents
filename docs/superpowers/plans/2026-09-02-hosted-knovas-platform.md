# Hosting KnovasPlatform ourselves — ship only the RemoteController

**Status:** brainstorm / decision input — not approved, nothing implemented.
**Date:** 2026-09-02
**Repos:** this document is a copy; the same file lives in `KnovasComponents` and `KnowledgeBase`.
**Related:** `KnovasComponents/docs/superpowers/plans/2026-08-14-section-b-buildout.md`, `KnovasComponents/docs/hosting-requirements.md`, `KnovasComponents/docs/certificates.md`, `KnowledgeBase/docs/Docs/01_SYSTEM/Decisions/ADR-0001-mtls-as-primary-security-boundary.md`.

---

## The question

Today a customer deployment is **two** components on **their** VM: `RemoteController` (RC — reads the
file share, uploads to Knovas) and `KnovasPlatform` (the search UI). What if we run the Platform in
**our** cluster and ship the customer nothing but the RC?

The RC has to stay on-premise — it is the thing that touches the file share. The Platform is, at its
core, a browser client for `/secured/query`. There is no law of nature that puts it on the
customer's hardware. But there are eight concrete couplings that do, and three of them are real
architecture, not configuration.

---

## TL;DR

**It is worth doing, and it makes the security story stronger, not weaker — but only if identity
federates to the customer's IdP rather than moving into our database.**

Recommended shape:

| Decision | Take |
|---|---|
| Where it runs | **One deployment per tenant** in our cluster (`<tenant>.knovas.ch`), GitOps ApplicationSet — the app stays single-tenant, so the security model doesn't change |
| Identity | **OIDC federation to the customer's Entra ID / Google Workspace.** We store an opaque subject id and group ids, never a credential |
| Section B's broker | Replace the tenant broker key with **direct IdP-token verification in `secure-api`** — the Section B plan already names this as the stronger endpoint it couldn't reach |
| Document bytes (PDF preview, thumbnails, download) | **Browser → local RC** with a Knovas-signed, short-lived, single-use token. Files never leave the customer network and no inbound hole is opened |
| Search-context sentences | New **server-side context endpoint** on `secure-api` — Weaviate already holds the chunks; the on-prem sidecar is a workaround for an API gap |
| Fallback | Everything above degrades cleanly to **search-only + open-via-UNC**, which works today with two config flags |

The thing to *not* do is host the Platform and keep its identity design: that would put the firm's
staff accounts in Knovas-operated PostgreSQL and hand us both the tenant certificate and the
assertion signing key — reversing the recorded Section B boundary and giving us nothing in return.

---

## 1. What hosting actually removes from the customer

From `docs/hosting-requirements.md`, the hosting-partner handover checklist has 12 items. Hosting
the Platform deletes six of them outright and shrinks two more.

| Handover item today | After |
|---|---|
| VM sized for N employees (up to 8 vCPU / 16 GB at 100 users) | Sized for a batch sync job — the concurrent-search workload leaves |
| Ubuntu 24.04, Docker, NTP | unchanged |
| SSH from VPN | unchanged (RC install) |
| VM → file share read-only | unchanged |
| Outbound HTTPS to Knovas API | unchanged |
| **Internal DNS or hosts entries for `knovas.<company>.ch`** | **gone** |
| **Platform TLS certificate for the FQDN, trusted on employee PCs** | **gone** |
| **HTTPS on 443 via host NGINX** | **gone** |
| **443 allowed from employee subnets/VPN only** | **gone** |
| Pilot folder path + read-only credentials | unchanged |
| (OneDrive) Graph credentials | unchanged |
| **Second mTLS bundle, different filenames, different directory** | **gone** — one bundle, one component |

`docs/certificates.md` exists almost entirely because the *same three PEM files* need three
different names in two different directories with two different uid requirements. Half of that
document evaporates. Add to that ~35 environment variables in `KnovasPlatform/.env.example` that
stop being the customer's problem, and the customer-facing surface becomes: one container, one env
file, one certificate bundle.

**The strategic gain is bigger than the setup gain.** Every UI change — Trefferliste, Cortex,
preview, branding — currently ships at the customer's upgrade cadence, over SSH, per VM. The search
UI is the surface that sells the product and it is gated by the slowest actor in the chain. Hosted,
we ship daily and we can finally see errors, latencies and usage instead of debugging blind.

**What stays on-premise:** the RC container, the read-only share mount, outbound HTTPS, the
certificate bundle, and — unchanged, because it was never our software — the employee PC's own
access to the share for **Öffnen**.

---

## 2. The coupling inventory

Everything the Platform gets from being on the customer's host, with evidence.

| # | Coupling | Evidence | Difficulty |
|---|---|---|---|
| 1 | **Document bytes** — PDF preview, thumbnail, download, DOCX/TXT/MSG markdown preview, `can_open` verification | `KnovasComponents/docker-compose.yml:37` mounts the share at `/mnt/autodoc:ro`; routes at `app.py:1279/1319/1349/1388` all `send_file` from it | **Hard** |
| 2 | **Search-context sidecars** — the sentences around each hit and the first-page snippet in the result card | RC writes them at `RemoteController/src/sync/knovas_uploader.py:155` → `context_sidecar.py:23` (`/var/rc-state/search_context`); the Platform reads them at `app.py:548` (`/mnt/autodoc/.search_context`), consumed in `_enhance_search_results` (`app.py:2438`). The unified compose shares the volume read-only (`docker-compose.yml:39`) | **Hard** |
| 3 | **Identity** — one shared company password per deployment; `WEB_SECRET_KEY` signs both the session cookie and the open tokens | `app.py:684-724`, `require_company_login` at `:877`; the per-IP throttle at `:708` documents why: "the shared login is a single credential" | **Hard** (already being rewritten by Section B) |
| 4 | **Tenant mTLS certificate on disk**, plus auto-renew via `/secured/sign_certificate` | `SEMANTIX_CLIENT_CERT/KEY/CA` in `.env.example`; renewal in `knovas_client.py:1096-1231` | **Medium** |
| 5 | **OneDrive enrichment JSONL** on the share | `SEARCH_ENRICHMENT_PATH=/mnt/autodoc/.search_enrichment.jsonl`, `_load_search_enrichment` (`app.py:2317`) | **Medium** |
| 6 | **Cortex/ontology state** — a writable fixture JSON plus a filter-state file on local volumes | `ONTOLOGY_FIXTURE_PATH`, `ONTOLOGY_FILTER_STATE_PATH`; `ontology_store.py:227` persists in place | **Easy** — `ONTOLOGY_SOURCE=graph` (`app.py:1683`) already reads the real KG API instead |
| 7 | **Open path mapping** — container path → Windows UNC / Linux mount | `OPEN_UNC_ROOT`, `OPEN_LOCAL_ROOT`, `OPEN_CLIENT_LOCAL_ROOT`; pure string mapping, no I/O | **Easy** — becomes a per-tenant config record |
| 8 | **Per-deployment `.env`** — branding, company display name, search tuning, identifier prefixes | `KnovasPlatform/.env.example` | **Easy** — per-tenant config record + a console tab |

Couplings 5–8 are a config-storage exercise: move them out of `.env` and local files into a
per-tenant record in our PostgreSQL, editable in the existing admin console
(`website/admin/console.html`). Couplings 1–3 are the actual design work.

---

## 3. The three hard ones

### 3.1 Document bytes

Note first what is **not** at stake. **Öffnen** already runs entirely in the user's browser: the
server only maps a container path to the UNC/local path that PC should use
(`docs/integration/opening-documents.md`). With `verify_files_on_disk: false` — a config flag that
exists today for large SMB corpora (`app.py:2434-2436`) — that mapping needs no file access at all.
So the primary open path survives hosting untouched. What needs bytes is **preview, thumbnail and
download**.

| Option | How | Cost |
|---|---|---|
| **A. Search-only** | Ship with preview/thumbnail off; open via UNC | Free today (two flags). Loses a feature we demo |
| **B. Text preview from Weaviate** | We already store the chunk text (GI-DATA-01 names Weaviate as an intended storage boundary). Add a `secure-api` endpoint that returns document text by pointer | New endpoint; covers DOCX/TXT/MSG. **No new data at rest** |
| **C. Browser → local RC** ⭐ | Hosted Platform mints a short-lived signed token; the *browser*, which is on the LAN anyway, fetches bytes straight from the RC | Bytes never leave the customer network, no inbound hole. Needs a LAN-trusted TLS cert on the RC and CORS |
| **D. Cloud → RC pull** | We call the customer's RC. The machinery exists: `clients.remote_controller_base_url` + probe (GI-RC-03), `rc_instance_token_hash` (GI-RC-05) | Re-opens an inbound hole at the customer and routes document bytes through us — trades one inbound port for another |
| **E. Reverse tunnel** | RC dials out, we proxy through it | No inbound, but the most new infrastructure and bytes still transit us |
| **F. Renditions at ingest** | RC uploads a preview rendition alongside the text | Simplest at runtime; new binary data at rest at Knovas → a data-protection conversation we don't need |

**Recommendation: B for text, C for PDF, A as the default when a tenant configures neither.**

The interesting part of C is the certificate, and we have already solved that problem once. `Öffnen`
requires the browser to reach the RC over HTTPS the employee's browser trusts; asking the customer
for an internal CA re-imports the burden hosting was supposed to remove. Instead: we issue
`rc-<tenant>.knovas.ch` with a public A record pointing at the customer's **private** IP and a
Let's Encrypt certificate obtained by **DNS-01** — exactly the mechanism built for `api.knovas.ch`
(`docs/superpowers/plans/2026-07-28-letsencrypt-api-knovas-ch.md`), which exists precisely because
that host couldn't do HTTP-01 either. The certificate ships to the RC through the channel that
already delivers its tenant certificate. The customer does no DNS and no PKI work.

The token mechanism also already exists: `src/open_tokens.py` mints `itsdangerous` tokens with a
120 s TTL and burns a single-use `jti` in SQLite. Hosted, the signature moves to a Knovas key and
the RC verifies it against a published JWKS — offline, no callback, same shape as the operator
verification it already performs.

### 3.2 Search-context sidecars

This is the coupling nobody would predict and the one that silently degrades the result list.

The RC extracts each document's sentences at upload time and writes a per-document sidecar; the
Platform reads it to render the sentences around a hit and the first-page snippet. The sidecar
exists because `/secured/query` deliberately does not return chunk text — `knovas_client.py:738`
says so in as many words: *"top_chunks holds extra match locations (no chunk text)"*.

So the on-prem sidecar is a **workaround for an API gap**, not a privacy feature: Weaviate already
holds those sentences. Options:

- **Server-side context endpoint** ⭐ — `secure-api` returns the sentences around a chunk for a
  pointer. Sentence numbering already exists (`2026-07-27-sentence-number-interpolation`). Deletes
  the sidecar writer, the reader, and a whole class of "why is the preview empty on this host"
  support tickets. Do this regardless of hosting.
- Serve sidecars from the RC over the same signed-token path as 3.1 (works, but keeps a mechanism
  that shouldn't exist).
- Upload sidecars to Knovas (duplicates into our storage text we already store).

### 3.3 Identity — the fork that decides everything

Hosting collides head-on with the recorded Section B design, and the collision has to be resolved
explicitly rather than discovered during implementation.

> **KnovasComponents holds the people. KnowledgeBase holds the enforcement.** […] The identity
> database is a **new, local PostgreSQL in KnovasComponents** […] putting firm staff accounts there
> would move personal data across the boundary the product sells.
> — `KnovasComponents/docs/superpowers/plans/2026-08-14-section-b-buildout.md`

And its residual-risk note on the broker assertion:

> the assertion narrows the trust boundary from "anything holding the tenant certificate" to "the
> Platform's broker process on the firm's own host." It does not eliminate it — that host holds both
> the certificate and the signing key. **Full elimination needs per-user client certificates or an
> IdP-signed token verified directly by secure-api.**

If we host the Platform, "the firm's own host" becomes *our* host. We would be signing assertions to
ourselves with a key we also hold, alongside the certificate — the assertion stops being a boundary
at all. Three ways out:

| | Path | Consequence |
|---|---|---|
| **I1** ⭐ | **Federate to the customer's IdP.** Hosted Platform is an OIDC relying party against Entra ID / Google Workspace; the ID token's subject and group claims are the principal. `secure-api` verifies the IdP token **directly** against the tenant's registered issuer + JWKS + audience | We store an opaque subject id and group ids — no credential, no MFA secret, no password reset path. This is exactly the "full elimination" the plan couldn't reach on-prem. **Hosting becomes the reason B2 gets stronger** |
| **I2** | **We host the identity database.** KC-F1…B1-3 as written, running on our infra | Fastest to build, reverses the recorded boundary, puts firm staff PII in Knovas-operated PostgreSQL, needs a new Auftragsverarbeitungsvertrag, and hands a law-firm buyer the objection on a plate. **Avoid** |
| **I3** | **Identity stays on-prem, in the RC.** The RC grows the identity store and the broker key; the hosted UI redirects login to the local RC | Preserves the Section B boundary exactly and keeps "we ship only the RC" literally true — but makes the RC a user-facing web surface with sessions, MFA and TLS. Most engineering |

**Recommendation: I1 as the default, I3 as the documented variant** for a tenant that refuses
federation. Note the convergence: **I3 and option C (browser → RC) need the same thing** — a
browser-trusted TLS endpoint on the RC. If we build `rc-<tenant>.knovas.ch` once, we can offer
either. That is the fallback that keeps the deal alive with the most conservative buyer, without
making everyone else pay for it.

The Section B plan changes as follows under I1: **KB-B2-1** registers a tenant's IdP issuer, JWKS
URI and audience instead of a broker public key; **KB-B2-2/3** verify an IdP-signed token instead of
a broker JWS (`alg` still pinned server-side, `tid` still compared with the certificate tenant,
`jti` still burned); **GI-BROKER-01…04** keep their statements with "registered broker key" replaced
by "registered tenant IdP key". ADR-0003 — *"why the subject is asserted by the customer's Platform
rather than per-user certificates or a direct IdP token"* — is not yet written, so we are not
reversing a published decision, we are picking the other branch before it hardens.

---

## 4. Where it runs

| | Topology | Trade-off |
|---|---|---|
| **T1** ⭐ | **One deployment per tenant.** Own namespace, own Ingress `<tenant>.knovas.ch`, own certificate from Key Vault CSI, generated by an ArgoCD ApplicationSet — the pattern already used for the platform layer | **The app stays single-tenant.** No confused-deputy problem, no new isolation invariant needed to start, near-zero application change: we are lifting the compose file into the cluster. Cost is a pod per tenant (~0.5 vCPU / 512 MB–1 GB), trivial below ~50 tenants |
| **T2** | **One multi-tenant deployment**, tenant resolved from host or session | Cheaper per tenant at scale, but one process now holds N tenants' client certificates and N sessions. Needs a new Golden Invariant, an Alloy model and careful per-tenant cache keying before it can be trusted |
| **T3** ⭐ | **T1 now, T2 when the tenant count justifies it** — but write the tenant-context seam (config lookup, certificate selection) from day one so T2 is a change of wiring, not a rewrite | Recommended |

Two rules for either: run it where it can reach `secure-api` **through the existing NGINX mTLS
edge** — do not add an in-cluster shortcut, GI-TRUST-01 says neither layer alone is sufficient — and
keep the tenant derived from the client certificate CN (GI-TENANT-03), which T1 preserves for free.

---

## 5. New invariants a hosted Platform needs

Sketches, to be sharpened into `Golden_Invariants.md` rows and Alloy models if we proceed:

- **GI-HOSTED-01** — The client certificate used for an outbound `/secured/*` call is the one bound
  to the session's tenant. No request may use another tenant's certificate; an unresolvable tenant
  fails closed. *(Vacuous under T1 — one certificate per pod. Becomes the load-bearing invariant the
  day we move to T2, which is exactly why it should be written under T1.)*
- **GI-HOSTED-02** — A local-file token is single-use, ≤120 s, bound to (tenant, subject, pointer),
  and verified by the RC against the tenant's registered Knovas public key. A token minted for one
  tenant never resolves at another tenant's RC.
- **GI-HOSTED-03** — The hosted Platform persists no document bytes and no end-user credential.
  Stated so it can be tested and so it can be put in front of a buyer.
- **GI-RC-08** — The RC serves file bytes only for a valid GI-HOSTED-02 token, only under the
  configured document root, and never for a path that escapes it. (Stands beside GI-RC-01's
  employee path and the planned GI-RC-07 tenant-admin path; neither relaxes the others.)

---

## 6. What the RC becomes

If it is the only thing we ship, it should be the only thing a customer has to understand.

1. **Self-enrollment.** One enrollment code; the RC fetches its own certificate bundle and writes it
   with the right ownership and mode. This deletes the "most common setup failure" documented in
   `docs/certificates.md` instead of documenting it better.
2. **Local file gateway** (optional, per tenant) — `GET /files/<pointer>` gated by a
   Knovas-signed token, serving preview, thumbnail and download to the browser on the LAN.
3. **Stop writing enrichment and context to the share** — `web_url` becomes ingest metadata,
   sentences come from the server-side context endpoint.
4. **Tenant-admin assertions** (already planned as KC-IN-1) so the hosted console can drive
   discover/sync without a Knovas employee JWT.
5. **Still one container, still `127.0.0.1` by default.** The goal is `docker run` + an enrollment
   code, and a `knovas.env` that has three values instead of the current unified stack's shared
   document mount, Platform port, login password and public base URL.

---

## 7. Two coherent packages

| | **Cloud-first** | **Split-plane** |
|---|---|---|
| Topology | T1 | T1 |
| Identity | I1 — customer IdP | I3 — identity in the RC |
| Bytes | B (text from Weaviate) + A | B + C (browser → RC) |
| RC gets | self-enrollment, tenant-admin path | + identity store, file gateway, LAN TLS |
| Customer needs | an IdP | a browser-trusted RC endpoint (we issue it) |
| Story | "We host the UI; your credentials stay with your IdP, your files stay on your share" | "We host the brains; your people and your files never leave your network" |
| Effort | Lower | Higher |

Both are honest. Neither claims documents stay on-premise, because the corpus text is already in our
Weaviate — that boundary was crossed at ingest, and any pitch that implies otherwise is one question
away from embarrassment. What genuinely stays local in both is **the original binaries** and, in
Split-plane, **the identity of the people searching**.

**I would build Cloud-first, structured so Split-plane is a per-tenant option** rather than a fork:
the RC file gateway and the IdP path are independent switches, and the same `rc-<tenant>.knovas.ch`
certificate serves both.

---

## 8. Phasing

| Phase | Outcome | Roughly |
|---|---|---|
| **0 — Prove it** | Run today's Platform image unmodified in our dev cluster against a pilot tenant, with `verify_files_on_disk=false`, preview/thumbnail off, `ONTOLOGY_SOURCE=graph`. Search works end to end from a browser outside the customer network | days |
| **1 — De-file the Platform** | Per-tenant config record + console tab (couplings 5–8); server-side context endpoint (3.2); text preview from Weaviate (3.1 B). No local file access remains on any code path | weeks |
| **2 — Identity** | OIDC RP in the Platform; issuer/JWKS registration and direct token verification in `secure-api`; Section B's KB-B2-* re-pointed. **This is the phase that must not be skipped** — without it we are running a shared password on the internet | weeks |
| **3 — Restore fidelity** | `rc-<tenant>.knovas.ch` certificate issuance; RC file gateway; PDF preview, thumbnails and download come back | weeks |
| **4 — Shrink the ship** | RC self-enrollment; delete the Platform half of the customer docs; rewrite `hosting-requirements.md` around one component | weeks |

Phase 0 is worth doing on its own even if the answer turns out to be no: it tells us, for a few days
of work, exactly which features break and how loudly.

---

## 9. Risks, honestly

- **The search UI moves from LAN/VPN-only to internet-reachable.** `hosting-requirements.md` currently
  tells buyers to restrict it to the company network. Mitigations: per-tenant IP allowlist, IdP
  conditional access, and — for the buyer who won't move — the Split-plane variant. This will come up
  in every security review; we should have the answer written before the first one.
- **We take on availability for a user-facing app.** Today an outage of the Platform is the
  customer's Tuesday; hosted it is our incident. The SLO document covers the secured API, not a UI —
  that gap needs closing, with someone actually on call.
- **Two deployment modes is a tax.** If we keep self-hosted Platform as an option, every UI change
  needs both paths tested. Decide deliberately whether hosted *replaces* self-hosted or joins it.
- **Section B is mid-flight.** Steps 01–03 (local identity store, per-user auth, People tab) are
  KnovasComponents-side work that I1 would partly discard. The cost of deciding this *late* is
  higher than the cost of deciding it now.
- **Multi-tenant creep.** T1 is safe because the app stays single-tenant. The day someone
  "optimizes" it into one shared deployment without GI-HOSTED-01, we have a cross-tenant bug waiting.

---

## 10. Decisions needed

1. **Hosted replaces self-hosted, or joins it as a second SKU?**
2. **Do we require an IdP for hosted tenants** (I1), or must Split-plane (I3) ship at the same time?
3. **Is an internet-reachable search UI acceptable to the pilot customers**, or do we need IP
   allowlisting / private connectivity from day one?
4. **Does preview fidelity matter enough to build the RC file gateway** (phase 3), or is search-only
   plus open-via-UNC enough for the next 12 months?
5. **Who carries the UI SLO and the pager?**

Question 2 is the one with the longest lead time — it is the one to answer first, because Section B
steps 01–03 are being built against the opposite assumption right now.

---

## Appendix — evidence index

| Claim | Where |
|---|---|
| Share mounted read-only into the Platform | `KnovasComponents/docker-compose.yml:37` |
| RC state volume shared into the Platform | `KnovasComponents/docker-compose.yml:39` |
| Context sidecars written by RC | `RemoteController/src/sync/knovas_uploader.py:155`, `src/sync/context_sidecar.py:23` |
| Context sidecars read by the Platform | `docbridge_integration/src/web_interface/app.py:548`, `:2438` |
| `/secured/query` returns no chunk text | `docbridge_integration/src/knovas_client.py:738` |
| File-serving routes | `app.py:1279` (download), `:1319` (PDF preview), `:1349` (thumbnail), `:1388` (preview-content) |
| Disk verification is already optional | `app.py:2434-2436` (`web.search.verify_files_on_disk`) |
| Shared company login, single credential | `app.py:684-724`, `:708`, `:877` |
| `WEB_SECRET_KEY` signs sessions *and* open tokens | `app.py:719-724` |
| Open tokens: 120 s, single-use `jti`, SQLite | `docbridge_integration/src/open_tokens.py` |
| Ontology can already read the real KG API | `app.py:1683` (`ONTOLOGY_SOURCE=graph`) |
| Öffnen is a browser-side path mapping | `KnovasPlatform/docs/integration/opening-documents.md` |
| Certificate filename divergence | `KnovasComponents/docs/certificates.md` |
| Hosting handover checklist | `KnovasComponents/docs/hosting-requirements.md` |
| RC base URL + probe registered per client | `KnowledgeBase/knovas-software/DB/migrations/20260516_remote_controller_access.sql`, GI-RC-03 |
| RC instance token | `…/20260518_remote_controller_instance_token.sql`, GI-RC-05 |
| DNS-01 certificate issuance already built | `KnowledgeBase/docs/superpowers/plans/2026-07-28-letsencrypt-api-knovas-ch.md` |
| Identity boundary and broker residual risk | `KnovasComponents/docs/superpowers/plans/2026-08-14-section-b-buildout.md` |
| Tenant from certificate CN; mTLS at edge *and* app | `Golden_Invariants.md` GI-TENANT-03, GI-TRUST-01 |
| Weaviate is an intended boundary for document text | `Golden_Invariants.md` GI-DATA-01 |
