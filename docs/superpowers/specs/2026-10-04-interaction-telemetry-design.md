# Design: Nutzung — interaction telemetry for the Knovas Platform

Date: 2026-10-04
Status: proposed
Betrifft: `KnovasPlatform/components/docbridge_integration`

## What this is

A way to record how the Platform is used — which controls are clicked, how long a view stays open, how long a pointer rests on a control, whether a search ends in a document — so the product can be evaluated and changed on evidence. Every visible component is trackable. A button, link, field, dialog, card or graph node with no name is unfinished work, not a silent omission.

It is a module of the Platform web app, code name `observe`, UI label **Nutzung**. It is off unless an operator turns it on. It lives in platform-db on the firm's own hardware. It does not phone home.

## Research

### What the product already wants to know, and cannot

Three existing documents name measurements and then stop because nothing records them.

- The Trefferliste design fixes the usage goal: people want to read the document, and the card exists only to help them choose. Nothing records whether a search ends in a preview, or how long that takes.
- The search-UI backlog defers the pdf.js decision until someone knows how often PDFs are opened relative to DOCX and MSG, and defers preview caching until someone knows whether the same preview is opened repeatedly. Both numbers are absent.
- The Experimente pack `product` already defines the metrics a usability test needs: task success, time to a useful result, satisfaction. Those rows are typed in by hand after a moderated session. Ordinary use of the installed product never produces them.

### What is recorded today

Two stores exist, and neither is this.

| Store | What it is | Why it is not the usage record |
|---|---|---|
| `audit_log` | Append-only security record: who changed an account, an ACL, an ingestion profile. Actor email is denormalised so the row stays readable after the account is deleted. | Purpose is accountability for consequential actions. Volume and content rules are the opposite of a click stream. Field values, query text and pointers are banned from it. |
| Knovas `POST /secured/analytics/engagement` | `view`, `click`, `download`, `dismiss` for one search session, so Knovas can tune ranking. No user identity, no query text. | The Platform stopped sending it on 2026-07-26, on purpose, when ratings were removed. `query_session_id` still comes back from `/secured/query` and is still unpacked in `knovas_client`, and `/api/search` then drops it on the floor. The signal never reaches the browser. |

The web app is Flask, Jinja and vanilla JS, with no build step. Every signed-in page includes `_sidebar.html` and then its own scripts. Identity, roles and the audit log are in platform-db on the customer's machine. The schema comment on that database states the boundary this design keeps: Knovas receives a signed opaque subject and a group list, and does not learn who works at the firm.

### What the law forces the shape to be

This is a constraint on the design, not a legal opinion. The firm is the controller. The sources are the FDPIC's guidance on workplace monitoring systems, Art. 26 of Ordinance 3 to the Employment Act (ArGV 3), and BGE 130 II 425.

ArGV 3 art. 26 forbids monitoring systems whose purpose is to watch how employees behave. A system used for another legitimate purpose — here, improving the software the firm runs — can be permissible when it is proportionate and people are told beforehand. The FDPIC names the systems that fail that test in practice: spyware, activity trackers, application and website logs tied to a person, mouse and keyboard logs, and people-analytics that scores individuals. Purpose decides. A click log that can be joined back to a named employee is behaviour monitoring even if the team that reads it talks about buttons.

The proportionate reading, and the one this design implements:

- The purpose is the quality of the software: which controls are used, which journeys stall, which formats are opened.
- The least intrusive form that answers those questions is aggregate counts by control and by role, not a timeline per person.
- People are told, in the product, before any event is stored.
- The module is off until the firm turns it on.

Mouse paths, keystroke timing, element text, query text and document names answer no question in the list above and are not collected.

## Approaches

### A. Third-party collector (PostHog, Matomo, a SaaS product analytics tool)

A script in the page, session replay available, funnels on day one.

Rejected as the default. The deployment rules already say the Platform ships with no telemetry, no update server and no CDN. A collector either sends behaviour off the firm's hardware or becomes another service the firm has to run and explain. Session replay records the document titles and snippets the search page is built to show. That fails both the content ban already used for audit and the proportionality test above.

### B. Extend `audit_log`, and turn the Knovas engagement API back on for everything

One table, one habit, and the ranking team gets its signal back.

Rejected. `audit_log` is the record of who did a consequential thing, stored with an email so it survives account deletion. Putting hover timing in it makes the security record a behaviour log and makes every audit reader a usage analyst. The engagement API is the right shape for four search actions and the wrong shape for the rest: it requires a `query_session_id` and a document pointer, it has no notion of a settings toggle or a Cortex node, and it is stored in the Knovas tenant rather than on the firm's machine. Search ranking and UI optimisation are different questions and stay different pipes.

