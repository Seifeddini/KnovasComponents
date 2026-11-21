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

(the builder writes here)
