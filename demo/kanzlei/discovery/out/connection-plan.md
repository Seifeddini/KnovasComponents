# Connection plan: quarzfels

Made by knovas-discovery 0.1.0 from the scan of 2026-11-20T17:13:20Z. Sector: law-firm; 13 people, 10 lawyers; IT provider: external.

Phases follow the class of each source. Prices read "price not set" until the founders set them in `pricing.yaml`.

## Phase 1: standard (class A)

| Source | Kind | Component | How | Price |
|---|---|---|---|---|
| `kanzlei:Quarzfels/Gemeinsam` | File share (common drive) | Knovas Connector | The share is mounted read-only on the Connector's machine; the folder is its watch root. | price not set |
| `kanzlei:Quarzfels/Gemeinsam` | Scanned post that nobody has filed | Knovas Connector (OCR) | Read like any PDF; image-only pages go through OCR. | price not set |

## Phase 2: standard with mapping (class B)

| Source | Kind | Component | What is set up or mapped | Price |
|---|---|---|---|---|
| `ad` | Active Directory | Platform (Verwaltung) | People and groups are entered in the Platform by hand today. Reading groups from the directory is not built. | price not set |
| `kanzlei:Quarzfels/Praxis/Autodoc` | Document store of a practice software (AutoDoc) | Knovas Connector, Brain Builder | Watch root on the store, one numbered folder per cycle. The matter comes from the file name through a file-pattern mapping in the Builder. | price not set |

## Phase 3: custom (class C)

| Source | Kind | Effort points | Effort class | Price |
|---|---|---|---|---|
| `praxis:KanzleiDB` | Database of a legal practice software (product not recognised) | 32 | XL | price not set |

### Effort of `praxis:KanzleiDB` (Database of a legal practice software (product not recognised))

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

## Not now (class D)

| Source | Kind | Why |
|---|---|---|
| `kanzlei:Quarzfels/Gemeinsam` | Dictation files | Not read. |
| `kanzlei:Quarzfels/Praxis/Autodoc` | Dictation files | Not read. |
| `kanzlei:Quarzfels/Mail` | Nothing the account can read | The scan saw nothing here with the account it was given. |
| `kanzlei:Quarzfels/Users` | Nothing the account can read | The scan saw nothing here with the account it was given. |

## What we need from the client

1. A service account that can read the folders we may index, and nothing else.
2. The share's address as the server sees it (IP address or name), and the SMB version.
3. A machine in their network for the Knovas site (Docker), or a virtual machine from their IT provider.
4. Which folders are out of scope (personnel, wages, partners), named by the client, in writing.
5. A decision: index the scan folder, or only what has been filed to a matter.
6. The list of people who get Knovas, and to which groups each belongs for the purpose of access.
7. A read-only account for the directory, if groups are to be compared with what is entered.
8. What the second part of a file name is: the reference a lawyer says aloud, or an internal number.
9. Whether e-mails are saved into the store (as .msg) and by whom.
10. A read-only database login, created by the IT provider.
11. The vendor's name, the product and its version; the vendor's consent if their contract requires it.
12. Someone at the firm who knows what the important fields mean.

## Open questions

1. Which folders must never be indexed?
2. Who may see what today: do folder permissions say it, or does everybody see everything?
3. How long does post stay in the scan folder before it is filed?
4. Who adds and removes people today, the firm or the IT provider?
5. Is the directory synchronised to Microsoft 365 (Entra Connect)?
6. Since when is the store in use, and where are the files from before?
7. Are matters locked for some people in the practice software?
8. Which product is it, and who is the vendor's contact?
9. Does the vendor offer an export or an interface that is supported across updates?
10. Which records matter most: clients and matters, deadlines, time and invoices?
11. `kanzlei:Quarzfels/Mail`: is it right that the account sees nothing here, or was the wrong account or folder given?
12. `kanzlei:Quarzfels/Users`: is it right that the account sees nothing here, or was the wrong account or folder given?
13. The intake names no target for Microsoft 365: does the client have one?

## Probes that failed, were partial or were skipped

None: every probe ran to the end.

Not looked at, because intake.yaml names no target: Microsoft 365.
