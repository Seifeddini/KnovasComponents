# Cleanup of old parts (CLEANUP-1)

State on 21 November 2026. Overview and status words: [README.md](README.md).

## What it is

A maintenance item on the MVP's build branch that removes what the Platform no longer needs, so that the code, the
dependencies and the installed image hold only what is used. It follows the founder's word of 21 November: "while
you're working on knovas components, make sure that in your branch you clean everything old up that we will not need
anymore. Of course the hidden views like the custom views can stay for now". A read-only survey of the build branch
produced the list; the founder then decided the doubtful cases the same day.

## What a person can do with it

Nothing new: the point is that what people use keeps working. Search, opening a document from the search page (the
document view and the original's link), the shared company login and the hidden views must behave as before. Every
removed point is its own change, so each can be undone alone.

**To be removed**

| What | Why |
|---|---|
| Files nothing refers to; stale notes that say the design was taken out (it came back on 10 October) | dead |
| Unused style rules and unused program functions (each re-checked for callers first) | dead |
| Unused libraries (six, among them a one-time-password library) and the system packages for ODBC and Access database files | not imported anywhere; a smaller image |
| The Cortex marketing demo (Cortex itself stays) | founder |
| The sample search hits used for demonstrations | founder |
| The Open Companion and its token routes, and opening a file on the server | founder; the server-side opening was off by default and cannot work in the Linux container |
| The foundation of the typed-node workbench (keeping what the dossier uses) | founder |
| The legacy way of renewing certificates (the default way stays) | founder |
| The old, non-secured connection to the Core and the "mock" mode | founder |

**To be kept**

| What | Why |
|---|---|
| The shared company login (no personal accounts) | founder |
| The hidden views: Cortex, Experimente, Eigene Ansichten, Einstellungen, the Builder | founder |
| The assistant, chats, shell, Admin, search and document view, views, harness, migrations, web server and deployment files, CI definitions, frozen tests | not this item's |
| The repository's stand-in for the Core's interface, if the CI or contract tests use it | a CI change is critical; the builder stops and reports instead |

## How it protects data and access

Fewer ways in: the Open Companion's token routes, a server-side "open this file" route and the old non-secured
connection to the Core go away; the old connection's search wrote the question text into a log line. Fewer libraries
and system packages in the image.

## Where it stands

| Item | State | Approves before main |
|---|---|---|
| CLEANUP-1 | in build: the first point (dead files and stale notes) is done in the builder's branch; nothing pushed; builder's notes not written | Siran (sensitive: it removes features, dependencies and image packages) |

Nothing has been removed from the build branch yet. The builder must build the image once at the end to show the
removed packages are not needed, and the coordinator runs the whole test suite when it integrates.

## Decisions taken

| Date | Who | What |
|---|---|---|
| 21 Nov | founder | Clean up what is no longer needed; the hidden views can stay |
| 21 Nov | founder | Remove too: the Cortex marketing demo, the sample search hits, the Open Companion, the typed-node workbench foundation, the legacy certificate renewal, the one-time-password library, the non-secured connection and the mock mode. Keep: the shared company login and the hidden views |

## Known limits and open points

- **Mock mode against the plan.** The plan lets builders use the mock interface until the demo law firm's data arrives,
  and follow-up FU-10 gives the "mock profile as a fallback" to PMVP-02 (the development instance). The founder's
  cleanup removes the mock mode. Which one holds for PMVP-02 is **open**.
- If removing the mock mode needs a change to a CI definition, the builder stops and reports (CI definitions are
  critical).
- A test that breaks for any other reason than testing removed code means the code was not dead: it is put back and
  reported.
- Out-of-date documents noticed earlier (a design note, the Experimente feature page's menu entry) are partly in this
  item's first point; the rest is open.

## Depends on

The survey of 21 November; the tests of each removed point and the navigation's and search page's tests; one image
build at the end.

## Owner

No component card; the coordinator ordered it on the founder's word, a builder session executes it, Siran approves.

**Where the detail is:** `docs/platform-mvp/work-orders/CLEANUP-1.md` (branch `feat/platform-mvp`; the builder's
branch `maint/pmvp-cleanup-1` is not pushed yet).
