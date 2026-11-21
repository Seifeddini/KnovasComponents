# Work order PMVP-30a: the dossier (facts with their source, documents)

| | |
|---|---|
| Item | PMVP-30a, plan `FEAT-2026-11-19-platform-mvp` § 4 Brain views B-02, B-04; § 5.2 ("Brain views"); contract L2-PLATFORM-BRAIN-VIEWS RESP-01, PRE-01, POST-02, POST-03 |
| Class | routine |
| Branch / worktree | `feat/pmvp-30a` from **`origin/feat/pmvp-11`** (the navigation, finishing; it holds the look), worktree `.worktrees/pmvp-30a`; merge `origin/feat/platform-mvp` in once (it holds the page parts) |
| Visual contract | `docs/platform-mvp/reference-screens/02-dossier.html` (and `out/02-dossier.png`) |
| Card | `docs/components/L2-platform-brain-views.card.yaml` (today "planned"; this item gives it its first code) |

## Goal

A person opens what the firm knows about **one thing** (a client, a matter, a contract): its facts, each with the
passage it comes from, and its documents. This is the first Brain view a member sees.

## What to build

1. **Module and route.** `src/brain_views/` (new package) for the reading, `src/web_interface/brain_views_routes.py`
   (a blueprint) for the page, one wiring line in `app.py`. Address `GET /dossier/<node_id>`; template
   `dossier.html` built from the page parts (`{% import "_parts.html" as parts %}`: `page`, `page_head`, `section`,
   `card`, `rowline`, `tabs`, `tag`, `link`), in the Workspace area of the navigation.
2. **One starting point** (PRE-01, B-02): exactly one node id. No list of all things, no "whole graph" page.
3. **Per-thing reads only** (POST-03): read the node, its facts and its documents with the per-node routes the
   Platform's client already has (`graph_node`, `graph_facts`, `graph_neighbors` with depth 1, `graph_get_many`,
   `graph_facts_batch`). **Never** `graph_export` or an unfiltered `graph_nodes`. A test makes the fake client fail
   on any whole-tenant call.
4. **As the person.** The reads go through the same per-person client the search page uses (find how the search
   page gets its client and identity). Never a service identity. A node the person may not see answers the same as a
   node that does not exist (404, no difference).
5. **Facts with their source** (B-04, POST-02): each fact shows its value and, on click, the passage it comes from
   (document, page, the passage text) using the part `card`/`rowline` and a link that opens the document at the
   passage the way the search page opens a hit. A fact without a source passage carries a mark ("ohne Beleg",
   `tag(kind="warn")`) and is never shown as if it had one. **Find out from the Core's code (KnowledgeBase
   `knovas-software/app/src/api/secure_api.py` and the graph services on `origin/master`; read with `git show`,
   check nothing out there) which field of a fact carries its source.** If no field does, stop and report which
   fields exist; do not guess.
6. **Documents**: the documents assigned to the node (the knowledge assignments `graph_node` returns), as rows that
   open the document view.
7. **Related things** stay out (PMVP-30b). Leave one section "Verbunden" with only the counts per kind if the node
   read gives them cheaply; otherwise leave it out and say so.
8. **The switch** `brain_views.enabled` in `src/platform_settings.py`'s `REGISTRY` (installation-wide, **off** by
   default, `BRAIN_VIEWS_ENABLED` pins it). Off: the route answers 404. Another builder (PMVP-25) adds assistant
   entries to the same registry at the same time; add yours as one block, next to each other, so the merge is easy.
9. **Hrefs** (follow-up FU-34): pass the parts only addresses built with `url_for`, never text from the Brain or a
   document. Every value from the Brain is escaped (the parts do it; never `|safe`).
10. **The card**: `L2-platform-brain-views.card.yaml` gets its source paths, the operation `GET /dossier/<id>` and
    its lines (PRE-01, POST-02, POST-03 with the tests as evidence; POST-01's 25-node limit is PMVP-34's, a `review`
    entry); `boundaries.yaml`; card checks as in `work-orders/PMVP-01.md`.

## Your tests (`tests/test_brain_views_dossier_*.py`)

Signed out 302 to sign-in; switch off 404; a member opens a node 200; a node the person may not see and a missing
node give the same 404; a fact with a source opens its passage; a fact without one is marked; no whole-tenant call;
a name or fact value holding `<script>` comes out as text; no customer text in a log line. Use the existing fakes
(`FakeGraphApi`, `workbench_app`, `member_client`, `admin_client` in `tests/conftest.py`); extend a fake only in your
own test file.

## Files you may change

`src/brain_views/**` (new), `src/web_interface/brain_views_routes.py` (new), `templates/dossier.html` (new), one
wiring line in `app.py`, the one registry block in `platform_settings.py`, a stylesheet of its own if the parts are
not enough (`static/css/brain_views.css`, tokens only), your tests, the card, its page, `boundaries.yaml`, the notes
below.

