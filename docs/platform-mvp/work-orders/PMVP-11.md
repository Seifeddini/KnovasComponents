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

Builder session of 2026-11-20 (AGENT-claude-code), `feat/pmvp-11` from `origin/feat/pmvp-10` (`5536443`), with
`origin/feat/platform-mvp` (`173adbe`) merged in once. Commits: `82a1111` (the merge), `9f375e1` (the navigation,
first working version, pushed early), `b8b5c5a` (tests), `793f8e1` (the cards), `5750dd1` (one more test that
follows the menu), and the one that carries these notes. Nothing here is verified by me; the numbers are what I saw.

### What was built

| Part | Files |
|---|---|
| The navigation | `templates/_sidebar.html`: `<aside class="app-sidebar" data-area="work\|admin">`, one `<nav>` with the area's entries. Workspace: the logo, "Suche", at the foot "Verwaltung" and "Builder" (outside the `<nav>`), the person. Verwaltung: "← Arbeitsbereich", the name of the area, the Admin pages the person may open |
| The row of tabs | `templates/_admin_tabs.html` removed. The seven Admin templates (`admin_people`, `_documents`, `_access_groups`, `_doc_fields`, `_ingestion`, `_system`, `_approvals`) lose its include and set `admin_tab` one line above the sidebar include instead of below the heading; nothing else in them changed |
| What feeds it | `app.py`: `admin_menu` (the seven pages: key, name, route), `_gate_roles`, `_admin_pages`, `_console_url` (now the first of those pages), and two values more in `_sidebar_context`: `nav_area` and `admin_pages`. One import (`inspect`) |
| The look | new `static/css/shell.css` (brand tokens only); `style.css`: one `@import url('shell.css')` after the tokens, and the rules it replaces removed |
| Tests of the builder | `tests/test_shell_nav_partial.py` (49, no database), `tests/test_shell_nav_styles.py` (33, no database), `tests/test_shell_nav_links.py` (29) |
| The cards | `L2-platform-shell.card.yaml`, `L2-platform-admin.card.yaml`, their pages, `boundaries.yaml` |

No route, no gate, no decorator, nothing under `identity/`, no script and no other stylesheet changed. No Builder
template changed (decision 10).

### What I decided, and why (the verifier should look here first)

1. **"May open" for Verwaltung is read from the route's own gate.** The console builds its gates inside its
   blueprint factory (`admin._require_roles`), so they cannot be imported, and `admin.py` is not this item's file.
   Each gate closes over the roles it lets in. `app.py`, `_gate_roles(endpoint)` reads that set from the registered
   view (`inspect.getclosurevars(view).nonlocals['allowed']`), and `_admin_pages()` offers a page when the person
   holds one of them. No role is named for the menu, in `app.py` or in the partial (two tests of mine hold that).
   What it costs: the function knows how the gate is written (a closure variable called `allowed`). If the gate is
   written another way one day, the entry disappears (closed, not open), and
   `test_verwaltung_and_its_menu_are_exactly_the_admin_pages_the_server_opens_for_the_person` fails for the
   administrator. The clean alternative is that the console hands out a "may open" function; that needs `admin.py`.
   **This is the one thing I would ask the coordinator to confirm.**
2. **A consequence of 1 that changes who sees a link.** Before, `_console_url` gave the link to an administrator
   and to an approver. Now it follows the gates, so an `ingestion_manager` gets "Verwaltung" too, leading to
   Ingestion, the one page that role may open (the old row of tabs already showed it to such a person, who could
   reach it only by typing the address). A member, an experimenter and an experiments manager get no link, as
   before. Ingestion's routes exist whenever accounts are on (`app.py` builds the Connector client then).
3. **The Builder link** is unchanged: `_builder_nav` already asks the Builder's own check (`user_may_build`, the
   same `team.access` the Builder's gate uses). My test asks the Builder as a person with each of ten sets of roles,
   two of them with a Builder role, and compares. Not covered by a page test: a consultant who holds only a grant
   on one workspace.
