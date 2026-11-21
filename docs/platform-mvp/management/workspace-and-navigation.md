# Workspace and navigation (the shell)

State on 21 November 2026. Overview and status words: [README.md](README.md).

## What it is

The frame every Platform page shares: one navigation that shows only the area a person is in and links to the other
areas only for people who may open a page there; the Knovas brand look (colours, type, logo); and a small set of shared
page parts from which every new screen is built, so that all screens look alike and follow the same five design rules:
one question per screen; one column and one main action; the navigation shows only the area you are in; details on
request; marks only for exceptions.

## What a person can do with it

- **Workspace:** the menu holds one entry, "Suche" (where a person also asks the assistant). At its foot the links
  "Verwaltung" and "Builder", each only for a person who may open a page there, and the person's own block, which leads
  to the settings.
- **Verwaltung:** "← Arbeitsbereich", then the Admin pages this person may open, the current one marked. The old row of
  tabs inside the Admin pages is gone.
- **Builder:** keeps its own menu for now, with a way back ("Zur Plattform"); the Builder items rework it.
- **Hidden, still reachable by address:** Cortex, Experimente, Eigene Ansichten, Einstellungen (reached through the
  person's block) and Feedback have no menu entry until a later item places them.
- **Narrow windows** (phones): the menu becomes a header with the entries in a row; it never makes the page scroll
  sideways.
- **Administrators** have a styleguide page that shows every brand value and every page part in each state.

Who sees which link to another area:

| Person | "Verwaltung" leads to | "Builder" |
|---|---|---|
| administrator | Personen | shown when the Builder's own check lets the person in |
| approver | Freigaben | same rule |
| ingestion manager | Ingestion (new: before, such a person could reach it only by typing the address) | same rule |
| member, experimenter, experiments manager | no link | same rule |

## How it protects data and access

- **The link is not the check.** Every page's server refuses a person without the right, whatever the menu shows. The
  menu reads the roles each Admin page's own gate lets in, so there is no second list of roles that could drift; a page
  whose gate cannot be read gets no link (closed, not open). The verifier asked the server as nine sets of roles with
  and without document fields (288 answers): the same answers before and after, and the menu showed exactly the pages
  that open.
- The navigation logs a person's id where it used to log an e-mail address.
- Every page part shows what it is given as text, so a name or sentence holding code or formatting cannot run.
  Parts take only addresses the Platform builds itself (FU-34).
- Nothing is loaded from outside the installation: the brand fonts are hosted by the Platform.

## Where it stands

| Item | What | State | Approves before main |
|---|---|---|---|
| PMVP-10 | brand colours and type on every page; styleguide page | verified 21 Nov (with notes); integrated | Jannik or Siran (routine) |
| PMVP-11 | one navigation, three areas; links by role | verified 21 Nov (with notes); integrated | Siran (sensitive) |
| PMVP-12 | ten shared page parts (column, page head, section, card, row, tabs, status dot, tag, numbered reference, link) | verified 21 Nov (with notes); integrated | Jannik or Siran (routine) |

Verified quality marks: lowest text contrast 4.76 to 1 (the bar is 4.5); a visible focus ring on every control; no
sideways scrolling at phone width on the new parts (the builder of PMVP-11 measured the same for the menu).

## Decisions taken

| Date | Who | What |
|---|---|---|
| 17 Nov | founder | The cluttered first mockups are rejected; screens follow the brand and the five rules; six reference screens (the founder's mockup package of 17 November) are the visual contract |
| 19 Nov | founder | A new top-level domain "Workspace" holds the assistant, the Brain views, the shell and Admin; Admin may move later if it grows (DEC-0083) |
| 20 Nov | founder | "Hide, place later": the Workspace menu shows only the search |
| 20 Nov | coordinator | The rule "the page must not scroll sideways on a phone" applies to new and reworked pages; the five Admin pages leave the known list with PMVP-40 |
| 21 Nov | coordinator | Each Admin page's gate carries its list of roles, and the menu reads that list |
| 21 Nov | founder | The hidden views stay (cleanup decision) |

Open: Olymp's CTO signs off each reference screen before pages are built on it (D-A5, due 24 November); the sources
record no sign-off yet.

## Known limits and open points

- **Sideways scrolling at phone width (FU-23):** the five Admin pages, the views page and the settings page, because of
  their own content. The Admin pages are reworked in PMVP-40; the other two stay listed until an item reworks them.
- **Contrast of old green tints (FU-28):** a few Admin and Experimente labels fall to between 3.46 and 4.46 to 1; fixed for Admin
  in PMVP-40.
- **The styleguide's gate is a copy** of the Admin gate, not the same one (FU-29).
- **Page parts accept any link address (FU-34):** the rule is that screens pass only addresses the Platform builds; a
  small hardening of the parts is still open.
- **The reference chip** can break away from its full stop on a phone (FU-35; the ask page handles it); looks that
  differ from the mockups on purpose are for the founder to see on the local copy.
- **Not in the new look yet:** the Cortex page keeps its own colours and fonts; the Builder's pages have their own
  colour set.
- **Not covered by a page test:** the Builder link for a consultant who holds only a grant on one workspace.
- **Small findings of the verification:** a person who is both approver and ingestion manager lands on Ingestion; a
  person record with malformed roles gives an error page (as before).

## Depends on

The Knovas brand kit (its colour and type file, copied unchanged); each area's own gates (Admin's roles, the Builder's
check); the Platform's web application, which computes the links.

## Owner

The engine owner role, proposed on the card; a person has still to confirm it.

**Where the detail is:** `docs/platform-mvp/work-orders/PMVP-10.md`, `PMVP-11.md`, `PMVP-12.md`;
`docs/components/L2-platform-shell.md`; `docs/platform-mvp/reference-screens/README.txt`;
`docs/platform-mvp/follow-ups.md`.
