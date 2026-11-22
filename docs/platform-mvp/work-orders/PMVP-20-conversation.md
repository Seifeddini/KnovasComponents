# Work order PMVP-20, conversation: the Knovas Assistent talks like an assistant, and still proves every claim

| | |
|---|---|
| Item | PMVP-20 (Jira SS-732), plan `FEAT-2026-11-19-platform-mvp` |
| Asked | 2026-11-22, the founder after trying the demo copy (demo tenant, Claude `claude-opus-5-5`): "the knovas assistant doesnt really behave like an LLM". «wagwan» and «document» each got only «Dazu finde ich nichts in Ihren Quellen», over a list «Die nächsten Stellen» of raw file names (`0ef33273-…_2024-055_Beilage-Vertrag`). Fixed **today** |
| Class | **sensitive** (Siran approves): what the assistant may say without a source |
| Branch / worktree | `feat/pmvp-20-conversation` from `origin/feat/platform-mvp`, worktree `.worktrees/pmvp-20conv` |
| Frozen tests | none new. `test_pmvp20_statements_acceptance.py`, `test_pmvp21_*`, `test_pmvp23_*` and every other file of `frozen-tests.txt` stay green and unchanged. A frozen assertion that contradicts the founder's direction (for example one that pins the no-answer text for a greeting) is sent to the coordinator with its name and line; the builder stops on it |
| Not here | the quote and number checks of statements (`check.py`'s five checks stay as they are), the provider module, the search page, any migration |

`P` = `KnovasPlatform/components/docbridge_integration`.

## The guarantee that stays

Every claim about the documents is a **checked statement with a word-for-word quote**. What is new is a second kind
of line the model may write, a **reply**, for turns that make no claim about the documents. The Platform, not only
the instruction, keeps a reply from becoming an unchecked claim.

## What to build

1. **Conversational replies.** The model may answer with one short line `{"reply": "…"}` only for turns that make
   no claim about the documents: greetings and thanks; "what can you do"; asking the person to be more specific
   (for «document»: which matter, which kind of document, what they want to know); saying what it looked for when
   nothing fits, then suggesting how to ask.
   **Rules, enforced by the Platform:**
   - at most 400 characters;
   - **no digits**;
   - it names no document, person or matter that appears in the passages: checked against the passages' titles and
     against capitalised words of the passages that are not common German words (or a simpler rule the builder can
     justify);
   - shown **without sources**, styled as the assistant speaking, not as a sourced statement;
   - beside statements a reply may only be a short lead-in (the builder chooses whether to allow it; if allowed,
     the same rules apply);
   - a reply that breaks a rule is dropped; if nothing remains, the old no-answer path applies;
   - a new `removed_by` reason counts it.
2. **The instruction.** Friendly and natural, in the person's language; `reply` for small talk, meta questions and
   clarifying questions; document questions still only with checked statements; when nothing fits, says so in its
   own words in a `reply` and asks one helpful follow-up question.
3. **The no-answer state.** «Dazu finde ich nichts in Ihren Quellen» only when the model gives neither statements
   nor a valid reply.
4. **«Die nächsten Stellen».** A clean title: a leading UUID and its underscore dropped; a `<matter>_<kind>` name
   shown as «2024-055 · Beilage-Vertrag», with «S. n» when there is a page. Shown only under a clarifying reply or a
   no-answer, at most 5 entries, each opening the document as the page already does. The same clean title wherever
   a source title is shown (under statements, in a stored chat).
5. **Brain-tools mode** (`assistant.mode = brain_mcp`) gets the same reply rules.
6. **Docs**: `management/assistant.md` (what a reply may and may not contain) and the assistant's card.

## Your tests (`P/tests/test_assistant_conversation*.py`, fakes only; the pinned venv)

- a greeting gives a reply without sources;
- «document» gives a clarifying reply and at most 5 clean entries;
- a reply with a digit is dropped; a reply naming a passage's matter or title is dropped;
- a document question still needs quotes;
- replies and statements mixed;
- clean titles, with and without UUID and page;
- the chat stores the reply and shows it again;
- nothing of the text in any log line, the loggers set to DEBUG explicitly (FU-46).

## The real check (demo tenant, Claude)

At most **4 questions**: «wagwan», «document», «Was kannst du?», one real document question («Welche
Kündigungsfrist gilt im Mietvertrag?»). Read only: `POST /secured/query` and `GET /secured/document/<uuid>/text`,
at most one request every 2 seconds; certificate, key and CA in `E:\Knovas\.secrets\demo-tenant\` and the
Anthropic key never printed, logged or copied. Per question: reply or statements, and whether each passed the
checks; the reply texts may be quoted to show the tone, no document text.

## Done when

- The frozen files pass and the frozen check prints OK; `tests/test_assistant_*.py` and the chat and page tests
  green apart from the baseline; the whole suite is not run.
- Commits `PMVP-20 conversation: …`, pushed; no pull request.
- Your notes are in this order.

## Builder's notes

Builder session, 2026-11-22, branch `feat/pmvp-20-conversation` from `origin/feat/platform-mvp` @ `06719eb9`, worktree
`.worktrees/pmvp-20conv`; `origin/feat/platform-mvp` merged at `ac1293ef` (the coordinator's re-pin). Nothing here is
verified; that is the verifier's.

**Stopped on, and decided.** The frozen `test_no_passages_means_no_generation_and_the_no_answer_reply` pinned
`generate.calls == []`; on the demo tenant the search returns nothing for «wagwan» (rows 0), so no reply could ever
come. Sent to the coordinator with the assertion; the coordinator re-pinned it to `len(generate.calls) <= 1`
(`ac1293ef`) and asked for the zero-passage call, built here.

**Built.**

- `assistant/conversation.py` (new): `check_replies(text, passages, *, statements_shown)`, `check_reply`,
  `holds_a_number`, `passage_names`, `names_a_passage`, `is_common`, `clean_title`, `nearest`; `check.is_reply` and
  the sixth reason `check.REPLY_DROPPED` (`reply_dropped`); `check_answer` leaves reply lines alone.
- `service.py`: the instruction rewritten (3,790 bytes; was 2,498); `Reply.reply_text`, `Reply.reply_kind`;
  `finished()` builds every checked reply (also the Brain tools'); with no passages the model is asked once, the last
  message saying "Numbered passages: none … answer with one reply"; the log line gains `reply=<kind|lead_in|->`.
- `brain_tools.py`: the same rules; a reply needs no tool result; `CLOSEST_PASSAGES` is the nearest five.
- The page (`assistant_routes.py`, `ask.html`, `ask.css`): a reply in a box under «Knovas Assistent», no reference,
  no quote; clean titles in every source list and quote card; «Die nächsten Stellen» at most five; the stored JSON
  keeps `reply` and `kind` (`v` stays 1, an older reader ignores them); the next turn's history holds the reply.

**The reply rules as built** (`conversation.py`; each has tests in `test_assistant_conversation*.py`):

| Rule | As built |
|---|---|
| form | an object with `reply` and without `statement`; with both it is a statement and needs its quote |
| text | a letter, all letters Latin (as a statement's); white space single, unseen characters out |
| length | at most 400 characters; beside statements (a lead-in) at most 200 |
| no number | no Unicode number character, none that looks like a digit, nothing `check.claimed_numbers` reads (number words, «einen Monat», months, Roman numerals, a capital letter alone); «E-Mail» and the English «I» before a contraction or a verb are words |
| no name | no word of a passage's title or pointer, no capitalised word of its text that begins no sentence, line or list item, unless common (function words, a list of general nouns, their stems and their compounds); folded and by stem, a genitive s too |
| one a turn | the first that passes; every further reply dropped |
| lead-in | shown above the statements only if one of them passed; else dropped |
| kind | `clarify`: the nearest five passages under it; `chat`, missing or unknown: none |

**Decided beyond the order** (the verifier should judge each):

1. *A reply-only turn is `no_answer`*, its `message` the reply: the harness (PMVP-03) scores the status, and a turn
   without a statement is no answer from the documents. The page tells them apart by `reply_text`.
2. *"No digits" became "no number"*: «drei Monate» or «Ende März» in a reply would be an unchecked claim as much as
   «3 Monate».
3. *The person's question words are not exempt from the name rule*: «Ja, Herr Vogt hat unterschrieben» would
   otherwise pass after «Hat Herr Vogt unterschrieben?». A name the passages do not hold may be echoed.
4. *Titles and pointers count*: a client's folder in the pointer (`Mandanten/Limmat/…`) is a name.
5. *The nearest five are cut in the service too*: fewer documents granted to the person and less passage text stored
   with the chat; under a `chat` reply nothing is stored or granted. The page cuts older stored chats to five.
6. *Clean titles on the pages only*; the prompt keeps the title as the Core gives it. A matter is digit groups joined
   by `-`, `.` or `/` before the first underscore; any other title stays as it is (after the UUID).
7. *The instruction is 1,292 bytes longer*: the passages' share of the 14,000-byte prompt budget shrinks by as much
   (8 contexts and 2 snippets now lose about 1,300 bytes to `fit_texts`).
8. *"In the chat list"*: the sidebar lists chat titles, which are the person's questions, not source titles; the
   clean title applies to every source shown on the chat pages, new and stored.

**Existing tests changed, with reasons.** `test_assistant_check.py::test_every_reason_is_counted_under_its_own_name`
and `test_assistant_brain_tools.py::test_a_quote_outside_the_tool_results_is_removed` (the sixth reason is a key of
`removed_by`, at 0); `test_assistant_service.py::test_the_examples_in_the_instruction_pass_the_check_they_teach` (the
instruction shows a second form and a reply example; the test now checks both); `test_assistant_service.py::
test_a_reply_without_a_check_has_every_reason_at_zero` and `test_assistant_context_reads.py::
test_no_passages_means_no_read` (with no passage the model is now asked once).

**The real check** (demo tenant, read only, Claude `claude-opus-5-5`; 4 model calls; to the Core only
`POST /secured/query` and `GET /secured/document/<uuid>/text`, 2.05 s apart: 7 POST and 3 GET, all 200, of which 3
POST were the same two one-word questions before the zero-passage call existed, without a model call):

| Question | Search | Model wrote | Shown |
|---|---|---|---|
| «wagwan» | 0 rows | a reply, English: "Hey, not much, all good here! I'm the Knovas Assistent, so just ask me anything about the firm's documents and I'll dig in." | **dropped** (number rule: the «I» of «I'm» read as the Roman one) → «Dazu finde ich nichts in Ihren Quellen». Fixed afterwards (the English «I»), pinned with this text; not asked again (4-question limit) |
| «document» | 0 rows | a `clarify` reply, English: "Happy to help you find it! Which matter is it about, what kind of document are you looking for, and what would you like to know about it?" | **the reply**, no list (nothing found) |
| «Was kannst du?» | 0 rows | a `chat` reply: "Ich bin der Knovas Assistent und beantworte gern Ihre Fragen zu den Dokumenten der Kanzlei, etwa zu Verträgen, Schriftsätzen oder Fristen. Nennen Sie mir einfach das Mandat oder das Thema, und ich suche die passenden Stellen heraus." | **the reply** |
| «Welche Kündigungsfrist gilt im Mietvertrag?» | 10 rows, 6 passages, 3 widened (8 s at the 2 s spacing) | no statement; a `clarify` reply that said what it looked for and also what the passages were about («… betreffen vor allem eine Klage und Fristen zur Stellungnahme. Können Sie mir sagen, um welches Mandat …») | **dropped** (name rule: «Stellungnahme» stands capitalised in the passages): «Dazu finde ich nichts» with five nearest, clean titles of the shape «9999-999 · Wort», «S. n» on the one with a page. The instruction now says a reply does not say what the passages contain; not asked again |
