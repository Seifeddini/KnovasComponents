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

**Built (2026-11-20, builder session).** Only new files, nothing existing changed: `P/tests/pmvp_harness/` (package,
`__main__`, `README.md`), `P/tests/fixtures/pmvp/` (three stub files), `P/tests/pmvp_pages.py`,
`P/tests/test_pmvp_harness_{inputs,report,cli}.py`, `P/tests/test_pmvp_pages_{helper,examples}.py`,
`P/tests/test_pmvp_walkthrough.py`. Commits `8ef18b2` (A, command line, stubs), `40d9d88` (B), `2b74cc4` (C),
`7369a21` (README), and this one (notes). Not verified by me; a verifier session checks it.

**Tests.** Frozen file: 29 passed, unchanged (all four lines of the check say OK). My own: 527 with `py -3` (482 on
the harness, 25 on the page helper, 11 examples on the real app, 9 in the walkthrough file; 524 pass and 3 of the
walkthrough's skip because Playwright is not installed for `py -3`). They were 121 at the first report and 130
after the coordinator's decisions; the rest came with the rework (last paragraph). Whole suite: see "Whole suite" below.

**Formats.** `demo_kanzlei/truth.py` (worktree `.worktrees/demo-kanzlei`, commit `6005034`) writes exactly what the
frozen file reads: the seven question fields from `_Questions.add`, the header `person,pointer,may_read,rule` and
`true`/`false` from `write_truth`. No difference found. Two things its README says and the harness follows: a
pointer `db:<TABLE>/<key>` means "only in the practice database" (never matches a passage, so those questions
fail), and `asked_as` is the person's `key` of `people.json`. The `rule` column is read past.

**Decisions beyond the work order** (all in new code; say if one should go the other way):

1. A questions file is refused when a question other than `negative` has no supporting pointer, or a `rights`
   question has no `asked_as`. The generator writes neither. Without the first the verdict is decided before
   anything is asked (never ok for an answer, always ok for a rights question). `expected_points`,
   `supporting_pointers`, `asked_as`, `in_mvp_set` may be absent from a record (defaults: empty, empty, `""`,
   false); a wrong type is refused. An id used twice is refused.
2. `negative`: "the no-answer reply" is read as status `no_answer` **and** no shown sentence.
3. `rights`: as the frozen file says, only the passages decide. First built so that a `busy` or `failed` reply
   counted as ok. Changed twice since (below): `ok` needs the status `answered` or `no_answer` exactly, in every
   kind.
4. `summary()` has, beside the pinned numbers: `leaking_questions`, `by_status` and `error_classes` (class name →
   count). `leaked` counts documents, each once per question. `by_kind` always lists all five kinds.
5. `report.json` per row: id, kind, in_mvp_set, ok, error, status, and counts. A status outside the contract's
   four is written as `other`, because a recorded file could put any text there. `asked_as` is not in it.
6. Pointers are compared by one key. First built keeping case; overridden in the rework (below): case is folded
   too.
7. The command line refuses an `--out` inside a git work tree (exit 2), because `judging.md` holds customer
   text. The walkthrough refuses `PMVP_WALK_OUT` the same way. First built for this work tree only; since the
   rework for any work tree. A file that cannot be
   read is exit 2 with role, line and reason; an `OSError` is reported by its class name, not its message.
8. `pmvp_harness.reply_record(id, reply)` writes one line of a replies file, so whoever records replies on an
   instance (PMVP-20's side) gets the format from one place. `MissingReply` is the error class of a question
   without a recorded reply.
9. `judging.md` is in German. It shows the expected points with "gefunden / nicht gefunden", the supporting
   documents, and marks a passage "zitiert", "Belegdokument" or "nicht erlaubt". It does not show passage text
   (the work order lists number, document, page).
10. The stub files use names the demo plan's own code already uses (firm "Quarzfels", the parties and people in
    its `truth.py`). I invented no new company or person names and screened nothing myself.

**What "refused" is today** (measured against the real app on this branch; the table is also at the top of
`P/tests/pmvp_pages.py`, and the four checks expect exactly this):

| Case | Today |
|---|---|
| signed out | An address under `/api/`: **401**, JSON `{"success": false, "error": "Anmeldung erforderlich"}`. Every other address: **302** to `/login?next=<address>`. That includes the Admin console's POSTs and the Builder's JSON routes under `/builder/api/` (see finding F3). Open without sign-in: `/login`, `/static/…`, `/favicon.ico`, `/api/health`, `/api/version`, `/api/stats`, the open-token redeem and spec routes. The sign-in gate runs before the token gate |
| wrong role | **403**. Admin console: the server's plain "Forbidden" page, for GET and POST. Builder page: its own 403 page; Builder JSON: 403 with `success: false`. Other administrator-only JSON (`/api/settings/platform`, `/sync`, `/api/views/saved/defaults`): 403 JSON. Exception: Experimente answers **404** to someone without its roles (the module hides itself); the check takes `status=404` for that |
| wrong owner | The same response as for an id that does not exist. Documents (`/api/document/<pointer>…`): **404** JSON `{"success": false, "error": "Not found"}` for a document the person's own search or listing did not return, byte for byte the answer for an unknown pointer. Builder workspace for someone invited to another workspace: **403**, the same page or JSON as for a workspace id that does not exist, because the role check answers before the lookup (an administrator gets 404 for an unknown id). Saved views raise the same "Ansicht nicht gefunden." (404) for a foreign and an unknown id; read in `identity/saved_views.py`, not measured |
| missing token | A POST, PUT, PATCH or DELETE without the header `X-CSRF-Token`: **403** JSON `{"success": false, "error": "CSRF token invalid or missing"}` before the route runs; `/builder/api/…` too. The Admin console's forms carry the token as the field `csrf_token`: without it **400**, the page again with "Formular ist abgelaufen", nothing changed. `/logout` and `/api/open-tokens/mint`: 400 JSON. Not gated (own bearer token): the Experimente machine API, the connector protocol, open-token redeem |

`assert_missing_token_refused` takes an optional `unchanged=` reader (a row count, a list) and fails when the
refused request changed something. Since the rework it sends the same request again with its token itself and
fails when that one comes back with the same status, so the refusal is shown to be about the token. `assert_wrong_owner_looks_missing` compares status, content type and body after
blanking each response's own address.

**Walkthrough: it ran, and it skips.** `py -3` has no Playwright, so in the suite the three `test_walkthrough[…]`
tests skip with "Playwright is not installed (it is optional)". To see it run I made a virtual environment in my
scratchpad (system site packages plus `playwright==1.64.1`, the version that fits the Chromium build 1248 already
in `%LOCALAPPDATA%\ms-playwright`). Nothing was installed system-wide and no browser was downloaded. There all 9
tests pass and 36 screenshots (18 pages × 2 widths) are in
`C:\Users\siran\AppData\Local\Temp\claude\1838582cff9d51e1\af9716ae-0421-42ac-9fac-6acb192cc6e6\scratchpad\pmvp03-walk`
(a temporary folder; copy what should be kept). The interpreter is `…\scratchpad\venv\Scripts\python.exe`.

The walkthrough serves `fixture_mode_app`, not `workbench_app`, and puts a stand-in for the Knovas Connector in
place before the app is built (a fixture in my file, no change to `conftest.py`). Reasons: findings F2 and F4.

**Findings on today's pages** (nothing changed; they are outside my files):

- **F1. Seven of the 18 pages scroll sideways at 390 pixels:** `/views` (692 px too wide), `/settings` (140),
  `/admin/people` (147), `/admin/documents` (240), `/admin/ingestion` (262), `/admin/approvals` (282),
  `/admin/system` (207). The nine Builder pages, `/` and `/ontology` do not. They are listed in
  `SCROLLS_SIDEWAYS_TODAY` in the walkthrough: green while they still scroll, red on any other page that starts to,
  and red on a listed page that stops (so the list cannot go stale). **Decision wanted (D2).**
- **F2.** `/ontology` with the tests' fake Knowledge Graph (`workbench_app`, `ONTOLOGY_SOURCE=graph`):
  `/api/ontology/summary` answers 500, because the fake has no export. In fixture mode the page is clean.
- **F3.** A signed-out request to `/builder/api/…` gets the 302 to the sign-in page, not a 401: the app's gate
  decides by the prefix `/api/` and runs first, so the Builder's own "401 Nicht angemeldet." is never reached in the
  real app. A script calling these routes follows the redirect into HTML.
- **F4.** `/admin/ingestion` without a reachable Connector: the folder picker's `/admin/ingestion/folders` answers
  502 after the name lookup fails (about two seconds). Handled by the page, but a response of 500 or more.
- **F5.** `GET /api/document/<pointer>` logs the pointer at INFO (`app.py`, `get_document`: "Document request:
  doc_id=…"). A pointer names clients and matters; the wall's own log lines avoid it.

**`P/src/experiments/` (rule 4).** The question set could live there later only as measurements, not as a pack: a
pack holds a domain's metrics, experiment types and evaluators, and an evaluator compares one metric between
variants from aggregates and rows, with no place for questions, expected documents or a rights table. What fits is
the result: one run per assistant build as a variant and one row per question id (`ok` as a proportion, the counts
as means), read by the built-in two-proportion and paired evaluators, the way the engineering pack takes one row
per query from CI. The questions and the judging sheet have to stay outside it, because evaluation output is shown
in the browser and written into the Knovas index.

**Whole suite.** `py -3 -m pytest -q -p no:cacheprovider --continue-on-collection-errors` with `PLATFORM_DB_REQUIRED=true`,
each run on a database of its own, both on 2026-11-20 on this Windows machine and at the same time.

| | passed | failed | errors | skipped | xfailed | xpassed | files failing at collection |
|---|---|---|---|---|---|---|---|
| base `89614f1` (a detached checkout in my scratchpad) | 6240 | 107 | 57 | 40 | 8 | 1 | 4 |
| this branch at `7369a21` (the notes commit changes no test) | 6387 | 107 | 57 | 43 | 8 | 1 | 3 |

The failing ids of the short summary are the same on both sides (107 failed, 3 with errors, and the files
`test_pmvp20_assistant_acceptance.py`, `test_pmvp20_sentence_split_acceptance.py` and
`test_experiments_runner_client.py` at collection). The one difference: `test_pmvp03_harness_acceptance.py`
failed at collection on the base and is green here. No new failing id. The 150 more results are the 29 frozen
tests and my 121 (118 passed, 3 skipped: the walkthrough without Playwright). Most of the base's failures are in
`test_experiments_frontend.py` (23), `test_github_write.py` (20), `test_sync_component_github_v2.py` (10),
`test_builder_assistant.py` (9), `test_brain_sync.py` (8), `test_release_images_workflow.py` (7) and
`test_sync_component.py` (7); I did not look into them. Both outputs are in my scratchpad (`base-run.txt`,
`mine-run.txt`). Both test databases are dropped.

**For the verifier.**

- The four frozen files: the check in `frozen-tests.txt`. `git diff 89614f1 --stat` shows only added files plus
  this section.
- Customer text: `test_pmvp_harness_cli.py::test_customer_text_is_in_the_judging_sheet_and_nowhere_else` takes
  every question, expected point, pointer, sentence, message and raw answer of the stub files and looks for them
  in `report.json`, stdout, stderr and the log. Exception messages: `_refused` in `test_pmvp_harness_inputs.py`
  also checks that no chained exception is left to print.
- Run the command line from `P/tests` as the README says, and read `judging.md` once as a judge would.
- The page checks failing: `test_pmvp_pages_helper.py` has a wrongly behaving route for each check.
- The walkthrough with a browser, if the scratch environment is still there:
  `PMVP_WALK_OUT=<folder> <scratchpad>\venv\Scripts\python.exe -m pytest tests/test_pmvp_walkthrough.py`.

**Open after the first report** (all five decided, see the last paragraph).

- **D1.** Should a `rights` question with a `busy` or `failed` reply count as ok? Today it does (frozen rule). In a
  run where the Core is down, the three rights questions of the measured set would count toward the 24.
- **D2.** F1: which item makes the Admin console, `/views` and `/settings` usable at 390 pixels, or does the bar
  apply to new MVP pages only? Until then they stay on the known list.
- **D3.** Decision 1 above (the two refusals of a questions file): keep, or accept such records and let them fail?
- **D4.** F3 and F5 are in product code. Maintenance items, or part of a page item?
- The stub files stand in for the demo plan's truth files. When DEMO-11's build exists, the same command runs on
  its `questions.jsonl` and `rights.csv`; nothing in the harness is tied to the stubs.

**Coordinator's decisions (2026-11-20), built in the commit after `e148e5f`.** D1, changed: a `busy` or `failed`
reply is never ok, whatever the kind, because nothing was shown and so nothing was tested (rule in `scoring.py`,
said in the README; the test of the opposite rule is replaced by 10 in `test_pmvp_harness_report.py`). Leaks are
still counted from the passages a reply carries; a busy or failed reply carries none, so its count stays 0. D2,
kept: the known list and its ratchet; the bar applies to new and reworked pages, the five Admin pages leave the
list with PMVP-40, `/views` and `/settings` stay listed until an item reworks them (the sentence is beside the
list). D3, kept: the two refusals of a questions file (decision 1). D4: F3 and F5 are on the coordinator's
follow-up list; not touched. D5, kept: an output folder inside the repository is refused (decision 7). The whole
suite was not run again after this change; it touches `scoring.py`, its tests, the README and one comment.

**Rework after the first verification (2026-11-20, verifier: FAIL on two wrong verdicts).** By the coordinator's
points; each has tests, most in the new `test_pmvp_harness_verdict.py`.

1. *Ok only when really tested.* `ok` needs the status `answered` or `no_answer` exactly (`scoring.TESTED`).
   What is not a reply raises `NotAReply` (a `TypeError`, message without content) in `score()` and is an error
   row without a score in `run()`: None, a dict, a missing or wrongly typed `status`, `sentences`, `passages`,
   sentence, reference, passage number or `removed`. Added by me in the same spirit: an `answered` reply without
   a sentence and a `no_answer` reply with one are not ok.
2. *A leak however the pointer is spelled.* One key for questions, rights table and replies
   (`pointers.normalise`): either slash, doubled slashes, leading `./` and `/`, trailing slash, white space
   around it, Unicode form, case. Leaked documents are still reported in the truth files' spelling. New number
   `unknown_pointers` per score, per row of `report.json` and in the summary beside `passages`; `score()` takes
   the set's pointers as `known=`, `run()` hands them in. The command line prints it and ends with exit 2,
   writing nothing, when every passage is unknown.
3. The prefix is cut only where it ends at a folder.
4. Two passages with one number: `NotAReply`. A passage without a pointer: a rights question is not ok, and
   neither is any question where the rights table is applied; it counts as unknown and never as a supporting
   document. A supporting pointer that names nothing (`""`, spaces, `/`) is refused when the file is loaded.
5. Contradicting rights rows are caught in any spelling, in `load_rights` (with both line numbers) and in
   `Rights(...)` built by hand.
6. An output folder inside any git work tree is refused, for `--out` and `PMVP_WALK_OUT`: walk up from the
   resolved path looking for `.git`, after taking off an extended-length prefix. On a machine whose temporary
   folder lies in a git work tree the command-line tests skip, saying so.
7. `sentences` or `passages` of the wrong type in a replies file: exit 2, "'sentences' is not a list".
8. No question to ask (an empty file, `--mvp-set` with none marked): exit 2.
9. A `removed` that is not a count is `NotAReply`; `score()` quotes nothing of a reply in an exception.
10. `assert_missing_token_refused` sends its own control with the token (header and form field) and fails when
    that comes back with the same status. The examples no longer send it themselves; one more example shows a
    member's 403 not passing as a token check.

The frozen file is unchanged and green (29). The whole suite was not run again, on the coordinator's word; the
run of this section's table is of `7369a21`. The walkthrough ran again with a browser (9 passed).

**Not folded by the pointer key, so such a passage is not recognised and counts as unknown:** percent-encoding,
a `#fragment`, a `file://` address, a `..` detour, white space inside a name (a no-break space for a space), a
UNC path without `--pointer-prefix`. A person id (`asked_as`, the table's `person`) is compared as written.

**Decision wanted (D6).** With a rights table, a real run should have no unknown passage at all, because the
table lists every document Knovas could show. Today one unknown passage among known ones is only counted: if it
is the forbidden document in a spelling the key does not fold, the rights question is still ok. Should a question
that is asked as somebody be not ok as soon as one of its passages is unknown to a given rights table?
