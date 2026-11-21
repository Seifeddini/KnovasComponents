# PMVP-20: second verification, and the founder's decision to pause

| | |
|---|---|
| Item | PMVP-20 (Jira SS-732) |
| Verified | `feat/pmvp-20` @ `074a334` (rework 1), fresh verifier session, 2026-11-19 |
| Verdict | **FAIL** (the second; the first was on `1f60910`) |
| Decision | Founder, 2026-11-20, on the coordinator's decision request (two failed gate attempts): **PMVP-20 is paused.** Other items are built first; the rework comes later |
| State | `feat/pmvp-20` is **not integrated**. Nothing of `src/assistant/` is on `feat/platform-mvp` |

## What failed

The splitter still joins two statements, so a statement without a reference is shown under the other's number
(`removed=0`). The verifier's 144 cases are in `docs/platform-mvp/evidence/pmvp20-verification-2/break_split.py`
(run it with the path of `P/src` as its argument): 89 joined. Line numbers are `src/assistant/service.py` @ `074a334`.

| # | Join | Example (the first statement has no reference) | Where |
|---|---|---|---|
| 1 | An abbreviation at the end of a sentence | "Der Streitwert beträgt CHF 2 Mio. Die Frist beträgt drei Monate [1]."; also "1'500 Fr.", "inkl. MwSt.", "& Co.", "Mrd.", "Jan.", "et al." | `_inside_sentence` ~738 |
| 2 | A single lower-case letter before the stop | "… nach Art. 271a Abs. 1 lit. a. Die Frist …[1]."; "Art. 271 f. Die Frist …" | ~729 |
| 3 | A token with a stop inside | "… in Mietvertrag_2023.pdf. Die Frist …[1]."; "u.a.", "e.V.", "a.a.O.", domains, e-mail addresses | ~736 |
| 4 | Markdown emphasis around a stop | "\*\*Die Kündigung ist nichtig.\*\* Die Frist …[1]."; "\*\*Die Frist ….\*\* [1] Die Miete beträgt CHF 500." | `_CLOSERS` ~617, ~700 |
| 5 | A stop with no space after it | "CHF 500.Die Frist …[1]."; "2023.Die"; "gültig?Die"; "!Die" | ~741, ~698 |
| 6 | A number after a preposition | "… stieg auf 12. Die Frist …[1]."; "um 10.", "bis zu 20.", "mit 18.", "seit 3.", and "fällig am 5. Die Frist …" | `ORDINAL_LEADS` ~607 |
| 7 | Rarer | "…" (U+2026) and full-width stops are not stops; "B. 2023 kündigte"; a zero-width space after a stop; a semicolon without a reference never cuts; a colon or dash after `[n]`; table rows | ~615, ~658 |
| 8 | The no-answer sentence in a wrapper | "\*\*Dazu finde ich nichts in Ihren Quellen [1].\*\*", "«…» [1].", "Antwort: … [1]." come back as answered | `is_no_answer_sentence` ~541 |

Number 6's "am 5." follows an example in the rework order ("preceded by an article or preposition"). That example
was the coordinator's second mistake in the same direction: a preposition before a number says nothing about
whether the stop ends the sentence.

Quality notes (referenced text now cut and lost): "Nach Art. 266a ff. OR gilt …[1]." keeps only "OR gilt …";
"am 1. und 15. jedes Monats"; "Hr. Dr. iur. M. Meier"; "Ziff. 5. des Vertrags"; "(vgl. [1]).".

## What passed

Frozen files unchanged (three blob ids OK); acceptance 22 and 24 passed, the builder's 153 passed; the whole suite
has the same failing ids as base `a55cdb0`. Rework items 3, 5 and 6 are done: the abbreviations are there, a
passage's page comes from its snippet's chunk (never from another), and the note on `/secured/fulfill-evidence` is
right (template evidence retrieval, no judgement, a second way to read documents). The system instruction no
longer asks for a number on the no-answer sentence. Every log line the branch adds carries only status codes,
counts, durations and exception class names. Scope kept.

## For whoever takes PMVP-20 up again

The cause is the design, not the lists: the splitter asks "could this stop be inside a sentence?" with open-ended
guesses (any abbreviation, any inner stop, any number after a small word). Each guess that is wrong joins.
The coordinator's proposal, not yet ordered:

1. **Every stop ends a sentence**, as do `?`, `!`, `…`, `;`, a line break and their full-width forms, with or
   without a following space. Exceptions come only from closed lists:
   - abbreviations that cannot end a sentence because something must follow them ("Art.", "Abs.", "Ziff.", "lit.",
     "Nr.", "vgl.", "gem.", "z. B.", "d. h.", "Dr.", "Hr."); "Fr." only before a capitalised name, never after a number;
   - a 1-2 digit number and a stop before a month name or a word from a closed list of ordinal nouns ("Quartal", …).
   Words that can end a sentence ("Mio.", "Mrd.", "Co.", "MwSt.", "usw.", "ff.", a single letter, a file name) cut.
2. **Normalise first:** Markdown emphasis, code marks and list marks are transparent to the splitter; zero-width
   and no-break spaces become spaces.
3. **The no-answer sentence** is recognised after the same normalisation (wrappers, quotes, a leading label).
4. **Frozen tests** from the 144 cases, plus the quality cases that must stay whole. The acceptance harness
   (PMVP-03) reports how much referenced text the cuts cost.

The founder's other options were a different approach altogether (the model writes one statement per line, or a
second model call checks each sentence against its passage); they stay open.

## What waits on PMVP-20

PMVP-22a, 22b, 23, 24, 25 and 31 (the assistant's pages and switch, the evidence trail from a reference), and the
assistant part of PMVP-33. Not waiting: PMVP-01, 03 (against the service's interface), 10, 11, 12, the Brain views
30a, 30b, 32, 34, Admin 40 to 43.

## Resumed on 2026-11-20 with another design

The founder resumed the item the same day and asked for a changed approach. The proposal above (stricter
cutting rules) was dropped; `PMVP-20-rework-2.md` replaces it: the model answers in statements with one source and
a word-for-word quote each, and plain code checks form, source, quote and numbers. Nothing is cut into sentences.
