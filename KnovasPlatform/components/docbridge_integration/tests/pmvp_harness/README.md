# The Platform MVP's acceptance harness (PMVP-03)

Test tooling for `FEAT-2026-11-19-platform-mvp` section 11. It lives in the tests folder, so it never reaches an
image. Three parts; all commands run from `KnovasPlatform/components/docbridge_integration` unless they say otherwise.

**One rule for all three:** no customer text in a log line, an exception message or `report.json`. `judging.md` is
the only file that holds questions, answers and document names. It and the screenshots stay outside every
repository: the command line and the walkthrough refuse a folder inside any git work tree (this one, the main
checkout, another repository).

## A. Score the assistant's replies

The truth files are the demo law firm plan's (`demo_kanzlei/truth.py`): `questions.jsonl` and `rights.csv`.
Until that build exists, `tests/fixtures/pmvp/*.stub.*` stand in: 14 invented questions of all five kinds, a rights
table, and recorded replies (some right, some wrong, one leak, one missing).

**Replies recorded on an instance** (the assistant does not need to be importable), from `tests/`:

```bash
cd tests
py -3 -m pmvp_harness score --questions fixtures/pmvp/questions.stub.jsonl \
    --replies fixtures/pmvp/replies.stub.jsonl --rights fixtures/pmvp/rights.stub.csv \
    --out <a folder outside every repository>
```

| Option | Meaning |
|---|---|
| `--replies` | JSON lines, one record per question id: `id`, `status`, `sentences` (`text`, `refs`), `passages` (`number`, `pointer`, optional `page`), `removed`, `message`, optional `raw_answer`. `pmvp_harness.reply_record(id, reply)` writes one |
| `--rights` | optional, and better given: with it a passage the table forbids to `asked_as` counts as a leak on any question, and the table says which documents exist |
| `--pointer-prefix` | what the instance puts before the truth files' pointers (a mount or source name). Cut off only where it ends at a folder |
| `--mvp-set` | only the questions with `in_mvp_set` (the 30 the plan measures on) |
| `--out` | gets `report.json` and `judging.md` |

It prints numbers only and ends with exit code 0 whatever the score. The plan's target (at least 24 of the 30 in
the measured set) is printed beside the result and not enforced.

**Exit code 2 means nothing was measured**, and nothing is written. The message names a file's role, a line and a
reason, never content:

- a file that cannot be read or has a broken line (also `sentences` or `passages` that are not lists);
- no question to ask: an empty questions file, or `--mvp-set` on a file that marks none;
- `--out` inside a git work tree;
- **every passage of the run names a document the truth files do not know.** Then the pointer prefix is probably
  wrong (`--pointer-prefix` forgotten or mistyped): no supporting document and no forbidden one could be
  recognised, so a report would say "no leak" about passages it could not read.

**Asking a running assistant** from Python, with a function of your own:

```python
from pmvp_harness import load_questions, load_rights, run, write_report

report = run(load_questions(path), ask, rights=load_rights(rights_path), pointer_prefix="kanzlei-share/")
write_report(report, folder)        # report.summary() is the numbers
```

`ask(question)` returns the assistant's reply for `question.question`, asked as `question.asked_as` when that is
set. A question that raises becomes a row with the exception's class name, and the run goes on. Look at
`summary["unknown_pointers"]` against `summary["passages"]` yourself: `run` does not stop on a wrong prefix.

### The automatic verdict `ok`

A first pass; people judge whether an answer is right. The rules per kind are at the top of
`tests/test_pmvp03_harness_acceptance.py`. On top of them (coordinator and verifier, 2026-11-20):

**A question counts as ok only when it was really tested.**

- `ok` can be true only for the status `answered` or `no_answer`, exactly. `busy`, `failed`, an empty status,
  `error`, `Busy`: nothing was shown, so nothing was tested. This matters for a `rights` question, which is
  otherwise ok as soon as none of its documents is among the passages. A run against a Core that is down scores 0.
- An `answered` reply shows at least one sentence and a `no_answer` reply none; one that contradicts its own status
  is not ok.
