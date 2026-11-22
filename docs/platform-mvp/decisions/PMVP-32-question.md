# Decision request PMVP-32: where a matter's process line comes from

**For the coordinator, and through the coordinator for the founder** (2026-11-22). No work order is written for
PMVP-32 yet. What the plan asks for exists in the Knovas GmbH tenant, but in no law firm's data. How it is read today
breaks the views' contract.

| | |
|---|---|
| Item | PMVP-32, plan `FEAT-2026-11-19-platform-mvp` § 4 **B-06** ("a process shows its main path first; variants open on request; about twelve steps per line"), B-02; spec item SPEC-platform-mvp-11 ("a matter in step 4 of 9 → one line, step 4 marked, the missing document named"); contract L2-PLATFORM-BRAIN-VIEWS PRE-01, POST-01, POST-03 |
| Gate | G3 (Tue 8 Dec): "dossier and process line open from an answer". Plan § 9.3 cut 2: "the process line becomes the existing Ablauf view in the new look" |
| Follow-up | FU-19 ("if PMVP-32 falls back to the existing Ablauf view, that code needs an owner"): decided with this request |
| Visual contract | `reference-screens/03-ablauf.html`: «Mandat 2026-0142 · Limmat Immobilien AG», «Ablauf «Kündigung Geschäftsmiete» · Schritt 4 von 7», seven steps, «Jetzt: Kündigung prüfen · seit 6 Tagen · Dr. M. Keller», a checklist with «fehlt noch», «Danach: …», «Alle 14 Mandate in diesem Ablauf →» |

## What was looked at

- Code, read only: the Platform on `feat/platform-mvp` @ `74478d1a`; the Core on KnowledgeBase `origin/master` @
  `72d19caf1` (`knovas-software/app/src/api/`, `DB/init.sql`); the demo law firm on KnovasComponents
  `origin/feat/demo-kanzlei`.
- Data: the **Knovas GmbH tenant**, through the **read-only** Knovas Brain tools (`knovas_check_readiness`: ready;
  `knovas_list_nodes`, `knovas_neighbors`, `knovas_explain`). Nothing was written to any tenant. No law firm's tenant
  and no demo tenant was read.

## The facts

**1. The Core has no process.** There is no process, step or workflow route in `app/src/api/`, and no such table in
`DB/init.sql`. A process exists only as graph nodes, edges and facts written by a loader.

**2. In the Knovas GmbH tenant, processes are nodes (the Client Zero convention).**
- 8 nodes of type `Process`, among them `PROC-feature-lifecycle` («Feature lifecycle», node
  `ffe32420-815b-44b8-825f-9dfc27a24a13`), `PROC-vision-to-work`, `PROC-release`, `PROC-sales`.
- «Feature lifecycle» has **11 steps**, which are `ProcessStep` nodes («Feature lifecycle: idea», «… brief», «…
  approved», «… implemented», «… killed»; their descriptions say "Step n of 11").
- The steps are linked by `transitions_to` and `reworks_to` edges. That is a main path and its rework variants in
  the data itself; the definition schema has only `transitions[].rework` and no main-path field
  (`graph_views/schemas/process-definition.schema.json:113`).
