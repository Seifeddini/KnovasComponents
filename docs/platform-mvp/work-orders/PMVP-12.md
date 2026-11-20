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

Builder session of 2026-11-20 (AGENT-claude-code). `feat/pmvp-12` from `origin/feat/pmvp-10` (`a625823`), with
`origin/feat/platform-mvp` (`b91a5ac`) merged in once and `origin/feat/pmvp-10` again when it moved (`5536443`).

### What was built

| File | What |
|---|---|
| `templates/_parts.html` | the ten macros, names and arguments as the table above has them; no script, nothing marked safe |
| `static/css/parts.css` | their rules: `k-` classes only, brand tokens only, no colour named |
| `static/css/style.css` | one line, the second rule: `@import url('parts.css');` |
| `templates/styleguide.html` | the section «Seitenteile»: each macro in each state with its call, and one page of all parts together |
| `tests/test_shell_parts_macros.py` (49), `…_css.py` (86), `…_styleguide.py` (52) | 187 tests |
| `L2-platform-shell.card.yaml`, its page, `boundaries.yaml` | the two files in `source_paths`; R-4 (the parts), R-2 and R-3 reworded |

`shell_routes.py` is unchanged: the section needs nothing from the route.

### What a screen builder must know

1. **Loading.** A page that links `css/style.css` has the parts; link nothing else. The Builder's pages do not load
   `style.css` (`templates/builder/_base.html` has its own two stylesheets): a Builder screen links
   `css/knovas-tokens.css` and `css/parts.css` itself. The parts set their own margins and box, so they look the
   same there.
2. **The classes** (the card hides them; use the macros, but a test may look for them): `k-page` `k-page--wide`,
   `k-page-head` `k-eyebrow` `k-title` `k-lede`, `k-section` `k-section-title`, `k-card` `k-card--now`, `k-rowline`,
   `k-tabs` `k-tab` `k-tab--on` `k-tab-count`, `k-dot` `k-dot--ok|warn|bad`, `k-tag` `k-tag--warn|bad|acc`, `k-ref`
   `k-ref--on`, `k-link` `k-link--dotted`.
3. **Three classes without a macro**, from the same lines of `app.css`: `k-head` (a row: the page head left, the
   one main action right, the action below when there is no room; `page_head` is no call block, so this is how an
   action gets beside it), `k-muted`, `k-small`.
4. **`rowline`**: the first child is the content and takes the room, the last is the end and keeps its width. When
   the content would be left with less than 12 rem (a button as the end, on a phone) the end goes below it. A row
   with a dot, content and an end puts dot and content into the first child.
5. **`tabs`**: `<nav aria-label="Reiter">` with links; the active one has `k-tab--on` and `aria-current="page"`;
   an `active` no item has marks nothing; a `count` of 0 is shown, a missing or `none` count is not.
6. **`ref_chip`**: its only text is the number. With `controls`, `aria-expanded` follows `active` (`true` when
   active). The chip is a box of its own, so a line may break between it and the full stop after it; keep the word,
   the chip and the stop in a `white-space: nowrap` element if that matters (PMVP-22b).
7. **`status_dot`, `tag`**: a state or kind that is not in the table draws the plain dot (grey) or the plain tag;
   it is never written into the class attribute.
8. **`link`, `tabs`, `ref_chip`**: an `href` is escaped as an attribute, not checked as an address. Build it with
   `url_for`; never take one from a document.
9. **Inside `.admin`** (the console's wrapper) `admin.css` styles every `button` (`.admin button`,
   `.admin-page .admin button`) and outranks `.k-ref` there: a chip drawn as a button would take the console's
   button look. No page does this yet; there, give the chip an `href`, or say so and the chip's rule gets more
   weight.

### What I decided, and why (the verifier should look here first)

1. **`parts.css` comes before `style.css`'s own rules, not after them.** The order says "loaded after the tokens
   and `style.css` the same way `style.css` loads the tokens"; an `@import` has to stand before every other rule,
   so the same way can only mean right after the tokens. It holds because every rule of `parts.css` hangs on a
   `k-` class, which outranks the element rules of `style.css` (the reset, `h1, h2`, `button`) in any order; the
   test `test_every_rule_hangs_on_a_k_class` keeps it so.
2. **`card(now=True)`**: the accent on the edge, a soft ring and `aria-current="true"`. The reference draws its
   «Läuft gerade» card plain; the order says the current card is marked, so the mark is the one the reference uses
   for what is current (`.pill.on`, `.st.now`).
3. **Tabs differ from the picture in weight and grey.** The reference's count is `--k-gray-400`, 2.6 to 1 on white.
   Here the count is `--k-color-text-muted` (4.76 to 1) at weight 400, and so that it is still lighter than its
   label, an inactive label is weight 500 in `--k-gray-600` (the reference: 400 in gray-500).
