# Work order PMVP-20, rework 1: the reference check never joins two statements

| | |
|---|---|
| Item | PMVP-20 (Jira SS-732); the first work order `PMVP-20.md` still holds, except rule 2's last sentence (replaced below) |
| Why | Verification of `feat/pmvp-20` @ `1f60910` on 2026-11-19: **FAIL** on one finding; everything else passed |
| Class | sensitive (Siran approves) |
| Branch | keep working on `feat/pmvp-20`; first merge `origin/feat/platform-mvp` into it (brings this order, the new frozen file and PMVP-21, which you don't touch) |
| Frozen tests | `test_pmvp20_assistant_acceptance.py` and, new, `test_pmvp20_sentence_split_acceptance.py`; blob ids in `docs/platform-mvp/frozen-tests.txt` |

## The finding

Sentences without a reference reach the reply. `service.py` (~597, `_starts_sentence`; ~632-638, "any number
before a stop does not end a sentence") joins two statements, so the one without a number is shown under the
other's number, with `removed=0`. Examples: "Die Frist beträgt drei Monate [1]. 2023 wurde sie verlängert." and
"Die Miete beträgt CHF 500. Die Frist beträgt drei Monate [1]." This is the case the check exists for (rule 2,
W-03, POST-01).

The cause is partly the first work order: "a cautious splitter that keeps an abbreviation inside one sentence is
better than one that cuts it" pointed the wrong way. **Replacement for rule 2's last sentence:** when the splitter is
unsure, it cuts. A wrong join can show an unreferenced statement (never acceptable); a wrong cut only drops or
shortens text (a quality loss the harness measures).

## Must do

1. **A reference closes its statement.** A stop, a semicolon or a line break right after `[n]` (with or without a
   following space, whatever case the next character has) ends the sentence.
2. **A number before a stop ends the sentence** unless it is plainly an ordinal or part of a date inside the
   sentence: e.g. a 1-2 digit number followed by a month name ("1. März 2024", "31. Dezember"), or preceded by an
   article or preposition ("im 3. Quartal", "am 5."). Amounts ("CHF 50.", "CHF 1'500."), years ("2023."), full
   dates ("1.1.2024.") and numbers after a noun ("Ziffer 5.") end the sentence. How you decide is yours; the frozen
   file pins the outcomes.
3. **Abbreviations:** add the forms people and firms use: "Hr.", "Fr.", "Fa.", "Kt.", "med." and the like, next to
   the legal ones you have.
4. **The no-answer sentence is never a cited answer.** If the answer is only `NO_ANSWER_TEXT` (with or without
   numbers), the reply is `no_answer` with the passages; inside a longer answer that sentence is not shown. Change
   the system instruction so it no longer asks for a number on that sentence.
5. **A passage's page comes from the same chunk as its text** (verifier's note: the page is taken from
   `top_chunks[0]`, the snippet from the first chunk that has one, so a reference can open the wrong page). Your
   own test with a Core hit whose first chunk has no snippet.
6. **Open point from plan § 5.2:** find out whether the Core's `/secured/fulfill-evidence` route could carry part
   of the check (KnowledgeBase `secure_api.py` on master). Answer it in your notes in three sentences at most;
   don't build anything for it.

Your own tests for 3 and 5. Keep `test_pmvp20_assistant_acceptance.py` green.

## Not in this rework (recorded in `follow-ups.md`)

Digit-heavy history over the token budget, history dropped whole when turns are long, timeouts longer than nginx's
180 s, the two older log lines that can carry text, the demo profile, escaping Markdown and links on the page.
Don't fix them here.

## Files

As in `PMVP-20.md`. Notes go under a heading `## Builder's notes, rework 1` at the end of **this** file.

## Done when

1. Both frozen files green and unchanged (check command in `frozen-tests.txt`); your tests green.
2. The whole Platform suite: `py -3 -m pytest -q -p no:cacheprovider --continue-on-collection-errors` (own test
   database, `PLATFORM_DB_TEST_DSN=…/knovas_platform_test_<name>`): same failing ids as the `feat/platform-mvp`
   commit you merged, or each new one explained.
3. Commits `PMVP-20: …` with the co-author line; `feat/pmvp-20` pushed. No pull request.

## Builder's notes, rework 1

Builder session, 2026-11-19. `origin/feat/platform-mvp` @ `a55cdb0` merged into `feat/pmvp-20` (`11167ea`); the
rework is commit `abf7dd5` plus these notes and the README. Nothing here is verified; that is the verifier's.

**What changed** (`P/src/assistant/service.py`, `P/src/knovas_client.py`, own tests):

1. *A reference closes its statement.* A stop, `;`, `!` or `?` right after a bracket group holding a digit ends
   the sentence, whatever follows (space or none, any case). References right after a stop still belong to the
   sentence before and end it too.
2. *Numbers.* A number before a full stop (`500`, `1'500`, `3,5`, `1.1.2024`, `2023`) ends the sentence, except
   a 1-2 digit number followed by a month (`MONTHS`) or preceded by an article or preposition (`ORDINAL_LEADS`).
   In the same spirit (when unsure, cut): a full stop before a lower-case word now ends the sentence unless the
   word before it is an abbreviation; `usw.`, `etc.`, `ff.`, `usf.`, `uvm.` end it unless a lower-case word
   follows; a single capital ends it unless it stands before a number (`S. 4`) or after a lower-case single
   letter (`z. B.`, `u. U.`); a stop glued to the next word ends a sentence only between a plain word and a
   capital (`Monate.Sie`). `;` without a reference never cuts.
3. *Abbreviations.* `ABBREVIATIONS` is grouped (law and citation; people, titles, firms and places; everyday;
   months) and now holds `hr`, `fr`, `fa`, `kt`, `med`, `dipl`, `ing`, `lic`, `iur`, `phil`, `rer`, `pol`,
   `oec`, `nat`, `mag`, `gebr`, `ges`, `fam`, `gde`, `bez`, `erw`, `bst`, `urt`, `beschl`, `anh`, `lt`, `exkl`,
   `insbes`, `allg`, `entspr`, `einschl`, `ehem`, `rd`, `rp`, `vorm`, `zus`, `ggü` and others.
4. *No-answer sentence.* A fragment that starts with `NO_ANSWER_TEXT` (markers stripped, any case) is dropped
   wherever it appears and not counted in `removed`; an answer of only that sentence is therefore `no_answer` with
   the passages. System instruction rule 4 now reads "reply with only this sentence, exactly as written and
   without any number … Never add it to an answer"; rule 2 asks for a number on "every statement of an answer",
   no longer on "every sentence".
5. *Page of the snippet's chunk.* `_snippet_with_location` reads the chunks first, in order, and returns the
   snippet with that chunk's page and sentence; a row-level snippet no chunk carries keeps an unknown location.
   Inside `keeping_snippets()` the row gets `snippet_page_number` and `snippet_sentence_number`;
   `passages_from_rows` uses them (None when unknown, never another chunk's page). Tests:
   `test_assistant_knovas_client.py::test_a_row_keeps_the_page_of_its_snippet_not_of_the_first_chunk`,
   `test_assistant_core.py::test_a_reference_opens_the_page_its_text_is_on` (first chunk without snippet, end to
   end), `test_assistant_service.py::test_the_page_is_the_snippets_own`.
6. A fragment without a letter (a list number such as `1.`, now cut off) is not a sentence, so numbered lists do
   not inflate `removed`.

**Open point from plan § 5.2, `/secured/fulfill-evidence`.** On KnowledgeBase master the route
(`secure_api.py` ~3878; off in `defaults.toml`, on in dev and prod) is an evidence retriever for template
filling: it takes field descriptions, runs its own retrieval under the same RBAC chunk filter and returns chunk
text with page and sentence, without an LLM. It never judges whether a given sentence is supported by a given
passage, so it cannot carry any part of the reference check, and calling it would be a second retrieval path,
which POST-02 rules out; a support (entailment) check would need a Core route of its own.

**For the verifier, and decisions for the coordinator.**

- *A comma after `[n]` does not cut* (rule 1 names a stop, a semicolon and a line break): "Die Frist beträgt drei
  Monate [1], und 2023 wurde sie verlängert." is still shown as one sentence under `[1]`. Cutting there too would
  follow "when unsure, cut", but splits ordinary sentences ("Laut Vertrag [1], gilt …") and drops their second
  half. Your call.
- The no-answer match is a prefix match on the German sentence. A "found nothing" in other words or another
  language has no `[n]`, so it is removed and the reply is `no_answer` anyway.
- Two of my own earlier tests pinned joins and were changed to the new rule
  (`test_a_stop_before_a_lower_case_word_ends_a_sentence`, `test_a_quoted_sentence_ends_with_its_stop`).
- Accepted losses from cutting: initials ("Hans M. Müller"), "Variante B. Danach …", "www.Knovas.ch".
- FU-06 to FU-14 untouched.

**Evidence** (from `P`, Windows, Python 3.13.5):

- `test_pmvp20_assistant_acceptance.py` 22 passed; `test_pmvp20_sentence_split_acceptance.py` 24 passed (14 of
  them failed before the rework); own tests `test_assistant_service.py`, `test_assistant_knovas_client.py`,
  `test_assistant_core.py` 153 passed. The frozen-test check prints `OK` for all three files.
- Whole suite, `PLATFORM_DB_REQUIRED=true py -3 -m pytest -q -p no:cacheprovider --continue-on-collection-errors`,
  each run on a database of its own (created for the run, dropped afterwards):
  `feat/pmvp-20` 6,441 passed, 105 failed, 56 errors, 40 skipped, 8 xfailed, 1 xpassed; the merged
  `feat/platform-mvp` commit `a55cdb0` 6,242 passed and exactly the same 105 failed and 56 errors (identical test
  ids, the Windows environment failures of the first notes). Collection errors: `test_experiments_runner_client.py`
  in both; on `a55cdb0` also both PMVP-20 frozen files (no `assistant` package there). No new failure.
- `py -3 scripts/check_ascii_py.py 11167ea..HEAD` -> 0 added lines with a byte > 0x7F.
