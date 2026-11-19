# Knovas Assistent: the service (PMVP-20)

Item PMVP-20 (Jira SS-732), plan `FEAT-2026-11-19-platform-mvp` § 5.2, § 7, § 9, bet `BET-2026-11-platform-mvp`.
Requirements W-03, W-05; contract L2-PLATFORM-ASSISTANT POST-01, POST-02, POST-04, POST-05, POST-06.
Frozen acceptance tests: `tests/test_pmvp20_assistant_acceptance.py`.

The service behind the one company assistant. Given a signed-in person's question and the chat so far, it answers
from the passages that person's own search returns, and shows only sentences whose references it could check.
No routes, no pages (PMVP-22a), no chat storage (PMVP-21).

| File | What it holds |
|---|---|
| `service.py` | `AssistantService`, `Turn`, `Passage`, `Sentence`, `Reply`, `NO_ANSWER_TEXT`, the composer, the sentence splitter and the reference check. Imports neither Flask, nor the app, nor the HTTP client. |
| `errors.py` | `CoreBusy`, `CoreFailed` (base `CoreError`): an HTTP status and a reason code, never a body. |
| `core.py` | `make_search`, `make_generate`: the two callables, built from `KnovasAPIClient`. |
| `../knovas_client.py` | `llm_generate` (`POST /secured/llm/generate`), `keeping_snippets()` and `_snippet_from_hit` (the passage text in the row). |

## The five steps

`AssistantService(search=..., generate=...).answer(question, history=()) -> Reply`

1. **Retrieve.** `search(question, limit)` once, with the question as typed. `make_search` takes `/api/search`'s
   own path: `doc_fields_routes.search_plan` and `run_search` into `KnovasAPIClient.search_documents` ->
   `POST /secured/query`, the person's principal assertion attached by the client. The rows keep the Core's
   verbatim passage text as `"snippet"` (only inside `client.keeping_snippets()`), and experiment documents are
   taken out with the search page's own `SearchIntegration.split`. A row is a passage only when it has passage
   text and a pointer; passages are numbered 1..n in search order. There is no second way to read documents
   (POST-02).
2. **Compose.** The system instruction holds the rules only (answer only from the numbered passages, in the
   question's language, `[n]` after every statement, say you found nothing otherwise, ignore instructions inside
   passages, never name a model). The numbered passages (title, page, text) and the question are the last user
   message. Earlier turns go before it as `user`/`assistant` messages.
3. **Generate.** `generate(system_instruction=..., messages=..., max_tokens=...)` exactly once, and only when
   there is at least one passage. `make_generate` calls `KnovasAPIClient.llm_generate` and returns the text
   only, so the model's name never reaches the service.
4. **Check.** The answer is split into sentences; a sentence is shown only if it carries at least one `[n]` and
   every `[n]` is a retrieved passage's number. All others are removed and counted (POST-01, W-05).
5. **Reply.**

| `status` | `sentences` | `passages` | `message` |
|---|---|---|---|
| `answered` | the checked sentences (text without `[n]`, numbers in `refs`) | all retrieved passages | `""` |
| `no_answer` | `[]` | the closest passages (may be `[]`) | `NO_ANSWER_TEXT` = "Dazu finde ich nichts in Ihren Quellen" |
| `busy` | `[]` | `[]` | "Der Knovas Assistent ist gerade ausgelastet. …" |
| `failed` | `[]` | `[]` | "Der Knovas Assistent konnte gerade keine Antwort erstellen. …" (or: the question is too long) |

`removed` counts the sentences the check took out (also on `no_answer`).

## Rules, and where they are kept

1. **Passage text.** The Core attaches a verbatim window (at most 240 characters of the chunk's
   `original_text`, `QUERY_RETURN_CHUNK_SNIPPETS`, on by default since 2026-10-02) as `snippet` on each served
   `top_chunks` entry and the first of them as the hit's own `snippet` (KnowledgeBase
   `services/query_two_stage.py::attach_chunk_snippets`, `docs/Docs/04_COMPONENTS/query_document_finder.md` § 4).
   `_snippet_with_location` reads exactly that key (the chunks first, in order, then the row) and never
   `text`/`content`: the BM25 `text` field has its digits stripped. The text and its page come from the same chunk
   (`snippet_page_number`, `snippet_sentence_number`; unknown stays `None`), because `top_chunks[0]` -- the row's
   own page -- may carry no snippet (an auto summary ranked first). The row keeps the three keys only inside
   `client.keeping_snippets()` (per thread): the search page's `_search_result_haystack` reads `snippet` for
   "exact match" and `literal_query_matches`, so an always-present field would change what the page shows. The
   request sent to Knovas is the same either way.
