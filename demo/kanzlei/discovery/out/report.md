# Discovery report: quarzfels

Made by knovas-discovery 0.1.0 from the scan of 2026-11-20T17:13:20Z. Sector: law-firm; 13 people, 10 lawyers; IT provider: external.

This report holds numbers, patterns, and the names of database tables and columns. It holds no name of a person, client, file, folder or group: those are in `detail.json`, which stays at the client.

## Summary

| Class | Meaning | Sources |
|---|---|---|
| A | standard: a released component connects it with configuration only | 2 |
| B | standard with mapping: a released component connects it; something is mapped for this client | 2 |
| C | custom: no component reads it; a connector or an export has to be built | 1 |
| D | not now: we cannot or should not connect it | 4 |

## Sources

| # | Source | Kind | Class | Knovas today | Price |
|---|---|---|---|---|---|
| 1 | `kanzlei:Quarzfels/Gemeinsam` | File share (common drive) | A | released (Knovas Connector) | price not set |
| 2 | `kanzlei:Quarzfels/Gemeinsam` | Scanned post that nobody has filed | A | released (Knovas Connector (OCR)) | price not set |
| 3 | `ad` | Active Directory | B | partly (Platform (Verwaltung)) | price not set |
| 4 | `kanzlei:Quarzfels/Praxis/Autodoc` | Document store of a practice software (AutoDoc) | B | released (Knovas Connector, Brain Builder) | price not set |
| 5 | `praxis:KanzleiDB` | Database of a legal practice software (product not recognised) | C | none | price not set |
| 6 | `kanzlei:Quarzfels/Gemeinsam` | Dictation files | D | none | not offered |
| 7 | `kanzlei:Quarzfels/Praxis/Autodoc` | Dictation files | D | none | not offered |
| 8 | `kanzlei:Quarzfels/Mail` | Nothing the account can read | D | none | not offered |
| 9 | `kanzlei:Quarzfels/Users` | Nothing the account can read | D | none | not offered |

### 1. File share (common drive): `kanzlei:Quarzfels/Gemeinsam`

Class **A** (standard: a released component connects it with configuration only), catalogue entry `fileshare`, found by the scan.

Knovas today: released (Knovas Connector). The share is mounted read-only on the Connector's machine; the folder is its watch root.

What the scan saw: 590 files (3.5 MB) in 162 folders; 40 (6.8%) in formats the Connector reads; newest file 2 days old, oldest 28 years old; 2 folders refuse the account.

Why this kind: `files` > 0 (measured: 590).

Risks:

- Folder permissions do not travel: who may see a document in Knovas is entered in Knovas.
- Old file dates: the Connector must run without a limit on file age.
- Only 7% of the files are in a format the Connector reads. Ask what the rest is before promising search over 'all documents'.
- 2 folders refuse the account. Confirm with the client that each is meant to be out of scope.
- 1 Office lock files (~$...) have an extension the Connector reads. They are not documents.

What we need from the client:

- A service account that can read the folders we may index, and nothing else.
- The share's address as the server sees it (IP address or name), and the SMB version.
- A machine in their network for the Knovas site (Docker), or a virtual machine from their IT provider.
- Which folders are out of scope (personnel, wages, partners), named by the client, in writing.

Questions:

- Which folders must never be indexed?
- Who may see what today: do folder permissions say it, or does everybody see everything?

### 2. Scanned post that nobody has filed: `kanzlei:Quarzfels/Gemeinsam`

Class **A** (standard: a released component connects it with configuration only), catalogue entry `scanner-output`, found by the scan.

Knovas today: released (Knovas Connector (OCR)). Read like any PDF; image-only pages go through OCR.

What the scan saw: 590 files (3.5 MB) in 162 folders; 40 (6.8%) in formats the Connector reads; newest file 2 days old, oldest 28 years old; 2 folders refuse the account.

Why this kind: `patterns.scanner_files` >= 3 (measured: 6).

Risks:

- Unfiled post has no matter and no rights; a confidential letter in the scan folder is visible to everyone who may see the folder.

What we need from the client:

- A decision: index the scan folder, or only what has been filed to a matter.

Questions:

- How long does post stay in the scan folder before it is filed?

### 3. Active Directory: `ad`

Class **B** (standard with mapping: a released component connects it; something is mapped for this client), catalogue entry `active-directory`, found by the scan.

Knovas today: partly (Platform (Verwaltung)). People and groups are entered in the Platform by hand today. Reading groups from the directory is not built.

What the scan saw: Active Directory: 13 people enabled, 7 disabled, 4 service accounts; 7 groups (0 empty), nested 2 deep; 1 computers.