### C. A local module with a closed event vocabulary and aggregate evaluation (recommended)

A small script, a batch endpoint, two tables, one admin page. Events name a control. The page answers a fixed list of product questions. Nothing in the tables names a person or a document.

This is the design below. It follows the Experimente precedent: a module of the same web app, a flag defaulting to off, tables that exist before the flag is on, vanilla JS, no new database engine. It does not feed Experimente automatically. A person who wants a number in a Nutzertest reads it off the Nutzung page and types the measurement in. That keeps the two modules independently removable.

## Decisions

| Question | Decision | Why |
|---|---|---|
| Where it runs | Platform web app. Blueprint `observe`. Tables in platform-db. Script `static/js/observe.js`, no build. | Same identity, CSRF, roles and deployment as the rest of the app. A second service would need its own auth and would still have to read roles from here. |
| Off switch | `OBSERVE_ENABLED`, default **false**. Off: the script tag is not rendered, `POST /api/observe` answers 404, the Nutzung tab is absent, no writer runs. Tables may exist and stay empty. | A firm that does not want it must not pay for it, and must not discover it by accident. Matches `EXPERIMENTS_ENABLED`. |
| Who it describes | A browser-tab visit and a role, never a user. | A user id would make every funnel a personnel record. Role answers "do administrators and members use different areas?" which is a product question. |
| What a visit is | A UUID v4 created in `sessionStorage` by the script. It dies with the tab. It is not the platform session id. | The platform session id joins to `users`. This one must not. |
| Content | Forbidden in the event, in the log line and in the URL. Server drops any property that is not on the allowlist. | Query text, titles, pointers and field values are the confidential material. The existing privacy rule for document fields already states this for audit; usage data gets the same rule. |
| Retention | Raw events 30 days (`OBSERVE_RAW_DAYS`). Daily rollup and visit-day rows 400 days (`OBSERVE_ROLLUP_DAYS`). | Funnels need a visit for a day. A year of daily counts is enough to see a release. Raw exists so an operator can check the rollup, then it goes. |
| Evaluation surface | One admin tab, **Nutzung**, reading aggregates only. Plus a JSON export of those aggregates. | The questions are known. A general event explorer would invite person-level reading that the schema was built to prevent. |
| Coverage | Every visible component is trackable. A component with no name is a failed test, and at runtime it is still recorded under `unnamed`. | Opt-in instrumentation is how a button ships and never appears in the evaluation. The product question is about the whole UI. |
| Search ranking signal | A second flag, `OBSERVE_SEARCH_ENGAGEMENT`, default **false**, independent of the first. | Restoring the pipe removed on 2026-07-26 is useful and is not this module. Default off keeps today's behaviour. |
| Experimente | No automatic write into `exp_measurements`. | A usability test and a year of production counts are different samples. The page shows the number; a person decides whether it is a measurement. |

## The two pipes

```text
browser                         platform-db                     Knovas
  |                                  |                            |
  |  POST /api/observe               |                            |
  |  (control, duration, role) ----> observe_events               |
  |                                  observe_daily                |
  |                                  observe_visit_days           |
  |                                  |                            |
  |  POST /api/analytics/engagement  |                            |
  |  (pointer, action, position) --> attach query_session_id      |
  |                                  |  (only if the second flag) |
  |                                  +---------------------------> /secured/analytics/engagement
```

The left pipe is this design. The right pipe is specified at the end and stores nothing in the observe tables. A click that means both (opening a result) emits one event on each pipe, and the pointer travels only on the right.

## Event envelope

One event, schema version 1:

| Field | Source | Rule |
|---|---|---|
| `v` | client | Must be `1`. Anything else is dropped. |
| `event_id` | client | UUID. Duplicates in one request are ignored. |
| `occurred_at` | client | Milliseconds. If it is more than one hour off `received_at`, the server stores `received_at` instead. Rollups bucket by `received_at`, so a skewed clock cannot file a day. |
| `visit_id` | client | UUID. Required. |
| `surface` | client, checked | One of the surface names below. |
| `action` | client, checked | One of `view`, `click`, `hover`, `impression`, `change`, `submit`, `error`. |
| `target` | client, checked | Dotted name, each segment `[a-z][a-z0-9]*`, at most five segments, at most 64 characters. Every visible component has one. The server does not keep a second whitelist, so a new name does not need a server change. |
| `props` | client, filtered | Only the allowlist below. Unknown keys are removed. The event is kept. |

The client does not send a role, a user id, a URL or a user agent. The server sets `role_key` from the signed-in user: the role keys sorted and joined with a comma (`admin,member`), or `anonymous` on the login page. A client-supplied role is ignored.