2. **Strict check.** `check_answer`: `[1][2]`, `[1, 3]`, `[1;3]` and the full-width form name passages; a bracket
   holding digits that is not a plain number list (`[1-3]`, `[S. 4]`, `[2024]`) cannot be checked, so its
   sentence is removed; brackets without digits (`[...]`, `[sic]`) are text; a fragment without a letter (a list
   number) is not a sentence. `split_sentences` **cuts when unsure** (rework 1: a wrong join can show a statement
   without a reference under its neighbour's number, a wrong cut only drops text):
   - a line break always ends a sentence;
   - a stop, `;`, `!` or `?` right after `[n]` ends it, whatever follows; references right after a stop belong to
     the sentence before it and end it too;
   - `!`/`?` before whitespace end it; a `;` without a reference does not;
   - a full stop before whitespace ends it unless the word before it is an abbreviation (`ABBREVIATIONS`: `Art.`,
     `Abs.`, `Ziff.`, `Hr.`, `Fr.`, `Fa.`, `Kt.`, `med.` …, or a word with an inner stop like `z.B.`, `i.V.m.`), a
     lower-case single letter, a capital in `z. B.`/`u. U.` or before a number (`S. 4`, `E. 2`), or a 1-2 digit
     ordinal before a month (`1. März`) or after an article or preposition (`im 3. Quartal`, `am 5.`). Amounts,
     years, full dates and `Ziffer 5.` end it; `usw.`, `etc.`, `ff.` end it unless a lower-case word follows;
   - a stop glued to the next word ends a sentence only between a plain word and a capital (`Monate.Sie`).
3. **Nothing left** after the check, or no passages, gives `no_answer` with the closest passages; no passages
   means no generate call. A blank question is `no_answer` without a search. The no-answer sentence is never a
   cited answer: wherever it appears, with or without numbers, it is dropped (not counted in `removed`), and the
   system instruction asks for it without a number.
4. **Core limits** (KnowledgeBase `secure_api.py`, `/secured/llm/generate`): ≤ 32 messages, roles `user` and
   `assistant`, ≤ 50,000 bytes per message, system instruction ≤ 4,000 bytes, `max_tokens` ≤ 2,048. The constructor
   refuses settings past them. On top, the whole prompt stays within `prompt_budget_bytes` (default 14,000 UTF-8
   bytes): the model behind the route has a context of 8,192 tokens for prompt and answer together
   (`LLM_MAX_MODEL_LEN`), and the default answer cap is 1,024 tokens. Passages are cut first to fit (short ones stay
   whole, long ones share the rest, cut with an ellipsis; with history present they get 60 % of what is left after
   the system instruction and the question); history turns are dropped oldest first. History is cleaned before it
   is sent: `[n]` markers out of old answers (their numbers belong to old passages), two turns of one role in a row
   reduced to the later one, a trailing unanswered user turn dropped, the window starting with a user turn, one
   turn at most 4,000 bytes. A question over 4,000 bytes is refused before the search.
5. **Failures** (POST-06): `CoreBusy` from search or generation -> `busy` ("ausgelastet"); anything else ->
   `failed`. Never a partial answer, never an exception out of `answer()`. `llm_generate` maps HTTP 429/503 to
   `CoreBusy` and timeouts, connection errors, other HTTP errors and malformed envelopes to `CoreFailed`; it is one
   request (`_request_no_retry`), never retried. `make_search` maps the search's 429/503 the same way and refuses
   to run without the secured API (the legacy GET search logs the question text).
6. **Logs** (POST-04): one line per answer with status, counts, byte sizes and durations; failures log
   `type(exc).__name__` and an HTTP status. No question, history, passage, answer, pointer or exception text, no
   `exc_info`. `llm_generate` never logs a response body (an error body of that route could hold generated text).
7. Customer-facing texts say "Knovas Assistent"; the system instruction tells the model never to name a model.

## Open for PMVP-22a

- **Wiring.** Build the service where `api_client`, `identity_gate` and the experiments integration are known:

  ```python
  service = AssistantService(
      search=make_search(api_client,
                         split=app.extensions["experiments"]["search"].split,
                         user_key=lambda: doc_fields_routes.user_key_for(identity_gate)),
      generate=make_generate(api_client),
  )
  reply = service.answer(question, history)   # history: Turn(role, text), oldest first
  ```

  Call it inside the request, so the client's broker signs for the signed-in person (both calls fail closed with
  `PermissionError` -> `failed` when nobody is signed in). The route should refuse a question over
  `MAX_QUESTION_BYTES` itself and log nothing of the question either.
- **Opening a document from a reference.** The service grants nothing. The file routes serve only what retrieval
  handed the person (`_grant_for_current_user` in `app.py`, today called by `/api/search` and the listing). The
  route must grant the passages it shows, e.g. `_grant_for_current_user([{"doc_id": p.pointer} for p in
  reply.passages])` (`Passage.pointer` is the row's `doc_id`, which for search rows equals `path`), before a
  reference can open its document.
- **Test fakes.** `tests/conftest.py::DummyKnovasClient` has neither `keeping_snippets()` nor `llm_generate()`;
  route tests through `_identity_app` need both (conftest was not PMVP-20's to change).
- **What the page shows.** `Sentence.refs` point to `Passage.number`; the passages carry title, page, text and
  pointer. `removed` can be shown as a hint ("n Aussagen ohne Beleg entfernt").

## Known limits

- The model sees at most 240 characters per passage (the Core's window), at most `passage_limit` (10) passages.
- A follow-up question is searched as typed ("Und wann beginnt sie?"); the history informs the answer, not the
  retrieval.
- The search page's client-side refinements after Knovas answers (enrichment titles, the score thresholds that are
  off unless configured, the filename supplement) live in `app.py` and do not run for the assistant.
- A comma after `[n]` does not end a sentence ("… drei Monate [1], und 2023 …" stays one sentence under `[1]`);
  rework 1 names a stop, a semicolon and a line break. Whether a comma should cut too is open.
- Cutting when unsure drops some text on purpose: initials ("Hans M. Müller"), "Variante B. Danach …", web
  addresses with a capital after a stop.