4. **The area comes from the address** (`request.blueprint == 'admin'`), as the order says. The current Admin page
   comes from the page: each Admin template sets `admin_tab` before the include. It cannot come from the address,
   because a refused POST draws the page again at its own address (`/admin/people/create`); a test of mine holds
   that. A bare render without the web app (the existing template tests) falls back to "a page that names its
   Admin page is in Verwaltung".
5. **Dokumentfelder** keeps its condition: the entry is drawn only while Knovas offers document fields
   (`doc_fields_capability`, from the console's context processor). The capability is asked only for a person whose
   menu holds that entry, and never on a page of the Workspace (reading it calls Knovas).
6. **The entries that are hidden keep their switches.** `_sidebar_context` still computes `cortex_enabled`,
   `experiments_nav`, `custom_views_nav` and `feedback_url`; the partial draws none of them. I left them because
   the Cortex page's own dock reads `console_url` and `feedback_url` from the same values, an existing test counts
   the warning of `_custom_views_nav`, and a later item places the entries. Until then a person reaches Cortex,
   Experimente and Eigene Ansichten by address only; Einstellungen through the person's block at the foot.
7. **The look** follows `reference-screens/app.css` (the rules under "sidebar"): 212 pixels wide (was 240, and 184
   on the Admin pages), the sunken surface, entries at 0.84rem in `--k-gray-600`, the current one on a white tile
   with `--k-shadow-sm`, the area's name in small capitals, the person under a line. Where I left the reference:
   - the area's name takes `--k-color-text-muted`, not `--k-gray-400`: gray-400 on the sunken surface is 2.4 to 1,
     the muted colour 4.5 to 1 (the look's bar from PMVP-10);
   - an entry under the pointer gets `--k-gray-100` (the reference is a still picture and has no such state); the
     keyboard focus keeps the 2-pixel outline in the accent colour;
   - the entries have no icons (the reference has none); the old ones are gone with the old rules;
   - the person's block still shows the firm's initial and "Angemeldet" ("stays as it is"), not a person's name.
8. **Narrow windows (900 pixels and below, where `style.css` stacks the frame).** The column becomes a head of two
   rows: the logo, the way back and, at the right, the area links and the person; below, the area's entries in a
   row that scrolls in itself (the sidebar's one script, unchanged, moves the current entry into view). Measured at
   390 pixels on every page I opened: with the page's content taken out, the document is 390 wide, so the menu never
   makes the page scroll sideways. The Admin pages themselves still do (525 to 672 pixels: their tables; FU-23).
   Two things to know: the keyboard reaches the entries before the area links and the person, which stand above
   them at the right (the order of the markup is the wide layout's); and on the Admin pages the person's block is
   now shown in a narrow window (`admin.css` hides it there; `shell.css` outranks that).
9. **`shell.css` has to outrank `admin.css`.** `admin.css` restyles the old navigation on its pages (16 rules,
   `.admin-page .app-sidebar` and below, base and narrow) and is loaded later; it is not this item's file. Every
   selector of `shell.css` therefore starts at `.app-shell .app-sidebar`, and each rule sets every property
   `admin.css` sets. `test_shell_nav_styles.py` reads both files and checks it rule by rule.
   Follow-up for whoever may change `admin.css`: remove those rules and the rules of the removed tabs
   (`.admin .tabs`); then the first step of the selectors can go.
10. **The Builder.** Its base template already offers the way back: "Zur Plattform" in its own menu
    (`templates/builder/_base.html`, `platform_url`, which is the search). The frozen test passes on it, so I changed
    no Builder template. It does not say "Arbeitsbereich"; the Builder items rework that menu.
11. **`style.css`: what was removed.** The rules of `.app-sidebar`, `.app-brand`, `.app-nav`, `.app-nav-item`,
    `.app-sidebar-foot`, `.app-user…` and `.app-avatar`, and their part of the narrow-window block (`.app-shell`
    and `.app-content`, the frame, stay). Also the four `.admin .tabs` rules under the comment that names
    `_admin_tabs.html`: they are not sidebar rules in the strict sense, but they style the partial this item removes.
12. **Log lines.** `_admin_pages` keeps the three lines `_console_url` had. The hidden link is logged with the
    person's id and roles (was the e-mail address). A failure to read the person is logged by its kind
    (`type(exc).__name__`, was the exception's text, which a database error could fill with anything). A test of mine
    reads the log at DEBUG for both people's addresses and names.

### Existing tests I changed (rule 3), each with its reason

All of them asserted the old menu or the row of tabs. 24 changed (7 of them renamed), 5 removed, 1 added; nothing
else in these files changed. "Hidden" below means the founder's decision of 2026-11-20.

| File | Test | Was | Now |
|---|---|---|---|
| `test_web_admin_documents.py` | `TestConsoleShell::test_tab_strip_is_one_partial_included_by_every_console_page`, renamed `…::test_the_tab_strip_is_gone_and_every_console_page_names_itself_to_the_navigation` | the tabs partial exists and the pages include it | it is gone; the pages set `admin_tab` before the sidebar |
| | `…::test_tab_strip_names_every_tab_that_exists`, renamed `…::test_the_console_menu_names_every_page_that_exists` | read the six routes in the partial | reads them in `app.py`'s `admin_menu` |
| | `…::test_the_doc_fields_tab_is_drawn_only_while_knovas_offers_them`, renamed `…_entry_…` | rendered the tabs partial; an approver by `me` | renders the sidebar with the pages handed in; an approver's menu is Freigaben alone |
| | `…::test_every_console_page_uses_the_scoped_admin_shell` | second assertion: the heading stands before the tabs include | no tabs include |
| | `TestAppWiring::test_sidebar_link_is_gated_on_the_admin_role` | `_console_url`'s source names `'admin'` and Personen | `_admin_pages` reads the route's gate, names no role, and Personen is the first page |
| | `TestTemplatesRender::test_tab_strip_renders_and_marks_exactly_one_active_tab`, renamed `…::test_the_console_menu_renders_and_marks_exactly_one_current_page` | the tabs partial | the sidebar's area Verwaltung |
| | `…::test_people_page_still_renders_with_the_strip`, renamed `…_with_the_console_menu` | "Zugriffsgruppen" from the tabs | from the menu (pages handed in), Personen marked once |
| `test_web_admin_approvals.py` | `TestApproverOnlyReachesTheTab::test_console_url_offers_approvals_to_a_pure_approver` | `_console_url`'s source names `'approver'` and Freigaben | Freigaben is in the menu, `_console_url` takes the first page a gate lets in, no role named |
| | `…::test_tabs_hide_admin_only_pages_for_an_approver` | the tabs partial with `me` | the sidebar with Freigaben alone (same two assertions) |
| | `…::test_tabs_still_render_when_me_is_none` | every tab appears without `me` | every entry handed in appears (the menu does not read `me`) |
| | `TestTemplate::test_the_strip_knows_the_tab` | route named in the tabs partial | named in `admin_menu` |
| `test_web_admin_ingestion.py` | `test_the_strip_knows_the_tab` | as above | as above |
| | `TestTabStripVisibility::test_ingestion_manager_without_admin_sees_ingestion_not_people` | the tabs partial with `me` | the sidebar with Ingestion alone (same two assertions); the real app answers in `test_shell_nav_links.py` |
| `test_experiments_frontend.py` | `TestTemplatesRender::test_pages_include_the_sidebar_with_the_item_active`, renamed `…_and_it_shows_the_workspace` (3) | Experimente marked as current | the Workspace menu, no entry, nothing marked (hidden) |
| | `TestSidebar::test_item_with_experiments_nav`, `…_is_active_on_the_module_pages`, `…_follows_cortex_and_precedes_settings`, `…_stays_when_cortex_is_off`, `test_icon_is_decorative` | described the entry | removed; `test_no_item_whatever_experiments_nav_says` (4) holds that there is none (hidden) |
| | `…::test_other_pages_still_render_with_and_without_the_item` | last assertion: the search page has the entry with `experiments_nav` | it has none |
| | `TestInTheApp::test_list_page_renders_for_an_experimenter` | one assertion: the module's page has the entry | it has none (hidden); the page answers |
| `test_experiments_routes.py` | `test_the_search_page_sidebar_offers_it_to_viewers_only` | a viewer's search page links to Experimente | it does not (hidden); the member's half stays |
| `test_experiments_switch.py` | `TestSwitchedOn::test_navigation_for_viewers_only` | two viewers have the entry | nobody has (hidden) |
| `test_custom_views_gates.py` | the two parametrized tests of the entry and the routes | `NAV_LABEL in home` where the feature is on | `not in` (hidden); the routes' answers unchanged |
| `test_custom_views_routes.py` | `test_the_page_sends_its_headers_and_marks_the_sidebar` | the entry marked as current | the Workspace menu, no such entry |
| `test_web_cortex_switch.py` | `TestCortexOn::test_it_is_in_the_navigation_by_default`, renamed `…::test_it_has_no_entry_in_the_navigation_until_a_later_item_places_it` | "Cortex" on the search page | not there (hidden) |
| `test_ontology_api.py` | `test_sidebar_never_shows_corpus_count` | one assertion: "Cortex" on the search page | not there (hidden) |
| `test_shell_styleguide.py` | `test_the_navigation_is_the_one_every_page_has` | the Feedback address among the entries | the one entry is the search; the address is on neither page (hidden) |

Three of these files are not found by the search terms of "Done when" 2: `test_custom_views_gates.py`,
`test_experiments_routes.py` and `test_experiments_switch.py` ask the search page for a hidden entry by its name or
its address. I found them by searching the tests for the five hidden words, `NAV_LABEL`, `experiments_nav` and the
modules' addresses, and ran `test_builder_in_the_app.py` too (it asks the settings page for the Builder link; it
needed no change). A file I did not think of would show in the whole suite.

### Checks

- **The frozen file.** `tests/test_pmvp11_navigation_acceptance.py`: 40 passed, none skipped (before the build 26
  passed and 14 failed), on a database of my own, `knovas_platform_test_pmvp11`.
- **The builder's tests.** 111 passed: `test_shell_nav_partial.py` 49, `test_shell_nav_styles.py` 33,
  `test_shell_nav_links.py` 29.
- **The files of "Done when" 2**, on `5750dd1`, each file in a pytest process of its own, one after the other on
  the one database: 35 files, 1,430 passed, 23 failed, none skipped, no error. The 23 are in
  `test_experiments_frontend.py` and are on the list of failing ids PMVP-10 recorded for this branch's base (tests
  that read what Node prints; on this machine the German characters arrive wrongly decoded). No failing id that the
  base does not have. The whole suite was not run (the order).

  | Files | Passed |
  |---|---|
  | `test_pmvp11_navigation_acceptance.py` 40, `test_pmvp10_brand_acceptance.py` 33 | 73 |
  | `test_shell_nav_partial.py` 49, `test_shell_nav_styles.py` 33, `test_shell_nav_links.py` 29, `test_shell_styleguide.py` 14, `test_shell_tokens.py` 88 | 213 |
  | `test_web_admin_approvals.py` 22, `_admin_doc_fields` 124, `_admin_documents` 62, `_admin_ingestion` 161, `_admin_people` 23, `_admin_system_doc_fields` 14, `_admin_system_extractor` 32, `_content_wall` 13, `_cortex_switch` 6, `_document_fields` 56, `_documents_find` 51, `_guarded` 5, `_login` 7, `_login_identity` 16, `_open_paths` 6, `_rejects_client_groups` 5, `_search_doc_fields` 62, `_timeouts` 6 (the 18 `test_web_*.py`) | 671 |
  | found by the order's search terms: `test_builder_shell.py` 114, `test_custom_views_page.py` 14, `test_custom_views_routes.py` 24, `test_doc_fields_mock_contract.py` 17, `test_experiments_frontend.py` 92 (and the 23 that fail on the base), `test_ontology_api.py` 16 | 277 |
  | found by my wider search: `test_custom_views_gates.py` 29, `test_experiments_routes.py` 116, `test_experiments_switch.py` 45, `test_builder_in_the_app.py` 6 | 196 |

  One thing for the other builders: a second process on the same database breaks both. Each test's schema holds the
  `citext` extension, and dropping the schema takes the extension with it. My screenshot server collided with a
  running test file once (the server failed at start; I ran the file again); after that, one process at a time.

- **Frozen files.** The check in `frozen-tests.txt`: five lines, all OK.
- **Screenshots**, outside the repository:
  `C:\Users\siran\AppData\Local\Temp\claude\1838582cff9d51e1\af9716ae-0421-42ac-9fac-6acb192cc6e6\scratchpad\pmvp11\out\`.
  `pmvp11-<page>-<width>.png` for the search as a member, an administrator and an approver, Personen and four more
  Admin pages as an administrator, Freigaben as an approver, the settings, the styleguide and the Builder, each at
  1440 and 390 pixels; `beside-<page>-<width>.png` puts the five the order names beside the reference screen of
  their area (`01-assistent.png`, `05-zugriff.png`), `beside-menu-<page>.png` the two menus alone at one scale;
  `pmvp11-facts.json` holds what the browser measured (area, entries, colours, widths). The real app with a fake
  Knovas and invented people, headless Chrome; the scripts are beside the pictures (`serve.py`, `shoot.py`,
  `compose.py`). The scratchpad does not survive a clean-up: copy what should stay.
- **The cards.** `card check` on the two cards: valid, 0 errors, 0 warnings. `card render`: 15 cards, 2 pages
  written; `card render --check`: 0 not current. `map-check --scope touched`, on `5750dd1` (the cards do not change after it): base
  `origin/feat/platform-mvp` (`173adbe`): problems 0, declared 0. Findings inside the scope (the two cards):
  `need_added` on `L2-PLATFORM-SHELL/http.GET.styleguide/N-1` (PMVP-10's line, which the base does not have yet),
  `over_budget` on L2-PLATFORM-ADMIN (24,419 tokens; 24,232 at PMVP-10), `no_contract` on three calls
  (`create_admin_blueprint`, `run_guarded`, `create_shell_blueprint`; FU-17 and PMVP-10's decision 9), and `app.py`
  touched without a card. With the base `origin/feat/pmvp-10` (this item alone): the same without `need_added`.
  Outside the scope: 5 cards over budget and 320 from `complete`. The shell's three declared uses cannot be
  observed (the code reader reads Python, not templates).
  Run from `E:\Knovas\KnowledgeBase-worktrees\pmvp-01\scripts`, read only; nothing reached a tenant.
- The test database `knovas_platform_test_pmvp11` is dropped.

### Open, for the coordinator

- **Decision 1** (how the gate is read) and **decision 2** (an ingestion manager now sees "Verwaltung").
- **FU-23 and the PMVP-03 walkthrough.** FU-23 stands as it is. At 390 pixels the five Admin pages
  (525 to 672 pixels wide) and `/settings` as an administrator (530, as before) still scroll sideways, each because
  of its own content: with the content taken out every one is 390 wide, and `/settings` with the menu taken out is
  still 530. `/settings` as a member does not scroll sideways (it has no settings card). So this item changes
  nothing in the walkthrough's list of known pages.
- **Outside this item's files, now out of date:** `src/web_interface/DESIGN-CANVAS.md` line 73 still names
  `templates/_admin_tabs.html` as a shared piece; `KnovasPlatform/docs/features/experiments.md` speaks of the
  module's menu entry ("Menüpunkt", lines 13, 193, 222 and 1562), which is hidden now;
  `static/cortex-bestand/index.html` is a still copy of the old sidebar's markup with addresses of a machine in a
  private network written into it (it loads `style.css`, so it now takes the new look through `shell.css`; I found
  no link to it).
- **`admin.css`** keeps rules nothing uses any more (decision 9).
- **`ingestion_enabled`** is still handed to the console's pages (`app.py`, where the console's blueprint is
  registered); only the row of tabs read it. I left the line: it is not what feeds the sidebar.
- **The styleguide's gate** is still a second copy of the console's rule (`shell_routes.py`; the shell card's review
  entry). `shell_routes.py` is not among this item's files.
- **The content column in a narrow window** is as wide as its content, not as the window (the search page's is
  324 of 390 pixels; `style.css`, `.app-shell { align-items: flex-start }`). It was so before; it is the page's
  layout, not the menu's.