`role_key` is capped at 80 characters. The built-in roles fit. A longer join is truncated on a comma boundary.

### Properties

| Key | Type | Use |
|---|---|---|
| `duration_ms` | int, 0–300000 | View time or hover time. Also stored as a column. |
| `position` | int, 1–50 | Rank as shown, same meaning as the engagement API. |
| `result_count_bucket` | `0`, `1-5`, `6-20`, `21-50` | Size of the list just shown. Not the exact count above 20, which is enough to see an empty search and not enough to fingerprint a query. |
| `has_more` | bool | The list offered "Mehr laden". |
| `file_kind` | `pdf`, `docx`, `txt`, `msg`, `eml`, `md`, `other` | Format mix the backlog asked for. The extension is mapped to this set; the filename is not stored. |
| `outcome` | `ok`, `empty`, `error`, `denied` | A submit or a view ended this way. |
| `step` | `-1` or `1` | Previous or next in the preview. |
| `viewport` | `desktop`, `narrow` | `narrow` means the viewport is under 760 px at the time of the event. Not the pixel size. |
| `field_key` | `[a-z][a-z0-9_]{0,40}` | Which filter changed. The value of the filter does not. |
| `honesty` | `[a-z0-9_]{1,32}` or the server rewrites anything else to `other` | The code Knovas already returns (`no_results_reason` and its siblings), never a sentence. |
| `rage` | bool | Three activations of the same target inside one second. |
| `scroll_bucket` | `0`, `25`, `50`, `75`, `100` | Furthest scroll of that page, sent once on `view` end. Not a scroll stream. |
| `from_surface` | a surface name | Where a navigation click came from. |
| `tag` | `a`, `button`, `input`, `select`, `textarea`, `summary` | Kept only when `target` is `unnamed`. |
| `nth` | int, 0–20 | Kept only when `target` is `unnamed`. Index of that tag within the surface, capped. |
| `source` | a basename from the `client.error` list | Kept only when `action` is `error`. |

A string that fails its pattern is removed, not truncated into storage. Numbers outside the range are removed. Booleans that are not booleans are removed.

### Surfaces

`login`, `search`, `cortex`, `experiments`, `settings`, `account`, `admin`.

The admin sub-pages are targets (`admin.people.view`), not extra surfaces, so the rollup stays comparable.

## Every visible component is trackable

A visible component is a control or a region a person can see as its own element. It is trackable when the collector can record the actions that element can receive, under a stable name that does not contain the text on screen.

| Kind | What it is | What is recorded |
|---|---|---|
| Control | `button`, `a`, `input` (except `type=hidden`), `select`, `textarea`, `summary`, and anything with `role` of `button`, `link`, `tab`, `menuitem`, `option`, `switch` or `checkbox` | `click` or `submit` or `change`, `hover`, and `view` for the time it holds focus |
| Region | A page, dialog, drawer, panel, card, toast, tab panel, nav group, result list, or a Cytoscape node | `view` or `impression` for the time it is on screen, and `hover` |
| Part | An icon, glyph or label inside a control or region that already has a name | Nothing of its own. It wears `data-observe-part` and events belong to the named ancestor |

Running text, layout boxes (`div`, `span`, `section`, `li`) and SVG shapes are not components. They belong to the nearest control or region. A hidden input, a CSRF field, a `<script>` and an element with `hidden` or `aria-hidden="true"` are not visible. An element that is in the DOM but not shown becomes trackable when it is shown, and it must already carry its name.

Repeated rows share one name. A result card is `search.result.card`, a document row is `admin.documents.row`, a choice input is `admin.doc_fields.choice`. Which instance it was is a prop from the allowlist (`position`, `field_key`, `file_kind`), never the title, the pointer, the label text or the value the person typed. Two cards are the same component. Fifty of them do not create fifty targets.

### The name

Every control and every region carries `data-observe="<target>"`. A region also carries `data-observe-region`. A part carries `data-observe-part` and no `data-observe` of its own. The name is `surface.region.component`, dotted, matching the target rule.

```html
<body data-observe-surface="search">
<section data-observe="search.results" data-observe-region>
    <button type="button" data-observe="search.result.open" data-observe-position="3"
            data-observe-file-kind="pdf">
        <svg data-observe-part>...</svg>
        Öffnen
    </button>
</section>
```

`data-observe-position` and `data-observe-file-kind` are the only data attributes copied into props, and only after the same validation as the server. The script never reads `textContent`, `value`, `innerText`, `className`, `id`, `href` or `placeholder`. The visible words on a button are not part of its name.

