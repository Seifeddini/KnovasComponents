# Brain views

State on 22 November 2026. Overview and status words: [README.md](README.md).

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

**Built: related things by kind (PMVP-30b).**

- See on a thing's dossier what is connected to it, by kind: one tab per kind with its count («Mandate 7»,
  «Verträge 34»), in German plurals from one table (the legal pack's kinds and Knovas GmbH's own); a kind outside the
  table keeps the Brain's name. More than 20 kinds, and things without a kind, are counted under «Weitere Einträge».
- Open a kind's tab: the list of those things, each with its relation under its name and a link to its own dossier.
  Sort it (name A–Z or Z–A, or by relation), filter it by name, and page through it 50 at a time. Without a script.
- «Dokumente» works the same way (sort, filter, 50 a page); a document always opens the same one, whatever the
  order or the page.
- If the related things cannot be read, the dossier still opens and says so in one line.

**Built: the 25-thing limit (PMVP-34).** Every view takes what it draws through one shared function; what does not
fit is a count («115 weitere») that opens a list of 50 rows a page. The dossier draws one thing (the thing itself);
its facts and documents are rows of a list.

**Planned, no work order yet:**

| Item | What | Note |
|---|---|---|
| PMVP-31 | the evidence trail ("Beleg-Spur") from a reference in an answer | third on the plan's cut list |
| PMVP-32 | the process line for one matter: main path, current step, what is missing | decided 22 Nov (`decisions/PMVP-32-question.md`): built per thing on Knovas GmbH's own processes (the Core has none; no law firm's data holds one; the old Ablauf reads the whole tenant and is not reused); law-firm process data asked of the demo plan and the Builder's bet |
| PMVP-33 | names in search results and answers link to the dossier | |

## How it protects data and access

- **Never more than 25 things drawn.** One shared function caps what a view draws; the rest is a count that opens a
  list. The frozen test `test_pmvp34_view_limit_acceptance.py` opens every view page by itself (a view added later
  included) for a thing with 140 neighbours, documents and facts, and fails if a page draws more than 25 or a count
  opens nothing.
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
| PMVP-34 the 25-thing limit | built 22 Nov (its frozen test green); no verification verdict yet | Jannik or Siran (routine) |
| PMVP-30b related things by kind | built 22 Nov; no verification verdict yet | Jannik or Siran (routine) |
| PMVP-32a, PMVP-32b | decided 22 Nov: the line on Knovas GmbH's own processes; work order written; same builder after 30b | Jannik or Siran (routine) |
| PMVP-31, 33 | not started | Jannik or Siran (routine) |

Gate G3 (Tuesday 8 December) asks that the dossier and the process line open from an answer.

## Decisions taken

| Date | Who | What |
|---|---|---|
| 19 Nov | founder | The views belong to the new domain "Workspace" (DEC-0083) |
| 19 Nov | plan, approved by the founder | If a gate is missed: the process line becomes the existing Ablauf view (cut 2), then the evidence trail is dropped and the numbered references stay (cut 3) |
| 20 Nov and 21 Nov | founder | The existing modelling views stay, hidden from the menu ("Hide, place later"; cleanup decision) |
| 22 Nov | coordinator | Related things only as tabs with counts and lists (no circles on the Überblick, as the reference draws none); kind names in the plural from one table; at most 20 named kinds (PMVP-30b, D1 to D4) |
| 22 Nov | coordinator | What a view draws is capped at 25; the rows of a list a count opens are not drawn things, and lists are paged at 50 (PMVP-34, D1) |
| 21 Nov | builder of PMVP-30a, for the verifier to judge | One read per thing; the section "Verbunden" left out (it needs PMVP-30b's reads); only the mark "ohne Beleg" built |

## Known limits and open points

- **The fact's passage cannot be shown (FU-38).** A fact records its document and its passage, but no Core route returns
  a passage's text or page by its id. So a click opens the document at its start, not at the passage. Both the plan's
  requirement and the card's promise ask for the passage. A request to the Core is written together with FU-37; until
  then the promise holds for the document, not the passage.
- **Related things follow the Core, not yet the person's rights.** The counts and lists are what the Core hands the
  person; until the Core's rights enforcement is on (CR-R1, the 12 January row B-07), everyone sees every related
  thing.
- **A thing with very many neighbours is read whole on every page view** (PMVP-30b, D4): the Core has no paged
  neighbours read, so sorting and filtering happen on the Platform over everything read. Accepted for the MVP.
- **Plurals come from one table**; a kind added to a tenant later shows its own (singular) name until the table
  knows it. Knovas GmbH's «Claim» (Aussage) and the legal pack's «Claim» (Forderung) share a key; the table takes the
  pilot's reading.
- **Marks for derived or uncertain facts** are not built; a field that refers to another thing shows its id, not its
  name (PMVP-33).
- **The existing Ablauf code** is not reused by PMVP-32 (decided 22 Nov), so it needs no owner here (FU-19).
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
