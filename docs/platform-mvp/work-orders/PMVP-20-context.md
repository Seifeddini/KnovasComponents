# Work order PMVP-20, context: the passage around each hit, read from the document's text

| | |
|---|---|
| Item | PMVP-20 (Jira SS-732); takes the place of Core request CR-1 for the pilot; FU-37 |
| Decided | 2026-11-22, the founder: "Yes, with limits" (the assistant may read the document-text route under the limits below). POST-02 is amended to these limits. |
| Found | 2026-11-22, the coordinator's read-only probe on the **demo tenant** (production Core): `GET /secured/document/<uuid>/text` answers 200 with the document's indexed text in reading order (`text`, `offset`, `next_offset`, `total_chars`, `truncated`, `page_from`, `page_to`, `source: indexed_text`; at most 20,000 characters a part); a hit's `top_chunks[].snippet` is found inside it once whitespace is normalised |
| Class | sensitive (Siran approves): what text of a document reaches the model |
| Branch / worktree | `feat/pmvp-20-context` from `origin/feat/platform-mvp`, worktree `.worktrees/pmvp-20c` |
| Not here | `src/assistant/check.py` (the quote and number checks stay as they are), the provider module (PMVP-25), the search page |

## The Core's route (read it, do not change it)

KnowledgeBase `origin/master`, `knovas-software/app/src/api/secure_api.py` ~2362-2470 (`document_text`) and
`services/document_reading_order.py`. Read with `git show`; check nothing out. Facts:

- the tenant is the certificate's;
- the principal is resolved **without** a claim (`from_request(client_id, None)`), so today it reads as the search
  does under `disabled`, sees open documents only under `enforcing`, and answers 401 under `brokered` (Core request
  CR-1c);
- a document the principal may not see answers the 404 of a missing one;
- paging by `offset`, `max_chars` (1-20,000), `max_chunks`; `422 invalid_paging` for bad values;
- admission class `metadata`; billed as `document_retrieval`;
- the route accepts a declared actor (`?actor=`), like search.

## The limits (the founder's, 2026-11-22; each one has a test)

1. **Only documents the person's own search just returned.** The `document_uuid`s of the hits that this question's
   search (inside `keeping_snippets()`, as the person) returned, and only those whose passage is used. Never a
   document id from the model, from a chat, from a URL or from an earlier question.
2. **The Core decides every read.** A 404, 401, 403, 422, 429, 5xx, a timeout or a connection error means: no
   context for that hit; its passage stays the search's snippet (today's behaviour). The answer never fails because
   a read failed.
3. **As the person.** The read goes through the same client and the same principal in force as the search
   (`asserting_for` / the broker) and declares the same actor. Today the Core takes no claim on a GET (CR-1c); once it
   does, the read sends it. Until then nothing narrows (as for search under `disabled`).
4. **Bounded.** At most `ASSISTANT_CONTEXT_DOCS` documents per question (default 8, the passages the prompt can
   hold), at most 3 parts of 20,000 characters per document, one read at a time, a total budget of
   `ASSISTANT_CONTEXT_SECONDS` (default 8 s) after which the remaining hits keep their snippets; one read per document
   per question (two hits in one document share it).
5. **Nothing of the text in logs**, at DEBUG too (FU-46: set the loggers to DEBUG explicitly in the test): counts and
   ids only (documents read, passages widened, fallbacks by reason).

## What to build

1. **Read the text** of a returned document through a new client method (route template
   `/secured/document/{id}/text`; the RB-FIX-1/RB-FIX-2 logging rules: route, status, short code, request id).
2. **Find the hit's chunk** in the text: whitespace normalised, a leading or trailing «…» dropped; first the whole
   snippet, else the longest run of at least 8 consecutive words of it; read the next part (`next_offset`) when the
   chunk is not in the part read, up to the limit. Not found: the snippet stays.
3. **Cut the context**: `ASSISTANT_PASSAGE_CHARS` (default 1,200) characters of that document's text around the
   chunk, at word boundaries, the chunk inside it (centred where the document allows). Page and sentence stay those
   of the chunk the search named (rework 1, item 5). The context replaces the snippet **as the passage given to the
   model and checked against**: the quote check (word for word, at least 3 words) and the number check run against
   this passage, unchanged.
4. **Everything else stays**: the search page (outside `keeping_snippets()` rows have no `snippet`), `service.compose`'s
   budget (say what is cut first if 8 contexts do not fit), PMVP-21's stored references (PMVP-23's D2 keeps the
   passage text: the context is what is kept).
5. **Docs**: `management/assistant.md` and `management/data-protection.md` (what is read, under which limits, what
   goes to the chosen provider: the context, as passages did; DEC-0084); the assistant's card.