Why this kind: `is_active_directory` == True (measured: yes).

Risks:

- Two lists of people to keep: the directory and Knovas. A person who leaves must be removed in both.
- Nested groups: who is in a group is not what the group's member list shows.
- Groups are nested 2 levels deep. Membership has to be resolved through the nesting.
- 7 disabled accounts: people who left are kept, not deleted.

What we need from the client:

- The list of people who get Knovas, and to which groups each belongs for the purpose of access.
- A read-only account for the directory, if groups are to be compared with what is entered.

Questions:

- Who adds and removes people today, the firm or the IT provider?
- Is the directory synchronised to Microsoft 365 (Entra Connect)?

### 4. Document store of a practice software (AutoDoc): `kanzlei:Quarzfels/Praxis/Autodoc`

Class **B** (standard with mapping: a released component connects it; something is mapped for this client), catalogue entry `autodoc-store`, found by the scan.

Knovas today: released (Knovas Connector, Brain Builder). Watch root on the store, one numbered folder per cycle. The matter comes from the file name through a file-pattern mapping in the Builder.

What the scan saw: 31,175 files (114.8 MB) in 63 folders; 703 (2.3%) in formats the Connector reads; newest file 2 days old, oldest 20 years old; 0 folders refuse the account.

Why this kind: `patterns.autodoc_names_share` >= 0.3 (measured: 0.9987).

Risks:

- The folders are number ranges: no folder says whose files it holds, and rights cannot be set by folder.
- Client, matter team and matter rights are in the practice software's database, not on the share.
- A large part of an old store is in formats the Connector does not read (.doc, TIFF scans).
- Only 2% of the files are in a format the Connector reads. Ask what the rest is before promising search over 'all documents'.
- 63 numbered folders: configure the Connector for one folder per cycle (sequential_subfolders) for the first read.
- 20 Office lock files (~$...) have an extension the Connector reads. They are not documents.

What we need from the client:

- A service account that can read the folders we may index, and nothing else.
- The share's address as the server sees it (IP address or name), and the SMB version.
- A machine in their network for the Knovas site (Docker), or a virtual machine from their IT provider.
- What the second part of a file name is: the reference a lawyer says aloud, or an internal number.
- Whether e-mails are saved into the store (as .msg) and by whom.

Questions:

- Since when is the store in use, and where are the files from before?
- Are matters locked for some people in the practice software?

### 5. Database of a legal practice software (product not recognised): `praxis:KanzleiDB`

Class **C** (custom: no component reads it; a connector or an export has to be built), catalogue entry `practice-database`, found by the scan.

Knovas today: none. No component reads records from a database yet. Needs a connector of its own kind behind Knovas Link, or a scheduled export the sync component reads.

What the scan saw: 23 tables, 73,994 rows: 12 records, 5 lookup, 4 software, 2 leftover; 0 foreign keys; SQL Server 2022, Developer Edition (64-bit), mixed authentication.

Why this kind: `vocabulary.legal_practice` >= 4 (measured: 8).

Risks:

- Vendors do not publish their tables: the mapping is worked out on site and can break with an update.
- Matter rights live here. Until Knovas reads them, a matter locked in the software is open in Knovas.
- Reading a production database needs care: read-only login, off-peak hours, no long transactions.
- The database declares no relations between its tables. They have to be worked out from column names, with someone who knows the software.
- 1 tables look as if they say who may see what. Until Knovas reads them, restrictions set in the software do not hold in Knovas.
- 2 tables look like backups or imports. They are not the client's current data and must not be mapped.

What we need from the client:

- A read-only database login, created by the IT provider.
- The vendor's name, the product and its version; the vendor's consent if their contract requires it.
- Someone at the firm who knows what the important fields mean.

Questions:

- Which product is it, and who is the vendor's contact?
- Does the vendor offer an export or an interface that is supported across updates?
- Which records matter most: clients and matters, deadlines, time and invoices?

Effort, counted from the scan (effort classes: S up to 8 points, M 9 to 16, L 17 to 26, XL above):

| Driver | Measured | Points by rule | Points |
|---|---|---|---|
| Tables that hold the firm's records | 12 record tables | 1 each, at most 12 | 12 |
| No foreign keys (relations have to be worked out) | 0 declared | none: 3 | 3 |
| Change markers on the record tables | 7 of 12 record tables have one (some) | all 0, some 2, none 5 | 2 |
| Rows that are removed without a flag | 9 record tables without a delete flag | present: 3 | 3 |
| Rights kept in the source | 1 rights tables | present: 5 | 5 |
| Fields the client defined itself | 2 custom-field tables | present: 2 | 2 |
| Product not in the catalogue | product not recognised | present: 5 | 5 |
| More than a million rows in the record tables | 73,164 rows in record tables | over one million: 2 | 0 |
| Only Windows authentication | authentication: mixed | Windows only: 2 | 0 |
| **Total** |  |  | **32** (effort class **XL**) |