## Files you must not change

The frozen files, `conftest.py`, the parts (`_parts.html`, `parts.css`), `_sidebar.html`, other routes, `knovas_client.py`
(if the client lacks a per-node read, stop and say which), anything under `src/identity/`.

## Done when

1. Your tests green; `tests/test_shell_*.py` and `tests/test_pmvp11_navigation_acceptance.py` still green; the
   frozen check all OK. **Do not run the whole suite.** Own test database `knovas_platform_test_pmvp30a`.
2. Screenshots of a dossier at 1440 and 390 px beside `02-dossier.png`, outside the repository (say where).
3. Card checks clean. Commits `PMVP-30a: …` with the co-author line; `feat/pmvp-30a` pushed. No pull request.

## Builder's notes

Builder session of 2026-11-21 (AGENT-claude-code). `feat/pmvp-30a` from `origin/feat/pmvp-11` (`4b7791b`), with
`origin/feat/platform-mvp` (`07078ce`) merged in once (`d58541b`).

### What was built

| File | What |
|---|---|
| `src/brain_views/__init__.py`, `src/brain_views/dossier.py` | the reading: `read_dossier(client, node_id)` → `Dossier` (name, kind, description, facts, documents); `NotFound` (unknown, not visible, malformed id, graph off in the Core), `Unavailable` (the node read failed) |
| `src/web_interface/brain_views_routes.py` | the blueprint: `GET /dossier/<node_id>`, tabs Überblick and Dokumente (`?tab=dokumente`); the switch; the grant; every href built with `url_for` |
| `templates/dossier.html`, `static/css/brain_views.css` | the page, from the parts (`page`, `page_head`, `tabs`, `section`, `rowline`, `card`, `tag`, `link`); `bv-` classes, tokens only |
| `app.py` | the wiring: one import and one `register_blueprint` call, after the settings resolver exists |
| `platform_settings.py` | one registry block: `brain_views.enabled`, choice `aus`/`an`, installation, fallback `aus`, pinned by `BRAIN_VIEWS_ENABLED` |
| `config/config.yaml` | **outside the order's list**: `brain_views.enabled: "${BRAIN_VIEWS_ENABLED:-aus}"`. `test_platform_settings.py::TestRegistry::test_each_settings_environment_variable_is_the_one_config_yaml_reads` requires a `${VAR:-…}` line for every non-secret registry key; without it that test fails. PMVP-25's assistant keys will need the same |
| `tests/test_brain_views_dossier_page.py` (26), `tests/test_brain_views_dossier_facts.py` (26) | 52 tests; the Core's per-thing answers are a `Brain` fixture on the shared `FakeGraphApi` (monkeypatched in the test file, `conftest.py` unchanged) |
| card, its page, `boundaries.yaml` | source paths, R-2, two operations, lines PRE-01, POST-02, POST-03 with evidence, three lines on the route; POST-01 stays PMVP-34's review entry |

### Where a fact's source is (item 5)

Read from KnowledgeBase `origin/master` with `git show`: `knovas-software/DB/init.sql` (`kg_node_fact`),
`app/src/services/brain_store/graph_repository.py` (`_fact`), `app/src/api/graph_api.py` (`node_detail`, `node_facts`,
`_serialize` passes the row whole). A fact carries **`provenance_pointer`** (the document), **`provenance_chunk_id`**
(the passage: a SentenceChunk id) and **`provenance_status`** (`ok`, `chunk_missing`, `pointer_detached`). Besides:
`kg_fact_evidence` rows (`GET /facts/<id>/evidence`: `chunk_id`, `document_id`, `stance`, `source_standing`), no text.

**No per-thing route returns a passage's text or its page**: not the node, the facts, the evidence or the Core's
`/nodes/<id>/dossier`; `/secured/document/<uuid>` gives a document's title and path; `/secured/fulfill-evidence` is a
scoped retrieval behind a switch, not a read by id. The client has no such method either. So the dossier shows, on
click, **the document** (name, folder, a link that opens it), **not the passage text and not the page**, and opens the
document at its start. I did not guess the passage (see "Stopped on").

### What I decided, and why (the verifier should look here first)

1. **One node read.** `graph_node` (`GET /nodes/<id>`) returns the node, its active assignments (narrowed by the Core
   with `filter_rows_by_pointer`) and its facts (narrowed with `_visible_facts`), so `graph_facts` is not called. The
   kind and the field names come from one `graph_get_many` round of two per-thing reads (`/node-types/<id>`,
   `/node-types/<id>/schema`); if either fails the page has no eyebrow and a fact falls back to its own label or
   «Angabe». Never `graph_export`, `graph_edges`, `graph_nodes`, `graph_node_types` (the test fake raises on them).