- What is not a reply at all is an error row `NotAReply` without a score: None, a dict, a missing `status`,
  `sentences` or `passages`, a part of the wrong type, a reference or passage number that is not a whole number,
  two passages with one number, a `removed` that is not a count. `score()` called directly raises `NotAReply` (a
  `TypeError`) for these; its message names the part and never what it held.
- A passage without a pointer cannot be checked. Where rights are checked (a `rights` question, or a rights table
  and a question with `asked_as`) such a reply is not ok.

**A leak is counted however the pointer is spelled.** The questions' supporting pointers, the rights table and
the passages are compared by one key (`pmvp_harness.pointers.normalise`): either slash, doubled slashes, a leading
`./` or `/`, a trailing slash, white space around it, the Unicode form and the **case** make no difference. A leaked
document is reported in the truth files' spelling. Not folded: percent-encoding, a `#fragment`, a `..` detour, white
space inside a name. Such a passage is not recognised, and it is counted:

- `unknown_pointers` (per row and in the summary, beside `passages`): passages whose document neither the rights
  table nor any question of the set names. With `--rights` this should be 0 on a real run, because the table lists
  every document Knovas could show. A few unknown passages mean a spelling the key does not fold; all of them mean
  a wrong prefix (exit code 2 above).

Other things worth knowing when reading a number:

- `expected_points` are looked up word for word (ignoring case and spacing) and shown to the judges. They never
  decide `ok`.
- `leaked` counts documents (each once per question), `leaking_questions` the questions.
- A questions file is refused when a question other than a `negative` one has no supporting pointer, a supporting
  pointer is empty, or a `rights` question has no `asked_as`: the generator writes none of these, and the verdict
  would be decided before anything is asked. A rights table is refused when two rows about one person and document
  disagree, however the document is spelled.
- Pointers of the form `db:<TABLE>/<key>` (answers only in the practice database) never match a passage; those
  questions are expected to fail.
- A person (`asked_as`, the table's `person`) is an id and compared as written.

`judging.md` is in German, for the judges: per question the text, the shown answer with its numbers, the passages
(number, document, page), the automatic marks, and a line to tick "richtig / falsch / unklar". When a reply carries
`raw_answer`, the model's answer before the reference check is shown beside the shown one.

## B. Page checks (`tests/pmvp_pages.py`)

One function per check the plan asks of every page: `assert_signed_out_refused`, `assert_wrong_role_refused`,
`assert_wrong_owner_looks_missing`, `assert_missing_token_refused`. They take the clients of `conftest.py`. What
"refused" is today is written at the top of `pmvp_pages.py`. A failure names the address and the role, never page
text. `tests/test_pmvp_pages_examples.py` shows them on one page per area; copy the class of your area.

`assert_missing_token_refused` carries its own control. It sends the request without the token (refused, nothing
changed) and then again **with** it, and the second one must not come back with the same status. So a 403 for the
wrong role, or a 400 for an incomplete form, does not pass as "the token is checked". Hand it a client that may
make the request and a request that is complete; after the check the request has been made once.

```bash
py -3 -m pytest tests/test_pmvp_pages_helper.py tests/test_pmvp_pages_examples.py -q -p no:cacheprovider
```

The examples need the test database (`PLATFORM_DB_TEST_DSN`), like every test on the real app.

## C. Browser walkthrough (`tests/test_pmvp_walkthrough.py`)

An administrator opens every page of the tables `PAGES["workspace"]`, `["admin"]` and `["builder"]` at 1440 and
390 pixels wide: no response of 500 or more, no console error, no sideways scrolling at 390. Add your pages to the
table of your area.

```bash
PMVP_WALK_OUT=<a folder outside every repository> py -3 -m pytest tests/test_pmvp_walkthrough.py -q -p no:cacheprovider
```

With `PMVP_WALK_OUT` set it saves `<area>-<page>-<width>.png` per page and width, as evidence for a pull request.
It needs the test database and Playwright with its Chromium (`pip install playwright`, `playwright install
chromium`); both are optional, and without them the three walkthrough tests are skipped. The checks of the table
itself run without a browser.

Pages that scroll sideways at 390 pixels today are listed in `SCROLLS_SIDEWAYS_TODAY`. A page that is fixed fails
until it is taken off the list. The bar applies to new and reworked pages.