## Your tests (`P/tests/test_assistant_context_*.py`, a recording fake Core)

- found and widened; found in the second part; not found → snippet;
- each refusal and failure above → snippet, the answer still produced;
- only returned documents read (a document id in the question or in the model's answer is never read);
- one read per document per question; the budget stops further reads;
- the cut at word boundaries with the chunk inside; page and sentence from the search's chunk;
- the quote check against the context (a quote from outside the context is removed);
- nothing of the text in any log line at DEBUG;
- the search page unchanged.

## Done when

- The frozen PMVP-20 (88), PMVP-21 (13) and every other frozen file pass; the frozen check is OK.
- `tests/test_assistant_*.py`, `tests/test_knovas_client_*.py`, `tests/test_web_*.py` green apart from the baseline.
- **On the demo tenant** (read only; certificate, key and CA in `E:\Knovas\.secrets\demo-tenant\`, never copied,
  printed or logged; Core `https://api.knovas.ch:8443`; the Platform client config in the coordinator's scratchpad
  `demo-tenant\config.yaml`; **only** `POST /secured/query` and `GET /secured/document/<uuid>/text`, at most one
  request every 2 seconds; never the Knovas GmbH tenant): for five questions about the demo firm's documents, write
  down per question how many passages were widened and their lengths, without their text. Do not call the language
  model; the verifier does that.
- Your notes are in this order.

## Builder's notes

Builder session, 2026-11-22, branch `feat/pmvp-20-context` from `origin/feat/platform-mvp` @ `2a0293d2`, worktree
`.worktrees/pmvp-20c`. Commits `39bbbf27` (code and tests), `b2fa9f76` (README, management pages, the card) and the
commit of these notes. Nothing here is verified; that is the verifier's.

**Built.**

- `KnovasAPIClient.document_text(document_uuid, *, offset=0, max_chars=20000, timeout=None)` (`knovas_client.py`): one
  `GET /secured/document/<uuid>/text` through `_request_no_retry`, so the principal in force is attached as on every
  call (`_with_principal`) and, inside `asserting_for`, the actor goes as `?actor=`; never retried. Nothing is sent
  unless the id parses as a UUID (the path holds its canonical form only), `offset` is a whole number, `max_chars` is 1
  to 20,000 and the secured API is configured. The answer is the Core's envelope (top level, or under `data`):
  `text`, `offset`, `next_offset`, `total_chars`, `truncated`; anything else is a `ValueError` and one INFO line (route,
  status, request id, body size). `/secured/document/{id}/text` joins `_ROUTE_TEMPLATES`, so RB-FIX-1's failure line
  and RB-FIX-2's error name the route, never the id.
- `assistant/core.make_read_text(client)`: the reader, `PART_CHARS` (20,000) a part, the time left as the timeout;
  HTTP 429/503 -> `CoreBusy`, every other HTTP error, a timeout, no connection, nobody signed in and a malformed part
  -> `CoreFailed` with a short reason, raised `from None`. None for a client without `document_text` (conftest's
  `DummyKnovasClient`): then nothing is read. `make_service` passes it to the service.
- `assistant/context.py` (new): `widen_passages(passages, read_text)`, the pure `tidy`, `find_chunk`, `cut_around`, the
  settings `ASSISTANT_CONTEXT_DOCS` (0 to 50, default 8), `ASSISTANT_CONTEXT_SECONDS` (more than 0, at most 60, default
  8; a wrong value gives the default and one warning naming the setting) and `ASSISTANT_PASSAGE_CHARS` (read as the
  client reads it, without a second warning).
- `assistant/service.py`: `Passage.document_uuid` (the row's `document_uuid`, default None); `AssistantService(...,
  read_text=None)`; step 1b widens the passages after `passages_from_rows` and before `compose`. Without a reader
  nothing changes: the frozen file builds the service without one.

**The five limits, where each is kept, and its test** (`test_assistant_context_*.py`: text 27, reads 52, core 59).

| Limit | Kept by | Tests (examples) |
|---|---|---|
| 1. only documents this question's search returned | the id is `Passage.document_uuid`, from the search row of a passage this question uses; the reads run in the service between the search and `compose`, before the model is called; nothing is kept between questions; experiment rows are split out before | `test_only_the_documents_this_search_returned_are_read` (both files: an id in the question, the chat, the model's answer, an address; the experiment row), `test_a_passage_without_the_cores_document_id_is_not_read`, `test_a_row_that_is_no_passage_is_not_read`, `test_nothing_is_kept_from_one_question_to_the_next`, `test_only_the_assistants_reader_calls_document_text` |
| 2. the Core decides every read | any exception of the reader keeps that hit's snippet; the whole widening is guarded; after 401, 429, 503 or nobody signed in, no further document of the question is read | `test_each_refusal_and_failure_keeps_the_snippet_and_the_answer_is_produced` (404, 401, 403, 422, 429, 500, 502, 503, timeout, transport, malformed, no principal, two other exceptions), the same over the real client (`..._and_the_answer`: 12 answers and transport errors), `test_a_malformed_part_keeps_the_snippet` (8 shapes), `test_widening_never_raises` |
| 3. as the person | the same client, inside the same `asserting_for` (`AsTheAssistant`); `_request_no_retry` attaches the principal and `?actor=` | `test_the_read_is_made_as_the_person_who_searched`, `test_inside_the_assistants_claim_the_read_declares_the_same_actor`, `test_document_text_with_nobody_signed_in_sends_nothing`, `test_make_service_wires_the_reader` |
| 4. bounded | `ASSISTANT_CONTEXT_DOCS`, 3 parts, `ASSISTANT_CONTEXT_SECONDS` with the time left as each read's timeout, one reading per document, one read at a time, no retry | `test_at_most_eight_documents_are_read_the_lowest_ranked_keep_their_snippets`, `test_assistant_context_docs_sets_how_many` (2, 0, 50), `test_the_time_budget_stops_further_reads_and_bounds_each_read`, `test_assistant_context_seconds_sets_the_budget`, `test_not_found_keeps_the_snippet_after_at_most_three_parts`, `test_two_hits_in_one_document_share_one_reading`, `test_a_refused_read_is_one_request_...` |
| 5. nothing of the text in logs | one INFO line per question with counts only; the client's failure line has route, status, code, request id | `test_nothing_of_the_text_reaches_a_log_line_at_debug` (the loggers `""`, `knovas_client`, `assistant*`, `web_interface`, `urllib3`, `requests` set to DEBUG explicitly, FU-46; success, a 404 with text in its body, a long document), `test_a_refused_read_is_one_request_and_its_line_names_the_route_not_the_document` |

And: found and widened (`test_the_passage_becomes_the_context_around_the_chunk`), found in the second part, across a part
boundary, not found; the cut at word boundaries with the chunk centred, at a document's start and end, a short document
whole; page and sentence from the search's chunk; the quote check against the context (a quote from the context beyond
the snippet is shown, one from outside the context is removed, a number checked against the context); the search page
unchanged (`test_the_search_page_reads_no_document_text`: one POST, no `snippet`; and no other caller of
`document_text` in `src`).

**Decided beyond the order** (the verifier should judge each):

1. *Where.* In the service (step 1b) with the reader injected, not inside `make_search`: the search callable stays
   `/api/search`'s path, and the reads take ids only from this question's `Passage` objects. A service without a
   reader reads nothing.
2. *A refusal for every document stops the question's reads*: after a 401, 429 or 503, or nobody signed in, the rest
   are `stopped` (the Core would answer the same; it saves requests and billing). Other failures affect their own
   document only.
3. *No extra read for the context's tail.* The next part is read only while the chunk is not in the text read. A chunk
   within 600 characters of a 20,000-character part boundary gets a window that takes more from the earlier side
   (centred within the text read, not the whole document).
4. *White space* of the context: one character per run, a line break where the run held one (the number check reads
   two numbers on two lines as two); no `…` added at the context's edges.
5. *Matching*: a match may not begin or end inside a word (a letter or digit on both sides); the first place counts;
   a run never crosses the `…` that joins two snippets; the longest run is found by a binary search on its length
   (a run that is found holds shorter runs that are found).
6. *A context no longer than the snippet* (the document is the chunk) keeps the snippet (`nothing_more`).
7. *Settings*: `ASSISTANT_CONTEXT_DOCS=0` reads nothing (an off switch); a read is not started with less than 0.2 s
   left; the per-document state lets a later part's failure keep what earlier parts found for other hits.
8. *The log line* carries counts only, no ids (the order allows ids; none are needed).
9. *The claim on the GET*: the read sends the principal as every GET of the client does today (the body field
   `principal_assertion`) plus `?actor=`. The Core ignores both on this route until CR-1c; when the Core picks CR-1c's
   form (a header or a query parameter), the client follows (one place, `_with_principal`).

**What `compose` cuts first** (measured with German text, 1,232 bytes per 1,200 characters; `compose` unchanged): with
no history (the ask page sends none today) 8 contexts and 2 snippets take 13,034 of the 14,000 bytes: nothing is cut.
Ten contexts (`ASSISTANT_CONTEXT_DOCS=10`) are cut to about 1,075 characters each. With history (PMVP-23) the passages
get 60 % of what is left: the snippets stay whole, each context is cut **from its end** to about 775 characters with
`…`, then the oldest history turns go. A chunk centred in a 1,200-character context survives that cut up to about 350
characters; a longer one loses its end in the prompt while the check still reads the whole context (rework 2,
decision 4).

**Existing tests changed:** none.

**The demo tenant** (read only, 2026-11-22; the Platform's own `KnovasAPIClient` with the coordinator's config, no
principal broker, so no claim was sent; a guard on the session allowed only `POST /secured/query` and
`GET /secured/document/<uuid>/text` and kept 2.05 s between requests; `ASSISTANT_CONTEXT_SECONDS=60` because of that
spacing; no language model call). Two runs of the same five questions, 43 requests each (5 POST, 38 GET, all 200),
the same lengths both times. Characters per passage in search order, the snippet before and the passage after:

| Question | Passages | Widened | Before | After |
|---|---|---|---|---|
| Wie lange ist die Kündigungsfrist im Mietvertrag? | 8 | 8 | 24, 172, 139, 48, 42, 61, 48, 31 | 493, 762, 488, 422, 632, 399, 467, 425 |
| Wer vertritt die Gegenpartei? | 6 | 6 | 57, 57, 61, 61, 95, 95 | 480, 507, 399, 337, 402, 530 |
| Was steht in der Abmahnung im Mandat 2023-041? | 10 | 8 (2: limit) | 139, 86, 20, 163, 61, 34, 21, 40, 26, 39 | 488, 703, 494, 762, 500, 559, 425, 438, 26, 39 |
| Wie hoch ist das vereinbarte Honorar? | 10 | 8 (2: limit) | 80, 214, 21, 150, 80, 214, 45, 49, 49, 49 | 726, 635, 502, 559, 645, 541, 511, 834, 49, 49 |
| Bis wann muss die Klageantwort eingereicht werden? | 10 | 8 (2: limit) | 172, 172, 42, 139, 48, 139, 139, 139, 48, 48 | 629, 762, 632, 494, 422, 476, 527, 488, 48, 48 |

- Every widened passage holds its whole snippet (white space aside): 38 of 38. One part per document: every demo document is short (337 to 840 characters, `total_chars`), so each
  context is the whole document, shorter than 1,200 characters; no fallback but the 8-document limit.
- Time: a read took 0.40 s (median), 0.82 s at most; a search 0.87 s. At the Platform's own spacing (5 requests a
  second) 8 reads take about 3 to 4 s of the 8 s budget.
- Not measured: a long document (more than one part), a refusal from the Core, the claim on the GET (no broker), an
  answer (no model call; the verifier does that).

**Evidence** (from `P`, Windows, Python 3.13.5; own database `knovas_platform_test_pmvp20c`, dropped afterwards):

- The nine frozen files, `tests/test_assistant_*.py`, `tests/test_knovas_client_*.py` and `tests/test_web_*.py` in one
  run: **2,915 passed**, none failed, none erroring (1 h 45 min; the shared database server was slow). Of these: the
  frozen files 497 (PMVP-20 88, PMVP-21 13, PMVP-03 29, PMVP-10 33, PMVP-11 40, RB-1/2 66, RB-3/4 106, RB-5 95, RB-6/7
  27); `test_assistant_*` 1,043 (the new context files 138); `test_knovas_client_*` 308; `test_web_*` 1,067. Nothing
  outside the baseline fails. The whole suite was not run.
- The frozen check prints OK for all nine files of `frozen-tests.txt`. `py -3 scripts/check_ascii_py.py
  2a0293d2..HEAD`: 0 lines.
- `brain-runner card check` (KnowledgeBase `origin/master`, extracted to my scratchpad, nothing checked out): the card
  valid, 0 errors, 0 warnings; `card render --root docs/components --check`: 21 cards, 0 problems, 0 not current.
- 29 single changes to the new code (each limit loosened or switched off, the matching and the cut changed, the
  reader's mapping, the client's id and answer checks, the service's wiring) each make one of the new tests fail; the
  scripts are in my scratchpad (`pmvp20c/mutate.py`).
- Demo tenant: above; 86 requests in two runs, nothing else sent; the answers stayed in memory, lengths and counts
  only were written.

**Open, for the coordinator.**

- **CR-1c**: under `brokered` the route answers 401 for every read, so «Ein» brings back the short passages; under
  `enforcing` it reads open documents only. Until CR-1c the read narrows by the tenant's posture, not by the person.
- A hit more than 60,000 characters into its document keeps its snippet (3 parts from the start; the Core gives no
  character offset for a chunk). A snippet that stands twice in its document takes the first place.
- Each read is billed (`document_retrieval`): up to 8 more billed requests per question.
- The contexts go to the provider the customer chose (DEC-0084); FU-33 (Anthropic's terms) is still open.
- The demo documents are all shorter than one context; the cut around a chunk inside a long document is tested with
  fakes only.
