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

Builder session of 2026-11-20 (AGENT-claude-code), `feat/pmvp-10` from `b9f31b7`. First round: commits `1c9f9fc`
(tokens, fonts, `style.css`), `62e67c7` (the page and the tests), `afdd675` (the card), `a625823` (notes). Second
round, after the coordinator's "Changes to this order": `b425d24` (merge of `feat/platform-mvp` at `1268019`),
`7c2536e` (mono for codes), `98de30e` (the navigation on the styleguide page), and the one that carries these
notes. Nothing here is verified by me; the numbers are what I saw.

### What was built

| Part | Files |
|---|---|
| The tokens | `static/css/knovas-tokens.css`: the reference file, same git blob (`54cbe37`). One `@import url('knovas-tokens.css')` is the first rule of `style.css` |
| The names | `style.css`, the `:root` block: all 22 names stay, each takes a brand token through `var(--k-…)`; one name is new, `--font-mono` |
| Type | `--font-body` and `--font-heading` are `--k-font-sans`. `static/fonts/ibm-plex-sans-latin-700-normal.woff2` and `ibm-plex-mono-latin-500-normal.woff2`: the reference files, same blobs, with their `@font-face` rules. Second round: 16 rules that set codes, ids, file names and diffs ask for `--font-mono` |
| The page | `shell_routes.py` (blueprint `shell`, `GET /styleguide`), `templates/styleguide.html`, 7 lines of wiring in `app.py` after the console's blueprint |
| Tests of the builder | `tests/test_shell_tokens.py` (88, no database), `tests/test_shell_styleguide.py` (14) |
| The card | `L2-platform-shell.card.yaml`, its page, `boundaries.yaml` |

No script and no existing template changed. Below the `:root` block of `style.css` and in the other stylesheets
one thing changed, by the order's change 1: one word in each of 16 rules (see "Mono for codes").

### What I decided, and why (the verifier should look here first)

1. **`@import`, not a `<link>` per template.** One line in the file that needs the tokens: the 18 templates that
   load `style.css` today, and any later one, get them without a line of their own, and no template changed. The
   cost: the browser asks for the tokens only when `style.css` has arrived, one more round trip before the first
   paint. The Platform serves `/static/` with `no-store` (`app.py`, `_prevent_stale_ui_assets`), so the file without
   `?v=` cannot go stale. PMVP-11's base template can turn it into one `<link>`.
2. **The names the frozen file does not pin.** Each takes the token the reference screens use for the same job
   (`reference-screens/app.css` and the six screens):

   | Name | Takes | Was | Why this token |
   |---|---|---|---|
   | `--text-secondary` | `--k-gray-600` `#475569` | `#5A6B80` | labels, meta lines, navigation entries: the reference's `.nav`, `.tag`, `.bub`. 7.58, 7.58 and 7.24 to 1 on the three backgrounds |
   | `--accent` | `--k-color-accent` `#2C4AC2` | `#3B79F2` | focus outlines, hover and active borders: the reference draws them in Knovas Blue (`.pill.on`, `.tabs .on`). `--k-blue-bright` is the brand's accent for dark surfaces only. So `--accent` and `--primary-color` are now one colour |
   | `--accent-rgb` | `44, 74, 194` | `59, 121, 242` | the triple of `--accent`; the brand's own `--k-color-accent-soft` and `-ring` are written with the same triple |
   | `--text-primary-rgb` | `30, 41, 59` | `40, 54, 71` | the triple of `--text-primary` |
   | `--callout` | `--k-color-accent` | `#6E88DC` | the 3px bar at the left of a notice: the reference's cited passage (`.peek`, screen 01) has it in Knovas Blue |
   | `--highlight` | `--k-color-accent-soft` `rgba(44, 74, 194, 0.10)` | `#D9E0F7` | found passages, the current entry, chips: the reference's tinted fill (`.ref`, `.tag.acc`). It is see-through; no rule uses it on a sticky element that has to cover what scrolls under it |
   | `--radius` | `--k-radius-md` 8px | 6px | buttons, inputs, navigation entries: the reference's `.btn`, `.nav`, `.sel` |
   | `--radius-lg` | `--k-radius-lg` 12px | 8px | cards and panels: the reference's `.card`, `.state`, `.note` |
   | `--gutter` | `calc(var(--k-space-md) + var(--k-space-2xs))` = 20px | 20px | see 3 |
   | `--pad-section` | `var(--k-space-md) var(--gutter)` = 16px 20px | 16px 20px | see 3 |
   | `--font-mono` (new) | `--k-font-mono` | | the name the rules for codes ask for; see "Mono for codes" |

   The two triples are numbers, not `var()`: the brand has no token that holds a triple. The frozen test checks them
   against their colours.
