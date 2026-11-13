# Custom views («Eigene Ansichten») Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A switchable, generic Platform page that frames an installation's own view files and lets them read the Brain as the signed-in user, plus Knovas's mapping changes and its two views «Ziele» and «Wetten».

**Architecture:** A new package `src/custom_views/` (settings, manifest, registry, query, budget, read, document) behind one blueprint `custom_views`. The page builds each view into one HTML document that runs in a sandboxed `srcdoc` frame and talks to the page through one `MessageChannel`; the page relays reads to `POST /api/custom-views/<id>/read`, which reads through the views' existing `GraphReader` and per-user `TopologyCache` plus a new batch facts read. Knovas's data comes from its Jira mapping (KnowledgeBase) and two view folders that never enter a repository.

**Tech Stack:** Python 3.13, Flask 3.0, Jinja2, jsonschema 4.26 (`Draft202012Validator`), pytest; plain JavaScript and CSS (no library) in the browser; brain-runner mappings (JMESPath) in KnowledgeBase.

**Spec:** KnowledgeBase `docs/ModernDocs/plans/2026-11-13-custom-views.md` (FEAT-2026-11-13-custom-views, approved 2026-11-13; branch `plan/custom-views` in `E:\Knovas\KnowledgeBase\.worktrees\custom-views`). Jira epic SS-618; tasks SS-623 … SS-632.

**Repositories and branches:**
- Platform: `E:\Knovas\KnovasComponents\.worktrees\custom-views`, branch `feat/custom-views` (cut from `origin/cl/jolly-fermat-ro3vkk`). All Platform paths below are relative to `KnovasPlatform/components/docbridge_integration/` unless they start with `KnovasPlatform/` or `docker-compose.yml`.
- KnowledgeBase: `E:\Knovas\KnowledgeBase\.worktrees\custom-views`, branch `plan/custom-views`.
- Knovas's views: `E:\Knovas\knovas-custom-views\` (a plain folder, never a repository).
- Neither branch is pushed or merged before the release of Sat 14 Nov (SS-617). Both branches have no upstream on purpose.

## Global Constraints

