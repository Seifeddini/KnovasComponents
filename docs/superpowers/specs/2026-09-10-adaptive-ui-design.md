# Adaptive UI — design

**Date:** 2026-09-10
**Status:** proposed design, awaiting owner guidance (see §12)
**Covers:** a Platform surface that composes itself from two inputs — what the
brain knows about the firm's world (graph, search, proposals) and what the
signed-in person needs and does — under the privacy, honesty and rate-limit
constraints this repository already enforces.
**Repositories:** `KnovasComponents` (customer-hosted Platform) carries
everything here. The brain (`KnowledgeBase`) needs no change for phases 0–1;
the two asks that would deepen it are named in §5.1 and are already in the
Pflichtenheft plan.

---

## 1 · Problem

The question, in the owner's words:

> Someone told me about dynamically adapting websites. The UI builds itself
> based on the user's needs and behaviour. How could we implement this for our
> platform in connection with our brain?

"The UI builds itself" covers four different things, and they should be told
apart before anything is built, because they differ in cost, risk and fit by an
order of magnitude each:

| Rung | What it means | Fit for Knovas |
| --- | --- | --- |
| 1 · Need-driven | The screen follows the person's role and their place in the firm's world: an approver sees what waits for a second signature; a lawyer sees the matters they are responsible for | Yes — derivable today from platform roles and, once semantic roles land, from the graph. Needs no tracking at all |
| 2 · Behaviour-driven | The screen follows what the person did: continue where you left off, the filters you actually use are open, the matter you worked in all week is one click away | Yes — but only per person, only with consent, only on the firm's host, and only over ids, never content |
| 3 · Server-driven layout | The server sends a *layout manifest* (which blocks, in which order, expanded or not, and why); the browser renders known blocks. This is the mechanism that makes rungs 1 and 2 one implementation instead of two | Yes — it is also the natural continuation of the schema-driven typed-node workbench, which already generates forms and readers from data |
| 4 · Generative UI | A language model composes the page at runtime | Not for v1. It is non-deterministic, unexplainable to a works council, needs a model in the Platform's path, and the Platform has none. The manifest contract of rung 3 leaves the door open: a model may later *propose* a manifest, and proposals never commit |

This design delivers rungs 1–3 and states 4 as a later experiment behind the
same contract.

The second half of the question — *in connection with our brain* — is the part
that makes this more than a personalisation feature. The brain (the Knovas
backend: semantic search over the tenant's documents and the Knowledge Graph
shown as Cortex) already holds the firm's structure: matters, parties,
documents, facts, filters that read the estate on the person's behalf, and
placements waiting for review. It also has an analytics channel through which
it learns, tenant-wide, from what people open and dismiss — a channel the
Platform has never fed. Adaptation therefore has two tiers, and the boundary
between them is the one this repository defends everywhere:

> **The brain adapts the substance for the firm. The Platform adapts the
> surface for the person. User identity never crosses.**

## 2 · What already exists

### 2.1 The brain's surface the Platform can reach

| Capability | Where | State |
| --- | --- | --- |
| Semantic search, `query_session_id` on every response | `POST /secured/query` via `KnovasAPIClient.search_documents` (`knovas_client.py:1609`) | LIVE; the session id is passed through (`knovas_client.py:2222`) and then discarded |
| Implicit engagement: `view`, `click`, `download`, `dismiss` with displayed position, ≤ 50 per call, no user identity accepted | `POST /secured/analytics/engagement` (`docs/KnovasAPI/Analytics_Integration_Guide.md`) | LIVE in the API, **never called by the Platform** |
| Explicit feedback: per-query relevance 1–5, permanent document importance/quality | `POST /secured/analytics/relevance-feedback`, `POST /secured/document/rating` | LIVE in the API, unused |
| Graph topology, node detail, neighbourhood, filters and placements, schema | `graph_export`, `graph_node`, `graph_neighbors` (zero callers today), `graph_filters`, `graph_placements` (`knovas_client.py:1742–1880`) | LIVE in graph mode (`ONTOLOGY_SOURCE=graph`); export cached per subject id (`ontology_graph.py`, `_export_cache_key`) |
| Document visibility answered by the backend | `document_readable(pointer)` (`knovas_client.py:1966`) | LIVE; fails closed |
| Semantic roles on schema attributes (`deadline`, `responsible`, `deputy`, `client`, `practice_area`, …) | Pflichtenheft design §5.6 | PLANNED (backend) |
| Identifier search (matter numbers, party names) | Pflichtenheft design §5.8 | PLANNED (backend) |
| Filters, facets, paging, sort on query | Pflichtenheft design §5.3 | PLANNED (backend); the filter rail §6.2 (Platform) follows it |
| Event spine and the Posteingang | Pflichtenheft design §5.5, §6.9 | PLANNED |

