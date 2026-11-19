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
