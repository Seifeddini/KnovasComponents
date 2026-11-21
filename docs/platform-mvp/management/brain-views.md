# Brain views

State on 21 November 2026. Overview and status words: [README.md](README.md).

## What it is

Pages that show an employee what the Brain knows about **one** thing (a client, a matter, a contract), where one
statement comes from, or where one process stands. Each view starts from exactly one thing, draws at most 25 things,
and turns the rest into a count that opens a list. Unlike the existing views made for people who model the Brain
(Cortex, Ablauf, the custom views), these never read the whole graph of a tenant.

## What a person can do with it

**Built: the dossier (PMVP-30a).**

- Open one thing and see its name, kind and description.
- Read its facts. A click on a fact shows the document it comes from (name, folder) and a link that opens it.
- See which facts have no source she can open: they carry the mark "ohne Beleg" and are never shown as if they had one.
- Open the tab "Dokumente": the documents assigned to the thing.
- Today a person reaches a dossier only by its address; links from names in results and answers come with PMVP-33.

**Planned, no work order yet:**

| Item | What | Note |
|---|---|---|
| PMVP-30b | related things by kind, as counts and lists | |
| PMVP-31 | the evidence trail ("Beleg-Spur") from a reference in an answer | third on the plan's cut list |
| PMVP-32 | the process line for one matter: main path, current step, what is missing | second on the cut list: it may become the existing Ablauf view in the new look |
| PMVP-33 | names in search results and answers link to the dossier | |
| PMVP-34 | the 25-thing limit as one shared function with its test | |

## How it protects data and access

- **One thing at a time.** The dossier asks the Core only about that one thing; a test fails if it ever asks for the
  whole tenant. (The Core's whole-graph export already fails for the Knovas tenant, so the views must not use it.)
- **As the person.** Every read is signed as the employee who is signed in, exactly as her search is. A thing she may
  not see answers exactly like a thing that does not exist.
- **Sources only she can open.** The Core may show a fact because any of its evidence is readable, so a fact's source
  can name a document this person may not read. Such a fact gets "ohne Beleg", and its document is neither shown nor
  opened.
- **Documents open** only those the Core listed for this person, and only for her.
- Everything from the Brain is shown as text; addresses are built by the Platform only.
- **Off by default** (a switch on the settings page); off, or without personal accounts, the dossier answers "not
  found".
- No customer text in log lines.

## Where it stands

| Item | State | Approves before main |
|---|---|---|
| PMVP-30a dossier | built; no verification verdict yet | Jannik or Siran (routine) |
| PMVP-30b, 31, 32, 33, 34 | not started | Jannik or Siran (routine) |

Gate G3 (Tuesday 8 December) asks that the dossier and the process line open from an answer.

## Decisions taken

| Date | Who | What |
|---|---|---|
| 19 Nov | founder | The views belong to the new domain "Workspace" (DEC-0083) |
| 19 Nov | plan, approved by the founder | If a gate is missed: the process line becomes the existing Ablauf view (cut 2), then the evidence trail is dropped and the numbered references stay (cut 3) |
| 20 Nov and 21 Nov | founder | The existing modelling views stay, hidden from the menu ("Hide, place later"; cleanup decision) |
| 21 Nov | builder of PMVP-30a, for the verifier to judge | One read per thing; the section "Verbunden" left out (it needs PMVP-30b's reads); only the mark "ohne Beleg" built |

## Known limits and open points

- **The fact's passage cannot be shown (FU-38).** A fact records its document and its passage, but no Core route returns
  a passage's text or page by its id. So a click opens the document at its start, not at the passage. Both the plan's
  requirement and the card's promise ask for the passage. A request to the Core is written together with FU-37; until
  then the promise holds for the document, not the passage.
- **No 25-thing limit yet** (PMVP-34); the dossier draws no graph today.
- **Marks for derived or uncertain facts** are not built; a field that refers to another thing shows its id, not its
  name (PMVP-33).
- **Owner of the existing Ablauf code** must be decided if PMVP-32 falls back to it (FU-19).
- **Page parts' link check (FU-34)** was planned with PMVP-30a but its order forbade touching the parts: still open.
- **Rights enforcement** is off for every tenant today; the dossier shows what the Core hands this person (see
  [data-protection.md](data-protection.md)).
- The component card still says "planned"; it gains its first code with PMVP-30a.

## Depends on

The Knovas Core's per-thing graph routes and its narrowing per person; the Platform's document routes; the shared page
parts and the navigation; the settings store (the switch); personal accounts (not the shared company login).

## Owner

The engine owner role, proposed on the card; a person has still to confirm it.

**Where the detail is:** `docs/platform-mvp/work-orders/PMVP-30a.md` (builder's notes on `feat/pmvp-30a`);
`docs/components/L2-platform-brain-views.md`; `docs/platform-mvp/follow-ups.md` (FU-19, FU-34, FU-37, FU-38).