Controls drawn in JavaScript set the same attributes when the node is created. Cortex graph nodes are Cytoscape objects, not DOM. Each node class (`entity`, `document`, `filter`, `edge`) is one component: `ontology.js` calls `track` from its `mouseover`, `mouseout` and tap handlers with `cortex.node.entity` and the rest, and never with the node's label.

`KnovasObserve.track(action, target, props)` is for moments that are not a DOM event, such as a results list just painted or a dialog just closed. It applies the same checks and queues. It does not throw.

### Coverage

The collector scans the document when it starts and watches it with a `MutationObserver`. Every element that matches the control list, and every `[data-observe-region]`, is registered. Registration is what "trackable" means at runtime: the element does not have to opt in a second time for click, hover or view.

An element that matches and has no `data-observe`, and is not a `data-observe-part` inside a named ancestor, is still registered. Its target is `unnamed`, with `tag` and `nth`. Each such registration also emits one `impression` on `observe.unnamed` so the Nutzung page shows the defect. `unnamed` is not a product signal. It is a component that shipped without a name.

The static test is what stops that happening. It fails when any of the following is true in `templates/` or under `static/js/`, vendor scripts excluded. In a script, a name counts if it is set on the element — `dataset.observe`, or the attribute inside the HTML string — before that element is appended or assigned to `innerHTML`. A `createElement('button')` whose variable receives the name on a later line passes. One that is inserted without a name fails.

- A control from the table above has neither `data-observe` nor `data-observe-part`.
- A `data-observe-part` has no named ancestor by the time it is inserted.
- A dialog, drawer, toast or result card has no `data-observe-region`.
- A `data-observe` value fails the target pattern.

The test is exhaustive relative to the UI that exists. The list of names in this spec is the naming of the search journey, which is the one the open product questions depend on. It is not a sample, and it is not permission to leave an admin button unnamed. A new visible component is unfinished until it has a name and the test passes.

`observe.js` is included from `_sidebar.html`, which every signed-in page already includes, and from `login.html`, which does not. The tag is rendered only when the flag is on. The script starts itself.

### Clicks

A `click` on the nearest registered component, and a keydown of Enter or Space on a registered button. One event, action `click`. A part forwards to its named ancestor.

If that click submits a form whose submit control is the same instrumented element, the event is action `submit` and the click is not also recorded.

### Hover time

`pointerenter` / `pointerleave` on every registered component, parts excluded. One event, action `hover`, with `duration_ms`, emitted on leave only when all of the following hold:

- `pointerType` is `mouse`. Touch is not hover.
- The pointer stayed at least 400 ms and at most 30 s. Shorter is noise. Longer is a parked pointer, and the event is capped at 30 s and still sent.
- The element is still the one that was entered. Moving across a child does not restart the timer (`pointerenter` is not retargeted; the listener uses the instrumented element, not the child).

No `mousemove`. No coordinates.

### View time

Every registered region is observed. An `IntersectionObserver` at threshold 0.5 emits `impression` with `duration_ms` when the region leaves the viewport or the page hides, once per time it becomes visible. A dialog, drawer or toast that is not shown does not accumulate time; when it opens it is a region like any other. The preview dialog adds `file_kind` to `search.preview.view`. A result card adds `position` to `search.result.card`.

A page view is the region on `body`: it starts when the script starts and ends on `pagehide` or when the document becomes hidden. `duration_ms` is visible time, with hidden intervals subtracted via `visibilitychange`. Action `view`, target `<surface>.view`, plus `scroll_bucket` and `viewport`. Coming back starts a new view rather than one giant interval, so a laptop left open overnight does not look like a six-hour reading of the search page.

A control that can take focus emits `view` for the time it holds focus (`search.query.focus`, and the same for every other field). The value of the field is not read.

### Names the search journey uses

These are the names behind the questions in the next section. Every other visible component on login, Cortex, Experimente, settings and admin is named by the same rule and held to it by the coverage test. A few of those names, so the rule is concrete: `admin.people.invite.submit`, `admin.documents.filter.submit`, `admin.approvals.approve`, `experiments.tab.metriken`, `cortex.zoom.in`, `settings.password.submit`. The test lists the rest; this spec does not repeat it.

