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
   goes to the chosen provider: the context, as passages did; DEC-0086); the assistant's card.

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

(empty)
