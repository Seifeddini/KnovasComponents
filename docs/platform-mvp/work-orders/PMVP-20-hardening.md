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

Builder session, 2026-11-21, branch `feat/pmvp-20-hardening` from `origin/feat/platform-mvp` @ `77e309c`, worktree
`.worktrees/pmvp-20h`. Commits `17dc837` (the order), `8d582f4` (the tests' umlauts back to `\u` escapes),
`160fcda` (what a second attack found), and the commit of these notes. Nothing here is verified; that is the
verifier's.

**Built, per "Must do".**

1. *Against the quote.* `check_quote` now returns the statement with `quote` (the passage's characters) and
   `span` (where they stand); `locate_quote` is new, `find_quote` stays. `check_numbers` reads the **whole**
   passage into tokens and offers only those that stand wholly inside the span, so context outside the quote
   still decides a reading (`ausser Acht`, `Ziffer`, a day before a month) and a token the quote cuts (`266 a`
   with the quote ending at `266`, a date) is not offered.
2. *Whole tokens.* No substring matching: a claim is a set of tokens, the quote offers a set, a claim needs one
   of its tokens. No "other readings" are offered any more (`266` for `266 a`, `271a` for `271a¹`, the numbers of
   a run). Tokens that are not digits carry a tag no text holds (`chr(0)`).
3. *What counts as a number* (README "The numbers" has the full list): digits as before, plus `minus`, `- 500`,
   per cent and per mille signs and words, scales, fraction words, mixed numbers, ISO and spaced dates; German
   number words 0 to 999,999 (also several words and hyphenated), English ones; `ein` and its forms, read with
   the word it counts; words that begin with or hold a number word (`dreimonatig`, `zweite`, `Drittel`,
   `Mindestdreimonatsfrist`); months, abbreviations and dates as one token; Roman numerals and enumeration
   letters after `Ziffer`, `Art.`, `lit.`, `Teil`, … and single capitals or capital Roman numerals anywhere;
   every character with a numeric value that is not 0-9 and 62 digit look-alikes must stand in the quote.
4. *Small points.* An object after a code fence on its line is a counted line (only a fence plus perhaps a
   language name is dropped). `shown_text(statement, marks)` takes out only marks of this reply's passages
   (`check_form` gets the passages). `compose` cuts each title to `MAX_TITLE_BYTES` = 200; docstring states the
   bound. No-answer wordings: a closed list (27, matched in order with up to 8 words between). `º ª ʼ µ` pass the
   Latin rule. A final full stop the passage lacks is passed over in a quote (tried after the exact forms).
5. Tests: frozen file unchanged; tests for every point with the verifier's inputs, the second attack's, and the
   cost cases (`test_what_the_hardening_costs`). 6. README rewritten for the rule, the list and the cost.

**Decided beyond the order** (the verifier should judge each):

- **`ein` counts, bound to the word it counts** (the first capitalised word among the next three, else the
  next). Supported by `ein X` in the quote, `1 X`, or `X` with no number in the two words before it; a quote's
  `ein` never offers the digit 1. Without the binding, any article in the quote supported "einen Monat" (second
  attack); without the `X` relief, every article cost the statement.
- **Compounds carry their number only for time, rooms and multiples** (`-monatig`, `-jährig`, `-zimmer`,
  `-fach`); `zweiseitig`, `dreimal` and all `ein…` words are only themselves (`einseitig`, `nicht einmal` offered 1).
- **The no-answer rule is wider than a list**: also any statement that speaks of the person's sources, of the
  assistant (`ich`, `mir`, `I`) or of "diese Frage" and holds a negation. A statement that a document lacks
  something ("Der Vertrag enthält keine Regelung …") stays one.
- Also not in the order: English number words, months and order words; scales (`Mio.`, `Mrd.`, `Mia.`, …);
  fraction words and mixed numbers; ISO and spaced dates; number words read through accents, `funf`, and Latin
  look-alike letters (by Unicode name); `l5`, `O,5`, `lO`; single capitals as enumeration letters (not an
  initial `A. Meier` in a passage); a hyphen plus white space between words in a passage reads neither way; a
  soft hyphen before a line break joins; a sign, a letter or `%` across a line break; `Stufe`, `Klasse`,
  `Kategorie`, `Zone`, `Phase` as enumeration words; the statement's noun `Acht`; `vom Hundert`, `v. H.`; unit
  letters `m l t s` are not article letters; dashes and per cent signs of more kinds; readings of a passage
  cached per answer (150,000 characters × 50 statements: 0.3 s).
- `compose` also leaves out a page number of more than nine digits: the order named only titles, but a
  4,000-digit page from a row breaks the same bound.

**Existing tests changed, with reasons** (all in `test_assistant_check.py`; none in the service tests):

- `_one` quotes the whole passage by default: numbers are now looked for in the quote (order item 1).
- `test_what_a_statement_claims`, `test_what_a_passage_offers`: new token form; `3,5 %` and `5% - 7%` carry the
  per cent, `1. März 2024` is one date, `ausser Acht` claims the noun or 8 (item 3, second attack); `2/3`,
  `14:30`, `271a¹`, `266 a contrario` no longer offer pieces (item 2); `1. März` + line break + `2024` is one
  date. The `eins …`/`dreimonatig …` rows moved to a new test, since they now claim.
- `test_a_number_written_as_the_passage_writes_it_is_found`: `271a` against `271a¹` moved to the removed list
  (item 2: `10⁶` supports no `10`).
- `test_a_statement_that_repeats_its_passages_number_mark_is_shown_without_it`: `[7]` is no longer taken out
  (item 4); now `[3]`, and `[7]` is in a new test as removed.
- `test_the_shown_text_…`: `shown_text` takes the passage numbers (item 4).
- `test_punctuation_and_spelling_the_model_adds_…`: the final-full-stop case moved to a new "found" test (item 4).
- `test_a_statement_with_a_letter_of_another_alphabet_…`: `µ` passes now (item 4); the Greek `μ` stays out.
- `test_a_statement_that_only_shares_words_with_the_no_answer_sentence_is_one`: the passage holds "eine Frist"
  and "eine Kündigungsfrist", since `ein` is a number now.

**The cost** (true statements now removed; README "What the check cannot see"): a number that stands in the
passage but not in the quoted piece; `ein X` when the quote holds `X` neither after `ein`, nor after `1`, nor
alone ("ein Arbeitszeugnis" against "ein Zeugnis"); `1 Monat` for `einen Monat`; a piece of a whole token (the year
or the day and month of a date, `271a` for `271a¹`, `2` for `2 Mio.`, a cut `BGE 140 III 115` for `… 115 E. 2`);
words that begin with a number word but name none, and names like `Zehnder`, missing from the quote; one number
written two ways (`2/3`/`zwei Drittel`, `31.12.2025`/`31. Dezember 2025`, `IV`/`4`, `2.`/`zweiten`); capital
abbreviations that read as Roman numerals (`IV`, `MV`) missing from the quote; first-person statements or
statements about the sources with a negation. Not measured against a model: the harness should report
`removed_by`, and `number_not_found` will rise.

**Stopped on — decisions for the coordinator.**

1. **`erste` cannot be a number** while the frozen file pins "Das steht in der ersten Stelle." (quote "Wort Wort
   Wort") as shown. So "die erste Mahnung" against "die zweite Mahnung", `Erstwohnung`, `erstinstanzlich` pass
   (second attack, realistic). `zweite`, `dritte` are numbers. Needs a frozen-file change or an accepted gap.
2. **French and Italian** number words and months are not read (`trois mois` against `six mois` passes). Swiss
   firms will ask in French and Italian; adding them is a scope and cost question (`sei`, `due`, `otto`, `neuf`,
   `cent`, `sept` collide with German and English words; `un`/`une`/`uno` would need the `ein` treatment).
3. Known and left open (README): a number of the quote in another role (`Art. 2` supports "2 Monate"; each end
   of a range is offered); `achte`/`achten` one token; `Absatz 2 500 Franken` offers `2 500`; HTML entities are
   text, so the page must render plain text (FU-11).

**Evidence** (from `P`, Windows, Python 3.13.5):

- `tests/test_pmvp20_statements_acceptance.py`: 88 passed, unchanged; the frozen check prints OK for all five
  files in `frozen-tests.txt`.
- Own: `test_assistant_check.py` 509, `test_assistant_service.py` 45, `test_assistant_core.py` 13,
  `test_assistant_knovas_client.py` 39: 606 passed. The whole suite was not run.
- `scripts/check_ascii_py.py 77e309c..HEAD`: 0 lines.
- The verifier's inputs: all removed or found as asked. 65 of the new tests fail against `77e309c`'s check.
- Attacks before the push: my own round (19 inputs, 14 wrong, fixed); a second agent's round with the code and
  the rules only; its cases replayed (111): 110 now as intended, the last a
  paraphrase cost kept (`Arbeitszeugnis`/`Zeugnis`). 20,000 fuzzed inputs: no exception.
