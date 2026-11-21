# Work order PMVP-40: people, groups and access in the new look

| | |
|---|---|
| Item | PMVP-40, plan `FEAT-2026-11-19-platform-mvp` § 4 Admin A-01 to A-04 (they exist; this item gives them the new look) |
| Class | routine (look only; no gate, role or rule changes) |
| Branch / worktree | `feat/pmvp-40` from **`origin/feat/pmvp-11`** (the navigation without the Admin tab row, finishing), worktree `.worktrees/pmvp-40`; merge `origin/feat/platform-mvp` in once (it holds the page parts) |
| Visual contract | `docs/platform-mvp/reference-screens/05-zugriff.html` (and `out/05-zugriff.png`): one column, one main action, marks only for exceptions |
| Card | `docs/components/L2-platform-admin.card.yaml` (over its context budget: FU-17) |

## Goal

The Admin pages for people, groups and access (`/admin/people`, `/admin/access_groups`, `/admin/documents`) look like
the reference screen and use the shared page parts, and every page's gate is covered by a test.

## What to build

1. **The look.** The three pages' templates use the parts (`page`, `page_head` with the main action beside the title
   via `k-head`, `section`, `card`, `rowline`, `tag`, `status_dot`) and the brand tokens. One column; the details of
   a person or a group open on request (the existing side panel or a page of its own, whichever the current code
   makes cheaper). Roles in plain words (A-02: the German label, not `ingestion_manager`). A group tree stays a tree.
2. **Marks only for exceptions.** An active person carries no badge; a deactivated one, a pending password change,
   a denial rule do, with their meaning in words.
3. **The old green and red tints** (FU-28): the `admin.css` rules at the lines FU-28 names take the brand's
   `--k-success-bg` / `--k-danger-bg` and keep 4.5 to 1. Rules of `admin.css` that the new templates no longer use
   are removed; list them.
4. **Tests for the gates** (FU-17): every route of these three pages that the Admin card lists without a test gets
   one (signed out 302 to sign-in; a member 403; the right role 200), with the page-test helper `pmvp_pages` from
   PMVP-03 if it is on your branch, else plain tests. The card's lines then name the tests as evidence; remove the
   `review` entries they replace.
5. **The card** over budget (FU-17): if it stays over its context budget, split it the way the map-components
   skill says (read `E:\Knovas\KnowledgeBase\.claude\skills\map-components\SKILL.md`), or say why not.

## Rules

1. **No behaviour change**: the same routes, forms, field names, gates and messages; JavaScript only where a template
   change forces it (say which). Existing tests of these pages stay green; change an assertion only where it pins
   old mark-up, and list each with its reason.
2. Hrefs only from `url_for` (FU-34); every value escaped.
3. Nothing scrolls sideways at 390 px.

## Files you may change

The templates of the three pages and the partials only they include; `static/css/admin.css` (rules of these pages
and the tints of FU-28); `static/js/admin_people.js`, `admin_documents.js` only where forced; `tests/test_web_admin_*`
(new tests; assertions under rule 1); the Admin card, its page, `boundaries.yaml`; the notes below.

## Files you must not change

The frozen files, `conftest.py`, `admin.py` and every route's logic or gate, `src/identity/**`, the parts, the
sidebar, other pages.

## Done when

1. `tests/test_web_admin_*.py`, `tests/test_shell_*.py`, `tests/test_pmvp11_navigation_acceptance.py` green; the
   frozen check all OK. **Do not run the whole suite.** Own test database `knovas_platform_test_pmvp40`.
2. Before/after screenshots of the three pages at 1440 and 390 px beside `05-zugriff.png`, outside the repository.
3. Card checks clean. Commits `PMVP-40: …` with the co-author line; `feat/pmvp-40` pushed. No pull request.

## Builder's notes

Builder session of 2026-11-21 (AGENT-claude-code). `feat/pmvp-40` from `origin/feat/pmvp-11` (`4b7791b`), with
`origin/feat/platform-mvp` (`07078ce`) merged in once (`1c6c7f2`). Four conflicts, each resolved by keeping both
sides: `style.css` imports the tokens, then `parts.css`, then `shell.css`; the shell card and `boundaries.yaml` carry
`shell.css` (PMVP-11) and `_parts.html`/`parts.css` (PMVP-12); the shell card's page re-rendered. `feat/pmvp-11` did
not move during the session. `feat/platform-mvp` moved afterwards (`855c92e`, PMVP-03 merged) and was not merged again
(the order says once), so `pmvp_pages` is not on this branch: the gate tests are plain tests.