2. **As the person.** The blueprint gets `lambda: api_client`, the web app's one client, whose `_RequestScopedBroker`
   signs every call as the person signed in on the request, as the search does; a test records the broker's user at
   each call. Without accounts (`identity_gate` None) the route answers 404 and asks nothing.
3. **When a fact counts as sourced.** All three fields say so (pointer and chunk id set, status `ok` or absent) **and**
   its pointer is one of the node's documents as the Core listed them for this person. Reason: the Core shows a fact
   when *any* of its evidence chunks is readable (`graph_access.visible_fact_ids`), so a visible fact's
   `provenance_pointer` can name a document the person may not read. Such a fact gets «ohne Beleg», and its pointer is
   neither shown nor granted (`test_a_fact_whose_source_the_person_was_not_handed_…`). The note under the facts says
   what the mark means: no passage *you can open here*.
4. **Opening a document.** The file routes serve only documents the person's own retrieval granted
   (`document_grants`). The dossier grants the node's documents (the Core's listing for this person) through
   `_grant_for_current_user`, handed in as `grant` exactly as `doc_fields_routes.attach` gets it for the document-fields
   listing; nothing else is granted, and only to that person (tested). The link: `url_for('preview_document', …)` for a
   PDF (the browser's viewer, as a PDF hit opens), `url_for('download_document', …)` for any other file. There is no
   addressable document view: the search page's preview is a dialog in `app.js` fed by a search hit (page, sentence
   numbers), which the dossier does not have.
5. **«Verbunden» is left out.** `GET /nodes/<id>` gives no edges and no neighbour counts; counts per kind would need
   `graph_neighbors` plus each neighbour's type, which is PMVP-30b's.
