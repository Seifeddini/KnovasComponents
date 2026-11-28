# The Brain of the demo law firm (28 November 2026)

`FEAT-2026-11-19-demo-law-firm`. The firm's Brain in tenant 817f2330, built with brain-runner 0.3.3 from the firm's own
sources (KanzleiDB on SQL Server, read as `knovas_ro`; the Kanzlei-ABC on the common drive). Definition and how to run
it: [`../brain/README.md`](../brain/README.md).

## 1. What is in it

| | Count |
|---|---|
| Matters (Akte), every one with parties by role, team, field of law and its totals | 1,500 |
| Parties (Partei), with their count across all matters | 2,500 |
| Same-name groups (probably one party entered twice) | 201 |
| Deadlines, appointments, invoices | 2,296 / 1,168 / 3,363 |
| Documents of the electronic matters | 1,195 |
| Staff, fields of law, the firm, glossary terms | 20 / 22 / 1 / 43 |
| Weisungen as processes, their steps | 5 / 20 |
| Edges from the sources | 27,160 |
| Derived edges: represented against / probably the same party | 1,357 / 319 (each direction) |
| Matters or deadlines placed at a step of a Weisung | 404 |

## 2. The rule checks cover 60 of the 1,500 matters

A Weisung is checked against a matter by the documents the matter holds: the conflict check, the engagement letter,
the power of attorney, the judgment and so on (DOKUMENTE.DokTyp). The stand-in has such documents only where the
generator wrote a matter out.

- **60 matters are kept electronically**: they hold at least one Word, PDF, text or mail file in AutoDoc. Their
  1,195 documents are in the Brain, and every Weisung that applies is checked against them.
- **The other 1,440 matters are records only.** The generator gave them placeholder files in formats Knovas skips
  (.doc, .rtf, .wpd, .tif, .jpg) and a spreadsheet each (DokTyp `Tabelle`, .xlsx), but no evidence documents: of the
  433 matters opened since Weisung 1 (11 March 2023), 17 hold a conflict check. Checked, 96 % of the firm's matters
  would show a breach, which says nothing about the firm. They are in the Brain with everything else (parties, team,
  deadlines, invoices, totals) but not placed in any Weisung.
- The spreadsheets alone do not make a matter electronic: counted, 735 matters would be "electronic" and almost
  every one would fail every check for the same reason.

Scope per Weisung (mappings `kanzleidb-ablauf-*`):

| Weisung | Checked against | Matters or deadlines |
|---|---|---|
| 1 Mandatsannahme | electronic matters opened since 2023-03-11 | 23 |
| 2 Fristenkontrolle | every open deadline that is not itself a Wiedervorlage | 254 |
| 3 Rechtsschrift | electronic matters with a filed Rechtsschrift | 39 |
| 4 Fakturierung | electronic matters | 60 |
| 5 Aktenabschluss | closed electronic matters | 28 |

**What would lift the limit:** the generator writing evidence documents (at least the DOKUMENTE rows with the right
DokTyp) for every matter opened since each Weisung, with a small, known share missing, as `truth/processes.json`
already does for the written-out matters. Then widen the scope in the mappings (drop the `elektronisch == 'ja'`
condition). That is a change to the generator, so it belongs to the generator's next build, not to the Brain.

## 3. Things worth knowing

- **One request per fact.** The Core has no bulk write: a node is created, then each fact is posted alone. The first
  load is about 200,000 requests; at the tenant's pace (6 to 8 a second) about 7 to 10 hours. Later runs write only
  what changed. During a load the Platform's search shares the tenant's limit and may answer 429.
- **Datatypes.** The Core accepts date, money ({amount, currency}), enum, text and entity_ref; `number` is refused
  (HTTP 400 at ontology apply). Amounts, counts and hours are text.
- **Process steps from evidence.** A matter's step in a Weisung is the first whose evidence is missing; the views'
  gap list (requirement paths in `processes.yaml`) then names what is missing. Steps the sources cannot evidence
  (the partner's proof-reading, the engagement confirmation) are in the descriptions, not invented as checks.