- Each instance is linked by `instance_of` to its process and by `in_state` to its current step. Example: the plan
  `FEAT-2026-11-06-organisation-onboarding-keys` (node `03353653-81f9-4962-a64d-7b9bd766feb9`) is `in_state` «Feature
  lifecycle: approved». Its facts say `current_step` = `approved`, `step_history` = `[["approved","2026-11-09",null]]`
  (gives «seit …») and `owner` = `PERS-siran` (a person's key; the name costs one more node read).
- About 40 feature plans are instances of «Feature lifecycle». The process's own neighbourhood lists them all, which
  gives «Alle N … in diesem Ablauf».

**3. "What is missing" is computed, not stored.** The Platform's process views walk each step's
`requires`/`produces` paths from the instance and count what they reach (`graph_views/process_views.py:52-69,
228-229`; step facts as `graph_views/definitions.py` reads them; the step facts were not read on the tenant here).
Requirements are **things reached over relations, not documents**. Documents are a node's assignments, and no
definition field ties a step to a kind of document. SPEC-11's "the missing document named" cannot be expressed
today.

**4. No law firm has any of it.**
- The legal vocabulary pack has `"process_templates": []` (`builder/packs/legal/pack.json:11`). Its `Matter` type
  has only `status` open/on_hold/closed.
- A Builder-published tenant holds no process nodes at all (`builder/tenant_definitions.py:7-9`: "The graph itself
  holds node types and edges, not processes").
- The demo law firm writes `truth/processes.json` (`KnovasConnector/scripts/demo_kanzlei/truth.py:89-132`), one entry
  per hero matter with `mandat`, `process`, `current_step`, `done_steps`, `missing`. Its five processes are code
  (`firm_documents.py:19-110`), and nothing carries them, or a matter's step, into the Brain. Its `missing` lists
  steps without evidence, not documents.

**5. Today's Ablauf reads the whole tenant.** Every context starts with `reader.topology()`
(`graph_views/topology.py:229`), which is `GET /node-types`, `/nodes` and `/edges` unfiltered
(`graph_views/reader.py:54`). The definitions are found in the whole node list too. That breaks POST-03 and B-02, and
there is no 25-node limit (`settings.py`: a fan-out threshold of 5 per artefact, lanes not capped). FU-19 is
confirmed. Its line logic (`trace()`) talks only to an index interface whose per-node variant
(`PartialIndex.ensure`, one `GET /nodes/<id>/neighbors?depth=1&include_edges=true` each) is per thing, so the logic
could be reused if the context were built without `topology()`.

**6. Per-thing reads that exist** (as the person; `knovas_client.py`):
- `graph_neighbors(id, depth=1, include_edges=True)` for the instance gives its process (`instance_of`), its current
  step (`in_state`) and its direct requirements. An instance has many other edges (about 100 for the plan above:
  `keeps`, `affects`, `part_of`); they are read and left out.
- The same read for the process gives its steps, their `transitions_to` and `reworks_to`, and its instances.
- `graph_facts_batch` gives the steps' facts (order, requirements) in one read.
- The Core's `GET /secured/graph/edges?node_id=&type=` (master honours both filters) is not offered by the client.
  Whether production runs that code was not checked.
- There is no paged read: a process with many instances is read whole each time.

## Options

**A. Build the line on the Client Zero convention, per thing (recommended).** One starting point: a thing with an
`in_state` edge. Reads: the instance's neighbours, the process's neighbours, one facts batch. The pieces come from:

| On the page | From |
|---|---|
| main path | the `transitions_to` chain |
| variants on request | `reworks_to` |
| current step | `in_state` |
| «seit …» | `step_history` / the `current_step` fact |
| who | the `owner` fact |
| «Danach» | the next step on the main path |
| «Alle N … in diesem Ablauf» | the `instance_of` edges, opening a list (PMVP-34's `paged`) |
| «fehlt noch» | the step's requirements reachable in one hop from the instance (things, not documents) |

- PMVP-34 caps the drawn steps.
- This works on the measured pilot (Knovas GmbH, DEC-0082) today, with real data.
- A thing without `in_state` shows nothing; the dossier's «Läuft gerade» card appears only where there is a line.
- A law firm's matter has no line until its data exists (option C).
- The existing Ablauf code is not reused, so FU-19 closes without an owner.
- Size: about 5 to 8 points. If split: 32a, the line with current step, «seit» and «Danach»; 32b, «fehlt noch» and the
  list of instances.

**B. The plan's cut 2: today's Ablauf in the new look.**
- It is the least screen work.
- It reads the whole tenant on every context, so it breaks POST-03 and B-02, and draws more than 25 nodes.
- It needs the founder to waive POST-03 for this view, and an owner for that code (FU-19).
- Its cost grows with the tenant, and the Knovas tenant's whole-graph reads have failed before (IN-2026-11-10-03:
  the export route; the Ablauf uses `/nodes` and `/edges`).
- Not recommended.

**C. Data first, for law firms (in addition to A, outside this plan's code).** Ask for:
- a law-firm process in the legal pack (`process_templates`);
- a loader or mapping that writes `instance_of`, `in_state` and `current_step` for matters (the demo plan's
  `processes.json` is the natural first source);
- a convention for "a step needs a document of kind X", which the Core can answer per matter (category, tag or
  document field).

Without C, no law firm sees a process line, and SPEC-11's "missing document" stays unbuilt. The demo plan and the
legal pack are other plans' work; the founder decides who takes it.

## What the coordinator and the founder must decide

1. **A, B or C** (recommended: A now, C asked of the demo plan and the Builder's bet).
2. With A: **the pilot's process line is measured on Knovas GmbH's own processes** (feature lifecycle, release,
   sales …), not on law-firm matters; the reference screen's law-firm words stay the look, not the data.
3. With A: **main path = `transitions_to`, variants = `reworks_to`** as the convention, written into the views'
   card.
4. With A: **«fehlt noch» means the step's requirements one hop from the instance** (things, not documents) in the
   MVP. SPEC-11's "missing document" waits for C.
5. Whether `GET /secured/graph/edges?node_id=&type=` is live in production. If it is, the process's instances can be
   counted without reading its whole neighbourhood; someone with production access checks it.

When 1 to 4 are answered, the coordinator (or a drafting session) writes `work-orders/PMVP-32.md`. PMVP-32 then
touches the Brain-views files PMVP-34 and PMVP-30b change (`brain_views/`, `brain_views_routes.py`, `dossier.html`
for the «Läuft gerade» card, the views' card), so it goes to the same builder after PMVP-30b, or starts after both are
integrated.