| Target | Action | When |
|---|---|---|
| `login.view` | view | Login page, visible time |
| `login.submit` | submit | `outcome` ok or error. Nothing else about the attempt |
| `search.view` | view | Search page, visible time |
| `search.query.focus` | view | Focus on the search box, duration only, never the text |
| `search.query.submit` | submit | Search requested |
| `search.results.show` | view | List painted. `result_count_bucket`, `has_more`, `outcome` (`ok` or `empty`), `honesty` |
| `search.results.load_more` | click | "Mehr laden" |
| `search.results.list` | click | "Liste anzeigen" |
| `search.filter.change` | change | A document-field filter changed. `field_key` only |
| `search.filter.clear` | click | "Filter entfernen" |
| `search.result.card` | impression | Card was at least half visible |
| `search.result.open` | click | Card or Öffnen. `position`, `file_kind` |
| `search.result.download` | click | Download. `position`, `file_kind` |
| `search.result.copy` | click | "Link kopieren". `position` |
| `search.result.external_open` | click | Opened in OneDrive / the companion. `position`, `file_kind` |
| `search.preview.view` | view | Dialog open, visible time, `file_kind` |
| `search.preview.close` | click | Close |
| `search.preview.step` | click | Previous or next. `step` |
| `search.preview.finding` | click | A finding chosen or stepped |
| `nav.search`, `nav.cortex`, `nav.experiments`, `nav.settings`, `nav.admin`, `nav.feedback` | click | Sidebar. `from_surface`. Feedback is the outbound Jira link; the event fires and the destination is not recorded |
| `cortex.view` | view | Cortex page |
| `cortex.node.select` | click | A graph node activated |
| `cortex.node.hover` | hover | A graph node, same hover rule |
| `cortex.filter.apply` | submit | A filter committed, no filter value |
| `experiments.list.view`, `experiments.detail.view`, `experiments.manage.view` | view | Those pages |
| `experiments.detail.open` | click | A row opened |
| `settings.view`, `account.view` | view | Those pages |
| `admin.people.view`, `admin.documents.view`, `admin.access_groups.view`, `admin.doc_fields.view`, `admin.ingestion.view`, `admin.approvals.view`, `admin.system.view` | view | Those tabs |
| `client.error` | error | `window.error` and `unhandledrejection`. `outcome=error`. No message, no stack. The source is recorded only as a basename when it is one of `app.js`, `observe.js`, `ontology.js`, `doc_fields.js`, `experiments_list.js`, `experiments_detail.js`, `experiments_manage.js`, `experiments_common.js`, `admin_people.js`, `admin_documents.js`, `admin_ingestion.js`, `admin_doc_fields.js`; otherwise the source is omitted |

A click on a component that reached the page without a name is stored as target `unnamed`, with `tag` and `nth` only. The structural signature does not include id, class or text, because result titles are rendered as text inside clickable cards. The coverage test treats any such component in the source as a failure, so `unnamed` in production means the test was bypassed.

### Rage and hesitation

Computed in the script, not by a second pass.

- Rage: the third `click` of the same target within 1 s carries `rage=true`. The first two are ordinary clicks.
- Hesitation is not a stored flag. The Nutzung page derives it: hover events whose target also has a click rate, read as "people rest on this and do not activate it". Storing a per-visit hover-then-no-click edge would be a small behaviour trace. The aggregate is enough.

## Transport

The script keeps a queue in memory.

- Flush every 5 s, or at 20 events, or on `pagehide`, whichever comes first. A request never carries more than 30 events. On `pagehide` the newest 30 are sent and the rest are dropped.
- `fetch` `POST /api/observe` with `keepalive: true`, `Content-Type: application/json`, and `X-CSRF-Token`. The token is the `data-csrf` attribute on the script tag itself, read while the script is evaluating. `keepalive` is what gets the last batch out on navigation. `sendBeacon` is not used, because it cannot set that header. Login and the sidebar both render the attribute; the script does not go looking through forms.
- Body cap 32 KB. Above that, the oldest events are dropped before send.
- Queue cap 200. Above that, the oldest are dropped and nothing is retried.
- The response is `202` with `{accepted, dropped}`. Network errors and non-202 responses are ignored. Dropped events are not retried. If `dropped` is non-zero the script waits 60 s before the next flush.
- The request is never awaited by search, preview or navigation. A thrown error inside the script is caught and does not reach the page.

Login, having no session, may post only `surface=login` and targets `login.view` and `login.submit`. Any other target from an anonymous caller is dropped. Signed-in callers may post every surface.

The server accepts at most 30 events per request and at most 120 stored events per `visit_id` per rolling minute. The surplus counts as `dropped`. The route does not log the body. A failed insert is logged as `observe write failed` with the count, never the payload, and the response is still `202` with `accepted: 0` so the page does not retry a poison batch.

CSRF failures are the exception: they return the same 403 as every other state-changing route, because a missing token is a bug in the page, not a usage event.

## Storage

Migration `0005_observe.sql`. The tables are created whether or not the flag is on, same reasoning as the Experimente migration: empty tables cost nothing, and turning the module on later must not be a migration.