### 6. Dictation files: `kanzlei:Quarzfels/Gemeinsam`

Class **D** (not now: we cannot or should not connect it), catalogue entry `dictations`, found by the scan.

Knovas today: none. Not read.

What the scan saw: 590 files (3.5 MB) in 162 folders; 40 (6.8%) in formats the Connector reads; newest file 2 days old, oldest 28 years old; 2 folders refuse the account.

Why this kind: `patterns.dictation_files` >= 1 (measured: 30).

### 7. Dictation files: `kanzlei:Quarzfels/Praxis/Autodoc`

Class **D** (not now: we cannot or should not connect it), catalogue entry `dictations`, found by the scan.

Knovas today: none. Not read.

What the scan saw: 31,175 files (114.8 MB) in 63 folders; 703 (2.3%) in formats the Connector reads; newest file 2 days old, oldest 20 years old; 0 folders refuse the account.

Why this kind: `patterns.dictation_files` >= 1 (measured: 1,215).

### 8. Nothing the account can read: `kanzlei:Quarzfels/Mail`

Class **D** (not now: we cannot or should not connect it), catalogue entry `no-access`, found by the scan.

Knovas today: none. The scan saw nothing here with the account it was given.

What the scan saw: no file visible to the account; 0 folders seen, 1 refuse the account.

Why this kind: no catalogue entry matches, and the account sees nothing here.

Risks:

- 1 folders refuse the account. Confirm with the client that each is meant to be out of scope.

Questions:

- `kanzlei:Quarzfels/Mail`: is it right that the account sees nothing here, or was the wrong account or folder given?

### 9. Nothing the account can read: `kanzlei:Quarzfels/Users`

Class **D** (not now: we cannot or should not connect it), catalogue entry `no-access`, found by the scan.

Knovas today: none. The scan saw nothing here with the account it was given.

What the scan saw: no file visible to the account; 0 folders seen, 1 refuse the account.

Why this kind: no catalogue entry matches, and the account sees nothing here.

Risks:

- 1 folders refuse the account. Confirm with the client that each is meant to be out of scope.

Questions:

- `kanzlei:Quarzfels/Users`: is it right that the account sees nothing here, or was the wrong account or folder given?

## File shares in numbers

| Fact | `kanzlei:Quarzfels/Praxis/Autodoc` | `kanzlei:Quarzfels/Gemeinsam` | `kanzlei:Quarzfels/Users` | `kanzlei:Quarzfels/Mail` |
|---|---|---|---|---|
| Files | 31,175 | 590 | 0 | 0 |
| Size | 114.8 MB | 3.5 MB | 0 bytes | 0 bytes |
| Folders | 63 | 162 | 0 | 0 |
| Deepest level | 1 | 3 | 0 | 0 |
| Files the Connector reads | 703 (2.3%) | 40 (6.8%) | 0 (?) | 0 (?) |
| Folders that refuse the account | 0 | 2 | 1 | 1 |
| Newest file, days | 2 | 2 | ? | ? |
| Oldest file, days | 7,618 | 10,433 | ? | ? |
| Files < 1 year / 1-5 / 5-10 / > 10 years | 5,162 / 9,215 / 7,945 / 8,853 | 56 / 21 / 8 / 505 | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 |
| AutoDoc names (share of files) | 99.9% | 0.0% | ? | ? |
| Number-range folders | 63 | 0 | 0 | 0 |
| Office lock files | 20 | 1 | 0 | 0 |
| Outlook archives | 0 | 0 | 0 | 0 |
| Dictation files | 1,215 | 30 | 0 | 0 |
| Scanner output | 1 | 6 | 0 | 0 |
| Files without extension | 0 | 0 | 0 | 0 |
| Paths over 240 characters | 0 | 0 | 0 | 0 |
| Empty files | 8 | 1 | 0 | 0 |
| Largest file | 91.2 kB | 91.0 kB | 0 bytes | 0 bytes |
| Listing: entries per second | 48,126.4 | 11,095.5 | ? | ? |
| Listing: seconds | 0.6 | 0.1 | 0 | 0 |
| First read: files to upload | 703 | 40 | 0 | 0 |
| First read: hours at 4 per minute | 2.9 | 0.2 | 0 | 0 |

Files per extension (the ten most frequent per root):

