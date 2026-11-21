# Quality and verification

State on 21 November 2026. Overview and status words: [README.md](README.md).

## What it is

How AI agents build the MVP without anyone losing track of what was built, checked and approved. One coordinating
agent cuts the work into small items, writes a work order and acceptance tests for each before any code exists, starts
a builder and then a separate verifier, and asks the founder when a decision is missing or a gate fails twice. People
review and merge; no agent merges.

## What a person can do with it

- **Read one work order per item**: goal, scope, allowed files, "done" line, the builder's notes and the
  coordinator's record of each verification.
- **See every open finding** in the follow-up list (FU-01 to FU-38 so far), each with the item that takes it up.
- **Approve** an item by its class (table below) before the build branch reaches main.
- **Judge answers** on the harness's judging sheet (Siran and one pilot user): "richtig / falsch / unklar".

## How an item is built and checked

| Step | Who | What |
|---|---|---|
| 1 | coordinator | writes the work order and the frozen acceptance tests; never writes product code, never verifies, never merges, never decides an open decision (DEC-0081) |
| 2 | builder | a fresh agent session per item, in its own copy of the code and its own branch; makes the tests pass, updates the component card in the same change, writes its notes; may not edit a frozen test or mark its own work done |
| 3 | verifier | a **fresh** agent session with no memory of the build; runs the checks, reads the change against the work order, tries to break it, reports; it does not fix |
| 4 | coordinator | integrates a passed item into the build branch; notes become follow-ups |
| 5 | people | review and merge into main, by class |

**Frozen tests.** Five acceptance test files (chat storage, harness, brand look, the assistant's statements,
navigation) are recorded by fingerprint; a check shows any change. Only the coordinator changes one: once for a check
that was too strict, and to withdraw the assistant's first two files when its design changed.

**Two-failure rule.** After two failed attempts at a gate the coordinator stops and writes a decision request. It has
happened twice: the assistant was paused by the founder and redesigned (20 November); the harness was accepted by the
founder with its limit (21 November). In both records the coordinator names where its own order was wrong.

**Page tests.** The plan asks each page item to be tested signed out, with the wrong role, as the wrong owner and
without the form's security token, and one browser walkthrough per area at desktop and phone width (it skips where the
browser tool is not installed). Items that write log lines have a test that the lines carry no customer text.

## The test harness and its known limit

The harness (PMVP-03) runs the test questions through the assistant and scores each reply: answered from the expected
documents? a document leaked to a person without the right? the no-answer reply where the sources say nothing? A busy
or failed reply never counts as correct; the target (24 of 30) is printed beside the result. Customer text goes only
into the judging sheet, never inside a code repository. Until real question sets arrive it runs on invented stub files.

**Known limit (FU-36), accepted by the founder on 21 November:** two file names that differ only by "ß"/"ss", a
ligature or a Unicode form are treated as the same document, so a question could pass on the other document and a
correct rights table holding both names is refused. Before the 30 test questions run on the pilot (PMVP-60), the
pilot's real file names are checked for such pairs.

## The baseline of failing tests

Only the coordinator runs the whole Platform suite, at each integration (one shared database server makes parallel
runs take hours). Baseline of 20 November on the build branch: **137 failed and 8 errors**, mainly tests that need a
Unix system or a tool not installed on the Windows machine, and 30 of PMVP-10, not yet integrated then. Each
integration is compared with it; a new failure must be explained. One test fails only under load (FU-30) and is re-run.

## Classes of change and who approves

| Class | Means | Examples here | Approves before main |
|---|---|---|---|
| Routine | templates, styles, view pages, test tools | PMVP-01, 03, 10, 12, 22a, 30a, 40 | Jannik or Siran (Jannik merges) |
| Sensitive | sign-in and sessions, role checks, the reference check, what text reaches the model, removing features | PMVP-11, 20, CLEANUP-1 | Siran |
| Critical | certificates, tenancy, secrets, database changes, CI definitions, the enforcement switch | PMVP-21, 25, 42 | proposed by an agent, approved by management |
| Production | updating the pilot instance | PMVP-62 | both founders |

The repository's rule is stricter than the plan's table: any database migration and any secret is critical, which
made PMVP-21 and PMVP-25 critical where the plan said sensitive.

## Where it stands

| Item | State | Approves before main |
|---|---|---|
| PMVP-01 component cards | verified 20 Nov (with notes); integrated | Jannik or Siran (routine) |
| PMVP-03 test harness | failed verification twice; accepted by the founder with its limit; integrated | Jannik or Siran (routine) |
| PMVP-04 work-order template, daily status file | not started; no daily status file in the repository yet | Jannik or Siran (routine) |

Verification records exist for PMVP-01, 03, 10, 11, 12, 20 and 21 and for the log sweep; none yet for 22a, 25, 30a.

## Decisions taken

| Date | Who | What |
|---|---|---|
| 19 Nov | founder | A coordinator role; Jannik merges routine, Siran the rest; no agent merges (DEC-0081) |
| 20 Nov | coordinator | A busy or failed reply is never "correct"; a rights question with a passage the rights table does not know for that person is not correct (21 Nov) |
| 21 Nov | founder | The harness is accepted with its limit (FU-36) |

## Known limits and open points

- **A mechanical check is not a judgement (FU-27):** the harness reports how many statements were removed and why; the
  people who judge read statement against quote.
- **Builders and verifiers do not run the whole suite**; they run the tests their order names.
- **Records lag the build:** the bet card in KnowledgeBase still lists integrated items as open (the coordinator
  writes it); the assistant's card names withdrawn test files (FU-18).

## Depends on

The people's review time (about 15 hours a week for three people); the demo law firm's test questions and rights table;
the pilot's own (PMVP-43); a test database of its own for each test run.

## Owner

The coordinator role (an agent session) for the process; Siran for approval of the plan and its decisions.

**Where the detail is:** `docs/platform-mvp/README.md`, `docs/platform-mvp/frozen-tests.txt`,
`docs/platform-mvp/baseline-failing-ids.txt`, `docs/platform-mvp/work-orders/PMVP-03.md`,
`docs/platform-mvp/follow-ups.md`; KnowledgeBase plan § 6 and § 11, decision DEC-0081.