```sql
CREATE TABLE observe_events (
    id            BIGSERIAL PRIMARY KEY,
    received_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    occurred_at   TIMESTAMPTZ NOT NULL,
    visit_id      UUID        NOT NULL,
    role_key      TEXT        NOT NULL,
    surface       TEXT        NOT NULL,
    action        TEXT        NOT NULL,
    target        TEXT        NOT NULL,
    duration_ms   INTEGER,
    props         JSONB       NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX observe_events_received ON observe_events (received_at);

CREATE TABLE observe_seen (
    day       DATE NOT NULL,
    visit_id  UUID NOT NULL,
    surface   TEXT NOT NULL,
    action    TEXT NOT NULL,
    target    TEXT NOT NULL,
    role_key  TEXT NOT NULL,
    PRIMARY KEY (day, visit_id, surface, action, target, role_key)
);

CREATE TABLE observe_daily (
    day          DATE    NOT NULL,
    surface      TEXT    NOT NULL,
    action       TEXT    NOT NULL,
    target       TEXT    NOT NULL,
    role_key     TEXT    NOT NULL,
    events       INTEGER NOT NULL,
    visits       INTEGER NOT NULL,
    duration_ms_sum   BIGINT  NOT NULL DEFAULT 0,
    duration_n        INTEGER NOT NULL DEFAULT 0,
    duration_hist     JSONB   NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (day, surface, action, target, role_key)
);

CREATE TABLE observe_visit_days (
    day               DATE    NOT NULL,
    visit_id          UUID    NOT NULL,
    role_key          TEXT    NOT NULL,
    event_count       INTEGER NOT NULL DEFAULT 0,
    searched          BOOLEAN NOT NULL DEFAULT FALSE,
    saw_results       BOOLEAN NOT NULL DEFAULT FALSE,
    zero_results      BOOLEAN NOT NULL DEFAULT FALSE,
    opened            BOOLEAN NOT NULL DEFAULT FALSE,
    previewed         BOOLEAN NOT NULL DEFAULT FALSE,
    downloaded        BOOLEAN NOT NULL DEFAULT FALSE,
    external_opened   BOOLEAN NOT NULL DEFAULT FALSE,
    reopened_preview  BOOLEAN NOT NULL DEFAULT FALSE,
    first_preview_ms  INTEGER,
    file_kinds        JSONB   NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (day, visit_id)
);
```

There is no `user_id`, no email, no IP, no user agent, and no document pointer. Adding one of those columns later is a new design, not a migration detail.

`duration_hist` counts fixed buckets, in milliseconds: `b400` (under 400), `b1000`, `b3000`, `b10000`, `b30000`, `b30000p` (30000 and above). Means come from the sum and the count. Percentiles come from the histogram. That is the same choice Experimente made: sufficient statistics in PostgreSQL, no second database.

`file_kinds` on the visit row is a counter object of the allowlisted kinds seen on `search.result.open` that visit, for example `{"pdf": 2, "docx": 1}`. It is how the format mix is aggregated without a document id.

`reopened_preview` becomes true when a second `search.preview.view` arrives for that visit. It does not say which document. That is the caching question the backlog can answer: the share of visits that open a preview more than once. It cannot answer "the same file three times", and that finer question is declined because it needs a document key.

`duration_ms` is stored only in the column. It is removed from `props` so the two cannot disagree.

Writes happen in the request, in one transaction: insert the raw rows, upsert the daily row, and upsert the visit-day row.

`observe_daily.visits` increments only when `INSERT INTO observe_seen ... ON CONFLICT DO NOTHING` actually inserts. The seen key is the daily grain plus `visit_id`, so a click and a hover on the same control each count the visit once, and two concurrent batches for the same grain count it once. `observe_seen` is purged on the same schedule as raw events. After that purge the daily `visits` number is left as it was and is not recomputed.

`observe_visit_days` is written only for an authenticated request. Login events update `observe_events` and `observe_daily` with role `anonymous` and do not touch the visit row, so a tab that logs in and then searches does not glue an anonymous row to a named role. Flags are boolean OR. `role_key` on the visit row is set on insert and not updated afterwards. `file_kinds` is merged in the application, in that same transaction, by adding the counts.

`first_preview_ms` is the difference, in stored `occurred_at`, between the first `search.preview.view` and the earliest `search.query.submit` of that visit-day. It is written once. A preview with no earlier submit leaves it null. The value is capped at 300000.

The events table stays unpartitioned. With every component emitting hover and impression, a firm of fifty active people is on the order of a few million raw rows in 30 days, not the few hundred thousand a click-only stream would be. Partitioning is the step to take if raw rows pass five million, and not before.

### Purge