- `kanzlei:Quarzfels/Praxis/Autodoc`: .doc 16,334, .tif 4,681, .jpg 3,362, .rtf 2,708, .dss 1,215, .xlsx 1,177, .xls 397, .zip 269, .htm 267, .docx 201
- `kanzlei:Quarzfels/Gemeinsam`: .doc 249, .rtf 87, .wpd 86, .tif 81, .dss 30, .pdf 24, .docx 12, .xlsx 7, .txt 4, .csv 1
- `kanzlei:Quarzfels/Users`: none
- `kanzlei:Quarzfels/Mail`: none

## Directories in numbers

| Fact | `ad` |
|---|---|
| Active Directory | yes |
| People, enabled | 13 |
| People, disabled | 7 |
| Service accounts | 4 |
| Groups | 7 |
| Groups without members | 0 |
| Deepest group nesting | 2 |
| Computers | 1 |

## Database `praxis:KanzleiDB` in numbers

23 tables, 73,994 rows, 0 foreign keys, 2 views and 0 procedures visible to the login. Product: not recognised. Words of a legal practice in table names: 8 of 10.

Roles are guesses from names, keys, columns and row counts; the consultant confirms them.

| Table | Rows | Columns | Key | Foreign keys | Rowversion | Changed at | Delete flag | Role |
|---|---|---|---|---|---|---|---|---|
| `AKTEN_BACKUP_20190312` | 711 | 7 | (none) | 0 |  |  |  | leftover |
| `tmp_Import_Adressen` | 30 | 6 | (none) | 0 |  |  |  | leftover |
| `FRISTARTEN` | 6 | 2 | FristArtID | 0 |  |  |  | lookup |
| `LEISTUNGSARTEN` | 9 | 3 | LeistArt | 0 |  |  |  | lookup |
| `RECHTSGEBIETE` | 22 | 2 | RgID | 0 |  |  |  | lookup |
| `ROLLEN` | 7 | 2 | RolleID | 0 |  |  |  | lookup |
| `ZUSATZFELDER` | 5 | 4 | FeldID | 0 |  |  |  | lookup |
| `ADRESSEN` | 2,500 | 20 | AdrNr | 0 | yes | ErfasstAm, MutiertAm | Inaktiv | records |
| `AKTEN` | 1,500 | 20 | AkteNr | 0 | yes | ErfasstAm, MutiertAm |  | records |
| `AKTENBETEILIGTE` | 4,578 | 3 | AkteNr, AdrNr, RolleID | 0 |  |  |  | records |
| `AKTENRECHTE` | 61 | 4 | AkteNr, Kuerzel | 0 |  |  |  | records |
| `DOKUMENTE` | 31,153 | 13 | DokNr | 0 | yes | ErstelltAm | Geloescht | records |
| `FRISTEN` | 2,296 | 10 | FristNr | 0 |  | ErfasstAm, MutiertAm |  | records |
| `JOURNAL` | 2,927 | 6 | JournalNr | 0 |  |  |  | records |
| `LEISTUNGEN` | 22,504 | 13 | LeistNr | 0 | yes | ErfasstAm, MutiertAm |  | records |
| `MITARBEITER` | 20 | 8 | Kuerzel | 0 |  |  | Aktiv | records |
| `RECHNUNGEN` | 3,363 | 13 | RechNr | 0 | yes | ErfasstAm, MutiertAm |  | records |
| `TERMINE` | 1,174 | 9 | TerminNr | 0 |  | ErfasstAm |  | records |
| `ZUSATZFELDWERTE` | 1,088 | 3 | FeldID, Schluessel | 0 |  |  |  | records |
| `SYS_BENUTZER` | 21 | 5 | Login | 0 |  |  |  | software |
| `SYS_NUMMERN` | 8 | 2 | Bereich | 0 |  |  |  | software |
| `SYS_PARAMETER` | 7 | 2 | Name | 0 |  |  |  | software |
| `SYS_SPERREN` | 4 | 4 | Tabelle, Schluessel | 0 |  |  |  | software |

## Named by the client

| Category | Product | Result | Catalogue entry |
|---|---|---|---|
| practice | unknown | confirmed by the scan | `practice-database` |

## Environment of the scan

| Fact | Value |
|---|---|
| Operating system | Linux 6.18.58.1-microsoft-standard-WSL2 |
| Python | 3.13.18 |
| Inside a container | yes |
| Docker | not found |
| CPUs | 8 |
| Memory | 33.7 GB |
| Free disk | 3.2 GB |
| Knovas API host answers | yes |
| Proxy variables set | none |

## Probes that failed, were partial or were skipped

None: every probe ran to the end.

Not looked at, because intake.yaml names no target: Microsoft 365.
