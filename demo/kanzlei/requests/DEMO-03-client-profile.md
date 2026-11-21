# DEMO-03: is this what our law-firm clients look like? (30 minutes, Siran)

`FEAT-2026-11-19-demo-law-firm`, item DEMO-03. The repositories do not say what a typical client has; the stand-in
was built on the assumptions below. Please mark each line **yes**, **no** or correct the number. No client is named
here and none should be named in your answers.

Every "no" changes `world.toml` or a list in the generator; nothing here is hard to change before a scenario runs.

## People

| # | Built | Yes / no / correction |
|---|-------|-----------------------|
| 1 | 13 people: 3 partners, 7 associates, 3 in the secretariat, one of them the bookkeeper (your decision of 20 Nov) | |
| 2 | Two practice teams (litigation; business law) | |
| 3 | People who left are still in the directory (disabled) and in old matters | |
| 4 | An external IT provider runs the directory and the file server; Knovas gets one read-only service account | |

## Matters and files

| # | Built | Yes / no / correction |
|---|-------|-----------------------|
| 5 | About 1,500 matters in 28 years; about 120 new matters a year now, 340 open | |
| 6 | About 31,000 documents in the practice software's store, 500 per numbered folder | |
| 7 | The software came in 2006; older matters exist as master data and as paper scans in an archive folder | |
| 8 | Most of the store is in formats we do not read (`.doc`, `.rtf`, TIFF scans, Excel, dictations). **This share is a choice for load, not an observation: what is the real share of unread formats?** | |
| 9 | File name `{GUID}_{AkteID}_{Typ}.{ext}`, with our matter reference (`2024-017`) as AkteID. **Is the AkteID at clients the reference a lawyer says aloud, or an internal number?** | |
| 10 | Outside the store: a common drive (templates, instructions, partners, personnel, accounting, scans not yet filed, dictations, IT), home drives, Outlook archives | |
| 11 | E-mails of a matter are saved into the store as `.msg`; everything else stays in Outlook, which Knovas cannot read | |

## Rights

| # | Built | Yes / no / correction |
|---|-------|-----------------------|
| 12 | By default everyone in the firm may open every matter | |
| 13 | A few matters are locked to their team **in the practice software**; the store's folders are open to all staff | |
| 14 | One information barrier: one associate is excluded from one matter | |
| 15 | The secretariat does not see fee notes and time sheets; the bookkeeper does | |
| 16 | Personnel and partner folders are restricted by folder permissions; the Knovas account can read them. Wages, home drives and mail archives it cannot | |

## Records

| # | Built | Yes / no / correction |
|---|-------|-----------------------|
| 17 | The practice database holds addresses, matters, parties, time entries, invoices, deadlines, matter rights and the document index. Knovas reads none of it | |
| 18 | We could get a read-only database login from the client's IT provider if we asked | |

## Ten kinds of questions lawyers really ask

In your words, not copied from any client's logs. The stand-in has 60 questions of five kinds (a fact in a matter, a
document, a step of a process, no answer in the files, asked by someone without the right). Which kinds are missing?

1.
2.
3.
4.
5.
6.
7.
8.
9.
10.
