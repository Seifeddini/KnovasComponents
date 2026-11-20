# Demo-Kanzlei: generator for the demo law firm

Builds a synthetic Swiss law firm from one description (`world.toml` and its seed): the files on its
share, and, in the client layout, its practice-software database, its directory and the truth files
that score a Knovas installation against it. Plan: `FEAT-2026-11-19-demo-law-firm`. How to run the
whole stand-in: [`demo/kanzlei/README.md`](../../../demo/kanzlei/README.md).

Everything is fictional. Company names are invented and screened against the commercial register
(`cli.py screen`); all e-mail addresses end in `.example`. Prose comes from templates; no language
model is called. The world, the manifest and the truth files are **never** written into the share.

## Two layouts

| | `--layout aktenplan` (default) | `--layout client` |
|---|---|---|
| For | A first end-to-end run: one folder per matter, fresh file times | The share as we find it at a client |
| Share | `Mandanten/{Mandant}/{Aktenzeichen}-{Titel}/01-Eroeffnung/…`, `Kanzlei/…` | One hidden share: the practice software's store in numbered folders, the common drive, home drives, mail archives |
| Records | none | T-SQL for a SQL Server: matters, addresses, time, invoices, deadlines, matter rights, document index |
| Directory | none | `directory.json` and `acl.json`: 13 people, leavers, service accounts, nested groups, folder permissions |
| Truth | `ground_truth/` (world, simple questions) | `truth/`: `questions.jsonl`, `rights.csv`, `people.json`, `processes.json`, `inventory.json` |
| File times | the build time | each document's own date |

Both name matter documents `{GUID}_{AkteID}_{Typ}.{ext}`, and both build the same file for file from
the same world and seed (POST-01).

## Build

