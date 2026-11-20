# Work order PMVP-10: the brand tokens in the Platform's styles, and a styleguide page

| | |
|---|---|
| Item | PMVP-10 (Jira SS-729), plan `FEAT-2026-11-19-platform-mvp` requirement F-7, § 5.1 (five design rules, the six reference screens) |
| Bet | `BET-2026-11-platform-mvp` (approved 2026-11-19) |
| Class | routine |
| Branch / worktree | `feat/pmvp-10` from `feat/platform-mvp`, worktree `.worktrees/pmvp-10` |
| Frozen tests | `P/tests/test_pmvp10_brand_acceptance.py`, blob id in `docs/platform-mvp/frozen-tests.txt` (the check command is in that file). `P` = `KnovasPlatform/components/docbridge_integration` |
| Card | `docs/components/L2-platform-shell.card.yaml` (cards first, DEC-0068: the card changes in the same work) |

## Goal

Every existing page of the Platform takes the Knovas brand's colours and type without a rule being rewritten, and
one page shows the tokens, so that the items after this one (PMVP-11 navigation, PMVP-12 page parts, the Brain
views, Admin) build on one look.

## The visual contract

`docs/platform-mvp/reference-screens/` (the founder's mockup package of 17 November, committed by the coordinator;
don't edit it): six screens as HTML and as PNG in `out/`, `app.css` for their layout, and
`assets/knovas-tokens.css`, which is KnowledgeBase's `brand/knovas-brand/css/knovas-tokens.css` unchanged. Open the
six screens before you start. This item builds none of them; it makes their colours and type the Platform's.

## What to build (the frozen tests pin the outcome)

1. **The tokens.** `P/src/web_interface/static/css/knovas-tokens.css`, the brand's file byte for byte (copy the
   reference). Every page that loads `css/style.css` gets it first: either one `@import` at the top of `style.css`,
   or a `<link>` before `style.css` in every template that has one. Choose one and say which in your notes.
2. **The existing names keep working.** `style.css` declares the Platform's variables in its `:root` block
   (`--primary-color`, `--bg-color`, `--text-primary` and so on); the other stylesheets (`admin.css`, `views.css`,
   `ontology.css`, `experiments.css`, `custom_views.css`, `builder/`) use them. Keep every name; give it the
   brand's value through `var(--k-…)`. The frozen file lists which token ten of them take (`BRAND_OF`). For the
   rest (`--text-secondary`, `--accent`, `--callout`, `--highlight`, radii, paddings) choose the brand token the
   reference screens use for that job; `--text-secondary` and every text colour keep a contrast of 4.5 to 1 on the
   three backgrounds. The two `-rgb` triples follow their colours. No rule below the `:root` block is rewritten
   here.
3. **Type.** Body text and headings in the brand's sans (`--k-font-sans`); the mono face stays for codes, ids and
   file names where the pages use it today through its own class or variable. Add the font files the brand uses
   and the Platform lacks (IBM Plex Sans 700, IBM Plex Mono 500) from `reference-screens/assets/fonts/` (licence
   `static/fonts/OFL.txt` covers them) with their `@font-face` rules. Nothing is loaded from outside the
   installation.
4. **The styleguide page** `GET /styleguide`, for administrators only (signed out: to sign-in; a member: 403; the
   same gate the Admin pages use, find it in `web_interface/admin.py`). A route file of its own
   (`P/src/web_interface/shell_routes.py`, a blueprint) with one wiring line where `app.py` registers the others;
   template `styleguide.html`. It shows, each with its token name and value: the brand and status colours as
   swatches, the text colours on the three backgrounds, the type scale in both faces, spacing, radii, shadows; and
   the plain parts that exist today (button, text field, link) as they now look. The parts of PMVP-12 (card, row
   line, tabs, status dot, reference chip) are **not** built here; leave one empty section named for them. The
   values shown must not drift from the tokens file: read them from it, or add a test of yours that fails when a
   shown token is not in it. Interface text in German, as on the other pages.
5. **The card.** `L2-platform-shell.card.yaml`: the new files in `source_paths`, the operation `GET /styleguide`
   with its line (administrators only; the server refuses everybody else) and the frozen test as its evidence;
   `boundaries.yaml`; the rendered page. `card check` 0 errors, `card render` with no stale page,
   `map-check --scope touched` with no problem about the card (commands run from KnowledgeBase `scripts/`,
   `py -3 -m brain_runner …`, with `--root`/`--repo` at your worktree; see the "Checks" of
   `work-orders/PMVP-01.md`). Nothing reaches a tenant.