6. **Marks.** Only «ohne Beleg» (the order's). B-04's other marks (derived or uncertain facts, the reference's
   «abgeleitet») are not built; the Core's `/nodes/<id>/dossier` carries a trust per fact if a later item wants them.
   Rejected facts (`curation_status: rejected`) are left out; facts follow the type's `sort_order`, then label.
7. **A field of kind `entity_ref`** links to that node's dossier (`url_for`), with the stored value (an id) as text; the
   name would need one more read per reference (W-08/PMVP-33).
8. **Not on the page** (the reference has them, the order does not ask): «Dazu fragen» (assistant), «offene Punkte»,
   «Läuft gerade», «Zuletzt», the related-kind tabs.
9. **The merge** (`d58541b`): `style.css` now imports tokens, `parts.css`, `shell.css`; the shell card, its page and
   `boundaries.yaml` name both items' files (`card render` regenerated the page).

### Stopped on (decisions for the coordinator)

- **The passage.** POST-02 holds for the document, not the passage: no per-thing route gives a passage's text or page
  (above). Options: a Core route that reads one chunk by id as the person (KnowledgeBase change; the client then
  gains one method); PMVP-31's evidence trail; or a local text search for the value, as Cortex does
  (`ontology_graph._evidence`, `find_mention`), which is a guess at the passage and was not done. The card's review
  entry on P-2 says so.
- **Two shell tests cannot both pass after the merge, and neither is in my list:**
  `test_shell_nav_styles.py::test_style_css_loads_the_tokens_first_and_then_the_navigation` pins the `@import` list
  to `[tokens, shell.css]`, `test_shell_parts_css.py::test_the_base_stylesheet_loads_the_parts_right_after_the_tokens`
  to `[tokens, parts.css]`. Each fails only on the list; the order holds for both. One of them (or both) needs
  `[tokens, parts.css, shell.css]`. The same happens when PMVP-11 is merged into `feat/platform-mvp`.
- **FU-34's hardening of `_parts.html`** was planned "with PMVP-30a", but this order forbids touching the parts; not
  done. The dossier passes the parts only `url_for` addresses (tested).
- **`config.yaml`**: one line outside the list (above).

### Checks

- **Tests** (own database `knovas_platform_test_pmvp30a`, one process): the two new files, 52 passed. With
  `tests/test_shell_*.py`, `tests/test_pmvp11_navigation_acceptance.py`, `tests/test_pmvp10_brand_acceptance.py` and
  `tests/test_platform_settings.py`: 581 tests, 579 passed, 2 failed (the two pins above, failing since the merge,
  before any dossier code). The whole suite was not run.
- **Frozen check**: all five OK.
- **Card**: `card check` 15 cards valid, 0 errors, 0 warnings; `card render --check` 15 cards, 0 not current;
  `map-check --scope touched --settings docs/components/map-check.yaml`, base `origin/feat/platform-mvp`: problems 0,
  declared 0. In scope: `need_added` on `http.GET.dossier._node_id/N-1` (a new operation), `no_contract` on
  `create_brain_views_blueprint` (review entry, as the shell's factory), and the earlier `over_budget` /
  `no_contract` of L2-PLATFORM-ADMIN and the shell; `app.py` and `platform_settings.py` touched without a card.
  Run from `KnowledgeBase-worktrees/pmvp-01/scripts`, read only; nothing reached a tenant.
- **In the browser** (headless Chrome, the real app with a fake Core, 1440 and 390 px): no failed request and none
  outside the installation; nothing scrolls sideways; lowest text contrast 4.76 to 1; a 2 px focus ring on tabs,
  links and the fact lines (links inside a closed fact are not focusable until it opens).
- **Screenshots**, outside the repository, in
  `C:\Users\siran\AppData\Local\Temp\claude\1838582cff9d51e1\af9716ae-0421-42ac-9fac-6acb192cc6e6\scratchpad\pmvp30a\shots\`:
  `dossier-1440.png`, `dossier-390.png` (Überblick, the first fact opened), `dokumente-1440.png`, `dokumente-390.png`,
  `compare-1440.png`, `compare-390.png` (beside `02-dossier.png`), `facts.json`. The tools (`serve.py`, `shoot.py`) are
  one folder up; the invented firm is the reference screen's.

### Rework 1 (2026-11-21, after the verifier's FAIL on `55a926f`)

`origin/feat/platform-mvp` merged once more (`4f8cbb5`: the navigation, the fixed `@import` tests `fe91aba`, FU-38,
CR-2; one conflict in `boundaries.yaml`, wording). Code `821ccfb`, card `1afbf53`.

1. **Experiment pointers.** `split=experiments_search.split` is handed in from `app.py` (as to the document-fields
   listing). `read_dossier`/`node_documents` take a `listable` test built on it: an `experiments/<domain>/<KEY>`
   pointer is not listed, is no fact's source (the fact gets «ohne Beleg») and is never granted or sent to
   `document_readable`. `test_an_experiment_document_is_not_listed_not_a_source_and_never_handed_over` is the
   verifier's case (listed, granted) read from the grant store itself.
2. **Grant on click.** Showing the dossier grants nothing. Every document link (list and facts) goes to
   `GET /dossier/<node_id>/dokument/<n>` (1 = the first by name). It reads `GET /nodes/<id>` again as the person,
   takes the n-th listable document, asks `document_readable(pointer)` as the person, and only then grants that one
   document (`_grant_for_current_user`, same row shape as search) and redirects. Not readable, out of range, a hidden
   or missing node, any failure: Flask's one 404, nothing granted. Tests: a stale assignment is not granted; a click
   grants exactly one document to that person; another person's click grants the first person nothing.
3. **Where it opens.** The row goes through search's own `_enhance_listing_rows` (open hints, OneDrive links), then
   the order of a hit (`app.js` `_previewActionsHtml`, `openPreview`): a OneDrive/SharePoint address →
   `document_external_open`; a PDF while `open.pdf_inline_in_browser` → `preview_document`; else, while
   `_download_open_enabled()` → `download_document`; else a plain message (409, «Nur auf Ihrem Rechner»), nothing
   granted: a hit's «Öffnen» (companion, file path on the person's PC) is browser code the dossier cannot run.
   Tested with PDFs inline on and off (an app built with `open.pdf_inline_in_browser: false`), a non-PDF, downloads off.
4. **`node_type_id`** goes into a path only as a uuid (`valid_type_id`); anything else: no type reads, no eyebrow
   (a core type's CURIE such as `euo:Person` included).
5. **The card.** `*/P-2` (POST-02, a passage opens) has no evidence any more; its review entry points at FU-38 and
   CR-2. What holds is on the dossier route: P-2 (showing hands over nothing; the list without experiments), P-4 (a
   fact without a mark opens its source document). The click route has its operation, N-1, P-1 (checked per click,
   one document, one person, same 404) and P-2 (opens where a hit does).

**Still open.** `document_readable` in `knovas_client.py` logs the pointer (`%r`) when the call itself fails (a
transport or TLS error): customer text in a log line, in a file this order forbids. The dossier logs only types.
The fix is one line in the client (log the exception type, not the pointer), with FU-09/FU-31.

**Checks (rework).** Own database `knovas_platform_test_pmvp30a`: `tests/test_brain_views_dossier_*.py` 70 passed;
`tests/test_shell_*.py` and `tests/test_pmvp11_navigation_acceptance.py` 441 passed; `tests/test_doc_fields_*.py`
281 passed; `tests/test_platform_settings.py` 55 passed: 847, 0 failed. Frozen check all OK. `card check` 15 valid;
`card render --check` 0 not current; `map-check --scope touched`, base `origin/feat/platform-mvp`: problems 0;
in scope `need_added` on the two routes' N-1 (new operations) and `no_contract` on the factory (review entry).
