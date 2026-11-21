# Work order PMVP-20, hardening: the number check and seven small points from the verification

| | |
|---|---|
| Item | PMVP-20 (Jira SS-732), after rework 2 passed verification with notes on 2026-11-21 (`feat/pmvp-20` @ `e1d92a4`, integrated in `feat/platform-mvp` as merge `49d4763`) |
| Class | sensitive (Siran approves) |
| Branch / worktree | `feat/pmvp-20-hardening` from `origin/feat/platform-mvp`, worktree `.worktrees/pmvp-20h` |
| Frozen tests | `test_pmvp20_statements_acceptance.py` stays unchanged and green |
| Not here | the provider choice (PMVP-25, built in parallel on `feat/pmvp-25`: it adds a provider module and touches `core.py`'s factory; do not touch those) |

## Why

The verification found no way past the checks as the frozen file defines them, but the number check reads the
passage too generously and does not see several kinds of numbers. The design's promise is "every number in the
statement stands in its evidence"; these points make it hold.

## Must do

1. **Numbers are checked against the quote, not the whole passage.** Every number in the statement must stand in the
   cited **quote** (the passage's own characters the reply carries). Two answers in the verification survived only
   because a "3" or "1" stood elsewhere in the passage. All the frozen file's cases have their numbers in the quote.
2. **The passage side reads whole number tokens, the same way as the statement side.** `2/3`, `14:30`, `10⁶`,
   `30.09.2027` and `266 a` are one token each (with their own meaning), so they support neither `3`, `30`, `10` nor
   `266`. Remove the substring matching (`check.py` ~433, ~482).
3. **More kinds of numbers count as numbers**, on both sides, and must match:
   - month names and their abbreviations (Januar … Dezember, Maerz/März, Jan., Feb., …): `1. April 2024` is not
     supported by `1. Maerz 2024`; `31. Dezember 2025` and `31.12.2025` are not the same token (the safe side);
   - number words 0 to 100 and their compounds, `hundert`, `tausend`, and number-word prefixes of compound words
     (`drei-`, `sechs-`, `zwei-` in `dreimonatige`, `zweijährig`): `dreimonatige` is not supported by
     `sechsmonatige`, `zwanzig` not by `zehn`, `ein Jahr` not by `zwei Jahre`; the equivalence digit = word stays for
     the plain words (3 = drei);
   - Roman numerals after `Ziffer`, `Ziff.`, `Art.`, `Kapitel`, `Anhang`, `Abschnitt`, `lit.`, `Teil` (`IV` is not `II`);
   - signs and units that change the value: `-500` / `minus 500`, `%` against `‰`;
   - any character with a Unicode numeric value (`unicodedata.numeric`) or a digit look-alike (`Ƽ`, `ƻ`) that is not
     an ASCII digit: the statement is removed unless the same character stands in the quote.
4. **Small points:**
   - a code-fence line followed by an object is counted when the object is removed (`check.py` ~157);
   - `_MARK` (~196) strips only `[n]` whose n is a passage number of this reply; `[2024]` stays text and its number is
     checked;
   - `compose` cuts each title to a fixed length so the last message always fits the Core's 50,000 bytes, whatever
     the titles; fix its docstring; a test with ten 6,000-byte titles;
   - a statement that rewords the no-answer sentence ("Dazu finde ich nichts in Ihren Unterlagen", "In Ihren Quellen
     finde ich dazu nichts") is not a statement: match a short closed list of such phrasings, case- and
     whitespace-insensitive;
   - the Latin-only rule lets `º`, `ª`, `ʼ` and `µ` through (they occur in real documents); others stay as they are;
   - a quote that adds only a final full stop the passage does not have is still found.
5. **Tests** for every point above, in `tests/test_assistant_check.py` / `test_assistant_service.py`, using the
   verifier's inputs (they are in the verification report quoted in your task). Keep all existing tests green; a
   changed existing test needs a reason in your notes.
6. **The README** (`src/assistant/README.md`): the rule now reads "every number in the statement stands in its quote",
   the list of what counts as a number, and the cost (what true statements are now removed).

## Files

`src/assistant/check.py`, `src/assistant/service.py` (only `compose`'s titles and the docstring),
`src/assistant/README.md`, `tests/test_assistant_*.py`, the notes at the end of this file. Never the frozen files,
`core.py`'s factory or a provider module, `conftest.py`.

## Done when

1. The frozen file green and unchanged; `tests/test_assistant_*.py` green; the frozen check all OK. **Do not run the
   whole suite.**
2. Commits `PMVP-20: hardening …` ending with the co-author line; `feat/pmvp-20-hardening` pushed. No pull request.

## Builder's notes

(the builder writes here)
