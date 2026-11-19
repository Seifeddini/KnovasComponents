# Work order PMVP-20 — Assistant service: retrieve, compose, generate, check

| | |
|---|---|
| Item | PMVP-20 (Jira SS-732), plan `FEAT-2026-11-19-platform-mvp` § 5.2, § 7, § 9 (KnowledgeBase `docs/ModernDocs/plans/2026-11-19-platform-mvp.md`) |
| Bet | `BET-2026-11-platform-mvp` (approved 2026-11-19) |
| Requirements | W-03, W-05; contract L2-PLATFORM-ASSISTANT POST-01, POST-02, POST-04, POST-05, POST-06 |
| Class | sensitive (Siran approves) |
| Branch / worktree | `feat/pmvp-20` from `feat/platform-mvp`, worktree `.worktrees/pmvp-20` |
| Frozen tests | `KnovasPlatform/components/docbridge_integration/tests/test_pmvp20_assistant_acceptance.py`, SHA-256 in `docs/platform-mvp/frozen-tests.sha256` (check: `sha256sum -c docs/platform-mvp/frozen-tests.sha256`) |

## Goal

The service behind the one company assistant ("Knovas Assistent"). Given a signed-in person's question and the chat
so far, it (1) retrieves passages through the same search the search page uses, as the same person, (2) numbers them
and composes a prompt with the last turns of the chat within the Core's limits, (3) calls `POST /secured/llm/generate`
once, (4) checks every `[n]` in the answer against the retrieved passages, and (5) returns a reply the page can show.
No routes and no pages in this item (that is PMVP-22a); no chat storage (PMVP-21).

## What to build (the frozen tests pin the interface)

`src/assistant/` (a new package; P = `KnovasPlatform/components/docbridge_integration`):

- `assistant/errors.py`: `CoreBusy` and `CoreFailed` (exceptions; a common base class is fine).
- `assistant/service.py`:
  - `NO_ANSWER_TEXT = "Dazu finde ich nichts in Ihren Quellen"`.
  - `Turn(role, text)`: a frozen dataclass; `role` is `"user"` or `"assistant"`.
  - `Passage` (number, pointer, title, page, text, …), `Sentence(text, refs: tuple[int, ...])`, and `Reply` with
    `status` in `{"answered", "no_answer", "busy", "failed"}`, `sentences`, `passages`, `removed` (int) and `message`.
  - `AssistantService(*, search, generate, …)` with `answer(question, history=()) -> Reply`.
    `search(question, limit) -> list[row]` and `generate(*, system_instruction, messages, max_tokens) -> str` are
    injected, so the service never imports Flask, the app or the HTTP client.
- An adapter that builds the two callables from the existing `KnovasAPIClient`, so PMVP-22a only wires them:
  - **search:** the same path as `POST /api/search` uses (`doc_fields_routes.search_plan` / `run_search` →
    `KnovasAPIClient.search_documents`). The principal assertion is attached by the client, as for search. The
    assistant has no second way to read documents (POST-02).
  - **generate:** a new `KnovasAPIClient` method for `POST /secured/llm/generate` (body: `messages`,
    `system_instruction`, `max_tokens`, `temperature`; response envelope `{"status": "success", "data": {"content",
    "model", "prompt_tokens", "completion_tokens"}}`). Use `_request_no_retry` (a generation must not be retried
    silently) and never log the response body: `_make_request` logs up to 600 characters of an error body, which
    for this route could hold generated text. Map HTTP 429 and 503 to `CoreBusy`; timeouts, other HTTP errors and
    malformed envelopes to `CoreFailed`.

## Rules and design decisions (the coordinator's, two-way-local)

1. **Passage text.** Rows from `search_documents` carry no passage text today: `_secured_query_hit_to_row` keeps
   locations but not the Core's verbatim chunk snippets (`QUERY_RETURN_CHUNK_SNIPPETS`, on by default since
   2026-10-02; `_chunk_text_from_hit` exists and is unused). Keep the snippet text in the row as `"snippet"` (an
   additive field; the search page must behave exactly as before). Find out from the Core's
   `/secured/query` response (KnowledgeBase `knovas-software/app/src/services/query_pipeline.py`,
   `docs/Docs/04_COMPONENTS/query_document_finder.md`) where the snippet sits. A row without text is not a passage:
   the assistant only cites what it can show.
2. **Strict check.** A sentence is shown only if it carries at least one `[n]` and every `[n]` is a retrieved
   passage's number. Others are removed and counted (`removed`). `[1][2]` and `[1, 3]` both name two passages. The
   shown sentence text has no `[n]` markers; its refs are in `refs`. Split sentences robustly enough for German legal
   text ("z. B.", "Art. 5", "1. März 2024"); a cautious splitter that keeps an abbreviation inside one sentence is
   better than one that cuts it.
3. **Nothing left** after the check, or no passages at all, gives `status="no_answer"`, `message=NO_ANSWER_TEXT` and
   the retrieved passages as "the closest passages". No passages → no generate call.
4. **Core limits** (KnowledgeBase `secure_api.py`, `/secured/llm/generate`): ≤ 32 messages, roles `user` and
   `assistant` only, ≤ 50,000 bytes per message, `system_instruction` ≤ 4,000 bytes, `max_tokens` ≤ 2,048. The
   numbered passages go into the last user message (the system instruction is too small); cut passages to fit;
   drop the oldest history turns first. The system instruction says: answer only from the numbered passages, in the
   question's language, put `[n]` after every statement, and say you found nothing if the passages do not answer.
   Never name an underlying model to the user.
5. **Failures** (POST-06): busy → `status="busy"` with a plain German message containing "ausgelastet"; any other
   failure in search or generation → `status="failed"` with a plain message. Never a partial answer, never an
   exception out of `answer()`.
6. **Logs** (POST-04, GI-DATA-04 style): ids, counts, durations and status only. No question, history, passage,
   answer or exception text, no pointer. Log `type(exc).__name__`, never `str(exc)`.
7. Customer-facing text says "Knovas Assistent".

## Files you may change

- new: `P/src/assistant/**`, `P/tests/test_assistant_*.py` (your own tests), `P/src/assistant/README.md`
- `P/src/knovas_client.py`: only the new generate method and keeping the snippet in the row (+ their tests in a new
  test file of yours)
- `docs/platform-mvp/work-orders/PMVP-20.md`: only the "Builder's notes" section at the end

## Files you must not change

- the frozen test file above; `P/tests/conftest.py`; `P/src/web_interface/app.py`; templates; stylesheets;
  migrations; anything of PMVP-21 (`P/src/identity/chats.py`, migration 0026)

If the work needs one of these, stop and write the reason under "Builder's notes" instead of changing it.

## Done when

1. `cd P && py -3 -m pytest tests/test_pmvp20_assistant_acceptance.py -q` is green, the file unchanged (SHA-256).
2. Your own tests cover the client method (body sent, assertion attached, 429/503 → CoreBusy, other → CoreFailed,
   no error body in the log) and the snippet field; `py -3 -m pytest -q` for the whole Platform suite is green
   (test DB: `PLATFORM_DB_TEST_DSN`, default port 55433, already running) or every failure is shown to fail on the
   base commit too.
3. `P/src/assistant/README.md` explains the five steps, the rules above and the open point for PMVP-22a (wiring and
   the `_grant_for_current_user` grant for opening documents from a reference).
4. Commits on `feat/pmvp-20` name the item (`PMVP-20: …`) and end with the co-author line; the branch is pushed.
   No pull request: the coordinator integrates after a verifier session passes it.

## Builder's notes

(the builder writes here: what was decided, what is open, what the verifier should look at)
