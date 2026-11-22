# Work order PMVP-32: the process line for one thing (32a: the line; 32b: what is missing, and the others in it)

**Written 2026-11-22 from the decisions in `docs/platform-mvp/decisions/PMVP-32-question.md`** (founder: option A,
measured on Knovas GmbH's own processes; coordinator: points 2 to 5). About 8 points, so it is cut in two:
**PMVP-32a** (5) and **PMVP-32b** (3), built one after the other by builder B after PMVP-30b.

| | |
|---|---|
| Item | PMVP-32a and PMVP-32b (Jira: filed by the coordinator), plan `FEAT-2026-11-19-platform-mvp` § 4 **B-06** ("main path first; variants open on request; about twelve steps per line"), B-01, B-02; spec item SPEC-platform-mvp-11; contract L2-PLATFORM-BRAIN-VIEWS RESP-01 ("one process instance as a bounded view"), PRE-01, POST-01, POST-03 |
| Follow-ups | **FU-19** closes: the existing Ablauf code (`graph_views/`) is not reused and gets no owner here. FU-34 and FU-26 apply |
| Class | **routine** (Jannik or Siran): reads and a page, as the person, behind the existing switch |
| Depends on | PMVP-30a (integrated); PMVP-34 (`limit.py`, the marks, the frozen `test_pmvp34_view_limit_acceptance.py`); PMVP-30b (the dossier's tabs and its neighbours read, which 32a uses for the dossier's card) |
| Branch / worktree | `feat/pmvp-32a` from `origin/feat/platform-mvp` once PMVP-30b is integrated, otherwise from `origin/feat/pmvp-30b` (merge `origin/feat/platform-mvp` in once 30b is). `feat/pmvp-32b` from `feat/pmvp-32a` in the same way. Worktrees `.worktrees/pmvp-32a`, `.worktrees/pmvp-32b` |
| Visual contract | `docs/platform-mvp/reference-screens/03-ablauf.html` and `out/03-ablauf.png`: the eyebrow, the thing's name as the title, the lede «Ablauf «…» · Schritt n von N»; the steps in a line (done ✓, the current one marked, the open ones); the card «Jetzt: <step>» with «seit …» and the checklist with «fehlt noch» (32b); «Danach: <next step>»; «Alle N … in diesem Ablauf →» (32b). On the dossier, the card «Läuft gerade» of `02-dossier.html` (32a). The reference's words are a law firm's; the pilot's data are Knovas GmbH's (founder) |
| Card | `docs/components/L2-platform-brain-views.card.yaml` (its page, `boundaries.yaml`) |
| Core requests | None. CR-1 and CR-2 are not used. Until CR-R1 every person sees every step and instance (the tenant is «Aus») |
| Frozen tests | none of its own (routine); PMVP-34's frozen `tests/test_pmvp34_view_limit_acceptance.py` must pass with the new pages, which it finds by itself |

`P` = `KnovasPlatform/components/docbridge_integration`.

## The data this reads (the Client Zero convention, seen on the Knovas GmbH tenant on 2026-11-22)

- A **process** is a node of type `Process` (e.g. «Feature lifecycle», key `PROC-feature-lifecycle`). Its **steps**
  are nodes of type `ProcessStep`, joined to it by `has_step`. A step's facts include `order` (as text, e.g. `"6"`),
  `owner_role`, `requires` and `produces` (JSON lists of `{type, required, min_count, path: [{relation, direction,
  to_type}]}`). Its name repeats the process's name («Feature lifecycle: approved»).
- Steps are joined by `transitions_to` (forward) and `reworks_to` (back).
- An **instance** (e.g. a feature plan) is joined to its process by `instance_of` and to its current step by
  `in_state`. Its facts include `current_step`, `step_history` (JSON `[[step, from date, to date or null], …]`) and
  `owner` (a key, e.g. `PERS-siran`).
- **An edge stores its two ends in id order** (`node_lo`, `node_hi`), **not in its direction**. The direction comes
  from the steps' `order`: a `transitions_to` goes from the lower order to the higher, a `reworks_to` from the higher
  to the lower. If an edge contradicts that, or a step has no usable `order`, show no line («Der Ablauf ist nicht
  vollständig beschrieben.») and log the process's id and the reason code.

## PMVP-32a: the line

### Goal

From a thing that is in a process, a person sees one line: where it stands, since when, and what comes next. A detour
opens on request. The dossier shows «Läuft gerade» for such a thing, and the card opens the line.

### What to build

1. **The read**, `P/src/brain_views/process_line.py` (new): `read_line(client, node_id) -> Line`, as the person, per
   thing:
   - the instance: `graph_node(node_id)` (name, kind, facts) and `graph_neighbors(node_id, depth=1,
     include_edges=True)` for its `instance_of` (the process) and its `in_state` (the current step). Neither of
     them, or more than one of either: "not in a process" (item 4);
   - the process's steps and the edges between them: **first** `GET /secured/graph/edges?node_id=<process>&type=
     has_step` through `graph_get_many` (one round, with `type=transitions_to` and `type=reworks_to` for each step,
     at most 25 steps). Then the steps' facts with one `graph_facts_batch`.
     **Fallback**: if `/edges` answers 404 (or 400, 405 or 501), **or** if any row it returns does not touch the
     process (or that step) or has another relation (a Core that ignores the filters would hand back the tenant's
     edge list), throw that answer away and use `graph_neighbors(<process>, depth=1, include_edges=True)`, keeping
     the `ProcessStep` neighbours and the edges among them. Say in your notes which path your fake Core's contract
     takes, and why. **Never call production to find out** (coordinator's decision 5);
   - kind names through one `graph_get_many` round of `/node-types/<id>` (as the dossier reads them), to recognise
     `Process` and `ProcessStep` by name;
   - never `graph_export`, `graph_edges()` without a node, `graph_nodes` or `graph_node_types` (POST-03).
2. **The main path and the variants** (coordinator, point 3). Sort the steps by `order`. The main path starts at the
   lowest order; from each step on it, the next is the `transitions_to` target with the **smallest order greater than
   its own**. It ends where there is none. Every other step and every `reworks_to` is a **variant**.
3. **The page** `GET /ablauf/<node_id>` (`brain_views_routes.py`, `templates/ablauf.html`, from the parts):
   - eyebrow: the thing's kind; title: its name; lede «Ablauf «<process name>» · Schritt n von N», where n is the
     current step's place on the main path and N the main path's length;
   - the line: the main path's steps, each named without the «<process name>: » prefix, marked done (before the
     current one), current, or open, **about twelve per line** (B-06): more than 12 main steps wrap to a second
     line. Everything drawn goes through `limit.bounded` (the thing and the steps, at most 25) and carries
     `data-bv-node`;
   - the card «Jetzt: <step>» with «seit <date>» from the last open entry of `step_history` (else the `current_step`
     fact's `valid_from`). **Who:** the `owner` fact only when it is not a key (`^[A-Z]+-`); a key is left out (a
     lookup by key is not offered per thing). The step's `owner_role` is not shown;
   - «Danach: <next main step>», or nothing at the end of the main path;
   - **variants on request**: «Andere Wege» (a `<details>`): the variant steps reachable from the current step
     (`transitions_to` off the main path, `reworks_to` back), as rows (`data-bv-row`);
   - a current step that is itself a variant (e.g. «killed»): the main path is drawn with no step current and a line
     «Dieser Eintrag hat den Hauptweg verlassen: <step>.»
4. **Not in a process**: a thing with no `in_state` or no `instance_of` gives a page «Für diesen Eintrag ist kein
   Ablauf hinterlegt.» (200), not an error. A hidden or missing thing gives the same 404 as the dossier.
5. **The dossier's card «Läuft gerade»** (`dossier.html`): when PMVP-30b's neighbours read shows an `in_state` edge
   to a `ProcessStep` neighbour, the «Überblick» shows a card «Läuft gerade · <step name without the prefix>» that
   links to `/ablauf/<node_id>`. It costs no further read and carries `data-bv-node` (it stands for a thing).
6. **The card**: the operation `GET /ablauf/<node_id>` with PRE-01, POST-01 (PMVP-34's frozen test as evidence),
   POST-03; the convention above (direction from `order`, main path, variants) written into R-lines; `boundaries.yaml`.

### Your tests (`P/tests/test_brain_views_process_line_*.py`)

Build the fake Core from the shapes above (as the tenant holds them; copy no real names into the tests). Then:

- signed out 302, switch off 404, a hidden thing 404;
- the main path: a process of 11 steps with jumps (2→6), a branch (6→11) and reworks (8→7, 6→2) gives the 10
  main steps in order, «Schritt 6 von 10» for an instance in step 6, «Danach» = step 7; the reworks and step 11 are
  variants;
- direction from `order`: the same edges stored with `node_lo` and `node_hi` swapped give the same line;
- an edge against the order, or a step without `order`: «nicht vollständig beschrieben»;
- «seit» from `step_history`, then from `valid_from`; an `owner` key is not shown;
- the `/edges` route: honoured, it is used; a 404 falls back to the neighbours; **an answer with an edge that does
  not touch the process is thrown away** and the neighbours are used;
- not in a process: the plain page; the dossier's «Läuft gerade» only for a thing with `in_state`, linking to its
  line;
- 30 main steps: at most 25 `data-bv-node`, wrapped at about 12 per line;
- no whole-tenant call; escaping of every name; logs at DEBUG (FU-46) carry no name or fact.

### Files you may change (32a)

`P/src/brain_views/process_line.py` (new); `P/src/brain_views/related.py` (only to expose the `in_state` neighbour
it already reads); `P/src/web_interface/brain_views_routes.py`; `P/src/web_interface/templates/ablauf.html` (new);
`templates/dossier.html` (the card only); `static/css/brain_views.css` (tokens only); your tests; the card, its page,
`boundaries.yaml`; `management/brain-views.md` and its row in `management/README.md`; the notes below.

### Done when (32a)

1. Your tests green; `tests/test_brain_views_*.py` and the frozen `tests/test_pmvp34_view_limit_acceptance.py` green;
   the frozen check all OK. **Do not run the whole suite.** Own test database `knovas_platform_test_pmvp32a`.
2. Screenshots at 1440 and 390 px beside `03-ablauf.png` and `02-dossier.png` (the card), from a fake Core shaped
   like «Feature lifecycle» (outside the repository; say where). Nothing scrolls sideways.
3. Card checks clean. Commits `PMVP-32a: …`, ending with the co-author line; `feat/pmvp-32a` pushed. No pull request.

## PMVP-32b: what is missing, and the others in the same process

### What to build

1. **«fehlt noch»** on the card «Jetzt» (coordinator, point 4): the current step's `requires` entries **whose path is
   one hop**. For each, count the instance's neighbours (from 32a's read) over that `relation` whose kind is
   `to_type`. The `direction` of the hop cannot be checked (edges keep no direction), so it is ignored; say so on the
   card. Present ones are listed (names, links to their dossiers, rows `data-bv-row`); an entry with fewer than
   `min_count` gets «fehlt noch» (`tag(kind="warn")`) and its `type`; a `required: false` entry that is missing is not
   marked. Entries with longer paths are left out, with one line «Weitere Voraussetzungen werden hier nicht geprüft.»
   No documents (SPEC-11's "missing document" waits for the law-firm data, option C).
2. **«Alle N … in diesem Ablauf →»**: the count of the process's `instance_of` edges (from the `/edges` route, or the
   neighbours fallback, as in 32a item 1), with `data-bv-more="<N − 1>"`, opening `GET /ablauf/<node_id>/alle`: the
   other instances as a list (names, links to their own line), sorted by name, filterable, 50 a page
   (`limit.paged`). No per-instance read: their current steps are not shown.
3. The card's lines for both.

### Your tests (`P/tests/test_brain_views_process_line_*.py`, more of them)

A step requiring two kinds over one hop, one present and one missing: one row and one «fehlt noch»; `min_count` 2
with one present: «fehlt noch»; `required: false` missing: no mark; a two-hop entry: left out, and the line shown;
the list: count, `data-bv-more`, paging, filter, escaping; PMVP-34's frozen test follows the new link and passes.

### Files you may change (32b)

As 32a, without `related.py` and `dossier.html`.

### Done when (32b)

As 32a, with the database `knovas_platform_test_pmvp32b` and commits `PMVP-32b: …` on `feat/pmvp-32b`.

## Files you must not change (both)

The frozen files (PMVP-34's among them), `P/tests/conftest.py`, `P/src/brain_views/limit.py`, `dossier.py` (except a
shared helper moved without changing its behaviour), the parts, `_sidebar.html`, `app.py`, `platform_settings.py`,
`knovas_client.py` (if a per-node read is missing, stop and say which), everything under `P/src/graph_views/` (the
old Ablauf: not reused, FU-19), `P/src/assistant/`, `P/src/identity/`.

## Open, for the coordinator

- Whether production's `GET /secured/graph/edges` honours `node_id` and `type` is not known. The guard in item 1
  throws a wrong answer away, but such an answer would still be the whole tenant's edge list on the wire once per
  view. Someone with production access should check it before the pilot.
- The Knovas GmbH tenant names its kinds in English (`Process`, `Decision` …); the line's eyebrow shows the kind as
  the Brain names it (PMVP-30b's tabs take plurals from its table).

## Builder's notes

### 32a

Builder session of 2026-11-22 (builder B, AGENT-claude-code). `feat/pmvp-32a` from `feat/pmvp-30b` (`330ca0fe`;
30b was not yet integrated), `origin/feat/platform-mvp` merged in before the push.

#### What was built

| File | What |
|---|---|
| `src/brain_views/process_line.py` (new) | `read_line(client, node_id) -> Line`: the reads (below), the main path, the variants, «seit», who; `main_path`, `step_name` |
| `src/web_interface/process_line_routes.py` (new) | `attach_process_line_routes`: `GET /ablauf/<node_id>`; what is drawn (`limit.bounded`), rows of about twelve (`rows_of`), German dates |
| `templates/ablauf.html` (new) | the page from the parts: head, the line (`<ol>` rows, `data-bv-node` per step), «Jetzt» (a `now` card) with «seit · who», «Andere Wege» (`<details>`, rows `data-bv-row`), «Danach», «Zum Dossier»; the main path as a list (`?ansicht=schritte`) behind the count when more than 24 steps |
| `templates/dossier.html`, `src/brain_views/related.py` | the card «Läuft gerade» (`data-bv-node`): `Related.in_state`, `Related.process` and `Related.step_name` from the neighbours already read |
| `static/css/brain_views.css` | the card, the line (upright on a phone), «Jetzt», «Andere Wege»; tokens only |
| `tests/test_brain_views_process_line_read.py` (29), `tests/test_brain_views_process_line_page.py` (20) | below |
| **the card split** (the coordinator's message of 2026-11-22) | see "The split" |

#### The reads, and which path my fake Core takes

1. `graph_node(id)` (via `brain_views.reads.node_payload`: the same NotFound/404 as the dossier), then
   `graph_neighbors(id, depth=1, include_edges=True)`: exactly one `instance_of` and one `in_state` edge to a row of
   the answer, or "not in a process".
2. One kind round (`/node-types/<id>`): the thing's own kind, the process's and the step's, and the neighbours' most
   frequent kinds up to 20 (so 32b's «fehlt noch» needs no new kind read). Not `Process` / `ProcessStep` by name:
   "not in a process". A failed (non-404) read of the process's or step's kind: Unavailable (502).
3. **The edges route first**: `GET /edges?node_id=<process>&type=has_step`; then one round with, per step,
   `…&type=transitions_to`, `…&type=reworks_to` **and `GET /nodes/<step>`**. The last is mine: no edge row carries a
   name, and the order lists none of the reads that would. It is a per-thing read as the person; a step that answers
   404 there is left off the line. **Fallback** to `graph_neighbors(<process>, depth=1, include_edges=True)` (the
   steps are the neighbours of the current step's kind, the edges those among them) when any `/edges` answer is 400,
   404, 405 or 501, when any row does not touch the node asked about or has another relation (a Core ignoring either
   filter), or when the process has more than 25 steps (the second round would exceed 25 steps' reads; a 30-step
   process is drawn through the fallback). Any other failure: Unavailable.
4. `graph_facts_batch(steps)` (the Core answers `facts_by_node`): `order` by the fact's label.

**My fake Core** (`ProcessCore` in the read tests) **honours both filters**, as KnowledgeBase master's
`graph_api.list_edges` does (`repo.list_edges(client_id, node_id=…, relation=request.args.get("type"))`); that is the
contract the coordinator named. The tests also run it answering 404 and ignoring the filters (the shape of
`tests/fake_brain_api.py` and `views_fixture.py`, which return the whole edge list): both fall back, and the
ignoring one is thrown away after its first answer. **Nobody called production.**

#### What I decided, and why (the verifier should look here first)

1. **"Not completely described"** (reason codes in the INFO log line with the process's id): `order_missing` (no
   `order` fact, or not a whole number: `"vier"`, `"4.5"`, `true`), `order_tie` (two steps of one order: the main
   path's "smallest order above" would be ambiguous and an edge between them could not be oriented),
   `edge_against_order` (an edge whose two ends are one step, or two steps of the same order), and
   `current_step_not_in_process` (the `in_state` step is not among the process's steps). An edge stores no
   direction, so "an edge against the order" can only be one that cannot be oriented; I found no other reading.
2. **The variants shown** («Andere Wege») are the ways from the current step: its `transitions_to` targets other than
   the next main step («Abzweigung») and its `reworks_to` targets («zurück»). Variant steps not reachable from the
   current step are not listed (the order says "reachable from the current step").
3. **Off the main path** (e.g. a «killed» step): every main step is drawn plain (no «done» either: which steps were
   done before leaving is not known without matching `step_history` keys to steps), «Jetzt» names the step, no
   «Danach», and the line «Dieser Eintrag hat den Hauptweg verlassen: …».
4. **More than 24 main steps**: the drawn steps are a window of 24 that holds the current step (centred where it can
   be), with the thing's head 25; the count (`data-bv-more`, «6 weitere») opens `?ansicht=schritte`, the whole main
   path as rows. Rows of about twelve: `ceil(n / 12)` rows as even as they go (13 → 7 + 6; 24 → 12 + 12).
5. **«seit»** is a date («seit 16. Nov. 2026»), not «seit 6 Tagen»: the order says «seit <date>», and a relative day
   count would change with the clock in tests and screenshots.
6. **Who**: the `owner` fact when it is not a key (`^[A-Z]+-`). Knovas GmbH's owners are keys (`PERS-…`), so the
   pilot shows no name (the order's rule; a key lookup is not offered per thing).
7. **«Zum Dossier»**: a link back on the line page (not in the reference; a way back from the line to the thing).
8. **Facts by label**: the steps' `order` and the instance's `current_step`, `step_history`, `owner` are read by
   their fact `label` (the tenant's facts carry one; seen read-only on Knovas GmbH's «Feature lifecycle: approved»:
   `order` "6", `requires` / `produces` JSON lists).

#### The split (the coordinator's message: every card within budget, map-check clean)

`L2-PLATFORM-BRAIN-VIEWS` was 17,839 tokens of 16,000 after 30b; with the process line it would have been about
28,000. It is now three cards, as RB-FIX-1 split Rollen and Agenten; every moved line keeps its old id as its first
alias, no line or evidence was dropped:

| Card | Holds | Tokens |
|---|---|---|
| `L2-PLATFORM-BRAIN-VIEWS` «Brain views» | `brain_views/__init__.py`, `limit.py`, **`reads.py`**, **`lists.py`** (both new), `brain_views_routes.py` (the blueprint, the switch, the gate), `brain_views.css`; the `*` lines (PRE-01, POST-01, POST-03); the eleven shared functions the other two cards call, as call operations, each with its line and tests | 9,157 |
| `L2-PLATFORM-DOSSIER` «Dossier» (new) | `dossier.py`, `related.py`, **`plurals.json`** (new), **`dossier_routes.py`** (new), `dossier.html`; the dossier's two routes; POST-02 | 14,499 |
| `L2-PLATFORM-PROCESS-LINE` «Ablauf» (new) | `process_line.py`, `process_line_routes.py`, `ablauf.html`; `GET /ablauf/<node_id>` with N-1, N-2 (PRE-01), P-1 (POST-03), P-2 (POST-01, the frozen test), P-3 (the convention), P-4, P-5 | 11,345 |

Code moves **without a change of behaviour**, outside 32a's file list because the split needs them:
- the dossier's routes, `open_target`, `_page` and the list helper from `brain_views_routes.py` into
  `dossier_routes.py` (`attach_dossier_routes`); `brain_views_routes.py` keeps the factory, the switch and the gate and
  attaches both views' routes to the one blueprint (endpoints stay `brain_views.*`);
- `NotFound`, `Unavailable`, `valid_node_id`, `valid_type_id` and the node read from `dossier.py` into
  `brain_views/reads.py` (the order allows "a shared helper moved without changing its behaviour"); `dossier.py`
  imports them back, so every old import still works;
- the lists' helpers (`sort_key`, `filter_text`, `matches`, `sort_choice`, `arrange`, the sort keys and labels) from
  `related.py`, and the list builder from the routes, into `brain_views/lists.py` (`listing` takes an `href` the route
  builds with `url_for`, so the module imports no Flask); the process line's list of instances (32b) uses it too;
- the plural table from `related.py` into `brain_views/plurals.json` (content unchanged; the script that moved it
  asserted it), and `related.py`'s and `dossier_routes.py`'s module docstrings shortened: the Dossier card was 16,158
  without them;
- `related.py` gives the card «Läuft gerade» its step name itself (`Related.step_name`), so the Dossier card imports
  nothing of the process line's.

**One existing test changed**, for the move only: `test_brain_views_related_read.py` (my own, PMVP-30b) imports the
list helpers from `brain_views.lists` instead of `brain_views.related`; a re-export would have left eight names
"not on the card". No assertion changed.

**map-check** (`--scope touched --worktree`, base `origin/feat/platform-mvp`): problems 0; **budget 0**; declared 0;
complete 3 `no_contract` (`create_brain_views_blueprint` as before, and the two attach functions; review entries, as
RB-FIX-1's); compatible 9 (`operation_removed` / `promise_removed` on L2-PLATFORM-BRAIN-VIEWS for the dossier's lines:
the check compares a card with itself and cannot see a line move to another card, as RB-FIX-1 noted).

#### The coordinator's other point: labels from the tenant

The Brain's node type has no label to take a plural from: the Core's type row is `id, name, description,
current_schema_version, parent_type_id, ancestors, managed_by` and the access fields (KnowledgeBase master
`graph_repository._node_type`). So the table stays the only source; it is now a data file (`plurals.json`), which a
later item can make per tenant. Recorded as the coordinator's follow-up.

#### Tests

- `test_brain_views_process_line_read.py` (29, no database): the main path of the order's 11-step process (jump 2→6,
  branch 6→11, reworks 8→7 and 6→2) gives 10 steps, «6 von 10», «Danach» step 7, the two ways; the same line with
  every edge's ends swapped; the edge cases of "not completely described"; «seit» from the history, then
  `valid_from`; the owner key; the edges route honoured, 404, unfiltered, the wrong relation, more than 25 steps,
  another failure; not in a process (none, two, the wrong kind); NotFound; per-thing reads only; no log line with a
  name or fact.
- `test_brain_views_process_line_page.py` (20): the gate (302, 404, the dossier's 404 for a hidden thing); the page
  (eyebrow, title, lede, states, marks, «Jetzt», «seit», who, «Danach», «Andere Wege»); off the main path; not
  completely described; not in a process; 30 main steps (24 drawn in two rows of 12 starting at step 7, the head 25,
  `data-bv-more="6"` opening 30 rows); 13 steps in rows of 7 and 6; the dossier's card for a thing in a step, none for
  a thing without, none on other tabs, no further read; no whole-tenant read; a failed read (502); mark-up as text;
  logs at DEBUG (`web_interface`, `brain_views` set explicitly, FU-46).

#### Checks

- **Tests** (own database `knovas_platform_test_pmvpB`, as the coordinator set it for builder B, not
  `…_pmvp32a`; one process): `test_brain_views_process_line_read.py` 29 and `test_brain_views_process_line_page.py`
  20 passed. With the frozen `test_pmvp34_view_limit_acceptance.py` (17, unchanged; it now also opens
  `/ablauf/<id>`, a thing in no process there), all of `tests/test_brain_views_*.py` (PMVP-30a's 70, PMVP-34's 28,
  PMVP-30b's 60) and `tests/test_pmvp11_navigation_acceptance.py`: **264 passed, 0 failed**, after the last code
  change. The whole suite was not run.
- **Frozen check**: all eleven OK.
- **Cards**: `card check` on the three: valid, 0 errors, 0 warnings; `card render --root docs/components --check`: 0
  not current, 0 problems; `map-check` as above (problems 0, budget 0).
- **In the browser** (headless Chrome, the real app, a fake Core in the Client Zero convention with invented names:
  a process «Vorhaben-Ablauf» of 11 steps, the jump, the branches and the reworks of the order's example, an instance
  in step 6 since 16 Nov with an owner name; 1440 and 390 px): the line drawn 11 (the thing and 10 steps); nothing
  scrolls sideways; no failed request; lowest text contrast 4.76 to 1; a focus ring on every link and on «Andere
  Wege». At 390 px the line stands upright (one step a row). At 1440 px a ten-step line leaves about 70 px a step:
  long step names wrap, and where the browser has no German hyphenation a word breaks without a hyphen
  («Abgenomm-en»); `hyphens: auto` is set, so a browser with the dictionary hyphenates. Screenshots, outside the
  repository:
  `C:\Users\siran\AppData\Local\Temp\claude\1838582cff9d51e1\af9716ae-0421-42ac-9fac-6acb192cc6e6\scratchpad\builderB\shots32a\`:
  `ablauf-*.png` («Andere Wege» opened), `compare-ablauf-*.png` beside `03-ablauf.png`, `dossier-laeuft-*.png` and
  `compare-dossier-laeuft-*.png` (the card «Läuft gerade» beside `02-dossier.png`), `kein-ablauf-*.png` (a thing in
  no process); `facts.json`. Tools: `serve.py`, `shoot.py` one folder up.

#### Open, for the coordinator

- The order's open point stands: whether production's `GET /secured/graph/edges` filters by `node_id` and `type` is
  not known; nobody here called production.
- `/edges` costs one more read per step than the order listed (`GET /nodes/<step>`, for the names).
- The dossier's list macros (`list_form`, `list_pages` in `dossier.html`) are not shared yet; 32b may not change
  `dossier.html`, so its list of instances will carry its own copy unless the coordinator wants them moved into a
  partial.

### 32b

(the builder writes here)
