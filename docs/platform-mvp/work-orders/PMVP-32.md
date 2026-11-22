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

(the builders write here, under «32a» and «32b»: what was decided, what is open, what the verifier should look at)
