# The demo law firm: a stand-in for a client

`FEAT-2026-11-19-demo-law-firm` (bet `BET-2026-11-brain-builder`). A synthetic Swiss law firm, **Quarzfels
Advokatur**, with its data the way we find it at a client: a file server, a directory, a practice-software database.
We use it to find what the Platform, the Connector and the connection layer lack before a client does, and for demos.

Everything is invented. Nothing real ever goes on it. Product of the case study: [`findings.md`](findings.md).

## What is in it

| At a client | In the stand-in | Reached by |
|---|---|---|
| File server with one hidden share | Samba file server, share `shares$`: the practice software's store in numbered folders, the common drive, home drives, mail archives. Folder permissions as they grew | SMB 3 at `172.28.215.10`, account `svc-knovas` (read-only) |
| Active Directory, run by an IT provider | Samba domain controller, domain `QUARZFELS`: 13 people, 7 who left (disabled), 4 service accounts, nested groups | LDAPS and Kerberos at `172.28.215.10` |
| The practice software's database | SQL Server, database `KanzleiDB`, 23 tables: 1,500 matters since 1998, 2,500 addresses, time, invoices, deadlines, appointments, notes, matter rights, the index of the store | TDS at `172.28.215.20`, login `knovas_ro` (read-only) |
| Mail in Outlook/Exchange | Not a source yet. E-mails filed to a matter are `.msg` files in the store; the rest are `.pst` archives nobody can read | decision D-B4 |

The firm has 3 partners, 7 associates and 3 people in the secretariat. Forty matters are written out (about 640
documents); the other 1,460 are records, most with placeholder files in formats the Connector skips, so the share is
as large and slow as a client's while Knovas receives about 740 documents.

## Run it

Needs Docker and Git Bash. The generator needs Python 3.11+ (see its
[README](../../KnovasConnector/scripts/demo_kanzlei/README.md) for the dependencies).

```bash
# 1. Build the data (small: 15 s, 420 files; add --full for the whole firm: 7 minutes, 31,800 files)
cd KnovasConnector
python scripts/demo_kanzlei/cli.py build --layout client --skip-zefix --out ~/demo-kanzlei/share --work ~/demo-kanzlei/work

# 2. Start the site and load the data into it
cd ../demo/kanzlei/site
./init-site.sh ~/demo-kanzlei/work/site ~/demo-kanzlei/share    # once: paths and random passwords into .env
./up.sh                                                          # domain controller + file server, SQL Server
./load.sh                                                        # files with their dates, permissions, database

# 3. Check it
./smoke.sh                                    # share, permissions, directory, database, a mount as at a client
./check-records.sh ~/demo-kanzlei/work/truth  # the database, read as knovas_ro, equals the truth files row for row
./check-share.sh ~/demo-kanzlei/work          # every file on the share equals the manifest (path and SHA-256)

# 4. Let a working day pass (once; ./load.sh goes back to the build)
./apply-changes.sh ~/demo-kanzlei/work/site/changes/tag-1
./check-records.sh ~/demo-kanzlei/work/truth/changes/tag-1     # the database as it must be afterwards
./check-share.sh ~/demo-kanzlei/work/truth/changes/tag-1       # the share as it must be afterwards
```

Stop: `./down.sh`. Start again: `./up.sh` (nothing is lost). Reset the data: `./load.sh` again (it says what it removes
first). Reset everything including the domain: `./reset.sh --all`, or `./down.sh --volumes`.

After a change to the people or groups in the generator: `./reset.sh --all && ./up.sh && ./load.sh`.

## Where things are

| | |
|---|---|
| `<work>/site/` | `directory.json`, `acl.json`, `db/*.sql`: what the site is built from |
| `<work>/truth/` | `questions.jsonl`, `rights.csv`, `people.json`, `processes.json` (the contract with the Platform plan, §6); `inventory.json`; `sources.json` and `records/<TABLE>.jsonl` (for the connection layer) |
| `<work>/manifest.jsonl` | every file on the share: hash, kind, matter, mess |
| `<work>/site/changes/tag-1/` | one working day: `db.sql`, `share/` (new and saved-over files), `delete.txt` |
| `<work>/truth/changes/tag-1/` | the state after that day: `records/`, `manifest.jsonl`, and `changes.json` (what changed, the question it makes answerable, the rights of the new files) |
| [`site/`](site/README.md) | the containers, their scripts, the accounts |
| [`requests/`](requests/) | what a person has to decide or create (tenant, certificates; the client profile) |
| [`reports/`](reports/) | one report per item or run |

The work folder is never inside the share and is never synced: a client's share has no file that says who its clients are.

## Checking the connection layer against it

`<work>/truth/sources.json` describes each source the way someone connecting it finds it: address, account, and for
every table its key, how changes show and how rows disappear. `records/<TABLE>.jsonl` holds every row as it should be
read. A connector is right when what it reads equals those files; `check-records.sh` does exactly that comparison
with plain SQL, as the proof that the oracle is true.

What the database makes a connector deal with, on purpose: no foreign keys; codes that point to lookup tables; three
kinds of change marker (a `rowversion`, `MutiertAm`, none); time entries entered days after their date; rows that
are flagged, rows that are removed; `-1/0` for yes/no in old tables; a 1900 date for "unknown"; fields the firm
defined itself; a backup table and an import table that are not the firm's data; duplicates of the same party; matter
rights that exist only in a table. The schema is invented: vendors do not publish theirs. It has the traits such
databases share, not one product's names.

**A second read.** `apply-changes.sh` plays the day after the stand-in's today: time is booked (some of it for last
week), two entries are removed, a matter is closed, deadlines are ticked off, the scanned court order is filed to
its matter, a note is written, a document is saved over, one is removed in the software and one deleted by hand.
`truth/changes/tag-1/changes.json` lists every inserted, updated and removed key per table and every added, changed
and deleted file. Whatever synchronises the sources must arrive at exactly that state: new rows found by their entry
time, removed rows found although nothing flags them, the new note found by search and refused to the associate
behind the information barrier.

A site of the connection layer (Knovas Link and its components) reaches the sources when its containers are on the
network `demo-kanzlei-net`, for example `docker network connect demo-kanzlei-net <container>`. The credentials are in
`site/.env` on the machine that runs the site and nowhere else.

## Connecting Knovas

Needs a tenant and its certificate, which a person creates: [`requests/DEMO-02-tenant-and-site.md`](requests/DEMO-02-tenant-and-site.md).
Until then nothing is uploaded anywhere.

## Limits

- One working day is played, in the database and on the share. The directory does not change yet (a joiner, a
  leaver, a move between teams), and there is no mass deletion to test the deletion guard: the rest of DEMO-53.
- SQL Server is the Developer edition: for development and test only.
- The texts are templates and drafts until a Swiss lawyer has read them (DEMO-42).
- Scanned PDFs are checked only where an OCR engine is installed.