4. **The warn dot has a darker edge** (`--k-warning` inside, one pixel): the bright yellow alone is 1.7 to 1 on
   white.
5. **Tokens only, and what a written length is.** Every colour, font size, weight, tracking, radius, padding,
   margin and gap is a token or a `calc()` of tokens (`test_type_radii_and_spacing_are_tokens`). Written out are
   only own sizes: the column (640, 720 px), the dot (9 px), the chip (18 px), lines of 1 and 2 px, the 12 rem of
   point 4 above. The page title is `calc(var(--k-text-xl) + var(--k-space-2xs))`, 26 px: the reference's 1.6 rem,
   which the scale has no step for (22, then 28).
6. **A tab row never widens the page** (`width: 0; min-width: 100%`). Found in the browser: under 900 px the
   shell's `.app-content` is as wide as what is in it, and five tabs pushed the page to 570 px at 390.
7. **The styleguide**: a call stands twice on one line of the template, as text and for real;
   `test_a_call_written_on_the_page_is_the_call_that_draws_the_example` holds the two together, and
   `test_the_template_types_no_class_of_a_part_by_hand` allows only the three helper classes.
8. **No contract line for the parts on the card.** The plan's § 7 gives none; R-4 says what they are, and the
   review entry says where the tests are.

### Stopped on

- **`tests/test_shell_styleguide.py::test_the_section_for_the_parts_of_pmvp_12_is_there_and_empty` fails, and has
  to.** It is PMVP-10's own test and pins the section as empty; this order fills it and does not list that file.
  I did not change it. It needs deleting (its successor is
  `test_shell_parts_styleguide.py::test_the_section_is_there_under_its_heading`). "`tests/test_shell_*.py` green"
  therefore holds for every id but this one.

### Checks

- **Tests** (own database `knovas_platform_test_pmvp12`; its drop was issued at the end and was still waiting
  for a checkpoint of the shared server when these notes were written): the three new files, 187 passed.
  With `tests/test_shell_styleguide.py`, `tests/test_shell_tokens.py`, `tests/test_pmvp10_brand_acceptance.py` and
  `tests/test_web_admin_people.py`: 345 tests, 344 passed, 1 failed (the id above). Twelve deliberate breaks of
  `_parts.html`, `parts.css` and the styleguide (a value marked safe, every tab current, a hex colour, no focus
  ring, copied mark-up and so on) each failed a test.
- **The whole suite: not run**, on the coordinator's word during the build (the sessions share one database
  server); the coordinator runs it at integration.
- **Frozen check**: the five files of `frozen-tests.txt` on this branch, all OK. (`origin/feat/platform-mvp` has
  since changed that list; not merged here.)
- **The card**: `card check` valid, 0 errors, 0 warnings; `card render --check` 15 cards, 0 not current;
  `map-check --scope touched --settings docs/components/map-check.yaml`, base `origin/feat/platform-mvp`
  (`b91a5ac`), head `b8dc4e3`: problems 0, declared 0. Findings inside the scope, none from this item: `need_added` on
  `http.GET.styleguide/N-1` and `no_contract` on `create_shell_blueprint` (PMVP-10's), `over_budget` and two
  `no_contract` on L2-PLATFORM-ADMIN (PMVP-01's); `web_interface` touched without a card (`app.py`, PMVP-10).
  Run from `KnowledgeBase-worktrees/pmvp-01/scripts`, read only; nothing reached a tenant.
- **In the browser** (headless Chrome, the real app, 1440 and 390 px): no request failed and none left the
  installation; nothing scrolls sideways at either width; at 390 every tab row scrolls inside itself; every link
  and button of the parts shows a 2 px ring on keyboard focus; the lowest text contrast the browser computed for
  a part is 4.76 to 1 (muted text on white; the tags: 4.84 warn, 5.91 bad, 6.27 acc, 6.92 plain).
- **Screenshots**, outside the repository, in
  `C:\Users\siran\AppData\Local\Temp\claude\1838582cff9d51e1\af9716ae-0421-42ac-9fac-6acb192cc6e6\scratchpad\pmvp12\shots\`:
  `parts-1440.png`, `parts-390.png` (the section), `styleguide-1440.png`, `styleguide-390.png` (the whole page),
  `compare-1440.png`, `compare-390.png` (the section beside the reference screens 02, 06, 01, 04, 03 and 05) and
  `compare-zusammen-1440.png`, `compare-zusammen-390.png` (the page of all parts beside 02 and 06);
  `facts.json` holds what the browser computed. The tools (`serve.py`, `shoot.py`) are one folder up.

### Open, for the coordinator

- The failing test above: delete it, or say who does.
- The styleguide page now has several `h1` (each `page_head` example draws one, as on a real page).
- The chip's accessible name is its number alone ("2"). A word before it ("Quelle 2") would need an argument the
  table does not have; PMVP-22b's call.
