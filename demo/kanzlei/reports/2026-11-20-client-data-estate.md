# The demo law firm with a client's data estate (20 November 2026)

`FEAT-2026-11-19-demo-law-firm` (bet `BET-2026-11-brain-builder`). Items DEMO-10 (the part that needs no tenant),
DEMO-11, DEMO-12, DEMO-20, the first part of DEMO-53, the drafts of DEMO-02 and DEMO-03, and the new work the founder asked for on 19 and 20
November: the data as at a client (database, file share, directory), 10 lawyers and 3 secretaries, no tenant yet, and
sources real enough to check the connection layer against. Serves S1 to S5 and the scenario "the firm's records".

Written by the agent that built it. **Not verified by anyone else**: a fresh session or the other founder verifies.

## 1. What exists now

| Part | Where | State |
|------|-------|-------|
| Generator, client layout | `KnovasConnector/scripts/demo_kanzlei/` (`--layout client`) | Built. 69 tests pass |
| Site: file server, directory, SQL Server | `demo/kanzlei/site/` | Built. Runs on this PC in Docker |
| Truth files of the plan's §6 | `<work>/truth/` | Built: 60 questions (30 in the MVP set), 9,672 rights rows, 13 people, 8 process states |
| Oracle for the connection layer | `<work>/truth/sources.json`, `records/*.jsonl`; `site/check-records.sh` | Built and proven against the running database |
| One working day as a change to the sources (DEMO-53, first part) | `<work>/site/changes/tag-1`, `<work>/truth/changes/tag-1`; `site/apply-changes.sh`, `site/check-share.sh` | Built and proven on the running site. The directory does not change yet, and there is no mass deletion |
| One page to start, stop, reset | `demo/kanzlei/README.md` | Written |
| Requests to a person | `demo/kanzlei/requests/` | DEMO-02 (tenant, certificates), DEMO-03 (client profile): drafts, not sent |