3. **The paddings stay at 20px.** The reference screens name no spacing token (`.card{padding:16px 18px}`), and the
   brand's grid has no 20px step. `var(--k-space-lg)` (24px) would have split one container against itself: `.admin`
   takes `--gutter` at full width (`admin.css:22`) and a written 20px below 900 pixels (`admin.css:1506`), and the
   other pages' `.container` writes its 20px too (`style.css:153`). So the value stays and is written as a sum of
   two tokens. If a sum is not wanted, the alternative is to leave the two names as plain lengths.
4. **The two font files keep the brand's file names** and are its Latin subsets (232 and 229 characters). The five
   files the Platform had hold 895 (sans) and 1,049 (mono): Latin Extended, Greek, Cyrillic, arrows. Each new face
   therefore has a `unicode-range` with the characters both new files hold (read from the files' own character
   tables with a script, in the evidence folder), so a character outside it, say "ř" or "→" in bold, keeps the 600
   face of the same family as before and does not fall to a system font. The brand's own `knovas-fonts.css`
   declares no `unicode-range`; there every file is the same subset. No test holds the range against the files
   (reading a WOFF2 needs a library the Platform does not have).
5. **The styleguide page stands in the frame the other pages have** (sidebar, `.container`), and its own layout
   rules are a `<style>` block in the template: this item may add no stylesheet and no rule to `style.css`. The
   block uses tokens only (a test of mine checks it). Every name and value on the page is read per request from
   `knovas-tokens.css` and from the `:root` block of `style.css` (`shell_routes.styleguide_sections`); a name the
   files do not declare raises, and `test_the_page_fails_when_a_shown_token_leaves_the_tokens_file` holds that.
   Beside what the work order lists, the page has a table "Die Namen der Plattform": the Platform's 23 names with
   the token each takes.
