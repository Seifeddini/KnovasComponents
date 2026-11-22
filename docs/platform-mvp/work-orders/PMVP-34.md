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

(the builder writes here: what was decided, what is open, what the verifier should look at)