### 2.2 The Platform's own signals

| Signal | Where | Notes |
| --- | --- | --- |
| Who is signed in, with which platform roles and access groups | `IdentityGate.current_user()`, `users`, `user_roles`, `user_access_groups` (`identity/migrations/0001_identity.sql`) | `admin`, `approver`, `ingestion_manager`, `member` |
| What waits for a second signature | `ApprovalService.pending()` (`identity/approvals.py:343`) | the approver's need, already computable |
| Sync state of the firm's RemoteController | `RemoteControllerClient.status()` | the ingestion manager's need |
| A key/value store per installation | `settings(key, value JSONB, updated_by)` | the journal design already keeps consent here |
| The Arbeitstag-Journal (matters opened, documents opened, searches run) | Task KC-E-5 in `docs/superpowers/plans/2026-08-15-pflichtenheft-d-j-components.md` | DESIGNED, **not on this branch**; opt-in; query text stored only as a keyed hash; admins see nothing per person |
| How the person uses the interface (which facets, which panels) | — | **nothing today**; `app.js` keeps no state across page loads and uses no browser storage |

### 2.3 Half of "builds itself" is already designed

The typed-node workbench
(`docs/superpowers/specs/2026-09-02-typed-node-workbench-design.md`) generates
every form, column and field reader from the graph schema and forbids a node
type's name in code. That is *structure from the brain*: what a screen can
contain. This design adds the second axis, *salience for the person*: which of
those things this person sees first, expanded, pre-filled or suggested. The two
compose: a block on the node page is still rendered from the schema; the
manifest only says where it sits and whether it is open.

### 2.4 Constraints that shape the design

- **Identity never crosses to Knovas.** The Secure API resolves a tenant from
  the certificate and a group list from the broker assertion; the analytics
  endpoints state that no user identity is accepted. Per-person adaptation is
  therefore computed on the customer's host, full stop.
- **Rate limits.** Query is ~12/min per tenant at the gateway; other secured
  endpoints ~1/s. A start page that issues one graph call per recent item
  would starve the search. Every brain read in this design is bounded and
  cached.
- **Honesty rules.** No screen invents what the API does not return; proposals
  never commit; rejection is permanent and says so; fixture mode degrades to
  an explicit state, never a 500; test data wears a badge.
- **Works council.** The journal design's three properties — off until the
  person turns it on, only the person reads their own, no per-person view for
  administrators — apply to everything here, because "what you worked on
  recently" is the same class of data as a journal.
- **Stack.** Vanilla JS, no build step, design tokens only, German UI copy,
  English code and comments, CSRF on every mutating route, `_sidebar.html` as
  the one shell.

## 3 · Approach

### 3.1 Two axes, two tiers

```
                      structure (what can be shown)      salience (what is shown first)
                      ────────────────────────────       ──────────────────────────────
brain  (firm-wide)    schema, graph, facets, ranking     placements to review, ranking learned
                      [typed-node workbench]              from engagement, "responsible = me"
Platform (per person) —                                  recents, pinned, open panels, scope chips
                                                          [this design]
```

The brain never learns *who* did something; it learns *that* the firm opens
document X for query Y at rank 3, and tunes ranking tenant-wide. The Platform
never re-ranks; it arranges.

### 3.2 A layout manifest, not a page generator

Every adaptive surface asks the server one question —
`GET /api/me/layout?surface=<home|search|node>` — and receives a manifest: an
ordered list of blocks, each with a state (`expanded`, `collapsed`, `hidden`),
optional inline items, and a machine-readable `reason`. The browser holds a
registry of known blocks and renders them in that order. Nothing else changes:
the sidebar, the header, the search box and the preview dialog keep their
places.