From `KnovasConnector/` (Python 3.11+; the tests also need the Connector's own dependencies, see below):

```bash
pip install -r scripts/demo_kanzlei/requirements.txt

# one matter, tidy folders (31 files, seconds)
python scripts/demo_kanzlei/cli.py build --out ../corpus/kanzlei --skip-zefix

# the client's share, small: 60 matters in the records, one written matter (about 420 files, 15 s)
python scripts/demo_kanzlei/cli.py build --layout client --out ../corpus/share --work ../corpus/share-work --skip-zefix

# the client's share, full: 1,500 matters, 40 written (about 31,800 files, 170 MB; 7 minutes on Windows)
python scripts/demo_kanzlei/cli.py build --layout client --full --out ../corpus/share --work ../corpus/share-work --skip-zefix
```

`--work` holds the world, plan, prose cache, manifest, `site/` and `truth/`. It defaults to a sibling
`.{out}-work` and must be outside `--out`. A client build empties `--out` first, and only if this
generator built it before (`LIESMICH_DEMODATEN.txt` is there); any other non-empty folder is refused.

`--until-stage world|plan|write|records|file|mess|site|ground_truth|verify` stops after that gate.

`python scripts/demo_kanzlei/cli.py screen` checks every invented company name of the full build
against Zefix. Run it after changing `namepool.py`.

## The client layout

```
<share>/LIESMICH_DEMODATEN.txt
<share>/Quarzfels/Praxis/Autodoc/000000-000500/{GUID}_{AkteID}_{Typ}.{ext}
                         Autodoc/000501-001000/…          500 documents per folder, in order of creation
                  Praxis/praxis.ini, Vorlagen/, Export/, Update/, Log/
                  Gemeinsam/Kanzlei/01-Vorlagen | 02-Organisation (Weisungen, Kanzlei-ABC) | 03-Compliance
                  Gemeinsam/Partner/            partners only
                  Gemeinsam/Personal/           partners only
                  Gemeinsam/Buchhaltung/        partners and the bookkeeper;  …/Lohn: not readable by the Knovas account
                  Gemeinsam/Scan/               scanned post nobody has filed yet (image-only PDFs)
                  Gemeinsam/Diktate | Archiv/Akten bis 2005 | IT
                  Users/<login>/                home drives: not readable by the Knovas account
                  Mail/<login>/                 Outlook archives: not readable by the Knovas account
```

- **Only the written matters are readable.** The other 1,460 matters have placeholders in formats the
  Connector skips (`.doc`, `.rtf`, `.wpd`, `.tif`, `.jpg`, `.xls`, `.xlsx`, `.zip`, `.dss`, `.htm`). The share
  is large and slow to walk; Knovas receives about 740 documents. (Plan §8.2: the Core is not loaded.)
- **A matter's files are scattered.** Folder names are number ranges. Who the client is, who works on
  a matter and who may open it is in the database, not on the share.
- **Rights.** Folder permissions cover the common drive. Matter rights (two locked matters, one
  information barrier) are rows in `AKTENRECHTE`; everyone can read the store's folders.
- **Mess, recorded in the manifest:** documents saved twice, filed under the wrong matter, copied in
  by hand under a free name, empty, cut off, in Latin-1; lock files; index rows whose file is gone.
- **Records:** the software came in 2006. Older matters are master data only and their papers are in
  `Gemeinsam/Archiv`.

### The database (`sqlgen.py`, `records.py`, `office.py`)

23 tables for a SQL Server. The schema is invented, because vendors do not publish theirs; it has the traits old
practice databases share, so that reading it is the same work as reading a client's:

| Trait | Where |
|-------|-------|
| No foreign keys; relations by column name | everywhere (`AkteNr`, `AdrNr`, `MandantAdrNr`, `Kuerzel`) |
| Codes that point to lookup tables | `RolleID`, `FristArtID`, `LeistArt`, `RgID`, `Status` |
| Change markers of three kinds | `TS` (rowversion) and `MutiertAm` on the big tables; `MutiertAm` only on `FRISTEN`; none on the link tables |
| Entered later than dated | `LEISTUNGEN.ErfasstAm` is up to three days after `Datum` |
| Deletion of three kinds | flag (`DOKUMENTE.Geloescht`, `ADRESSEN.Inaktiv`); rows removed (`LEISTUNGEN`, `FRISTEN`, link tables); never (`AKTEN`) |
| Old types and conventions | `datetime` for dates, `smallint` -1/0 for yes/no (`MITARBEITER.Aktiv`), `ntext`, `varchar` codes, 1900-01-01 for an unknown date |
| Fields the firm defined | `ZUSATZFELDER`, `ZUSATZFELDWERTE` (the court's own number of a matter is here) |
| Text that is in no file | `JOURNAL`: notes typed into the software |
| Not the firm's data | `SYS_*` (users, counters, locks), `AKTEN_BACKUP_20190312`, `tmp_Import_Adressen` |
| The same party twice | 100 duplicate addresses under other spellings |
| Rights only in a table | `AKTEN.Gesperrt`, `AKTENRECHTE` |

The written matters are rows like any other and their billing matches their documents (the fee note of matter
2024-017 says CHF 21'840; its time entries add up to that). The index `DOKUMENTE` matches the store, except where
mess is planted: rows whose file somebody deleted, and files copied in by hand that have no row.

`world.toml` → `[records]`, `[estate]`, `[estate.mess]` set the sizes. `as_of` is the stand-in's today:
nothing is dated later, and it never follows the clock.

## Truth files (`<work>/truth`, plan §6)

| File | Content |
|------|---------|
| `questions.jsonl` | 60 questions (30 with `in_mvp_set`), 12 in the small build. `fact`, `document`, `process`, `negative`, `rights` |
| `rights.csv` | `person,pointer,may_read,rule`: one row per person for every file Knovas could show |
| `people.json` | The 13 people with their group paths |
| `processes.json` | Where each carefully written matter stands in the firm's five processes |
| `inventory.json` | Not in §6: counts per source, folders the Knovas account cannot read, the mess, database row counts |
| `sources.json` | Not in §6: each source as someone connecting it finds it. For the database: every table's key, how a change shows, how a row disappears, and what the table is (records, lookup, the software's own, a leftover) |
| `records/<TABLE>.jsonl` | Not in §6: every row of every table as it should be read. The oracle for a connector; `demo/kanzlei/site/check-records.sh` proves the loaded database equals it |

Rules that the plan's §6 leaves open, as built here (to be confirmed):

- A `rights` question has `expected_points: []`. Its `supporting_pointers` are the files that hold the
  answer, none of which `asked_as` may read. Correct is: no answer from them, none of them cited.
- A pointer `db:<TABLE>/<key>` means the answer is only in the practice database. Knovas does not read
  it today, so these four questions are expected to fail; none is in the MVP set.
- `rule` is one of `default:kanzlei`, `default:ohne-akte` (a file without a matter in its name),
  `locked:<matter>`, `conflict:<matter>`, `role:sekretariat-keine-finanzen`, `group:<directory group>`,
  `account:<login>`, `acl:<folder>` (refused by that folder's permissions).

## Verify

```bash
python scripts/demo_kanzlei/cli.py verify --layout client --out ../corpus/share --work ../corpus/share-work
```

Checks that the share is exactly the manifest and that every written document extracts the way the
Connector extracts it. Scanned PDFs need an OCR engine (tesseract). Where there is none, they are
reported as not checked and the gate still passes: run it in the Connector image to check them.

## Freshness (aktenplan layout)

The Connector's default drops files whose **mtime** is older than 30 days. `verify` warns at 25 days.
Before a demo on the tidy layout:

```bash
python scripts/demo_kanzlei/cli.py touch --out ../corpus/kanzlei
```

The client layout keeps old file times on purpose: a client's share is old, and the Connector must
be configured for it (no `max_document_age_seconds`).

## Tests

```bash
DEMO_CORPUS_REQUIRED=1 python -m pytest tests/unit/demo_kanzlei -q
```

`tests/conftest.py` imports the Connector, so its dependencies must be installed
(`pip install -e ".[dev]"`, with `knovas-extract` from the ref that
`scripts/ci/check_knovas_extract_pin.sh` prints).
