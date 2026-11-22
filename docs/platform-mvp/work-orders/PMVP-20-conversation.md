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
