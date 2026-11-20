# Work order PMVP-11: one navigation, three areas

| | |
|---|---|
| Item | PMVP-11 (Jira SS-730), plan `FEAT-2026-11-19-platform-mvp` § 5.1 rule 3, contract L2-PLATFORM-SHELL POST-01 |
| Bet | `BET-2026-11-platform-mvp` (approved 2026-11-19) |
| Class | sensitive (Siran approves): who sees a link to which area |
| Founder decision | 2026-11-20, on the coordinator's question: **"Hide, place later"**. The Workspace menu shows only the search. Cortex, Experimente, Eigene Ansichten, Einstellungen and Feedback get no entry until a later item places them; their addresses answer as before |
| Branch / worktree | `feat/pmvp-11` from **`origin/feat/pmvp-10`** (the look, in verification), worktree `.worktrees/pmvp-11`; merge `origin/feat/platform-mvp` in once for this order and the frozen file |
| Frozen tests | `P/tests/test_pmvp11_navigation_acceptance.py`, blob id in `docs/platform-mvp/frozen-tests.txt`. `P` = `KnovasPlatform/components/docbridge_integration` |
| Cards | `docs/components/L2-platform-shell.card.yaml` (the navigation and SHELL POST-01, evidence = the frozen file); `L2-platform-admin.card.yaml` if its files change |

## Goal

The navigation shows only the area a person is in (reference screens: `docs/platform-mvp/reference-screens/shell.js`
and `app.css` lines 8 to 17 show the three menus). This is the first of the new screens a person sees.

## What to build (the frozen tests pin it)

1. **One element, three areas.** `templates/_sidebar.html` stays the one source: `<aside class="app-sidebar"
   data-area="work|admin|builder">`, its `<nav>` holding the entries of the area the page belongs to. A page under
   `/admin` belongs to `admin`, everything else that uses the sidebar to `work`.
2. **Workspace (`work`).** The logo, the one entry "Suche" (marked current on the search page only), and at the
   foot the links "Verwaltung" and "Builder", each shown only to a person who may open a page there. "Verwaltung"
   leads to the first Admin page the person may open (an approver: the approvals). The block with the person at the
   bottom stays as it is (it leads to the settings, which is how a person still reaches them). No entry for Cortex,
   Experimente, Eigene Ansichten, Einstellungen, Feedback.
3. **Verwaltung (`admin`).** "← Arbeitsbereich" (to the search), the heading "Verwaltung", then the Admin pages
   the person may open, under today's names and with today's conditions (the ones `_admin_tabs.html` uses: roles,
   the Dokumentfelder capability, ingestion switched on), the current one marked. No link to the Builder here. The
   row of tabs inside the Admin pages goes away in the same change: one navigation.
4. **Builder.** Its pages keep their own menu in this item (the Builder items rework it). Make sure a way back to
   the Workspace is there; add it if it is missing.
5. **The link is not the check.** Decide "may open" with the checks the routes themselves use: one function per
   area, no second list of roles. No route's gate changes. The frozen file asserts that the pages answer 403 and
   302 exactly as today.
6. **The look**, from the reference screens with the brand tokens: a quiet sunken sidebar, group headings in small
   capitals, the current entry on a white tile, the back link, the area links at the foot above the person. New
   rules go into a stylesheet of their own, `static/css/shell.css`, loaded the way `style.css` loads the tokens;
   the old sidebar rules in `style.css` that no longer apply are removed. At 390 pixels the menu stays usable and
   the page does not scroll sideways because of it.
7. **The cards**, in the same change; `card check`, `card render`, `map-check --scope touched` as in
   `work-orders/PMVP-01.md`.

## Rules

1. Addresses and gates stay. `refused` today, for your tests: signed out = 302 to `/login?next=…`; wrong role =
   403 (follow-up FU-26).
2. No customer text in a log line. The function that decides the Verwaltung link logs a person's e-mail address on
   `feat/platform-mvp` (`_console_url`); on your branch log the user id instead, or nothing.
3. Existing tests that assert the old menu or the row of tabs change with it: change only those assertions, and
   list each changed test with its reason in your notes. A test that fails for another reason is a finding, not
   something to adapt.
4. No new dependency; no script beyond what the sidebar has today.

## Files you may change

- `P/src/web_interface/templates/_sidebar.html`, `_admin_tabs.html` (remove) and the Admin templates that include
  it; a Builder template only for the way back (say which)
- `P/src/web_interface/app.py`: only what feeds the sidebar (the area, the two area links)
- new `P/src/web_interface/static/css/shell.css`; in `style.css` the one line that loads it and the removal of
  sidebar rules that no longer apply
- `P/tests/test_shell_nav_*.py` (your own tests); existing tests under rule 3
- the two cards, their pages, `docs/components/boundaries.yaml`
- `docs/platform-mvp/work-orders/PMVP-11.md`: only the "Builder's notes" section

## Files you must not change

- the frozen test files; `P/tests/conftest.py`; any route's gate or decorator; `P/src/identity/**`; the Admin
  blueprint's logic; other stylesheets; `docs/platform-mvp/reference-screens/**`

## Done when

1. `cd P && py -3 -m pytest tests/test_pmvp11_navigation_acceptance.py -q -p no:cacheprovider` green and not
   skipped (test database of your own, `knovas_platform_test_pmvp11`); the frozen check in `frozen-tests.txt` all OK.
2. Your tests green. **Do not run the whole Platform suite** (several sessions share the database server). Run
   every test file that renders a page with the sidebar or the tabs: search the tests for `app-sidebar`, `app-nav`,
   `admin-tabs`, `_admin_tabs`, `aria-current`, `Verwaltung`, `console_url`, and run those files plus
   `tests/test_web_*.py`, `tests/test_shell_*.py` and `tests/test_pmvp10_brand_acceptance.py`. Say which files you
   ran. The coordinator runs the whole suite when it integrates.
3. Screenshots outside the repository (say where): the search page as a member and as an administrator, an Admin
   page as an administrator and as an approver, at 1440 and 390 pixels, beside the reference screens.
4. The card checks. Commits `PMVP-11: …` ending with the co-author line; `feat/pmvp-11` pushed. No pull request.

## Builder's notes

(the builder writes here: what was decided, what is open, what the verifier should look at)
