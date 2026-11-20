# Work order PMVP-03: the acceptance harness

| | |
|---|---|
| Item | PMVP-03 (Jira SS-727), plan `FEAT-2026-11-19-platform-mvp` § 11 (test plan and measures), § 8 point 4 (data for building and testing) |
| Bet | `BET-2026-11-platform-mvp` (approved 2026-11-19) |
| Class | routine (test tooling; no product code, nothing in the image) |
| Branch / worktree | `feat/pmvp-03` from `feat/platform-mvp`, worktree `.worktrees/pmvp-03` |
| Frozen tests | `P/tests/test_pmvp03_harness_acceptance.py`, blob id in `docs/platform-mvp/frozen-tests.txt` (the check command is in that file). `P` = `KnovasPlatform/components/docbridge_integration` |

## Goal

The tools every later item and the pilot use to show their work: (A) run the test questions through the assistant
and score the replies against the plan's measures, (B) one helper for the page tests every page item needs, (C) one
browser walkthrough per area as evidence on a pull request.

The assistant itself (PMVP-20) is paused and not on this branch. The harness does not need it: it scores **a reply**
(the shape the PMVP-20 acceptance tests pin: `.status`, `.sentences` with `.text` and `.refs`, `.passages` with
`.number` and `.pointer`, `.removed`, `.message`) and asks through a function the caller hands in.

## A. The package `P/tests/pmvp_harness/` (the frozen tests pin it)

Lives in the tests folder on purpose: the image copies `src/` and `config/` only, so the harness never reaches a
customer. Importable as `pmvp_harness` the way `tests/builder_harness.py` is.

- `load_questions(path, *, only_mvp_set=False)`, `load_rights(path)`, `score(question, reply, *, rights=None,
  pointer_prefix="")`, `run(questions, ask, *, rights=None, pointer_prefix="")`, `write_report(report, dest)`.
  What each returns and how the automatic verdict `ok` is decided is written at the top of the frozen file.
- The file formats are the demo law firm plan's: see `KnovasConnector/scripts/demo_kanzlei/truth.py` on branch
  `origin/claude/lawyer-demo-corpus-plan-acgbns` or in worktree `.worktrees/demo-kanzlei` (read only; `write_truth`,
  `_Questions.add`, `rights_rows`). If the format there differs from the frozen file's, stop and say where.
- **A command line** on top (your own tests): `py -3 -m pmvp_harness score --questions <file> --replies <file>
  [--rights <file>] [--pointer-prefix <text>] [--mvp-set] --out <folder>`, run from `P/tests`. `--replies` is a
  JSON-lines file, one record per question id with the reply's fields, so replies recorded on any instance can be
  scored without the assistant being importable. It prints the summary (numbers only) and exits 0 when it ran,
  whatever the score; the plan's target (at least 24 of the 30 in the measured set) is printed beside the result,
  not enforced.
- **Stub truth files** in `P/tests/fixtures/pmvp/`: `questions.stub.jsonl` (ten to fifteen records, all five
  kinds, at least two `rights` and two `negative`), `rights.stub.csv`, `replies.stub.jsonl` (some right, some
  wrong, one leak, one missing). Invented law-firm data in the demo plan's style; no real names.
- `judging.md` is for the people who judge (Siran and one pilot user): per question the text, the shown answer
  with its numbers, the passages (number, document, page when the reply has one), the automatic marks, `removed`,
  and a line to tick "richtig / falsch / unklar". When a reply carries the model's unchecked answer (attribute
  `raw_answer`, optional, PMVP-20 may add it), show it beside the shown one so a judge sees what the check cut.

## B. Page-test helper `P/tests/pmvp_pages.py` (your own tests)

One function per check the plan asks of every page (§ 11: "signed out, wrong role, wrong owner, missing token"),
built on the existing fixtures in `conftest.py` (`anon_client`, `member_client`, `alice_client`, `bob_client`,
`admin_client`, `admin_client_no_csrf`, `workbench_app`):

- signed out: refused the way the Platform refuses today (find it: redirect to sign-in or a status code);
- wrong role: refused the way the Platform refuses today;
- wrong owner: the same response as for a thing that does not exist (no difference a person could tell);
- missing token: a changing request without its CSRF token is refused.

Each check fails with a message that names the address and the role, never page text. Show the helper on three
existing pages, one per area (search or documents for Workspace, a people or groups page for Admin, a Builder
page), in `P/tests/test_pmvp_pages_examples.py`. Write in your notes what "refused" is today for each case; later
work orders quote it.

## C. Browser walkthrough `P/tests/test_pmvp_walkthrough.py` (your own tests)

The way `tests/test_builder_screens.py` does it: Playwright through `pytest.importorskip`, skipped when Chromium is
missing, the app served from a thread. One table per area (`workspace`, `admin`, `builder`) of the pages to walk;
later items add their pages to it. For each page at 1440 and 390 pixels wide: no response of 500 or more, no
console error, no sideways scrolling at 390. With `PMVP_WALK_OUT=<folder>` set, save one screenshot per page and
width there (evidence for the pull request; the folder is never inside the repository). Start with the pages that
exist today.

## Rules

1. **No customer text in a log line, an exception message or `report.json`**: ids, counts and class names only
   (founder, 2026-11-19). Customer text is in `judging.md` and nowhere else.
2. No new dependency: nothing in `requirements.txt` changes (Playwright stays optional, as today).
3. No product code: nothing under `P/src/` changes. If the helper or the walkthrough needs a change there or in
   `conftest.py`, stop and write the reason in your notes.
4. Read `P/src/experiments/` (packs, evaluators) before you start and say in your notes, in three sentences at
   most, whether the question set could later live there. Don't build on it here.

## Files you may change

- new: `P/tests/pmvp_harness/**`, `P/tests/pmvp_pages.py`, `P/tests/test_pmvp_harness_*.py`,
  `P/tests/test_pmvp_pages_*.py`, `P/tests/test_pmvp_walkthrough.py`, `P/tests/fixtures/pmvp/**`,
  `P/tests/pmvp_harness/README.md` (how to run A, B and C, in one page)
- `docs/platform-mvp/work-orders/PMVP-03.md`: only the "Builder's notes" section at the end

## Files you must not change

- the frozen test files; `P/tests/conftest.py`; anything under `P/src/`; `requirements.txt`; `pyproject.toml`

## Done when

1. `cd P && py -3 -m pytest tests/test_pmvp03_harness_acceptance.py -q -p no:cacheprovider` is green, the frozen
   files unchanged (all lines of the check in `frozen-tests.txt` say OK).
2. Your own tests green; the walkthrough either runs or skips for the stated reason (say which in your notes, and
   if it ran, where the screenshots are).
3. The whole Platform suite: `py -3 -m pytest -q -p no:cacheprovider --continue-on-collection-errors` with a test
   database of your own (`PLATFORM_DB_TEST_DSN=postgresql://platform:testpw@127.0.0.1:55433/knovas_platform_test_pmvp03`,
   `PLATFORM_DB_REQUIRED=true`; create it with `docker exec pmvp-platform-test-db createdb -U platform …`, drop it
   afterwards): the same failing ids as `feat/platform-mvp`, or each new one explained. The two PMVP-20 frozen files
   fail at collection on both (PMVP-20 is not integrated); that is expected.
4. Commits `PMVP-03: …` ending with the co-author line; `feat/pmvp-03` pushed. No pull request: the coordinator
   integrates after a verifier session passes it.

## Builder's notes

(the builder writes here: what was decided, what is open, what the verifier should look at)