### What was built

| File | What |
|---|---|
| `templates/admin_people.html` | one column of the parts: the head with «Person hinzufügen» beside it, the filters, a card with one row per person, the details panel below the list, the dialog; roles in plain words |
| `templates/admin_access_groups.html` | head with «Gruppe anlegen» (the page's one main action), sections Gruppen (the tree), Gruppe anlegen, Ordnerregeln (a row per rule), Regel speichern |
| `templates/admin_documents.html` | head, filters, Feldfilter (unchanged), a card with one row per document, «Mehr laden», drawer (unchanged), the purge section |
| `static/js/admin_documents.js` | `renderRow` draws a loaded row as the template does (forced: the rows were `<tr>`, they are the parts' row line now); nothing else in the file changed |
| `static/css/admin.css` | FU-28's tints, the removed rules (below), the pages' rules in the brand tokens, a section for what the parts do not draw |
| `tests/test_web_admin_gates.py` | new, 57 tests: the role gate of every route (item 4) |
| `docs/components/L2-platform-admin.card.yaml`, new `L2-platform-access.card.yaml`, their pages, `EXT-brain-api.md`, `boundaries.yaml` | the card split (item 5) |

`admin_people.js` is unchanged: its rows are `div`s now, and it only reads `[data-person-row]`, `[data-person-select]`,
`[data-person-detail]` and the ids, which stay.

### The look (items 1 and 2)

- **One column**: `parts.page(wide=True)` (720 px), `parts.page_head` with the main action beside it in `k-head`,
  `parts.section`, `parts.card`, `parts.rowline`, `parts.tag`. No status dot: no page has a state that is a dot.
- **Details on request**: the existing panel, now below the list instead of beside it (cheaper than a page of its own:
  no route). As before, the first person's details are open when the page loads and «Verwalten» switches them; the
  selected row shows its name and button in the accent.
- **Roles in plain words** (A-02): a table at the top of `admin_people.html` gives each key a name and a line -
  Administration, Freigabe, Einlesen, Mitglied, Experimente, Experimente verwalten. The checkboxes still send the key
  (`name="roles" value="admin"`); the filter's options still carry the key as value. A role the table does not know
  is shown by its key. **The wording is mine; the founder may want other words.**
- **Marks only for exceptions**: an active person carries nothing; `deaktiviert` (bad), `gesperrt` (warn) and
  `Passwortwechsel offen` (warn) are tags, and the details panel says what each means in a sentence. A document carries
  a warn tag with its own state word when the state is not `active`; an active one carries nothing (before: a green
  `active` on every row, `—` when there was none). «Administratorgruppe bei Knovas» is an `acc` tag with its old
  title. No page of the three has a denial rule today (the data has none), so there is nothing to mark there.
- **The group tree stays a tree**: the flat list from the Brain is drawn as nested lists by `parent_id`; a group whose
  parent is not in the list is drawn at the top, every group exactly once (a loop in the ids cannot repeat or hide one;
  tried with an orphan, a two-group loop and a group that is its own parent).
- **Access groups are shown as the data holds them** (by id on rules, documents and people), as before; showing names
  would need the documents script to know them. «offen» (no group) is plain muted text, not a mark.
- **Messages and texts** are the old ones word for word (ledes, hints, notices, errors, empty states).

### admin.css (item 3)

- **FU-28's tints**: `.admin .msg.ok` (was :290) and `.msg.error` (:284), `.check-ok .check-state` (:393) and
  `.check-fail .check-state` (:395) take `--k-success-bg` / `--k-danger-bg`; the purge section's background takes
  `--k-danger-bg`. Text on them: success 4.79 to 1, danger 5.93 to 1 (computed). `.state-active` (:164) and
  `.document-status` (:948) are not re-tinted but removed: the pages no longer draw them.
- **Removed, because no template or script uses them any more**: `.admin .state-active, .state-disabled,
  .state-locked` and their `::before`; `.admin .role-chip`; `.admin-page .admin .role-chip` (its colours);
  `.admin-page .admin .document-status` (its green tint); `.admin-page .admin tbody tr.is-selected` and its `:hover`;
  `.admin-page .admin .people-row-action` (out of the `.row-action` rule); `.admin-page .admin .state-active,
  .state-disabled, .state-locked`; `.people-layout`; `.people-table-wrap`; `.admin-page .admin .people-table-wrap
  table`; `.select-column`; `.folder-rules-section` and `.folder-rules-section > .hint`; `.folder-rule-form`, also
  out of the 900 and 720 px lists; the 900 px block of `.people-layout` and `.person-detail-panel`; the old look of
  `.knovas-admin-badge` (two rules; the class stays on the hint's wrapper, which test_web_admin_doc_fields.py counts).
- **Kept although unused**: `.admin-page .admin .role-chip` and `.document-status` in the shared mono rule, only because
  `tests/test_shell_tokens.py` (MONO_RULES, not this order's file) names both; a comment says so.
- **Restyled with tokens** (rules of these pages): `.admin-toolbar`, `.document-bulk-bar`, `.choice-chip` checked,
  `.person-detail-panel`, the two borders of the details panel, `.person-avatar` (navy, as the reference's avatar).
- **Not touched**: `.admin .tabs` (the removed strip's rules) and the `.admin-page .app-*` rules for the old navigation
  (`test_shell_nav_styles.py` reads them; the shell's card review names both).
- **No rule of admin.css names a class of the parts** (`test_shell_parts_css.py`): the pages' `<main>` carries
  `admin-column`, and the spacing hangs on that.

### Gate tests (item 4)

`tests/test_web_admin_gates.py`, live through the real app and sign-in, for every route of the three pages, the
console's entry `GET /admin/` and `GET /admin/system` (FU-17 names its gate with the people routes): signed out 302 to
the sign-in and nothing run; a member 403 with a valid token and a complete form, nothing run; an administrator 200
with the smallest valid form. Guarded actions run with the bypass off, so they are queued and the fake Knovas client
records no call. The three JSON routes of the fields drawer get the signed-out test; their member 403 and
administrator 200 are TestDocFieldsLive's. Each N-1 line on the two cards names these tests; the eight `review`
entries «Seen in the code, not covered by a test» are gone. **No existing test was changed.**

### The card (item 5)

Split, the way the map-components skill registers a component: the card was 24,547 tokens (code alone 16,724) against
16,000. `L2-PLATFORM-ACCESS` («Zugriff») takes `admin_documents.py`, `admin_access_groups.html`,
`admin_documents.html` and `admin_documents.js`, `L2-PLATFORM-ADMIN/R-2` (now `L2-PLATFORM-ACCESS/R-1`, the old id as
its first alias), a new R-2 for erasing the corpus, the eleven routes with their N-1 lines (old ids as first aliases)
and the three names `admin.py` imports from it. Each card `uses` the other (run_guarded one way; the routes and the two
executors the other). After: L2-PLATFORM-ADMIN 13,835 tokens, L2-PLATFORM-ACCESS 13,894.

### What I decided, and why (the verifier should look here first)

1. **One href is not from `url_for`**: «Gruppe anlegen» in the head is `href="#create-access-group"`, as before. After
   a POST the page is drawn at the POST's address; `url_for(..., _anchor=...)` would load `GET /admin/access-groups`
   again and lose the message. It is a fixed anchor on the page, not an address; a comment in the template says so.
2. **A row of people is a `div.person-row` around `parts.rowline()`**: the macro takes no attributes, and the script
   needs its data on the row. The hairline between rows therefore hangs on the wrapper (`.people-list .person-row`).
3. **`div.admin-head-text` around `parts.page_head`**: with a lede of three lines the head took the whole row and the
   action fell below it; the wrapper lets the head shrink, and the action still goes below on a phone.
4. **One primary button per page**: «Regel speichern» became `secondary`; «Gruppe anlegen» is the main action.
5. **The Feldfilter's result table stays a table** (built by the script, only when Knovas offers the listing); at 390
   px it scrolls inside its own box, the page does not.

### Checks

- **Tests** (own database `knovas_platform_test_pmvp40`, one process at a time): the eight `tests/test_web_admin_*.py` files, 495 passed
  (the new gate file 57 of them); the eight `tests/test_shell_*.py` files with
  `tests/test_pmvp11_navigation_acceptance.py`, 439 passed and 2 failed - the two below under «Stopped on»; besides,
  the tests of `test_doc_fields_render.py` and `test_experiments_switch.py` that render these pages, 6 passed.
- **Frozen check**: the five files of `frozen-tests.txt`, all OK.
- **Cards**: `card check` on both cards valid, 0 errors, 0 warnings; `card render --check` 16 cards, 0 not current;
  `map-check --scope touched` (base `1c6c7f2`, head the last commit): problems 0; budget 0 findings; complete 5
  `no_contract` (`create_admin_blueprint` and `run_guarded` as before, and the three names of L2-PLATFORM-ACCESS:
  plan § 7 gives none, review entries say so); compatible 11 `operation_removed` on L2-PLATFORM-ADMIN - the eleven
  routes that moved to L2-PLATFORM-ACCESS with the same ids, no consumer. Run from
  `KnowledgeBase-worktrees/pmvp-01/scripts`, read only; nothing reached a tenant.
- **In the browser** (headless Chrome, the real app with invented people, groups, rules and documents, full pages at
  1440 and 390 px): before, all three pages scrolled sideways at 390 and the lowest text contrast was 3.46 (the green
  `aktiv`); after, nothing scrolls sideways at either width, the lowest contrast is 4.76, a keyboard walk shows a ring
  on every link, button and field of the page, no request failed or left the installation, and exactly one link is
  marked current.
- **Screenshots**, outside the repository, in
  `C:\Users\siran\AppData\Local\Temp\claude\1838582cff9d51e1\af9716ae-0421-42ac-9fac-6acb192cc6e6\scratchpad\pmvp40\shots\`:
  `before-*` and `after-*` for `people`, `people-selected` (the fourth person's details), `access-groups`, `documents`
  and `documents-more` (after «Mehr laden»), each at 1440 and 390; `beside-*` puts before, after and `05-zugriff.png`
  side by side; `before-facts.json` and `after-facts.json` hold what the browser measured. The tools (`serve.py`,
  `shoot.py`, `compose.py`) are one folder up.

### Stopped on, open for the coordinator

- **Two shell tests contradict each other once PMVP-11 and PMVP-12 meet, and both fail on this branch**:
  `test_shell_nav_styles.py::test_style_css_loads_the_tokens_first_and_then_the_navigation` wants the imports
  `[knovas-tokens.css, shell.css]`, `test_shell_parts_css.py::test_the_base_stylesheet_loads_the_parts_right_after_the_tokens`
  wants `[knovas-tokens.css, parts.css]`. Every page needs both files, so no `style.css` satisfies both. Neither file is
  this order's to change; one of them (or both, to the three imports) needs changing where PMVP-11 and PMVP-12 are
  integrated.
- **The parent card**: `L1-WORKSPACE` (KnowledgeBase) does not list `L2-PLATFORM-ACCESS`; outside this repository.
- **The role words** want a person's yes (the third point of «The look»).

## Verification (coordinator's record)

- **2026-11-21, fresh verifier session, `feat/pmvp-40` @ `3d46214`: PASS WITH NOTES.** No behaviour change: all eight
  actions (plus enable and rule delete) driven through the rendered forms on base `4b7791b` and head gave identical
  forms, status codes, messages and effects; no Python changed. All 21 routes have gate tests; the eight review
  entries are replaced by test evidence. Nothing scrolls sideways at 390 px (base 567/665/769 px); lowest contrast
  4.76 (base 3.46); 52 controls with a focus ring; names, paths and titles escaped. Card split clean.
- Integrated into `feat/platform-mvp` by the coordinator (merge `b0c0d56c`; card conflicts resolved, pages re-rendered).
- Notes, ordered as a small follow-up: the shell card still points at two lines that moved to the access card
  (`map-check --scope all`: 4 findings); the signed-out tests cannot catch a missing route gate and only a member is
  tried (a gate widened to approver would pass); two primary «Gruppe anlegen» buttons; a visible label that differs
  from its aria-label; a raw English state word («quarantined»). The German role names wait on the founder.