Why a manifest and not client-side rules: the decision needs data the browser
must not hold (other people's aggregates, the ACL check, the brain), it must
be testable as a pure function, and it must be explainable in one place. Why
not a page generator: the block vocabulary is fixed and reviewable; a
court-facing product cannot render a component it has never seen.

### 3.3 Stable skeleton, adaptive content

The PRD's own principle ("Predictability: maintain stable layouts") is a hard
rule here. Navigation never reorders. Primary controls never move. The search
box is always first on the start page. What adapts is the *content region
below the search box*, the *order and open state of secondary panels*, and
*suggestions offered as chips*. A suggestion is never applied silently: a scope
chip is one click away, not pre-selected.

### 3.4 Needs before behaviour

Rung 1 works with zero tracking and ships first. A person's platform roles,
the approvals waiting for them, the sync state they manage, and — once
semantic roles exist — the matters where a `responsible` or `deputy` fact
points to the Person node carrying their e-mail identifier, all produce a
start page that is already theirs. Behavioural adaptation (rung 2) is layered
on top for people who switch it on, and the page is complete without it.

### 3.5 Counts, not a diary

Behavioural signals are folded into a bounded per-user **profile** the moment
they arrive: a counter and a last-seen timestamp per key, a capped list of
recent ids. There is no event log of interface use. The profile holds ids only
— node ids, pointers, block ids — and every label is resolved at render time
through the brain, which answers only for what this person may still see
(§6.3). A matter the person was walled off from yesterday therefore cannot
appear in "Weiterarbeiten" today, and there is no title in the database to
leak.

## 4 · Scope

| # | What adapts | Signal | Gate |
| --- | --- | --- | --- |
| 1 | Start page: "Weiterarbeiten" (recent matters and documents), "Wartet auf Sie" (approvals; placements on recent matters), "Meine Akten" | roles, approvals, profile, graph placements, semantic roles | roles: none · profile: consent · placements: graph mode · Meine Akten: semantic roles (PLANNED) |
| 2 | Search: suggested scope chip for the matter the person works in; facet rail order and open state; "Nicht relevant" on a hit | profile, identifier search | scope: `scope` on `/secured/query` (PLANNED) · facets: F3 (PLANNED) · dismiss: none |
| 3 | Node page and Cortex: panel order and open state (fields, neighbourhood, "Warum?", filters) | profile | typed-node workbench (PLANNED); Cortex drawer today |
| 4 | Einstellungen: consent, pinned items, "nicht mehr vorschlagen", reset | — | identity (LIVE) |
| 5 | The brain learns from the firm: engagement reporting from preview, open, download, dismiss | search session | none — **quick win** |
| 6 | Cold start: firm-wide, k-anonymous aggregates seed a new person's defaults | profiles | consent of ≥ `ADAPTIVE_MIN_USERS` people |

**Out of scope, deliberately**

- Reordering navigation or moving primary controls (§3.3).
- Re-ranking or filtering search results in the browser. Ranking is the
  brain's; the Platform feeds it (§5.4) and shows what it returns in the order
  it returns.
- A per-person view for administrators, or any cross-user profile.
- Sending anything about a person to Knovas. Engagement events carry the
  session id, the pointer and the displayed position, as the API documents.
- Machine-learned or generative layout in v1 (§1, rung 4).
- A second read model. Visibility is the backend ACL; a suggestion is only
  ever something the brain returned for this principal (§6.3).
- Time capture. The Arbeitstag-Journal (J2/J3) stays its own feature with its
  own consent; §5.2 says how the two relate.

## 5 · Signals

### 5.1 From the brain

| Need | Call | Budget and cache |
| --- | --- | --- |
| Labels and current visibility of recent nodes | the per-subject topology export already cached by `GraphOntologySource._export` | 0 extra calls while the export is fresh (TTL); a node absent from the export is dropped from the manifest |
| Current readability of recent documents | `document_readable(pointer)` | at most `ADAPTIVE_MAX_RECENT_DOCS` (default 5) calls per home load, cached per user for `ADAPTIVE_TTL_SECONDS` (default 300) |
| Placements waiting for review on the person's recent matters | `graph_placements(node_id, 'active')` | at most `ADAPTIVE_MAX_RECENT_MATTERS` (default 3) calls, same cache; rendered lazily after the page is usable (§8.1) |
| "Meine Akten" | nodes whose `semantic_role ∈ {responsible, deputy}` fact references the Person node with an `email` identifier equal to the signed-in e-mail | PLANNED backend (§5.6/§6.8 of the Pflichtenheft design); until then the block is hidden, never faked |
| Scope suggestion while typing | `GET /secured/graph/identifiers/search` | PLANNED; until then the chip is derived from the `akten_id` groups already in the result list, which the browser has |
| "What needs my attention" at firm scale | `GET /secured/events` via the Posteingang poller | PLANNED; replaces the placements poll above, block contract unchanged |

Two backend asks, both already in the Pflichtenheft plan and merely
re-prioritised by this design: semantic roles on attributes, and identifier
search. Nothing here needs a new endpoint.

### 5.2 From the person

**Consent.** `settings` key `ui.adaptive.consent.<user_id>` →
`{"enabled": bool, "consented_at": iso|null, "version": 1}`. No row means off.
The switch lives in Einstellungen under a new card "Persönliche Anpassung"
with the consent text spelled out (§7.4). It is independent of the journal's
`journal.consent.<user_id>`: a firm may allow one and forbid the other, and
neither implies the other. `ADAPTIVE_UI_DEFAULT` (default `off`) lets a firm
that has settled the question with its works council start people on `on`;
the per-user switch always wins.

**Profile.** One row per consenting user:

```sql
CREATE TABLE IF NOT EXISTS ui_profile (
    user_id    UUID        PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    profile    JSONB       NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`ON DELETE CASCADE` is the leaver rule, as in the journal. The JSON is bounded
by construction:

```jsonc
{
  "v": 1,
  "counters": {                       // key -> [count, last_at]; keys from a closed allow-list
    "facet:author": [12, "2026-09-09T14:02:11Z"],
    "panel:node.evidence": [0, null],
    "block:home.continue_working": [31, "2026-09-10T08:11:40Z"]
  },
  "recent": {
    "matters":   [{"id": "<node uuid>", "n": 5, "at": "…"}],   // ≤ 20, ids only
    "documents": [{"id": "<pointer>",   "n": 2, "at": "…"}]    // ≤ 20, ids only
  },
  "pins":   ["block:home.my_matters"],
  "hidden": ["suggest:scope:<node uuid>"]                      // "nicht mehr vorschlagen" — permanent
}
```

**Signals.** The browser batches small events and posts them to
`POST /api/me/signals` (CSRF-gated, `navigator.sendBeacon` on unload). The
server folds each into the profile and discards it; it never stores the batch.
The allow-list is a module constant, and anything else is dropped the way the
engagement API drops unknown actions:

```
open_matter · open_document · open_evidence · facet_expand · facet_apply ·
panel_open · scope_apply · suggestion_accept · suggestion_dismiss
```

**Never stored:** query text (not even hashed — the journal owns that
question), document titles, node labels, page content, IP or user agent,
anything about another user.

**Decay.** Weight at read time is
`count · 0.5^(age_days / ADAPTIVE_HALF_LIFE_DAYS)` (default 14), so a matter
worked on in March does not outrank the one opened this morning, and nothing
needs a sweep to fade.

**Relation to the journal.** When the journal ships, its recording hook
(`journal_hooks.install_journal_hooks`) fires on the same routes as
`open_matter` and `open_document`. The two stores stay separate on purpose:
the journal is an attributable time record with a retention clock and a CSV
export; the profile is an unattributed shape of preference with no history. A
single hook may feed both, each only under its own consent.

### 5.3 From the firm — cold start without personal data

A new person has no profile. Rather than a blank page or a guessed one, the
manifest falls back to **firm defaults**: aggregates over the profiles of
consenting users, computed only when at least `ADAPTIVE_MIN_USERS` (default 3)
contribute, exposing only block and facet keys with counts — the same
k-anonymity rule the journal design uses for its format statistics. The
Verwaltung shows these aggregates on the System tab ("Welche Filter die
Kanzlei nutzt"), and nothing per person, anywhere.

### 5.4 Feeding the brain — the quick win

`app.js` already knows the displayed position of every hit and every open.
Three hooks and one proxy route close the loop the Analytics guide describes:

| Browser event | Engagement action |
| --- | --- |
| `openPreview(index)` | `view`, `position = index + 1` |
| "Öffnen" / companion / client-path open | `click` |
| degraded download | `download` |
| new "Nicht relevant" affordance on the card | `dismiss` (the only explicit signal; the hit stays visible, greyed, with an undo) |

`POST /api/search/engagement {query_session_id, events[]}` →
`KnovasAPIClient.report_engagement` → `/secured/analytics/engagement`,
fire-and-forget, batched to ≤ 50, never retried, never blocking the UI,
skipped entirely when the session id is the local test sentinel. This carries
no user identity and needs no consent: it is the tenant telling its own search
engine what the firm found useful, which the API documents as the intended
use. It is also the only path in this design by which behaviour changes *what*
is found rather than *how it is arranged*.

## 6 · The decision layer — `src/ui_profile.py`

Pure, Flask-free, in the style of `preview.py` and the planned `journal.py`.

### 6.1 One function

```python
def decide(surface: str, ctx: Context) -> Manifest
```

`Context` carries: the person's roles; consent state; the decayed profile (or
the firm defaults, or nothing); the resolved brain facts the composer fetched
within budget (visible recents with labels, placements count per recent
matter, pending approvals count, sync state); and feature gates (`graph_mode`,
`semantic_roles_available`, `scope_available`, `facets_available`).
`Manifest` is the §8.1 shape. No I/O, no clock reads except the injected
`now`.

### 6.2 Rules, not weights

Rules are a table in code, each with an id, a predicate, an effect and a
German explanation template. First cut:

| Rule | Effect | "Warum sehe ich das?" |
| --- | --- | --- |
| role `approver` and pending approvals > 0 | `home.pending_approvals` expanded, first after search | "Sie sind Freigebende, und N Anträge warten auf eine zweite Person." |
| role `ingestion_manager` or `admin`, RC configured | `home.sync_state` collapsed | "Sie verwalten die Ingestion." |
| ≥ 1 visible recent matter | `home.continue_working` expanded, items by decayed weight | "Zuletzt geöffnet: Dienstag 16:20." |
| recent matter with active placements > 0, graph mode | `home.pending_for_me` with count | "Knovas hat N Passagen zu dieser Akte zur Prüfung vorgeschlagen." |
| semantic roles available, ≥ 1 matter where responsible/deputy = me | `home.my_matters` expanded | "Sie sind als verantwortliche Person eingetragen." |
| one recent matter carries ≥ 60 % of decayed matter weight, scope available | `search.scope_suggestion` chip for it | "Sie haben diese Woche vor allem in dieser Akte gearbeitet." |
| facet weight above median of used facets | facet `expanded`; else `collapsed`; never `hidden` | "Diesen Filter nutzen Sie häufig." |
| `panel:<x>` never opened in 30 days | node panel `collapsed`, never removed | "Dieses Panel haben Sie noch nicht geöffnet." |
| key in `pins` | forced `expanded`, first in its group | "Von Ihnen angeheftet." |
| key in `hidden` | omitted from suggestions | — (the person asked for it) |
| no consent | rung-1 rules only; rung-2 blocks omitted; `profile_state = "off"` | — |

Every emitted block carries `reason` (rule id) and `why` (the rendered
template). The UI shows `why` behind a small "Warum sehe ich das?" control on
adaptive blocks and chips.

### 6.3 Invariants — suggestions never widen

**ADAPT-01.** Every node id or pointer in a manifest was returned by the brain
for *this* principal in *this* request cycle (from the per-subject export or a
`document_readable` check). A profile entry that fails that resolution is
dropped silently and pruned from the profile on the next write. The manifest
therefore cannot show a label the person may not see, and the profile cannot
remember one.

**ADAPT-02.** The manifest changes order, state and suggestions only. It
contains no document text, no search result, and never a ranking. `decide()`
has no access to a result list.

**ADAPT-03.** No route reads another user's profile. The user id comes from
the session and lands in the `WHERE` clause; there is no parameter for it.
Administrators reach aggregates only through §5.3, and only above the minimum.

These are the three properties a buyer's works council will ask about. They
are small enough for a Platform-side Alloy model in the idiom of
`models/alloy/node_grants.als` planned by the workbench design; whether to add
one is §12.

## 7 · Surfaces

### 7.1 Start page (`/`)

Today: greeting, search box, results. New: a `<section id="homeBlocks">`
under the search box, empty in the template, filled from the manifest. Blocks,
in the only order the rules can produce:

1. `pending_approvals` (approvers) · `sync_state` (ingestion managers) — need,
   no consent
2. `my_matters` — need from the brain, gated on semantic roles
3. `continue_working` — behaviour, consent
4. `pending_for_me` — behaviour × brain, consent and graph mode, loaded lazily

A person with no roles, no consent and no graph sees exactly today's page.
Nothing is added to say "nothing here".

### 7.2 Search (`/`)

- **Scope chip** above the results, "Nur Akte Weber AG" with one click to
  apply and an ✕; pre-selected never. Applies `scope.node_ids` once the query
  contract carries it; until then it toggles the existing `_groupByAkte`
  grouping to that group only and says so in its `why`.
- **Facet rail** (after F3 lands): the manifest returns the rail order and
  open state; `filters.js` renders them in that order. No facet is ever hidden
  by adaptation — a collapsed group still shows its heading.
- **"Nicht relevant"** on each card: greys the card, sends `dismiss`, offers
  "Rückgängig". Does not remove the hit; the ranking is not the Platform's to
  edit.

### 7.3 Node page and Cortex

The workbench's generic node page composes its panels — fields, neighbourhood,
"Warum?", filters, editors — from the schema; the manifest for `surface=node`
adds `state` per panel and an order within the secondary group. The primary
group (name, type, fields) is fixed. In Cortex today, the drawer remembers per
user whether the evidence list opens expanded. Both read the same `panel:*`
counters.

### 7.4 Einstellungen

New card "Persönliche Anpassung":

- Switch with the consent text: *"Knovas merkt sich auf diesem Server, welche
  Akten und Dokumente Sie zuletzt geöffnet und welche Filter und Bereiche Sie
  genutzt haben, und ordnet Ihre Oberfläche danach. Gespeichert werden
  Kennungen und Zähler, keine Suchbegriffe und keine Inhalte. Nur Sie sehen
  diese Daten; die Verwaltung sieht nichts Persönliches. Sie können jederzeit
  alles löschen."*
- "Angeheftet" and "Nicht mehr vorschlagen" lists with remove buttons.
- "Anpassung zurücksetzen" (erases the profile row; consent stays) and "Alles
  löschen" (erases profile and consent).

### 7.5 Verwaltung

Nothing per person. The System tab gains one panel, "Nutzung der Oberfläche",
showing the §5.3 aggregates or the sentence "Weniger als N Personen haben die
Anpassung eingeschaltet; es werden keine Zahlen gezeigt."

## 8 · Contracts

### 8.1 Layout manifest

```jsonc
GET /api/me/layout?surface=home
{
  "surface": "home",
  "profile_state": "off" | "cold" | "warm",   // no consent | firm defaults | own profile
  "generated_at": "2026-09-10T08:12:01Z",
  "blocks": [
    { "id": "home.pending_approvals", "state": "expanded", "reason": "role_approver",
      "why": "Sie sind Freigebende, und 2 Anträge warten auf eine zweite Person.",
      "items": [{"kind": "approval", "id": "…", "label": "Zugriff ändern: Ordner Weber", "href": "/admin/approvals"}] },
    { "id": "home.continue_working", "state": "expanded", "reason": "recent_activity",
      "why": "Zuletzt geöffnet: Dienstag 16:20.",
      "items": [{"kind": "matter", "id": "<node uuid>", "label": "Weber AG / Kündigung", "href": "/ontology#…", "at": "…"}] },
    { "id": "home.pending_for_me", "state": "expanded", "reason": "placements_on_recent_matters",
      "load": "/api/me/blocks/home.pending_for_me" },      // lazy: fetched after first paint
    { "id": "home.my_matters", "state": "hidden", "reason": "gated:semantic_roles" }
  ]
}
```

Rules: `blocks` is ordered; unknown ids are ignored by the browser; a block
with `load` renders a skeleton and fetches once; `state: "hidden"` with a
`gated:` reason is how the manifest stays honest about a feature the
deployment does not have — the browser renders nothing for it. Errors on a
lazy block render inside that block ("kann gerade nicht geladen werden"),
never as a page error.

### 8.2 Signals

```jsonc
POST /api/me/signals   // X-CSRF-Token; 204; ignored when consent is off
{ "events": [
    { "kind": "open_matter",   "id": "<node uuid>",  "at": "2026-09-10T08:10:00Z" },
    { "kind": "facet_expand",  "id": "author",        "at": "…" },
    { "kind": "panel_open",    "id": "node.evidence", "at": "…" }
] }
```

≤ 50 events; unknown `kind` dropped; `id` validated per kind (uuid for nodes,
pointer shape for documents, allow-listed slugs for facets, panels and blocks).

### 8.3 Preferences and consent

```
GET  /api/me/consent                  → {"enabled": bool, "consented_at": …}
POST /api/me/consent   {"enabled": bool}
POST /api/me/preferences {"pin": "block:home.my_matters"} | {"unpin": …} | {"hide": "suggest:scope:<id>"} | {"unhide": …}
POST /api/me/reset      {"scope": "profile" | "all"}
```

### 8.4 Engagement proxy

```jsonc
POST /api/search/engagement   // X-CSRF-Token; 202 always (fire-and-forget)
{ "query_session_id": "…", "events": [{"action": "view", "pointer": "…", "position": 3}] }
```

## 9 · KnovasComponents slice

| Layer | File | Job |
| --- | --- | --- |
| Decision | `src/ui_profile.py` *(new)* | `Context`, `Manifest`, `RULES`, `decay()`, `fold_signal()`, `decide()`; pure |
| Store | `src/identity/ui_profile_store.py` *(new)* | `UiProfileStore(conn)`: `consent`, `set_consent`, `load`, `fold`, `pin/unpin/hide/unhide`, `reset`, `firm_defaults(min_users)`; every statement takes `user_id` from the caller's session |
| Migration | `src/identity/migrations/000N_ui_profile.sql` *(new)* | the table in §5.2; `N` = the next free number at merge time (0002–0004 are claimed by unmerged plans; `identity/migrate.py` refuses an edited applied file, so the number is fixed when the branch lands, not now) |
| Composer | `src/web_interface/me.py` *(new blueprint, `/api/me/*`)* | gathers `Context` within the §5.1 budget through the same `client_factory` and `page_context` conventions as `admin.py`; caches brain facts per user for `ADAPTIVE_TTL_SECONDS` |
| Engagement | `src/knovas_client.py` *(extend)* — `report_engagement(query_session_id, events)`; `app.py` — `POST /api/search/engagement` | §5.4 |
| Browser | `static/js/blocks.js` *(new)* — registry `{id → render(block)}`; `static/js/adaptive.js` *(new)* — fetch manifest, mount blocks, batch signals, `sendBeacon`; `app.js` *(extend)* — engagement hooks, scope chip, "Nicht relevant" | no build step; tokens only |
| Templates | `index.html` — `<section id="homeBlocks">`; `settings.html` — the §7.4 card; `admin_system.html` — the §7.5 panel; `ontology.html` — drawer state | `window.__DOCBRIDGE__` gains `adaptiveEnabled` and `profileState` so first paint needs no round trip for rung 1 |
| Config | `config/config.yaml`, `config.template.yaml`, `knovas.env.example`, `docker-compose.yml` | `ADAPTIVE_UI_ENABLED` (default `true`), `ADAPTIVE_UI_DEFAULT` (`off`), `ADAPTIVE_HALF_LIFE_DAYS` (14), `ADAPTIVE_TTL_SECONDS` (300), `ADAPTIVE_MAX_RECENT_MATTERS` (3), `ADAPTIVE_MAX_RECENT_DOCS` (5), `ADAPTIVE_MIN_USERS` (3), `ENGAGEMENT_REPORTING_ENABLED` (`true`) |
| Docs | `KnovasPlatform/docs/features/adaptive-ui.md` *(new)*; `docs/specifications.md` §2.5; `RELEASE_NOTES.md` | capability labels per block (G9) |

Identity off (`IDENTITY_ENABLED=false`, shared company login): there is
nobody to adapt for. The blueprint is not registered, `profile_state` is
`off`, and rung 1 reduces to what the shared login can know — nothing per
person. Engagement reporting still works; it is per tenant.

## 10 · Normative design rules

1. **The skeleton never moves.** Navigation order, primary controls and the
   search box are fixed. Adaptation reorders and opens secondary content only.
2. **Suggest, never apply.** A scope, a filter or a matter is offered as one
   click; it is never pre-selected on the person's behalf.
3. **Arrange, never rank.** The Platform shows the brain's results in the
   brain's order. Behaviour reaches ranking only through the documented
   engagement channel.
4. **Ids, not content.** The profile stores identifiers and counters; labels
   are resolved through the ACL at render time (ADAPT-01).
5. **Consent first, and only for behaviour.** Need-driven blocks (roles,
   graph) need no consent because they store nothing; behaviour-driven blocks
   need the person's switch.
6. **Only the person, ever.** No per-person view for anyone else; firm
   aggregates only above `ADAPTIVE_MIN_USERS`.
7. **Every adaptation explains itself** in one German sentence and can be
   pinned, hidden for good, or reset.
8. **Honest gates.** A block whose source is not deployed is `hidden` with a
   `gated:` reason, never rendered from invented data; fixture mode says
   "Wissensnetz-Modus erforderlich" where the workbench does.
9. **Bounded brain reads.** A surface load spends at most the §5.1 budget and
   degrades block by block.
10. **No node type, block type or facet name from data appears in code**
    beyond the closed allow-lists; the workbench rule extends to the manifest.

## 11 · Sequencing

| Phase | Delivers | Depends on |
| --- | --- | --- |
| 0 — feed the brain, build the spine (days) | `report_engagement` + proxy + the three hooks; `ui_profile.decide()` with rung-1 rules; `blocks.js`/`adaptive.js`; `GET /api/me/layout` serving `pending_approvals` and `sync_state`; "Nicht relevant" | identity on `main` (LIVE) |
| 1 — the person (weeks) | consent card, `ui_profile` table and store, signals route, `continue_working`, `pending_for_me` (placements, lazy), scope chip via `akten_id`, Cortex drawer state, "Warum sehe ich das?", pins/hidden/reset, firm defaults, System-tab aggregates, feature doc | phase 0; graph mode for `pending_for_me` |
| 2 — the brain's roles (after backend) | `my_matters` from semantic roles; scope chip through `scope.node_ids` and identifier search; facet rail order/state | Pflichtenheft §5.6, §5.8, §5.3 + Platform §6.2 |
| 3 — attention at scale | `pending_for_me` re-sourced from the Posteingang poller; workbench node page panels | Pflichtenheft §5.5/§6.9; typed-node workbench |
| later — experiment | a model *proposes* manifests within the same block vocabulary; proposals never commit; only if a firm allows the LLM path | phases 0–2, an explicit owner decision |

Phase 0 needs nothing from the backend and nothing from the person, and it is
the phase that makes the brain adapt: it is the one to start with.

## 12 · Risks, testing, guidance

### Risks

| Risk | Mitigation |
| --- | --- |
| Rate limit exhaustion from the start page | hard budget (§5.1), per-user TTL cache, lazy blocks; a budget test fails CI when a surface exceeds it |
| A stale recent leaks a walled-off matter's label | ADAPT-01: labels resolved through the per-subject export, pointers through `document_readable`, failures drop the item; a test walls a node and asserts its absence |
| Works-council objection | opt-in per person, counts not diary, no per-person admin view, one-click erase; the consent text says all four |
| Adaptation confuses more than it helps at 7–50 seats | rung 1 first, rung 2 opt-in, everything explainable and pinnable; firm defaults keep cold start sane; nothing is ever removed, only collapsed |
| Migration number collision with unmerged plans | number chosen at merge time (§9); the ledger refuses an edited file, so it is never renamed after |
| Facet and scope adaptation waiting on the backend | both are `gated:` in the manifest and simply absent until then; nothing in phases 0–1 depends on them |

### Testing

- `tests/test_ui_profile.py` — `decide()` over every rule, decay arithmetic,
  allow-list rejection, `pins`/`hidden` precedence, `profile_state`
  transitions; no database.
- `tests/test_ui_profile_store.py` — real `platform-db` (skipped without one,
  required in CI): consent off → `fold` is a no-op; cascade on user delete;
  `firm_defaults` returns nothing below the minimum; a second user's rows are
  unreachable through any store method.
- `tests/test_web_me.py` — Flask app + fake store and fake client: manifest
  shape, lazy block errors stay inside the block, budget never exceeded (the
  fake client counts calls), ADAPT-01 with a walled node, CSRF on every
  mutating route (extend `test_csrf_enforcement.py`), 401 handling.
- `tests/test_engagement_proxy.py` — batching ≤ 50, sentinel session skipped,
  upstream failure → 202 and a log line, never a retry.
- Optional: `models/alloy/adaptive_visibility.als` pinning ADAPT-01/03 with
  two mutants (a suggestion outside the visible set; a store method without
  the user predicate).

### Guidance requested from the owner

1. **Consent default** — per-user opt-in with `ADAPTIVE_UI_DEFAULT=off`; a
   firm may flip the default after its own works-council process? *Default:
   yes.*
2. **Engagement reporting** — switch it on by default as tenant-level,
   identity-free feedback to the brain? *Default: yes;
   `ENGAGEMENT_REPORTING_ENABLED` for firms that object.*
3. **"Nicht relevant"** — worth a card affordance, or wait for a 1–5 rating
   design? *Default: dismiss only; ratings need their own UX.*
4. **Backend priority** — pull semantic roles (§5.6) and identifier search
   (§5.8) forward in the Pflichtenheft plan, since they unlock "Meine Akten"
   and the scope chip? *Default: ask the backend owners; nothing here blocks
   on them.*
5. **Alloy** — model ADAPT-01/03 as the Platform's second Alloy tree, or keep
   them as tests? *Default: tests now, model when the workbench's tree
   exists.*
6. **Journal coupling** — one recording hook feeding journal and profile under
   separate consents (§5.2)? *Default: yes.*

## 13 · Related

- `docs/superpowers/specs/2026-09-02-typed-node-workbench-design.md` —
  structure from the schema; this design adds salience
- `docs/superpowers/specs/2026-08-15-pflichtenheft-d-j-design.md` — §5.3
  filters, §5.5 events, §5.6 semantic roles, §5.8 identifiers, §6.2 filter
  rail, §6.9 Posteingang, §6.12 journal
- `docs/superpowers/plans/2026-08-15-pflichtenheft-d-j-components.md` — Task
  KC-E-5, the journal's consent and privacy pattern this design copies
- `docs/KnovasAPI/Analytics_Integration_Guide.md` — the engagement channel
- `docs/Frontend Product Requirements Document – Multi-format Search UI.md` —
  "Predictability" and §9 "user-level personalization"
- `docs/search-ui-backlog.md` — "erst die API, dann die UI"