6. **The navigation on `/styleguide` is the one every page has**, with its Feedback entry (the order's change 2).
   In the first round the route left the entry out, because the frozen check then refused every outside `href`;
   that line of the route is gone.
7. **The gate is written out in `shell_routes.py`** in the words of the console's `require_admin`. That one is
   built inside `create_admin_blueprint` and cannot be imported, and `admin.py` is not this item's file. Two copies
   of one rule (review entry on the card).
8. **The route exists only with accounts on** (the same `if identity_gate is not None` as the console): one shared
   login knows no administrator.
9. **The card.** The code reader shows two operations; both are listed. `GET /styleguide` has its line as a need
   (`N-1`), as the console's routes have theirs. `create_shell_blueprint` has no line, as the console's factory has
   none (review entry). I added `R-3` (the styleguide) and reworded `R-2`.

### Mono for codes (the order's change 1, second round)

The first round stopped here: codes, ids and file names took the mono face through `var(--font-heading)`, the
frozen test pins that name to the sans, and no rule could be rewritten. The order now allows the one word. In 16
rules `var(--font-heading)` became `var(--font-mono)`; nothing else in them changed. For each rule I looked at what
the templates and scripts put into it.

| Stylesheet | Changed | The rules (line, what the rule sets) |
|---|---|---|
| `style.css` | 1 | 1441 `.settings-list dd.mono` (build id, asset version) |
| `admin.css` | 6 | 403 `.admin-pre` (a command in a `<pre>`); 425 `.danger-zone input[type=text]` (the tenant id, typed to confirm); 434 `.danger-zone code` (the tenant id); 533 `input.folder-path` (folder paths); 826 `.ptr` (pointers); 925 the chips' rule `.role-chip, .access-chip, .document-status` (role ids, access-group ids, a document's status code) |
| `views.css` | 2 | 331 `.views-diff` (a diff); 429 `.views-docs-box-key` (a box's key) |
| `experiments.css` | 6 | 48 `.kx-mono` (keys, ids, commit hashes); 400 `.kx-code`; 699 `.kx-table a.kx-key-link, .kx-key` (experiment keys); 807 `.kx-pre`; 829 `.kx-markdown code`; 1621 `.kx-token-box code` (a token) |
| `custom_views.css` | 1 | 161 `.cv-mono` (a view's id) |

Two corrections to my first list. Its entry `admin.css:925 .document-status` is a rule with three selectors; the role
and access-group chips are in it, and they hold ids, so the rule changed as one. (`.access-chip` also carries two
words, "offen" and "besonders schützenswert".) And the list said 27 rules "set codes, ids and file names"; looked at
one by one, 11 of them do not:

**Left with `--font-heading`, 11 rules.** What they set is no code, id, file name or diff, and no heading either.
They are in the sans now. Say if any of them should be mono.

| Rule | What it sets |
|---|---|
| `style.css:532` `.document-metaline` | the small label line of a search hit: the format and the date, or "Experiment", its domain and status |
| `style.css:1406` `.app-avatar`, `admin.css:1063` `.person-avatar` | an initial in a circle |
| `style.css:1433` `.settings-list dt`, `experiments.css:1164` `.kx-fieldlist dt` | the labels of a list of facts |
| `admin.css:366` `.check-ms` | a duration in milliseconds |
| `views.css:49` `.views-asof` | the time the data is from |
| `views.css:309` `.views-tile-count`, `experiments.css:572` `.kx-count` | a count |
| `experiments.css:278` `.kx-chip--lang` | an evaluator's language, or "eingebaut", as a chip |
| `experiments.css:293` `.kx-tag` | keywords (Schlagworte); in two tables group names |

The other 25 rules with `var(--font-heading)` in these five stylesheets set headings, titles and navigation; they
keep it. The mono face also stays where a rule names it itself: `style.css:1166` (`.preview-body code`),
`admin.css:1884`, `ontology.css:1729` and `1754`. `test_shell_tokens.py` holds the 16 rules by their selectors (19)
and five headings.

### Left as they are (the coordinator records both as follow-ups)

1. **The Cortex page keeps its own colours and type.** `ontology.css:91-109` declares eleven of the Platform's
   names again for that page (`--primary-color: #2859cd`, `--accent`, `--highlight`, `--callout`, the text colours,
   border, card, sunken surface, and fonts that are not installed: Inter and SFMono). Before and after differ in
   1.5 % of the pixels there.
2. **The Builder's pages do not load `style.css`**; they have tokens of their own (`css/builder/tokens.css`, Royal
   Blue `#1A45C7`). Before and after are the same picture, pixel for pixel.

### Clashes: a rule's own colour beside the brand's (for PMVP-12 and PMVP-40)

Line numbers at the branch's head (second round). "Old" means a value of the palette `style.css` had before.

**`style.css`** (18 of its 28 literals are not the brand's)
- toasts in the old palette: 1259-1261 `.toast--error`, 1265-1267 `.toast--success`, 1271-1273 `.toast--info`
  (`#12308C` on `#E9EDFB`, a blue beside the new one)
- old green and red as text: 1942 `.platform-settings-ok` `#1a6b4a`, 1943 `.platform-settings-error` `#be123c`,
  672 `.badge-error` and 783 `.login-error` `#991b1b`, 782 `#fecaca`
- 491 `.document-thumb::after`: a fade to `rgba(253, 253, 253, 0)`, the old off-white, on a page that is now white
- shadows that are not the brand's navy tint: 846, 1250 `rgba(7, 23, 45, …)`
- 1948 `.sync-table` `#e5e7eb` (a neutral grey; the brand's are cool)
- not a clash, but literals: 1494-1496 `.admin .tabs a` write `#475569`, `#0f172a`, `#e2e8f0`, which are the
  brand's gray-600, gray-900 and gray-200

**`admin.css`** (53 of 57), for PMVP-40
- the console's own frame in the old ice blue, beside the new sunken surface `#F8FAFC` and border `#E2E8F0`: 600-601
  `.admin-page .app-sidebar`, 626 `.app-nav-item.active`, 768, 780, 786-787, 794 (table), 803, 811 (selected row),
  876-877 (select), 966, 968 `.admin-toolbar`, 1042, 1082, 1108 (person panel), 1343, 1345, 1383, 1385, 1427
- chips and bars in old blues: 933 `.role-chip`, 937-939, 944 `.access-chip` (`#1946A8`), 1209, 1211
  `.document-bulk-bar`, 1258 `.choice-chip`, 1670 `.knovas-admin-badge`
- tints of the old green `26, 107, 74` beside the new `--success-color` `#15803D`: 164, 291-292, 327, 393, 950
- tints of the old red `196, 30, 30` beside the new `--error-color` `#B91C1C`: 165, 270, 272, 285-286, 329, 395,
  415-416, 423, 430
- 15-16 `--admin-warn` `#8a5a00` (the brand's warning is `#B45309`); 568 `#5A6B80` (the old `--text-secondary`)
- shadows: 1143, 1147 `rgba(18, 35, 70, …)`

**`views.css`** (24 of 32)
- old Royal Blue: 423, 425 `#1a45c7` (`.views-docs-box`), 346 `#1a3a8a` on `#e5edff`, 424 `#eef2ff`
- old green 349, 415 `#1a6b4a`; ambers 333, 355, 398, 409-410, 428; reds 215, 239, 408
- neutral greys 432-434 `#6b7280`, `#374151`, `#f6f7f9`; 387 a black shadow `rgba(0,0,0,.08)`

**`experiments.css`** (21 of 22)
- 794-796 `.kx-banner--info` (the old blue `#12308C`), 296 `.kx-tag` `#E7EBFA`
- 12-15 `--kx-warn`, `--kx-good-rgb` (`26, 107, 74`), `--kx-bad-rgb` (`196, 30, 30`): the old green and red as
  triples, beside the new `--success-color` and `--error-color`
- 20-27 the eight chart series colours (a palette of its own)
- 850, 855, 1347 shadows; 1277, 1380 `#E6E9EF`

**`ontology.css`** (115 of 150): the block 91-109 above, and literals through the whole file (98 blues).

**Seen on the pages, not a literal colour**
- `.btn` is fully round (`border-radius: 999px`, `style.css`); the reference's buttons have 8px.
- Headings take `--text-primary`; the reference's are navy (`--k-color-text-strong`). The Platform has no name for
  it (`.site-greeting` asks for `--title-color`, which nothing declares).
- Found-passage marks are lighter than before: the soft accent on white is about `#EAEDF9` (1.17 to 1 against
  white), the old `#D9E0F7` was 1.32 to 1.
- `--text-muted` on a `--highlight` fill is 4.07 to 1, below 4.5 (the old pair was 4.14 to 1). The frozen test
  checks the three backgrounds, not this fill.
- With accounts on, the sign-in page's first field is `type="email"`, which `.login-form input[type="text"]` does
  not match: it has the browser's look. Before and after.
- At 390 pixels these scroll sideways, before and after alike: search results (423), Personen (537), settings
  (530), System (597; 537 in the first round, while its `<pre>` was in the sans) and Ansichten. No page scrolls
  sideways that did not before; `/styleguide` does not.

### Literal colours per stylesheet

Counted with a script (in the evidence folder): hex values, `rgb()`/`rgba()`/`hsl()` with numbers of their own, colour
keywords, and a custom property that holds an `r, g, b` triple; comments left out.

| Stylesheet | Before | After | Not the brand's |
|---|---|---|---|
| `style.css` | 42 | 28 | 18 |
| `admin.css` | 57 | 57 | 53 |
| `views.css` | 32 | 32 | 24 |
| `ontology.css` | 150 | 150 | 115 |
| `experiments.css` | 22 | 22 | 21 |
| `custom_views.css` | 0 | 0 | 0 |
| `builder/builder.css` | 0 | 0 | 0 |
| `builder/tokens.css` | 87 | 87 | 84 |
| `knovas-tokens.css` (the brand's file, where colours belong) | | 76 | |

`style.css` lost the 14 hex values of its `:root` block; the two triples there are still counted.

### Evidence

Playwright is not installed here (`ModuleNotFoundError: playwright`), and I installed nothing. The screenshots were
taken with the installed Chrome, headless, driven over its DevTools protocol by a script (`shoot.py`, with the
`websockets` module that was already there), against the real app with accounts on and a fake Knovas (`serve.py`,
which builds the app the way `tests/conftest.py` does; invented people and documents). Before is `b9f31b7`, after
is this branch.

Folder, outside the repository: `%TEMP%/claude/1838582cff9d51e1/af9716ae-0421-42ac-9fac-6acb192cc6e6/scratchpad/pmvp-10-evidence` (`screenshots`, `reports`, `tools`)

It is a temporary folder of this session: a restart of the machine or a clean-up can clear it. Copy it if it is to
be kept.

- `before-*.png` and `after-*.png` at 1440 and 390: `login`, `search`, `search-results`, `admin-people`,
  `admin-system`, `settings`, `builder`, `cortex`, `views`, `styleguide` (before: the 404), `styleguide-member`
  (after: the 403). Whole page, not only the window.
- `before-facts.json`, `after-facts.json` (and two more for Cortex and Ansichten): per page the computed fonts and
  colours, the fonts loaded, the page's width, every request that failed. After: none but the member's 403 on
  `/styleguide` and, before and after alike, a 500 the fake Knovas gives Cortex's summary.
- `colours.py`, `clashes.py` and their output; `woff2cmap.js` and its output; the map-check reports; the two suite
  runs.

Second round, `after2-*.png` at 1440 and 390: `admin-people` (the role chips), `admin-system` (the `<pre>`),
`settings` (the build id), `search-results`, `styleguide` (the sidebar with its Feedback entry) and
`views-diff-sample`. I could not reach the diff on its real screen: the change-request screen of Ansichten needs a
definitions bundle and a repository, which the screenshot server's fake Knovas does not give. `views-diff-sample`
is `/views` with one `<pre class="views-diff">` put into the page by the screenshot script, drawn by the page's
own stylesheets; a line on the picture says so. The second round's facts file has the computed face per element:
IBM Plex Mono for `.role-chip`, `.admin-pre`, `.settings-list dd.mono` and `.views-diff`, IBM Plex Sans for the
ones left.

What I saw in the first round: all seven faces load on `/styleguide`. On the pages that take the base styles'
names (sign-in, search, the console, settings, Ansichten, the styleguide) body text and `h1` compute to IBM Plex Sans and the body colour to
`rgb(30, 41, 59)`; before, `h1` was IBM Plex Mono on sign-in, search, settings and Ansichten. The pages' layout did
not move. Cortex computes to its own (`Inter`, `rgb(32, 40, 59)`), the Builder to its own, both as before.

### Checks

Second round, at `98de30e` (the head before these notes), in this worktree.

- **Frozen.** `tests/test_pmvp10_brand_acceptance.py`, the coordinator's changed file: 33 passed, none skipped
  (database `knovas_platform_test_pmvp10`, `PLATFORM_DB_REQUIRED=true`). The check of `frozen-tests.txt`: five
  lines, all OK, with the file's new blob id.
- **The builder's tests.** 102 passed: `test_shell_tokens.py` 88, `test_shell_styleguide.py` 14.
- **The whole suite** (`py -3 -m pytest -q -p no:cacheprovider --continue-on-collection-errors`):

  | | Passed | Failed | Errors | Skipped | xfailed, xpassed | Files that do not collect |
  |---|---|---|---|---|---|---|
  | base | 6,243 | 137 | 6 | 40 | 8, 1 | 4 |
  | first round, tree `8a6de78` | 6,349 | 107 | 6 | 40 | 8, 1 | 4 |
  | second round, `98de30e` | 6,375 | 107 | 6 | 40 | 8, 1 | 4 |

  Every failing id of the second round fails at the base too. The 30 ids that left are the frozen file's (3 of its
  33 pass at the base). No new failing id. The 132 more that pass are those 30 and the builder's 102. What still
  fails does so on all three runs, in 18 files (the largest groups: `test_experiments_frontend.py` 23,
  `test_github_write.py` 20, `test_sync_component_github_v2.py` 10, `test_builder_assistant.py` 9); I did not look
  into why, beyond `test_german_text.py` (no `snowballstemmer` on this machine). The six errors are
  `test_experiments_schema.py::test_deep_nesting_in_text_is_a_validation_error`. The four files that do not collect:
  `test_pmvp03_harness_acceptance.py` and the two PMVP-20 frozen files (expected), and
  `test_experiments_runner_client.py` (an `AttributeError` on `socket`). The id lists are in the evidence folder
  (`reports/base-failing-ids.txt`, `after-failing-ids.txt`, `after2-failing-ids.txt`).

  The base: the whole suite ran at `b9f31b7`, before any change. The base is now `1268019`; between the two,
  nothing under `KnovasPlatform/` changed but the frozen file. I ran that file alone at `1268019` (30 failed, 3
  passed, the same 30 ids) and did not run the other 6,402 tests a second time.
- **The card.** Unchanged in the second round. `card check`: 15 cards, 0 errors, 0 warnings. `card render --check`:
  0 not current. `map-check --scope touched`, base `origin/feat/platform-mvp` (`1268019`), head `98de30e`:
  problems 0, declared 0. The scope is now two cards, because `admin.css` is the Admin card's file. Findings inside
  the scope: `need_added` on `L2-PLATFORM-SHELL/http.GET.styleguide/N-1` (the new operation's own line; it has no
  consumer) and `no_contract` on `call.web_interface.shell_routes.create_shell_blueprint` (decision 9); and three
  of the Admin card that PMVP-01 listed and this item did not cause: `over_budget` (24,232 tokens) and
  `no_contract` on its two calls. Reported beside them: `app.py` touched without a card (the wiring; the web app
  has none). Outside the scope: 5 cards over budget and 320 from `complete`. Run from a KnowledgeBase checkout's
  `scripts/`, read only; nothing reached a tenant.
- The test databases are dropped, and the two temporary checkouts in the scratchpad are removed.

### Open, for the coordinator

- The 11 rules left with `--font-heading` ("Mono for codes"): say if any should be mono.
- Decision 3 (the sum for the 20px).
- The whole suite's base: see Checks.
- A trap for whoever shares a test database: the migrations put the `citext` extension into the first schema that
  asks. A process that is killed (not stopped) leaves its schema behind with the extension in it, and every later
  test in that database fails with `type "citext" does not exist` until the schema is dropped. It happened to me
  once in my own database.

## Verification (coordinator's record)

- **2026-11-21, fresh verifier session, `feat/pmvp-10` @ `5536443`: PASS WITH NOTES.** Frozen check 5 of 5; the brand
  file 33 passed, none skipped; the builder's shell tests 102 passed. Scope: 17 files, all allowed; the 16 rules of
  the second round changed one word each and set codes, ids, paths or a diff. All 22 old variable names kept, 20
  through `var(--k-...)`; tokens and fonts equal the reference; nothing leaves the installation. `/styleguide`:
  signed out 302, member 403, approver 403, administrator 200, POST 405. Eleven pages at both widths keep their
  widths and are drawn in IBM Plex. Card checks clean. A reduced run of 2,595 page and style tests: every failure
  is on the base too.
- Integrated into `feat/platform-mvp` by the coordinator (merge `863cee3`). The whole suite is run by the
  coordinator against `baseline-failing-ids.txt` once PMVP-11 and PMVP-12 are in as well.
- Carried forward: `docs/platform-mvp/follow-ups.md` FU-28 to FU-30.
