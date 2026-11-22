# Work order PMVP-34: the 25-node limit as one shared function, with its test

**Decided by the coordinator on 2026-11-22** (see "Decisions"); its acceptance test is frozen.

| | |
|---|---|
| Item | PMVP-34 (Jira: filed by the coordinator), plan `FEAT-2026-11-19-platform-mvp` § 4 **B-01** ("No view draws more than 25 nodes. Everything beyond is a count that opens a list"); § 11 ("Nodes drawn in any view: never more than 25 — automatic test on PMVP-34"); spec item SPEC-platform-mvp-09; contract L2-PLATFORM-BRAIN-VIEWS **POST-01** (today a `review` entry on the card: "PMVP-34 writes the 25-node limit as one shared function with its test") |
| Follow-ups | none name this item. FU-19 (the existing modelling views read the whole tenant and have no limit) stays with PMVP-32's decision; FU-34 applies (hrefs from `url_for` only) |
| Class | **routine** (Jannik or Siran): a pure function, two attributes in a template, and a test. No rights, no storage, no Core call |
| Depends on | PMVP-30a (the dossier, integrated `405022da`) |
| Branch / worktree | `feat/pmvp-34` from `origin/feat/platform-mvp` (`74478d1a` or later), worktree `.worktrees/pmvp-34`. PMVP-30b builds on this branch (see the coordinator's grouping) |
| Visual contract | none of its own. `reference-screens/04-umgebung.html` shows what a drawn view looks like: one circle per kind with a count, a card listing what is in it |
| Card | `docs/components/L2-platform-brain-views.card.yaml` (its page, `boundaries.yaml`) |
| Core requests | none. CR-1 and CR-2 change nothing here |
| Frozen tests | `P/tests/test_pmvp34_view_limit_acceptance.py`, blob `7cb498315670e30a4369e945515c05a0deab98b0` in `docs/platform-mvp/frozen-tests.txt` (17 tests). Proven by the drafting session against a throwaway reference: 17 pass; on today's branch 15 fail (2 pass with no view drawing anything); 12 of 12 mutants killed. The routine class stays; the test is frozen because § 11 names it as the measure |

`P` = `KnovasPlatform/components/docbridge_integration`.

## Goal

Every Brain view a member opens draws at most 25 things, and what does not fit is a count that opens a list. The
limit lives in one function that every view calls, and one test opens every view page with a very large
neighbourhood and counts what it draws. A view added later is checked without anyone editing the test.

## What to build

1. **`P/src/brain_views/limit.py`** (new):
   - `MAX_DRAWN = 25`.
   - `bounded(items, *, limit=MAX_DRAWN, reserved=0) -> Bounded`, a frozen result with `shown` (a tuple, in the
     order given), `rest` (how many were left out) and `total`. `reserved` counts what the view draws anyway (its
     starting point, for example), so `len(shown) <= limit - reserved`. A `limit` above `MAX_DRAWN`, or a negative
     `limit` or `reserved`, raises `ValueError`: a view may draw fewer, never more.
   - `more_text(rest) -> str`: «1 weiterer Eintrag», «115 weitere»; `""` for 0 (SPEC-09: «115 weitere»).
   - `paged(items, page, *, size=50) -> Page` for the lists the counts open (`items`, `page`, `pages`, `total`; a page
     out of range gives the last page, never an error). PMVP-30b and PMVP-32 use it.
2. **What counts as drawn** (decision D1), as a mark in the mark-up that the test counts:
   - `data-bv-node` on each thing a view **draws**: its starting point, a step of a process line, a circle, a card
     that stands for a thing;
   - `data-bv-row` on each **row of a list**. A list is where "the rest" goes; it is paged (50 a page), sorted and
     filtered (B-03), and its rows are not drawn nodes;
   - `data-bv-more="<n>"` on the link that opens the rest, next to the count.

   Put the marks into `dossier.html`: the page head of the thing (`data-bv-node`), and the rows of «Angaben» and
   «Dokumente» (`data-bv-row`). Change nothing else on the page.
3. **The test is frozen**: `P/tests/test_pmvp34_view_limit_acceptance.py` pins all of this. Make it pass; never edit
   it. It checks:
   - Unit tests of `bounded`, `more_text` and `paged`: the edges (0, 1, 24, 25, 26, 140), `reserved`, the errors.
   - **The page test.** Build the app with a fake Core holding one thing with a very large neighbourhood: 140
     neighbours of six kinds, 140 documents, 140 facts, 40 of them of kind `entity_ref`. Then start from every GET
     route of the `brain_views` blueprint that takes a `<node_id>` (read from `app.url_map`, so a new view route is
     checked without editing the test). Put that thing's id in, and follow, to a depth of two, every link on the page
     that goes to another `brain_views` GET route (tabs, lists, «weitere»). On every page that answers 200 with HTML:
     at most 25 `data-bv-node`; and wherever a `data-bv-more` stands, it holds a positive count and its link
     answers 200 (that the count equals the total minus what is drawn is each view's own test). A route that redirects (a document opening) is skipped. The dossier must be
     among the pages checked, or the test fails.
   - The fake raises on every whole-tenant call (`graph_export`, `graph_edges` without a node, `graph_nodes`,
     `graph_node_types`), as PMVP-30a's tests do (POST-03).
4. **The card**: the line `L2-PLATFORM-BRAIN-VIEWS/*/P-1` (POST-01) gets this test as its evidence; its `review`
   entry goes; `limit.py` is in the source paths (the folder already is); `boundaries.yaml`.

## Rules

1. `limit.py` imports nothing of Flask, the app or the client; it is ASCII-only, with German words as `\u` escapes.
2. Shown in the order given. The view decides what comes first (for example the main path, or the newest).
3. No view's behaviour changes in this item, except for the marks on `dossier.html`.
4. The test reads the marks only, never the pages' wording, so a later builder can reword a page freely.

## Decisions (coordinator, 2026-11-22)

- **D1, drawn versus listed** (decided: lists are exempt from the 25 cap). "Drawn" are the things a view shows as nodes. The rows of the list that a count opens
  are not drawn. Plan B-01 says "everything beyond is a count that opens a list", and B-03 says sets are lists that
  can be sorted and filtered. Lists are paged at 50 rows. The stricter reading (every page at most 25 things,
  lists included) would page every list at 25 rows; say so if you want that.
- **D2, the dossier's facts are rows.** A fact of kind `entity_ref` links to another thing's dossier, but it stands
  in the list «Angaben», so it is a row, not a drawn node.
- **D3, frozen before the build.** § 11 names this test as the measure for "never more than 25". Because it finds
  the routes by itself, later views (PMVP-30b, PMVP-32) do not edit it.

## Your tests

The frozen file, plus your own unit tests if you want more (`P/tests/test_brain_views_limit_*.py`). Use the existing fakes (`FakeGraphApi`, `workbench_app`, `member_client` in `tests/conftest.py`)
and extend a fake only in your own test file.

## Files you may change

`P/src/brain_views/limit.py` (new); `P/src/web_interface/templates/dossier.html` (the three marks only);
`P/tests/test_brain_views_limit_*.py` (new, your own); the card, its page, `docs/components/boundaries.yaml`;
`docs/platform-mvp/management/brain-views.md` and its row in `management/README.md`; this order's "Builder's notes".

## Files you must not change

The frozen files (`test_pmvp34_view_limit_acceptance.py` among them), `P/tests/conftest.py`, `P/src/brain_views/dossier.py`, `brain_views_routes.py`, the parts,
`_sidebar.html`, `app.py`, `knovas_client.py`, everything under `P/src/assistant/` and `P/src/identity/`.

## Done when

1. The frozen `tests/test_pmvp34_view_limit_acceptance.py` green and unchanged (its blob id); your tests,
   `tests/test_brain_views_*.py`, `tests/test_shell_*.py` and
   `tests/test_pmvp11_navigation_acceptance.py` green; the frozen check all OK. **Do not run the whole suite.** Own
   test database `knovas_platform_test_pmvp34`.
2. A deliberate break shows the test works: a view that draws 26 marks, or a `data-bv-more` with a wrong number,
   fails it. Say in your notes what you broke and that you put it back.
3. Card checks clean.
4. `brain-views.md`: «Never more than 25 things drawn» moved from the limits to what holds, with the test named.
5. Commits `PMVP-34: …`, ending with the co-author line; `feat/pmvp-34` pushed. No pull request.

## Builder's notes

Builder session of 2026-11-22 (builder B, AGENT-claude-code). `feat/pmvp-34` from `origin/feat/platform-mvp`
(`5cbefb66`); `origin/feat/platform-mvp` (`cee2a4a1`, the RB-6/RB-7 re-pin) merged in once, no conflict.

### What was built

| File | What |
|---|---|
| `src/brain_views/limit.py` (new) | `MAX_DRAWN = 25`, `PAGE_SIZE = 50`; `bounded` → `Bounded(shown, rest, total)`; `more_text`; `paged` → `Page(items, page, pages, total, size)` with `previous`, `next`, `first`, `last`; `page_number` (a `?seite=` value to a page number). No Flask, app or client import; ASCII only |
| `templates/dossier.html` | the three marks only (below) |
| `tests/test_brain_views_limit_unit.py` (new, 28) | what the frozen test leaves open: `reserved` above the limit, non-numbers refused, `page_number`, a page's neighbours and places, the module's imports and ASCII |
| card, its page, `boundaries.yaml` | `*/P-1` (POST-01) has the frozen test as evidence, its review entry is gone; a `hides` line for the limit and the marks; `boundaries.yaml`'s reason names `limit.py` (the folder was already a source path) |

### What I decided, and why (the verifier should look here first)

1. **Where the marks sit.** PMVP-30a's tests pin `<details class="bv-fact" data-fact="…">` and
   `<div class="bv-fact" data-fact="…">` as exact strings, and the parts (`page_head`, `rowline`) take no attribute and
   may not be changed. So: `data-bv-node` on a plain `<div>` around `parts.page_head(…)` (the thing's head; inside
   the column's flex the wrapper is the item, and the head looks the same: `shots34/compare-dossier-1440.png`);
   `data-bv-row` on `<div class="bv-fact-line">` (one per fact, in both branches) and on `<div class="bv-doc">` (one
   per document row). One mark per row, no existing test changed. The message page (no dossier) carries no mark.
2. **`reserved` above `limit`** raises `ValueError` (the view would already draw more than its limit); `reserved ==
   limit` gives nothing shown and everything in `rest`. A non-integer (`2.5`, `"25"`, `True`) is refused too.
3. **Extras for PMVP-30b and PMVP-32**, which may not change `limit.py`: `page_number(value)` (anything that is not
   a plain ASCII number of at most nine digits is page 1; `paged` then clamps to the last page) and the `Page`
   properties `previous`, `next`, `first`, `last`, so the lists need no page arithmetic of their own.
4. **`more_text`** refuses a negative or non-integer count (`ValueError`) rather than printing it.

### The deliberate break (Done when 2)

Run against `dossier.html` with the frozen test, each put back afterwards (the file's diff is the three marks):

- a `data-bv-node` on each of the first 25 facts (26 with the head): `test_every_view_page_draws_at_most_25` and
  `test_the_dossier_draws_its_thing_and_lists_its_facts` fail ("draws 26 things");
- a link `data-bv-more="0"`: `test_the_rest_is_a_count_that_opens_a_list` fails ("a count that is no count");
- a link `data-bv-more="115"` to `/dossier/<id>/dokument/9999`: the same test fails ("the count's list answers 404").

### Checks

- **Tests** (own database: `knovas_platform_test_pmvpB`, as the coordinator set it for builder B, not
  `…_pmvp34`; one process): the frozen `test_pmvp34_view_limit_acceptance.py` 17 passed (15 failed before);
  `test_brain_views_limit_unit.py` 28; `test_brain_views_dossier_*.py` 70. With `tests/test_shell_*.py` and
  `tests/test_pmvp11_navigation_acceptance.py`: **558 passed, 0 failed**. The whole suite was not run.
- **Frozen check**: all eleven OK (after the merge as well).
- **Card**: `card check` valid, 0 errors, 0 warnings; `card render --check` 21 cards, 0 not current; `map-check
  --scope touched`, base `origin/feat/platform-mvp`: problems 0; in scope only the earlier `no_contract` on
  `create_brain_views_blueprint` (review entry). Run from KnowledgeBase-worktrees `pmvp-01/scripts`, read only.
- **In the browser** (headless Chrome, the real app, a fake Core with the reference screen's invented firm and 412
  documents; 1440 and 390 px): 1 drawn thing on «Überblick» and on «Dokumente»; nothing scrolls sideways; no failed
  request; lowest text contrast 4.76 to 1. Screenshots, outside the repository:
  `C:\Users\siran\AppData\Local\Temp\claude\1838582cff9d51e1\af9716ae-0421-42ac-9fac-6acb192cc6e6\scratchpad\builderB\shots34\`
  (`dossier-*.png`, `dokumente-*.png`, `compare-dossier-*.png` beside `02-dossier.png`, `facts.json`); the tools
  (`serve.py`, `shoot.py`) one folder up.

### Open

- Nothing stopped this item. The «Dokumente» tab still lists every document on one page (412 rows in the shots):
  rows are exempt from the cap (D1), and paging them is PMVP-30b's item 4.
