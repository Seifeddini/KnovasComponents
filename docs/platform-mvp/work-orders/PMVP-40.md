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

(the builder writes here)