A daemon thread in the web process, the same shape as the Experimente job thread, wakes hourly. It deletes `observe_events` and `observe_seen` older than `OBSERVE_RAW_DAYS`, and `observe_daily` and `observe_visit_days` older than `OBSERVE_ROLLUP_DAYS`. It runs only when the flag is on. It does not run a query that returns event bodies to a log.

## The questions the page answers

The Nutzung tab is the organised view. It is not a stream viewer. Each block exists because a decision already in the docs needs it, or because a dead control cannot be seen any other way.

| Question | Decision it informs | Reading |
|---|---|---|
| Do searches end in a document? | Trefferliste: the preview is the goal | Of visits with `searched`, the share with `previewed`, `opened`, `downloaded` or `external_opened` |
| How long until that document? | `time_to_value_s` in the product pack | Histogram of `first_preview_ms` |
| Is the preview actually read? | Whether the dialog earns its cost | Histogram of `search.preview.view` duration |
| PDF, DOCX or MSG? | pdf.js, backlog item 3 | Sum of `file_kinds` on visits in the period |
| Do people open a preview more than once? | Preview caching, backlog item 2 | Share of previewing visits with `reopened_preview` |
| Which searches find nothing? | Empty-state and ranking, locally | Share of `searched` visits with `zero_results`. The words are not stored |
| Which controls are unused? | Remove or relocate them | Targets with impressions and a click count of zero over 28 days. A non-zero `observe.unnamed` is a coverage defect, not a product finding |
| Where do people rest and not activate? | Affordances that look clickable | Hover counts beside click counts for the same target |
| Where does the UI ignore the second click? | Broken or slow controls | Events with `rage=true`, by target |
| Who reaches which area? | Whether a module is invisible to the role it was built for | View counts by `role_key` for `search`, `cortex`, `experiments`, `admin` |
| Is the client throwing? | Regressions after a release | `client.error` counts by day |

The page offers three ranges: 7, 28 and 90 days, read from `observe_daily` and from sums over `observe_visit_days`. It never lists a `visit_id`. It never offers a per-person filter, because there is no person column to filter on.

Export is `GET /api/observe/export?days=28`, admin only, the same aggregates as JSON. That file is what a firm can hand to Knovas. It contains no visit id and no raw event. Raw events are not exported and are not rendered.

Residual risk, stated on the page: in a small firm a rare role can single out the one person who holds it ("the administrator searched twenty times"). The mitigation that is in the design is the absence of a user id, so the row cannot be joined to an email. The mitigation that is not in the design, on purpose, is suppressing small roles. During a rollout the administrator is often the only user, and hiding that row would hide the only data.

### Copy

On Einstellungen, when the flag is on, this paragraph and no toggle (the operator turns the module on; a member cannot turn it off for themselves, because the record is not per person and a partial opt-out would bias the aggregates):

> Die Platform zeichnet auf, welche Bedienelemente benutzt werden und wie lange eine Ansicht geöffnet bleibt. Sie zeichnet nicht auf, wonach Sie suchen, welche Dokumente das sind, oder wer Sie sind. Die Auswertung sieht Rollen, keine Personen. Die Aufzeichnung dient der Verbesserung der Software und nicht der Beurteilung der Arbeit.

The Nutzung tab repeats the last sentence above the numbers.

## Search engagement, the second flag

`OBSERVE_SEARCH_ENGAGEMENT` defaults to false. When false, nothing in this section exists: the route is not registered and the client does not call it.

When true:

- `/api/search` keeps the `query_session_id` it already receives inside `knovas_client` and today discards. It stores the latest one per signed-in user in a new table `query_sessions(user_id TEXT, query_session_id TEXT, expires_at INTEGER)` inside the same SQLite file `document_grants` already uses, with the same one-hour TTL. It is not returned to the browser and not written to platform-db. Multi-worker gunicorn can see it for the same reason grants are in SQLite.
- The result-card actions that already know a pointer (`open`, preview, download, external open) also `POST /api/analytics/engagement` with `{action, pointer, position}`. Mapping: preview view → `view`, result open → `click`, download → `download`. There is no dismiss control in the UI, so `dismiss` is not sent. The call uses the CSRF header, is not awaited, and is not retried.
- The route checks the pointer against `document_grants` for the current user. An ungranted pointer is dropped. The route attaches the stored `query_session_id` and forwards to `POST /secured/analytics/engagement`. No session id means the forward is skipped. The forward runs in a daemon thread with a one-second timeout. Failure is logged as `engagement forward failed` with the HTTP status and without the pointer.
- Nothing about this call is inserted into `observe_events`. The pointer does not appear in a log line of this route.