Not built: anything that needs a tenant (the Connector's upload, search, the Builder), the scenario scripts (DEMO-50),
the rest of the change script (directory changes, a mass deletion, a source that is unreachable), the mail source.

## 2. What was run, and the result

All on 20 November 2026, on the full build (`--layout client --full`, seed 20260906, `as_of` 2026-11-18).

| Check | Result |
|-------|--------|
| Generator's tests (`tests/unit/demo_kanzlei`) | 69 passed (30 from before, unchanged; 39 new) |
| Build gates: world, plan, write, records, file, mess, site, ground truth, verify | all passed. `verify`: 10 scanned PDFs **not checked**, no OCR engine on this PC |
| Two builds from the same world (small build, both layouts) | identical file for file, and the same file times in the client layout (POST-01). Before: 19 of 31 files differed |
| Company names against the commercial register (Zefix) | 785 names checked, 0 in the register (after one rename, §4) |
| Site smoke test (`smoke.sh`) | 22 of 22 passed |
| Database against truth (`check-records.sh`) | 23 of 23 tables equal, row for row, read with the read-only login |
| The same check after changing three rows by hand | reported all three (a value, a trailing space, a removed row): the check is not empty |
| Share against the manifest (`check-share.sh`: path and SHA-256 of every file) | 31,838 of 31,838 identical |
| Before the working day, against the state after it | 11 tables differ, as they must |
| The working day applied (`apply-changes.sh`: 65 statements, 3 files added or saved over, 3 deleted) | afterwards 23 of 23 tables and 31,837 of 31,837 files equal the expected state |
| The build loaded again | database and share equal the build again; the site was then stopped |

What the smoke test proves, in a client's terms: the share is reached by IP address over SMB 3 and is hidden; the
Knovas account can read and cannot write; it is refused on wages, home drives and mail archives; a partner reads the
partners' folder through two levels of nested groups and an associate is refused; a person who left cannot log in;
the directory answers over LDAPS with 13 enabled people; the database login reads and cannot write; all 31,838 files
arrived with their own dates; and the share mounts read-only with the kernel's CIFS client, as on a Connector host.

Sizes of the full build: 31,838 files, 122 MB, 63 numbered folders in the store. The Connector would read 743 of them
(703 in the store, 40 on the common drive). Database: 1,500 matters, 2,500 addresses, 22,504 time entries, 3,363
invoices, 2,296 deadlines, 1,174 appointments, 2,927 notes, 31,153 document rows. Build 7 minutes on Windows, load 2.

## 3. Decisions I took where the plan was silent (to confirm)

| # | Ruling | Why | Cost if wrong |
|---|--------|-----|---------------|
| 1 | The AkteID in a file name is the matter reference (`2024-017`), as in the plan's §6 example | Keeps the contract with the Platform plan | A rename of all store files; DEMO-03 asks you what clients have |
| 2 | Only the written matters are readable; the rest of the store is placeholders in formats the Connector skips | Plan §8.2: do not load the Core | One knob to add readable placeholders after the capacity check |
| 3 | Matter rights are in the database only (`AKTENRECHTE`); the store's folders are open to all staff | This is the situation the plan describes as intended | none: it is the finding |
| 4 | A `rights` question has no expected points; its pointers are the files that must not be cited | §6 does not say | The harness (PMVP-03) must read it this way: please confirm with its coordinator |
| 5 | `db:<TABLE>/<key>` pointers for answers that are only in the database; 4 such questions, none in the MVP set | They fail until Knovas reads records: that is scenario "the firm's records" | none |
| 6 | The information barrier is a lateral hire (an associate who came from the other side's firm), not "a company that is client in one matter and opponent in another" | Simpler to explain and to check; same rule shape (`conflict:<matter>`) | A second planted case |
| 7 | The database schema is invented, with the traits old practice databases share | Vendors publish no schema (searched 20 Nov); Vertec's documentation confirms plain tables without views or procedures | D-B5 (a vendor's test installation) would replace it |
| 8 | Mail is not a source: `.msg` in the store, `.pst` archives otherwise | D-B4 is open | The decision request below |
| 9 | The stand-in's "today" is fixed in the world file (18 Nov 2026) | Otherwise two builds differ | Move the date and rebuild |

## 4. Defects found in the generator (not Platform findings)

| # | What | Done |
|---|------|------|
| 1 | **The name screening never worked.** Zefix answers "no such firm" with HTTP 404; the code took that for a failed request, so no name could pass and builds were always run with `--skip-zefix` | Fixed; three tests |
| 2 | **A real company name was in the generator since September**: "Alpenblick Gastro GmbH" is an active firm in the register | Renamed to "Sennenfluh Gastro GmbH"; the screening now covers every company the generator can name |
| 3 | Two builds differed (document times, PDF ids) | Fixed (POST-01) |
| 4 | `manifest.jsonl` was written into the share | Moved to the work folder |
| 5 | `verify` failed the full build, and passed the small one, for the same missing OCR engine | Now "not checked" in both |
| 6 | All 33 simply written matters were opened on the same day in 2019; some documents were dated after today | Opening days rise with the running number; nothing is dated after `as_of` |

## 5. What the stand-in already shows, before any scenario has run

These are **not findings yet** (a finding needs a run against the Platform). They are what I expect S2, S4 and "the
firm's records" to turn up, so that nobody is surprised:

- Who the client of a matter is, who works on it and who may open it is in the database. Knovas reads no database.
- The two locked matters and the information barrier cannot be expressed by folder: every file sits in an open folder.
- The store holds lock files (`~$….docx`), empty and cut-off files and Latin-1 text. Whether the Connector skips,
  reports or uploads them is not known.
- All file times are old. With the Connector's default age filter almost nothing would be uploaded.
- The Knovas account cannot read three folder trees. Whether the Platform says so or shows nothing is not known.
- The court's own number of a matter and the notes typed into the software are in no file.

## 6. Open, and who has to act

| What | Who |
|------|-----|
| Tenant, test tenant, certificates, where the site runs: [`requests/DEMO-02-tenant-and-site.md`](../requests/DEMO-02-tenant-and-site.md) | Siran, both founders for production (D-B2, D-B3) |
| Client profile, 30 minutes: [`requests/DEMO-03-client-profile.md`](../requests/DEMO-03-client-profile.md) | Siran |
| Mail as a source: a Microsoft 365 tenant (D-B4), or stay with `.msg` and `.pst` | Siran |
| Rulings 1, 4 and 6 above | Siran; ruling 4 also the Platform plan's coordinator |
| The plan's text: §3, §4, §5.3 and §9 still describe 7 people, no record source and no database | the agent that wrote the plan (it has the instruction) |
| Verification of this work | a fresh session or the other founder |
| Push of branch `feat/demo-kanzlei` and pull requests | on your word: nothing is pushed |

Next items once a tenant exists: DEMO-10 (the Connector reads the store, first search), DEMO-21 to DEMO-23. Without a
tenant: the rest of DEMO-53 (a joiner, a leaver and a move between teams in the directory; one mass deletion for the
deletion guard; a source that is unreachable), DEMO-50 (scenario scripts), DEMO-30/31 (the Builder checklist).

## 7. The working day (DEMO-53, first part)

The day after the stand-in's today, 19 November 2026, as a change to the database and the share. The statements are
computed as the difference between the tables before and after, so they cannot disagree with the truth.

| What happens | What it tests in whatever reads the sources |
|--------------|---------------------------------------------|
| 24 time entries on twelve matters; six of them dated a day last week | finding new rows by entry time, not by their date |
| Two time entries removed | finding absence where nothing flags it |
| Three addresses changed, a matter closed, two deadlines done, an invoice paid | updates, with `MutiertAm` and `rowversion` moving |
| The scanned court order filed to matter 2024-017: a new file in the store, the scan gone, an appointment and a deadline entered | a move across folders; database and share changing together |
| A note written in matter 2024-017 with a fact that is in no other file | one new question becomes answerable; the associate behind the information barrier must still be refused |
| A document saved over under its old name | changed content, same path |
| One document removed in the software, one file deleted by hand | a flagged row with its file gone; a row that still claims a file |

The directory does not change on that day, and no rights change. Both are the rest of DEMO-53.