## Rules

1. **Existing pages keep working.** No template's structure changes (at most the one `<link>` line), no script
   changes, no route other than the new one. Addresses stay.
2. **What clashes is listed, not fixed.** After the change, look at the sign-in page, the search page, one Admin
   page and one Builder page at 1440 and 390 pixels wide. Where a rule's own literal colour now clashes with the
   brand (say, a hard-coded blue beside the new one), write the file and line in your notes for PMVP-12 and
   PMVP-40. Count the literal colours per stylesheet in your notes.
3. **Evidence.** Screenshots of those four pages and of `/styleguide`, before and after, at both widths, saved
   **outside** the repository (say where in your notes). Use Playwright the way `tests/test_builder_screens.py`
   does if it is installed here; if not, say so and describe what you looked at instead. Install nothing
   system-wide.
4. No new dependency. No customer text in a log line (founder, 2026-11-19): the new route logs nothing but what
   the framework logs.

## Files you may change

- new: `P/src/web_interface/static/css/knovas-tokens.css`, font files under `P/src/web_interface/static/fonts/`,
  `P/src/web_interface/shell_routes.py`, `P/src/web_interface/templates/styleguide.html`,
  `P/tests/test_shell_*.py` (your own tests)
- `P/src/web_interface/static/css/style.css`: the `:root` block, the `@font-face` rules, the `@import`
- `P/src/web_interface/templates/*.html`: only the `<link>` line for the tokens, if you choose that way
- `P/src/web_interface/app.py`: only the wiring of the new blueprint
- `docs/components/L2-platform-shell.card.yaml`, its rendered page, `docs/components/boundaries.yaml`
- `docs/platform-mvp/work-orders/PMVP-10.md`: only the "Builder's notes" section at the end

## Files you must not change

- the frozen test files; `P/tests/conftest.py`; `docs/platform-mvp/reference-screens/**`; the rules of any
  stylesheet below `style.css`'s `:root` block, and every other stylesheet; scripts under `static/js/`;
  `requirements.txt`; migrations; anything under `P/src/identity/`

If the work needs one of these, stop and write the reason under "Builder's notes" instead of changing it.

## Done when

1. `cd P && py -3 -m pytest tests/test_pmvp10_brand_acceptance.py -q -p no:cacheprovider` is green and not skipped
   (three of its tests need the test database: `PLATFORM_DB_TEST_DSN=postgresql://platform:testpw@127.0.0.1:55433/knovas_platform_test_pmvp10`,
   `PLATFORM_DB_REQUIRED=true`; create it with `docker exec pmvp-platform-test-db createdb -U platform …`, drop it
   afterwards); the frozen files unchanged (every line of the check in `frozen-tests.txt` says OK).
2. Your own tests green. The whole Platform suite
   (`py -3 -m pytest -q -p no:cacheprovider --continue-on-collection-errors`, same database): the same failing ids
   as `feat/platform-mvp`, or each new one explained. `test_pmvp03_harness_acceptance.py` and the two PMVP-20
   frozen files fail at collection on both (not built or not integrated yet); that is expected.
3. The card checks of point 5.
4. Commits `PMVP-10: …` ending with the co-author line; `feat/pmvp-10` pushed. No pull request: the coordinator
   integrates after a verifier session passes it.

## Changes to this order (coordinator, 2026-11-20, after the first build)

1. **Mono for codes.** The pages set codes, ids, file names and diffs in `var(--font-heading)`, which was the mono
   face; with headings in the brand's sans they turned sans too. That was a gap in this order. In the rules that
   set such text (the builder's notes list 27, in `style.css`, `admin.css`, `views.css`, `experiments.css`,
   `custom_views.css`), `var(--font-heading)` becomes `var(--font-mono)`; nothing else in those rules changes.
   Rules that set real headings keep `--font-heading`. This is the one exception to "no rule is rewritten" and to
   "every other stylesheet" under "Files you must not change".
2. **The navigation on the styleguide page is the same as on every page**, with its Feedback entry. The frozen
   test's last check was too strict (it rejected a plain link to another site); the coordinator changed it, the new
   blob id is in `frozen-tests.txt`.

## Builder's notes

(the builder writes here: what was decided, what is open, what the verifier should look at)
