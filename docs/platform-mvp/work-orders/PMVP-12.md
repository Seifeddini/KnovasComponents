# Work order PMVP-12: shared page parts

| | |
|---|---|
| Item | PMVP-12 (Jira SS-731), plan `FEAT-2026-11-19-platform-mvp` requirement F-7, § 5.1 (five design rules) |
| Bet | `BET-2026-11-platform-mvp` (approved 2026-11-19) |
| Class | routine |
| Branch / worktree | `feat/pmvp-12` from **`origin/feat/pmvp-10`** (the look; built, in its second round, not yet verified), worktree `.worktrees/pmvp-12`. When `feat/pmvp-10` moves, merge it in |
| Frozen tests | none of its own; the five in `docs/platform-mvp/frozen-tests.txt` stay unchanged and green where they are green on your base |
| Card | `docs/components/L2-platform-shell.card.yaml` (the card changes in the same work) |

## Goal

The parts every new screen is made of, once, so that the screens after this one (dossier, process line, access,
sources, the ask page) are built from the same pieces and look like the reference screens
(`docs/platform-mvp/reference-screens/`, `app.css` lines 18 to 47 hold these parts as the mockups draw them).

The screen builders start right after you and rely on the names below. **Do not change a name or an argument**; if
one cannot work, stop and say why.

## What to build

1. **`P/src/web_interface/static/css/parts.css`** (`P` = `KnovasPlatform/components/docbridge_integration`), loaded
   after the tokens and `style.css` the same way `style.css` loads the tokens. Every class starts with `k-`
   (the existing stylesheets have their own `.card`, `.btn`, `.tag`; nothing of theirs changes). Colours, type,
   radii and spacing only through `var(--k-…)` tokens; no literal colour.
2. **`P/src/web_interface/templates/_parts.html`**, Jinja macros, imported as
   `{% import "_parts.html" as parts %}`:

   | Macro | Draws | Notes |
   |---|---|---|
   | `parts.page(wide=False)` (call block) | the one centred column (`k-page`; 640 px, wide 720 px) | one column and one main action per screen |
   | `parts.page_head(title, eyebrow=None, lede=None)` | eyebrow, `h1`, lede | |
   | `parts.section(title)` (call block) | a small upper-case heading (`h2`) and its content | |
   | `parts.card(now=False)` (call block) | `k-card`; `now=True` marks the one card that is current | |
   | `parts.rowline()` (call block) | one row of a list: content left, end right, a hairline between rows | the caller puts two children in |
   | `parts.tabs(items, active)` | `items` = list of dicts `key`, `label`, `href`, optional `count`; `active` = a key | links, `aria-current="page"` on the active one; the count in a lighter weight |
   | `parts.status_dot(state, label)` | a dot; `state` is `ok`, `warn` or `bad` | `label` is its accessible name (not shown); a `warn` or `bad` dot never stands alone: the caller writes the problem and the fix beside it (rule 5) |
   | `parts.tag(text, kind="")` | a pill; `kind` is `""`, `warn`, `bad` or `acc` | marks are for exceptions only |
   | `parts.ref_chip(number, active=False, href=None, controls=None)` | the numbered reference; a link when `href` is given, else a button; `aria-expanded` and `aria-controls` when `controls` is given | PMVP-22b gives it its behaviour; here the mark-up and both states |
   | `parts.link(text, href, dotted=False)` | a link; `dotted=True` for a name that opens a dossier (W-08) | |

   Everything a macro is given is escaped (autoescape stays on; no `|safe`): a name or a sentence that holds
   `<script>` or Markdown must come out as text.
3. **The styleguide.** The section PMVP-10 left empty for these parts shows each macro in each state, with its
   call written beside it, built from the macros themselves (not from copied mark-up).
4. **The card.** `L2-platform-shell.card.yaml`: the two new files in `source_paths`; `boundaries.yaml`; the rendered
   page; `card check` 0 errors, no stale page, `map-check --scope touched` no problem about the card (see "Checks"
   in `work-orders/PMVP-01.md`). Nothing reaches a tenant.

## Rules

1. Readable and usable: text contrast 4.5 to 1 or better on its background; a visible focus ring on every link and
   button; nothing scrolls sideways at 390 pixels; a tab row that does not fit scrolls inside itself.
2. No script in this item. No existing page changes: no template other than `styleguide.html` uses the parts yet.
3. No new dependency; nothing loaded from outside the installation.

## Your own tests (`P/tests/test_shell_parts_*.py`)

Each macro renders its classes and states; escaping (a title, a tag text, a tab label and a link text that hold
`<script>alert(1)</script>` come out escaped); `tabs` marks exactly the active item; `status_dot` carries its label
as an accessible name; `ref_chip` is a link with `href` and a button without; `parts.css` holds no literal colour and
no outside address; the styleguide shows every macro.

## Files you may change

- new: `P/src/web_interface/static/css/parts.css`, `P/src/web_interface/templates/_parts.html`,
  `P/tests/test_shell_parts_*.py`
- `P/src/web_interface/templates/styleguide.html` and `P/src/web_interface/shell_routes.py` (the parts section)
- `P/src/web_interface/static/css/style.css`: only the one line that loads `parts.css`, if that is how it is loaded
- `docs/components/L2-platform-shell.card.yaml`, its rendered page, `docs/components/boundaries.yaml`
- `docs/platform-mvp/work-orders/PMVP-12.md`: only the "Builder's notes" section at the end

## Files you must not change

- the frozen test files; `P/tests/conftest.py`; every other stylesheet, template and script; `app.py`;
  `docs/platform-mvp/reference-screens/**`; `requirements.txt`

## Done when

1. Your tests green; `tests/test_pmvp10_brand_acceptance.py` and `tests/test_shell_*.py` green (test database of
   your own: `knovas_platform_test_pmvp12`); the frozen check in `frozen-tests.txt` all OK.
2. **The whole suite once, on your branch only**
   (`py -3 -m pytest -q -p no:cacheprovider --continue-on-collection-errors -rfE`): compare its failing and erroring
   ids with `docs/platform-mvp/baseline-failing-ids.txt` on `origin/feat/platform-mvp` (the coordinator's run of the
   build branch; if the file is not there yet when you finish, say so and list your failing ids instead). Do not
   run the base yourself.
3. Screenshots of the styleguide's parts section at 1440 and 390 pixels wide, outside the repository (say where),
   next to the matching parts of the reference screens.
4. The card checks. Commits `PMVP-12: …` ending with the co-author line; `feat/pmvp-12` pushed. No pull request.

## Builder's notes

(the builder writes here: what was decided, what is open, what the verifier should look at)