- The Brain is the only source of truth for the Platform; a view never calls Jira or any source, and nothing writes to the Brain (spec § 3, F-8).
- Product code names no client, node type, relation, fact or key (DEC-0037). German UI text; identifiers and docs in English.
- The feature is on only when `custom_views.enabled` is true **and** user accounts are on (`identity_gate is not None`) **and** `_ontology_source_is_graph()`; otherwise no entry and every new route answers 404.
- `custom_views.enabled` and `custom_views.dir` are read from `config.yaml` / environment only (`CUSTOM_VIEWS_ENABLED`, default `false`; `CUSTOM_VIEWS_DIR`, default `/mnt/custom-views`). They are not on the settings page.
- Limits (spec P-1, P-4): a file 512 KB; a view 2 MB; at most 50 views; view id `^[a-z0-9][a-z0-9-]{0,39}$`; endings `.html` `.js` `.css`; folder re-read at most every 30 s; at most 200 nodes a read; budget 60 facts a minute per process (`custom_views.facts_per_minute`); one read per user at a time; at most 40 facts and 10 link specs per query.
- Manifest `roles`: `["*"]` = every signed-in user; `[]` = nobody (founder, 2026-11-13: everyone may open Knovas's views; RBAC later, SS-620).
- No database migration, no new dependency, no change to an existing route's behaviour.
- Commit messages name the Jira task and the plan, e.g. `Custom views: manifest (SS-625; FEAT-2026-11-13-custom-views)`, and end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Never mark a task done in Jira or the bet card; the founder verifies (decision D-5).

## Review Focus

1. **A view folder or file that is a symbolic link or a Windows junction.** Expected: the view is refused, also on Windows where `O_NOFOLLOW` does not exist. Pinned in Task 3.
2. **A manifest whose `reads` names a type the tenant does not have, or a fact a node lacks.** Expected: the read answers 200 with no nodes or without that fact, never 500. Pinned in Task 7.
3. **Markup in a manifest's title or description, or in an invalid view's reason.** Expected: the page and the JSON show it as text. Pinned in Task 9.
4. **A view file changed between the document and a later read.** Expected: each document is built from one snapshot of the files; the next rescan shows the new fingerprint. Pinned in Task 3.
5. **A double click: two reads by one user at once.** Expected: the second answers 429 at once with `Retry-After`, never waits. Pinned in Task 5 and Task 9.

---

## Part A — the Platform (KnovasComponents, `feat/custom-views`)

### Task 1: Settings and the switch (SS-625)

**Files:**
- Create: `src/custom_views/__init__.py`, `src/custom_views/settings.py`
- Modify: `config/config.yaml` (new top-level block after `experiments:`)
- Test: `tests/test_custom_views_settings.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) class CustomViewsSettings: enabled: bool = False; dir: str = "/mnt/custom-views"; facts_per_minute: int = 60; max_nodes: int = 200; rescan_seconds: int = 30; file_max_bytes: int = 524288; view_max_bytes: int = 2097152; max_views: int = 50`
- Produces: `def load_settings(config) -> CustomViewsSettings` (reads `custom_views.*` through `config.get_bool/get_int/get`; a value that does not parse falls back to the default and logs a warning).

- [ ] **Step 1: Write the failing tests**

```python
def test_the_switch_is_off_by_default(tmp_path): ...       # config without custom_views → enabled False, dir "/mnt/custom-views"
def test_values_come_from_the_environment(tmp_path, monkeypatch):
    # CUSTOM_VIEWS_ENABLED=true, CUSTOM_VIEWS_DIR=<tmp>, CUSTOM_VIEWS_FACTS_PER_MINUTE=12 → enabled True, dir, 12
def test_a_bad_number_keeps_the_default(tmp_path, monkeypatch):  # CUSTOM_VIEWS_FACTS_PER_MINUTE=abc → 60
```
Build the config with `config_loader.set_config(<tmp config.yaml>)` that contains the same block as `config/config.yaml`.

- [ ] **Step 2: Run, expect FAIL** — `pytest tests/test_custom_views_settings.py -q` → `ModuleNotFoundError: custom_views`.
- [ ] **Step 3: Implement** `load_settings` and add to `config/config.yaml`:

```yaml
custom_views:
  enabled: "${CUSTOM_VIEWS_ENABLED:-false}"
  dir: "${CUSTOM_VIEWS_DIR:-/mnt/custom-views}"
  facts_per_minute: "${CUSTOM_VIEWS_FACTS_PER_MINUTE:-60}"
```
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `Custom views: settings and switch (SS-625; FEAT-2026-11-13-custom-views)`.

### Task 2: The manifest (SS-625)

**Files:**
- Create: `src/custom_views/manifest.py`, `src/custom_views/manifest.schema.json`
- Test: `tests/test_custom_views_manifest.py`

**Interfaces:**
- Produces: `class ManifestError(ValueError)` (message in German, plain text, never the file's content beyond the field name).
- Produces: `@dataclass(frozen=True) class Manifest: id: str; title: Mapping[str, str]; description: Optional[Mapping[str, str]]; order: Optional[float]; roles: Tuple[str, ...]; entry: str; scripts: Tuple[str, ...]; styles: Tuple[str, ...]; reads: Mapping[str, Tuple[str, ...]]; link_hosts: Tuple[str, ...]`
- Produces: `def parse_manifest(view_id: str, raw: bytes) -> Manifest` and `def Manifest.allows(self, roles: frozenset) -> bool` (`"*"` in roles → True; empty → False; else intersection).

The schema (Draft 2020-12, `additionalProperties: false` everywhere): `format` const 1; `title` and `description` objects with required `de`, `en` strings ≤ 120 / ≤ 500 chars; `order` number; `roles` array of strings, required; `entry` string matching `^[A-Za-z0-9_-]{1,60}\.html$`; `scripts` items `^[A-Za-z0-9_-]{1,60}\.js$`; `styles` items `^[A-Za-z0-9_-]{1,60}\.css$`; `reads` required object, ≥ 1 property, property names `^[A-Za-z][A-Za-z0-9_]{0,63}$`, values arrays (≤ 40) of the same pattern; `links` object with `hosts` array of host names `^[a-z0-9.-]{1,253}$`. JSON larger than 64 KB is refused before parsing.

- [ ] **Step 1: Write the failing tests** — one valid manifest round-trips; each of these raises `ManifestError`: missing `roles`, missing `reads`, empty `reads`, an unknown key, `entry: "../x.html"`, a script `a.js.map`, a host with a scheme, a fact name with a dot, invalid JSON, 70 KB of JSON. `allows`: `["*"]` with `frozenset()` → True; `[]` with `{"admin"}` → False; `["admin"]` with `{"member"}` → False.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** with `Draft202012Validator` (checked once at import, as `src/builder/schema/__init__.py` does).
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `Custom views: manifest (SS-625; …)`.

### Task 3: The registry and safe file reads (SS-625)

**Files:**
- Create: `src/custom_views/registry.py`
- Test: `tests/test_custom_views_registry.py`

**Interfaces:**
- Consumes: `CustomViewsSettings`, `parse_manifest`, `Manifest`.
- Produces: `@dataclass(frozen=True) class ViewFile: name: str; data: bytes; sha256: str; mtime: float`
- Produces: `@dataclass(frozen=True) class View: manifest: Manifest; files: Mapping[str, ViewFile]` (all files of one view are read in one scan: the snapshot)
- Produces: `@dataclass(frozen=True) class Invalid: id: str; reason: str`
- Produces: `def folder_refusal(path: str, documents_root: Optional[str]) -> Optional[str]` — reason when the folder is missing, is a link, lies inside `documents_root`, or `os.access(path, os.W_OK)` is true.
- Produces: `def read_view_file(folder: str, name: str, limit: int) -> ViewFile` — raises `ManifestError` for a link, a non-regular file, or more than `limit` bytes.
- Produces: `class Registry: __init__(self, settings, *, documents_root=None, clock=time.monotonic, log=logger)`; `views() -> List[View]` (sorted by `order`, then id); `invalid() -> List[Invalid]`; `get(view_id) -> Optional[View]`; `for_roles(roles: frozenset) -> List[View]`; `refusal() -> Optional[str]`. Rescans when `clock()` passed `rescan_seconds` since the last scan; logs every fingerprint change (`custom_views: <id>/<file> changed sha256=<first 12>`).

`read_view_file`: `os.lstat` the folder and the file and refuse `S_ISLNK`; on POSIX open with `O_RDONLY | O_NOFOLLOW | O_NONBLOCK`; on Windows refuse when `os.path.realpath(p) != os.path.abspath(p)` (junctions); then `os.fstat` the descriptor and refuse unless `S_ISREG`; read `limit + 1` bytes and refuse when more than `limit`. Reuse `_autodoc_candidate`'s two `commonpath` checks for confinement (copy the logic into this module; `app.py` is not imported here).

- [ ] **Step 1: Write the failing tests** (folders built under `tmp_path`):
  `test_no_folder_means_no_views`, `test_a_valid_view_is_listed_with_fingerprints`, `test_an_invalid_view_is_skipped_with_its_reason_and_never_hides_a_valid_one`, `test_ids_outside_the_pattern_are_ignored`, `test_a_file_over_512_kb_refuses_the_view`, `test_a_view_over_2_mb_is_refused`, `test_more_than_50_views_keeps_the_first_50_by_id`, `test_a_symlinked_file_or_folder_is_refused` (skip when `os.symlink` raises `OSError`), `test_a_file_named_in_the_manifest_but_missing_refuses_the_view`, `test_a_rescan_happens_only_after_30_seconds` (fake clock), `test_a_changed_file_shows_a_new_fingerprint_and_is_logged` (caplog), `test_folder_refusal_inside_documents_and_writable` (on Windows assert the writable case via `os.access` monkeypatch).
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** `registry.py` as above.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `Custom views: registry and safe file reads (SS-625; …)`.

### Task 4: The query and what a manifest allows (SS-627)

**Files:**
- Create: `src/custom_views/query.py`
- Test: `tests/test_custom_views_query.py`

**Interfaces:**
- Consumes: `Manifest`.
- Produces: `class QueryError(ValueError)` (→ 400); `class Forbidden(Exception)` (→ 403, always the message `"Diese Ansicht darf das nicht lesen."`).
- Produces: `@dataclass(frozen=True) class LinkSpec: relation: str; direction: Literal["out", "in"]`
- Produces: `@dataclass(frozen=True) class Query: type: str; ids: Optional[Tuple[str, ...]]; linked_to: Optional[Tuple[str, ...]]; linked: Optional[LinkSpec]; where: Mapping[str, Tuple[str, ...]]; facts: Tuple[str, ...]; links: Tuple[LinkSpec, ...]; limit: int`
- Produces: `def parse_query(body: Any, *, max_nodes: int) -> Query` and `def authorize(query: Query, manifest: Manifest) -> None`.

Rules: `type` required string; `ids` ≤ 200 strings; `linked` = `{"to": [ids ≤ 200], "relation": str, "direction": "out"|"in"}`; `where` maps fact → string or list of ≤ 20 strings; `facts` ≤ 40; `links` ≤ 10; `limit` 1…`max_nodes`, default `max_nodes`; any other key → `QueryError`. `authorize`: `type in manifest.reads`, every name in `facts` and `where` in `manifest.reads[type]`, else `Forbidden`.

- [ ] **Step 1: Write the failing tests** — a full combination test: for every `type ∈ {allowed, other}`, `facts ∈ {allowed, other}`, `where ∈ {none, allowed fact, other fact}`, assert `authorize` raises `Forbidden` exactly when something is outside the manifest (D-4: every combination). Plus `parse_query` refusals: unknown key, 201 ids, `limit: 0`, `limit: 201`, `direction: "both"`, a non-dict body.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `Custom views: query and manifest checks (SS-627; …)`.

### Task 5: A budget that refuses, and one read per user (SS-627)

**Files:**
- Create: `src/custom_views/budget.py`
- Test: `tests/test_custom_views_budget.py`

**Interfaces:**
- Produces: `class RefusingBudget: __init__(self, per_minute: int, clock=time.monotonic)`; `take(self, units: int) -> Optional[float]` — takes all units and returns None, or takes nothing and returns the seconds until they would be there (rounded up, ≥ 1). Capacity = `per_minute`; refills continuously.
- Produces: `class OneAtATime: try_enter(self, key: str) -> bool`; `leave(self, key: str) -> None` (thread-safe, in process; a key enters again only after `leave`).
- Produces: `def shared_budget(per_minute: int) -> RefusingBudget` (one per process, like `graph_views.pacing.shared_bucket`).

- [ ] **Step 1: Write the failing tests** — `take(60)` on a fresh 60/min budget → None, then `take(1)` → ≈ 1 s; after 30 s fake time `take(30)` → None; `take(61)` → never all (returns ≥ 1 and takes nothing); `OneAtATime`: second `try_enter("u")` → False until `leave("u")`; another key enters.
- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement.** **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `Custom views: refusing budget (SS-627; …)`.

### Task 6: Facts in batches (SS-627)

**Files:**
- Modify: `src/knovas_client.py` (after `graph_get_many`, ~2315), `src/graph_views/reader.py`
- Modify: `tests/views_fixture.py` (`FakeViewsClient` gains the batch route)
- Test: `tests/test_graph_reader_batch.py`

**Interfaces:**
- Produces: `KnovasAPIClient.graph_facts_batch(self, node_ids: List[str]) -> Tuple[int, Any]` — `POST {base_url}/secured/graph/facts/batch` with `json=self._with_principal({"node_ids": node_ids})`, `headers=self._get_headers()`, `timeout=self.http_read_timeout`, `allow_redirects=False`; returns `(status, parsed json or None)`; a transport error → `(599, None)`; at most 500 ids (`ValueError` above). No retries (the read answers the caller instead).
- Produces: `GraphReader.facts_batch(self, node_ids: Iterable[str]) -> Dict[str, Optional[List[Dict]]]` — chunks of 500; answer `data.facts_by_node` (the route's success envelope: `{"success": true, "data": {"facts_by_node": {...}}}`; accept a top-level `facts_by_node` too); an id absent from the answer → `[]` is **wrong** (absent means hidden or unknown) → `None`. A 404 or 405 from the route → falls back to `facts_many` for that chunk and sets `self.batch_unavailable = True`; any other status ≥ 400 → `None` for every id in the chunk.
- `FakeViewsClient.graph_facts_batch(node_ids)` serves the same envelope from the fixture graph, leaving hidden and unknown ids out; a constructor flag `batch=False` makes it answer `(404, None)`.

- [ ] **Step 1: Write the failing tests** — batch answers the same facts as `facts_many` for visible ids; a hidden id is `None`; 501 ids make two calls; `batch=False` falls back and sets `batch_unavailable`; a 503 gives `None` for the chunk; the principal is in the body (fake client records the body).
- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement.** **Step 4: Run, expect PASS.** Also run `pytest tests/test_views_graph.py -q` (unchanged behaviour).
- [ ] **Step 5: Commit** — `Graph reader: facts in batches over POST /facts/batch (SS-627; …)`.

### Task 7: The read (SS-627)

**Files:**
- Create: `src/custom_views/read.py`
- Test: `tests/test_custom_views_read.py`

**Interfaces:**
- Consumes: `Query`, `LinkSpec`, `Manifest`, `GraphReader.facts_batch`, `graph_views.topology.SubjectState` (`.index` with `nodes`, `by_type`, `neighbours(node_id, relation=None, direction=None)`, `type_of`, `complete`), `graph_views.facts.fact_values`, `RefusingBudget`.
- Produces: `class BudgetExceeded(Exception): retry_after: float`; `class TooBroad(Exception)` (→ 422).
- Produces: `def run_read(query: Query, manifest: Manifest, state, reader, budget: RefusingBudget, *, max_nodes: int) -> Dict[str, Any]` returning `{"nodes": [...], "total": int, "links_hidden": int, "complete": bool, "incomplete_why": [..], "read_at": ISO-8601 UTC}`.

Order of work: candidates = `state.index.by_type.get(query.type, [])` → narrowed by `ids` → narrowed by `linked` (a node qualifies when one of its `neighbours(…, relation, direction)` is in `linked_to`; ids of types outside `manifest.reads` match nothing) → if `where` is set and more than `max_nodes` candidates remain → `TooBroad` → the nodes that need facts (all candidates when `where` is set, else the first `limit` by name) → `budget.take(len(ids_needing_facts))` → `BudgetExceeded` → `reader.facts_batch(ids)` → filter by `where` (trimmed, case-insensitive; a node whose facts are `None` is dropped and counted under `facts_unreadable`) → first `limit` by name → for each node `facts` = only the requested names present in `fact_values` → `links` per `LinkSpec` from `neighbours`, keeping only ends whose `type_of` is in `manifest.reads` and counting the rest in `links_hidden`. `incomplete_why` gets `node_cap` (more matched than `limit`), `facts_unreadable`, `topology_partial` (`not state.index.complete`).

- [ ] **Step 1: Write the failing tests** over `views_fixture.client_zero_graph()` with a `GraphReader(FakeViewsClient(g), acquire=None)` and `TopologyCache(...).get(...)`: by type; `ids`; `linked` in and out; `where` single and list; facts filtered to the requested and allowed; a link to a type outside `reads` is hidden and counted; a type the tenant lacks → 0 nodes, complete; `where` over 200 candidates → `TooBroad`; budget empty → `BudgetExceeded` and no facts read (fake records no batch call); `limit` 2 of 5 → `node_cap`; a node whose facts read fails → dropped, `facts_unreadable`; full combination of link end types × `reads` (D-4).
- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement.** **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `Custom views: the read (SS-627; …)`.

### Task 8: The document (SS-626)

**Files:**
- Create: `src/custom_views/document.py`, `src/custom_views/bridge_client.js`
- Test: `tests/test_custom_views_document.py`

**Interfaces:**
- Consumes: `View`, `ViewFile`.
- Produces: `def new_nonce() -> str` (`secrets.token_urlsafe(18)`); `def build_document(view: View, nonce: str) -> str`.
- `build_document` writes, in this order: `<!DOCTYPE html><html lang="de"><head><meta charset="utf-8">`, the policy meta tag, one `<style nonce>` per `styles` file (content with `</` escaped as `<\/`), `</head><body>`, the entry markup, `<script nonce>` with the bridge client, one `<script nonce>` per `scripts` file (`</script` escaped as `<\/script`), `</body></html>`.
- The policy (exact): `default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; img-src data:; font-src data:; base-uri 'none'; form-action 'none'; require-trusted-types-for 'script'`.
- The entry markup is refused (→ the view is invalid, checked in Task 3's scan) when it contains `<script`, `<link`, `<base`, `<meta`, `<iframe`, `<object`, `<embed`, ` on…=` attributes, `style=` attributes, `src=`/`href=` other than `#…` or `data:` (case-insensitive scan; the policy is the second line of defence).
- `bridge_client.js` defines `window.knovas = { read(query) → Promise, open(url), resize(height), ready(callback) }`. It waits for one `message` from `window.parent` whose `data.type === "knovas.port"` and takes `event.ports[0]`; afterwards it ignores every other message. Requests carry an increasing `id`; answers are matched by `id`. It exposes no other global.

- [ ] **Step 1: Write the failing tests** — the policy tag is the first element in `<head>` after the charset; every `<script` and `<style` carries the same nonce; two builds give two nonces; a script file containing `</script>` cannot close the element; the entry checks refuse each listed construct; the output contains no `http:` or `https:` reference unless a view file holds one in a string.
- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement** (and call the entry check from `registry` so an unsafe entry makes the view invalid). **Step 4: Run, expect PASS** (also `tests/test_custom_views_registry.py`).
- [ ] **Step 5: Commit** — `Custom views: the document and the bridge client (SS-626; …)`.

### Task 9: Routes, wiring, sidebar, headers, audit (SS-626, SS-627)

**Files:**
- Create: `src/web_interface/custom_views_routes.py`
- Modify: `src/web_interface/app.py` (settings and registry near the experiments setup ~2111; `_custom_views_nav()` beside `_experiments_nav()` ~1732; `_sidebar_context()` ~1765; registration inside `if identity_gate is not None:`; `_prevent_stale_ui_assets` ~886 adds `/custom-views` and `/custom-views/`)
- Modify: `src/web_interface/templates/_sidebar.html` (entry after Experimente; header comment lists `'custom_views'`)
- Test: `tests/test_custom_views_gates.py`, `tests/test_custom_views_routes.py`

**Interfaces:**
- Consumes: everything above; `identity_gate.current_user()` (`User.id`, `User.roles`), `identity_gate.connection()`, `identity.audit.record(conn, *, action, actor, target_type, target_id, detail)`, `view_service.cache`, `view_service.settings.workers`, `graph_views.topology.subject_key`, `GraphReader`, `_ensure_csrf_token`, `_csrf_token_is_valid`.
- Produces: `def create_custom_views_blueprint(*, gate, registry: Registry, settings: CustomViewsSettings, client: Callable[[], Any], cache, workers: int, enabled: Callable[[], bool], page_context: Callable[[], dict], csrf_valid: Callable[[str], bool]) -> Blueprint` with blueprint name `"custom_views"`.
- Routes and answers (spec P-2 to P-4):
  - `GET /custom-views`, `GET /custom-views/<view_id>` → `custom_views.html`, `active_nav="custom_views"`; headers `X-Frame-Options: SAMEORIGIN`, `Content-Security-Policy: frame-ancestors 'self'; frame-src 'none'; object-src 'none'; base-uri 'self'`, `Cross-Origin-Opener-Policy: same-origin`, `Cache-Control: no-store`.
  - `GET /api/custom-views` → `{"views": [{"id","title","description"}], "invalid": [...]}`; for admins each view also carries `files: [{"name","sha256","changed_at"}]` and `invalid` is filled (others get `[]`).
  - `GET /api/custom-views/<view_id>/document` → requires header `X-CSRF-Token` valid and `Sec-Fetch-Dest` absent or `empty` (else 403); answers `{"title": {...}, "html": "...", "links": [hosts]}` with `Content-Security-Policy: sandbox`, `X-Content-Type-Options: nosniff`, `Cache-Control: no-store`.
  - `POST /api/custom-views/<view_id>/read` → 401 no user; 404 feature off, unknown or forbidden view; 400 `QueryError`; 403 `Forbidden`; 422 `TooBroad`; 429 with `Retry-After` from `OneAtATime` or `BudgetExceeded`; 409 when not graph mode; 502 on `ReadFailed`; else 200 with `run_read`'s answer. Every 200 and every 403 writes `audit.record(gate.connection(), action="custom_view.read", actor=user, target_type="custom_view", target_id=view_id, detail={"type": q.type, "nodes": n, "complete": bool, "outcome": "ok"|"forbidden"})`.
- No route parameter is named `doc_id` (that name triggers `require_readable_document`).

- [ ] **Step 1: Write the failing tests.** Full-app tests follow `tests/test_web_cortex_switch.py` with the identity app of `conftest._identity_app(..., extra_yaml=...)` and `monkeypatch.setenv("ONTOLOGY_SOURCE", "graph")`; the API client is a subclass of `FakeViewsClient` that also has the attributes `DummyKnovasClient` provides (`attach_principal_broker`). `test_custom_views_gates.py`: every combination of switch (on/off) × accounts (on/off) × graph mode (on/off) × a valid view for the user (yes/no) gives the sidebar entry and 200 on `GET /custom-views` only when all four hold, else no entry and 404 on all four routes (D-4). `test_custom_views_routes.py`: list contents and order; `roles: []` hides a view and answers 404 like an unknown id; `invalid` only for admins and rendered as text (a title `<img src=x onerror=alert(1)>` appears escaped in the page and as plain JSON text); document refused without `X-CSRF-Token` and with `Sec-Fetch-Dest: document`; document headers; read without CSRF → 403 (existing gate); read with `access_groups` in the body → 400 (existing gate); the 403 for a fact outside `reads` writes an audit row with `outcome: forbidden`; a second concurrent read by one user → 429 (hold the first read inside a fake `facts_batch` with a `threading.Event`); the page's headers.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** the blueprint and the wiring. `_custom_views_nav()` returns False when the feature is off, there is no user, or `registry.for_roles(user.roles)` is empty; wrap it in try/except like `_experiments_nav()`. At start, when the switch is on, log `registry.refusal()` at WARNING and treat the feature as off while a refusal exists (`documents_root` = the configured AutoDoc path).
- [ ] **Step 4: Run, expect PASS**, then `pytest tests/test_web_cortex_switch.py tests/test_views_graph.py tests/test_experiments_frontend.py -q` (sidebar and gates unchanged for everything else).
- [ ] **Step 5: Commit** — `Custom views: routes, sidebar entry, headers and audit (SS-626; SS-627; …)`.

### Task 10: The page and the bridge host (SS-626)

**Files:**
- Create: `src/web_interface/templates/custom_views.html`, `src/web_interface/static/js/custom_views.js`, `src/web_interface/static/css/custom_views.css`
- Test: `tests/test_custom_views_page.py`

**Interfaces:**
- Consumes: the four routes of Task 9; the bridge protocol of Task 8 (`knovas.port`, request `{id, kind: "read"|"open"|"resize", ...}`, answer `{id, ok, data|error, status}`).
- Page skeleton: as `experiments_list.html` (sidebar include, `meta csrf-token`, `style.css` + `custom_views.css` with `?v={{ asset_version }}`), a picker (`<nav>` with one link per view, titles via `textContent` in JS or Jinja autoescape), a bar `Eigene Ansicht · <title>`, a frame host `<div id="cvFrameHost">`.
- `custom_views.js`: on picking a view, removes the old frame, fetches the document (`X-CSRF-Token` header), creates `iframe` with `sandbox="allow-scripts"`, `referrerpolicy="no-referrer"`, `srcdoc = html`, and on its `load` posts `{type: "knovas.port"}` with `port2` of a new `MessageChannel` to `iframe.contentWindow` (target origin `"*"`, the frame's origin is opaque). It listens only on `port1`. Per message: shape-check; `read` → one at a time (a second gets `{ok:false, status:429}`), `POST …/read` with the CSRF header, relay status and JSON; `open` → `new URL(url)`; refuse unless `protocol === "https:"`, no `username`/`password`, `port === ""`, and `hostname` exactly in the document answer's `links` → `window.open(href, "_blank", "noopener,noreferrer")`; `resize` → `Number.isFinite`, clamp to 200…20000 px, set `style.height`. All text the page draws uses `textContent`. For admins the page shows, under the picker, each view's files with fingerprint (first 12 characters) and change time, and the invalid views with their reasons.

- [ ] **Step 1: Write the failing tests** (server side): the page renders the picker in `order`; the template contains `sandbox="allow-scripts"` and no `allow-same-origin`; the admin block appears for an admin only.
- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement.** **Step 4: Run, expect PASS.** The browser behaviour is proven in Task 12.
- [ ] **Step 5: Commit** — `Custom views: the page and the bridge host (SS-626; …)`.

### Task 11: Generic guard, deployment, operator's page (SS-628)

**Files:**
- Create: `tests/test_custom_views_generic.py`, `KnovasPlatform/docs/custom-views.md`, `tests/fixtures/custom_views/probe/` (neutral fixture view: `view.json`, `index.html`, `probe.js`, `probe.css`)
- Modify: `docker-compose.yml` (`docbridge-web`: environment `CUSTOM_VIEWS_ENABLED: ${CUSTOM_VIEWS_ENABLED:-false}`, `CUSTOM_VIEWS_DIR: /mnt/custom-views`; volume `- ${CUSTOM_VIEWS_PATH:-./data/no-custom-views}:/mnt/custom-views:ro`), `KnovasPlatform/docs/deployment/knovas-tenant.md` (one paragraph pointing to the new page)

**Interfaces:**
- The generic guard scans `src/custom_views/**`, `src/web_interface/custom_views_routes.py`, `custom_views.html`, `custom_views.js`, `custom_views.css` for: `af639810`, `Knovas GmbH`, `client-zero`, `BET-`, `FEAT-`, `GOAL-`, `SS-`, `jira`, `atlassian`, and every node type and relation name in `tests/views_fixture.py`'s graph (case-insensitive, word boundaries) and fails on any hit.
- The probe view reads one type, then runs the probes of Task 12 and writes each result into its own page as text.
- The operator's page covers: what a custom view is (trusted code, the risk in plain words, why the switch is off by default, SS-621 before any client), the switch and the folder, the manifest fields, the three bridge calls, the read query and answer, the limits, the rule "draw data with `textContent`", where the audit entries are.

- [ ] **Step 1: Write the failing guard test.** **Step 2: Run, expect FAIL** only if a hit exists (then fix the code, not the test). **Step 3: Add compose, docs and the probe view.** **Step 4: Run** the whole custom-views suite `pytest tests -q -k custom_views` → PASS; `docker compose config` with `CUSTOM_VIEWS_ENABLED=true` shows the variable and the read-only mount.
- [ ] **Step 5: Commit** — `Custom views: generic guard, compose, operator's page (SS-628; …)`.

### Task 12: Browser evidence (SS-628)

**Files:**
- Create: `docs/superpowers/reports/2026-11-custom-views-browser-evidence.md`
- Test: the probe view of Task 11, served by a local development server (Task 16 shows how to start one).

The probe view runs and reports, each as `PASS`/`FAIL` text: `document.cookie === ""`; `fetch("/api/custom-views")` rejects; `fetch("https://example.com")` rejects; `new Image().src = "https://example.com/x.png"` triggers `error` without a request (check the server log and the browser's network panel); `location.href = "https://example.com"` leaves the frame unchanged; a link click with `target=_top` does nothing; `window.open(...)` returns null; a form post is blocked; a `<script>` and a `<style>` inserted through `innerHTML` from a string do not run / do not apply (trusted types where present); `knovas.open("https://example.com/")` is refused and `knovas.open("https://<allowed host>/")` opens a tab; a `postMessage` from another tab to the frame is ignored; opening `/api/custom-views/probe/document` directly as a page shows JSON and runs nothing.

- [ ] **Step 1:** Run the probe in current Chrome (automated with the Claude-in-Chrome tools where available) and record the results with a screenshot.
- [ ] **Step 2:** The founder runs it in Edge, Firefox and Safari (checklist in the report); results recorded with browser versions.
- [ ] **Step 3:** If any line fails in any browser: stop, record it, and ask the founder (spec § 8).
- [ ] **Step 4: Commit** the report — `Custom views: browser evidence (SS-628; …)`.

---

## Part B — KnowledgeBase (`plan/custom-views`)

### Task 13: Paperwork before code (SS-623)

**Files:**
- Create: `docs/ModernDocs/client-zero/ontology/decisions/DEC-0072-*.md` … `DEC-0076-*.md` (D-1 … D-5 of the spec; skill `decision-pack`; decided by the founder 2026-11-13, his words quoted)
- Create: the component cards the plan touches (skill `map-components`, DEC-0068): the Platform web app's custom views part and the Jira mapping; `docs/ModernDocs/plans/2026-11-13-custom-views.estimate.yaml` (`scripts/count_work_units.py --plan`)
- Modify: `docs/ModernDocs/steering/bets/BET-2026-11-client-zero.yaml` (`plans:` gains `FEAT-2026-11-13-custom-views`; items CV-0 … CV-9 = W-0 … W-9 with their Jira keys and points), `docs/ModernDocs/plans/2026-11-10-write-back.md` (§ 7: one row R14 pointing to SS-618), `docs/ModernDocs/plans/2026-11-13-custom-views.md` (glossary «Eigene Ansicht / custom view»; proposed invariants named)
- Already on the branch: `docs/ModernDocs/steering/vision.yaml` (`served_by`, S-1)

- [ ] **Step 1:** Write the five decision records with the skill; numbers DEC-0072 … DEC-0076 (check the folder first; take the next free ones).
- [ ] **Step 2:** Card the components with the skill; add `serves`/`motivated_by` links in `plan-links.yaml` as the skill asks.
- [ ] **Step 3:** Bet card and write-back row; estimate worksheet.
- [ ] **Step 4: Verify** — `py -3 scripts/steering/validate.py --no-git` → `0 errors`; `py -3 -m pytest scripts/client_zero/tests -q -k "steering or decision or card"` → PASS.
- [ ] **Step 5: Commit** — `Custom views: decisions D-1…D-5, cards, estimate, bet card (SS-623; FEAT-2026-11-13-custom-views)`.

### Task 14: The Jira mapping: link, plan and bet (SS-624)

**Stop point:** `jira-issues.yaml` may write `url` only when `Issue.url` is an attribute in `docs/ModernDocs/client-zero/ontology/terms.yaml`, and since 2026-11-12 that file changes only through the Builder's mirror commit (DEC-0041). On this branch's base the first mirror commit (runbook 10.3 h) has not landed. Before Step 3: if `terms.yaml` has no `Issue.url`, stop and ask the founder whether the mirror commit of his export (after his Builder steps) goes onto this branch.

**Files:**
- Modify: `docs/ModernDocs/client-zero/mappings/jira-issues.yaml`
- Modify: `scripts/client_zero/tests/test_jira_issues.py` (not frozen; checked 2026-11-13)

**Interfaces:**
- New fact: `url: {value: "join('', ['https://knovas.atlassian.net/browse/', r.external_id])", priority: 10}`
- New relations:
  `- {relation: part_of, to_type: FeaturePlan, to_key: "r.fields.labels[?starts_with(@, 'FEAT-')]", single: true, origin: canonical}`
  `- {relation: part_of, to_type: Bet, to_key: "r.fields.labels[?starts_with(@, 'BET-')]", single: true, origin: canonical}`
- The header comment gains the two relations and the `url` fact, naming FEAT-2026-11-13-custom-views.

- [ ] **Step 1: Write the failing tests** in `test_jira_issues.py`: `test_every_issue_carries_its_jira_link` (`url == "https://knovas.atlassian.net/browse/SS-2"`); `test_a_feat_label_links_the_epic_to_its_plan_and_a_bet_label_to_its_bet` (plan `edges_add` contains `("part_of", "FEAT-…")` and `("part_of", "BET-…")` when the tenant has both nodes; seed them in `_tenant` with types `FeaturePlan` and `Bet`); `test_two_labels_of_one_kind_give_no_edge_and_a_report` (`unresolved` kind `ambiguous_target`); `test_a_label_naming_nothing_gives_no_edge_and_a_report`; other labels ignored. Update the expectations of `test_each_issue_record_becomes_an_issue_with_its_facts` (facts gain `url`) and `test_nothing_else_from_the_stream_reaches_the_plan_or_the_brain` (the allowed fact set gains `url`).
- [ ] **Step 2: Run, expect FAIL** — `py -3 -m pytest scripts/client_zero/tests/test_jira_issues.py -q`.
- [ ] **Step 3: Implement** the mapping change (after the stop point).
- [ ] **Step 4: Run, expect PASS**, plus `py -3 -m pytest scripts/brain_runner/tests -q -k "config or mapping"`.
- [ ] **Step 5: Commit** — `Jira mapping: link, plan and bet of an issue (SS-624; FEAT-2026-11-13-custom-views)`.

---

## Part C — Knovas's views (`E:\Knovas\knovas-custom-views\`, no repository)

Both views: plain HTML/CSS/JS; German; every value from the Brain through `textContent`; a header line «Gelesen um HH:MM», a button «Neu laden», a banner when an answer has `complete: false` (naming the reasons in German); `roles: ["*"]`; `links.hosts: ["knovas.atlassian.net"]`.

### Task 15: «Ziele» (SS-629)

**Files:** `ziele/view.json`, `ziele/index.html`, `ziele/ziele.js`, `ziele/ziele.css`

`reads`: `Vision: [key, statement, status]`, `Goal: [key, period, period_key, status, owner]`, `Bet: [key, short_name, status, priority, priority_rank]`, `FeaturePlan: [key, status]`, `Issue: [key, status, issue_type, url]`.

Reads: all `Vision`; all `Goal` with links `serves` out and `part_of` out; all `Bet` with link `serves` out; on opening a bet: `FeaturePlan` linked `part_of` in to the bet, then `Issue` where `issue_type = Epic` linked `part_of` in to those plans and to the bet (one read each). Tree, folding per level with what it hides («3 Wetten · 1 Epic»), a level switch, a period picker from `period_key` (defaults to the current quarter), groups «Ohne Vision», «Ohne Ziel», «Ohne Plan»; an epic linked to a plan and to the bet appears once under the plan; an epic click → `knovas.open(url)`.

- [ ] Build against the workstation server (Task 16) with the probe of real data; check every vision, goal and bet in `knovas_list_nodes` appears; record the check in Jira SS-629 as a comment.

### Task 16: «Wetten» (SS-629) and the workstation demo (SS-630)

**Files:** `wetten/view.json`, `wetten/index.html`, `wetten/wetten.js`, `wetten/wetten.css`; `E:\Knovas\knovas-custom-views\README.txt` (how to start the demo)

`reads`: `Bet: [key, short_name, status, priority, priority_rank, approved_on, proposed_on, appetite_ends_on, appetite_weeks, appetite_founder_hours]`, `FeaturePlan: [key, status]`, `Issue: [key, status, issue_type, created_on, due_date, resolved_on, url]`.

Timeline from the earliest start to the latest end (at least today + 4 weeks), month ticks, today line; rows grouped «Jetzt», «Danach», «Später» by `priority`, ordered by `priority_rank`; bar `approved_on` (else `proposed_on`) → `appetite_ends_on`, label «N Wo · H h»; «kein Zeitbudget» without an end; a bet folds into its epics (read on opening), each with a bar `created_on` → `due_date` filled by the share of child issues with `resolved_on`; «N Aufgaben ohne Epic».

Demo server (README): a PostgreSQL container on port 55433 for accounts (as the tests use), env from `KnovasPlatform/docs/deployment/knovas-tenant.md` § 3 with `IDENTITY_ENABLED` on, `PLATFORM_DB_DSN`, `ONTOLOGY_SOURCE=graph`, `CUSTOM_VIEWS_ENABLED=true`, `CUSTOM_VIEWS_DIR=E:\Knovas\knovas-custom-views`, the workstation certificate; start from `src/` with `create_app().run(host="127.0.0.1", port=5077)`.

- [ ] Build; start the demo; show the founder (W-7): entry appears with switch and folder and disappears without either; both views show the Brain's data.

---

## The founder's steps (not implementation tasks)

- **W-8 (SS-631):** the Builder steps (sent in chat on 2026-11-13), the mirror commit, `brain_mapping_local.sh sync` / `jira` on server01, approving the epic label list before Claude sets the labels.
- **W-9 (SS-632):** images from the branch, `server_01_home`, verification, approving the pull requests.