This restores the signal the 2026-07-26 design removed, behind a flag, and leaves the ranking data in Knovas where that API already puts it. It is not a back door for the usage stream.

## Configuration

All three go in `knovas.env`.

| Setting | Default | |
|---|---|---|
| `OBSERVE_ENABLED` | `false` | The usage module. |
| `OBSERVE_RAW_DAYS` | `30` | Delete raw events older than this. Minimum 1, maximum 90. |
| `OBSERVE_ROLLUP_DAYS` | `400` | Delete aggregate rows older than this. Minimum 28, maximum 800. |
| `OBSERVE_SEARCH_ENGAGEMENT` | `false` | The ranking forwarder. |

A value outside the range is clamped and logged once at startup. A non-boolean is false.

## Failure and abuse

- The script failing, the endpoint being down, or the database refusing the insert does not change search, preview, login or admin.
- Invalid events are dropped, not partially repaired, except for props, which are stripped key by key so one bad property does not eat a click.
- The rate limit is the abuse control. There is no IP blocklist, because the IP is not stored.
- The endpoint is authenticated (or anonymous-but-login-only) and CSRF-protected. It is not a public collector.
- Reads of the aggregates require the `admin` role, the same gate as Personen and System. The export uses that gate. There is no API token.

## Tests

Server tests, in the style of the existing web tests:

- Flag off: the script tag is absent from the search page and from login, `POST /api/observe` is 404, the Nutzung tab is absent.
- A batch with an extra prop `query` equal to a sentence is stored with that prop gone and the click present.
- A prop `file_kind=pdf` is stored. `file_kind=secret.pdf` is removed.
- A client-supplied `role_key` is ignored; the stored role is the signed-in user's.
- An anonymous post of `search.query.submit` is dropped; an anonymous `login.submit` with `outcome=error` is stored with role `anonymous`.
- Two batches for one visit set `searched` and then `previewed`, and the second preview sets `reopened_preview`. `first_preview_ms` is set once and not overwritten.
- The daily histogram bucket increments by one for a 2 s hover (`b3000`).
- The 121st event in a minute for one visit is dropped and the response says so.
- A failed insert does not raise out of the route.
- The engagement flag off: the route is not registered. The flag on: a granted pointer is forwarded with the stored session id, an ungranted pointer is not, and `observe_events` gains no row either way.

A static test over `observe.js` asserts the file does not reference `textContent`, `innerText`, `placeholder` or `.value` as something it reads. The page scripts may use those for their own rendering; the observer may not.

A coverage test walks every template and every non-vendor script and fails on a visible control or region with no name, as specified under Coverage. The test is part of the same run as the server tests. A page that adds a button without `data-observe` does not pass.

## Out of scope

- Session replay, mouse coordinates, heatmaps drawn from pointer paths, scroll streams, keystroke timing, clipboard contents.
- Any transmission of events or aggregates to Knovas, other than the optional engagement forwarder, which sends only the four ranking actions the Secure API already defines.
- A per-user or per-email report. The schema has nowhere to put it.
- Writing measurements into Experimente.
- Putting ratings or a "nicht relevant" control back.
- The Knovas Connector and RemoteController. They have no person at a browser.
- Partitioning, and a second database.

## Where the code will live

For the plan that follows this spec, and not built by this document:

| Piece | Path |
|---|---|
| Migration | `KnovasPlatform/components/docbridge_integration/src/identity/migrations/0005_observe.sql` |
| Ingest, rollup, purge, export | `KnovasPlatform/components/docbridge_integration/src/web_interface/observe.py` |
| Script | `KnovasPlatform/components/docbridge_integration/src/web_interface/static/js/observe.js` |
| Script tag | `_sidebar.html`, and `login.html` for the two login events |
| Names on existing controls | the templates and the JS that builds result cards, the preview dialog, Cortex nodes |
| Admin tab and page | `_admin_tabs.html`, `templates/admin_usage.html`, a route on the admin blueprint |
| Settings sentence | `templates/settings.html` |
| Engagement forwarder | a short route in `app.py` and a table in the grants SQLite, both behind the second flag |
| Tests | `tests/test_observe.py`, plus the static assertion in the frontend static tests |

## Order of work

1. Schema, ingest, validation, rollup, purge, tests. No page yet. The module can be on in a pilot and inspected with SQL.
2. The script, registration of every visible component, and the coverage test. Names land on every current template and every DOM-building script in this step, not only on search. Search is the journey the Trefferliste decision depends on; an unnamed admin button fails the same test.
3. The Nutzung tab and the export, including the `observe.unnamed` defect count.
4. The engagement forwarder, separately, only if that flag is wanted.
