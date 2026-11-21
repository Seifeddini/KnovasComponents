# Work order PMVP-20, passages: give the model passages long enough to answer from

| | |
|---|---|
| Item | PMVP-20 (Jira SS-732); follow-up FU-12 ("each passage uses 1 of up to 3 snippets per hit, at most 240 characters") |
| Found | 2026-11-21, the coordinator's read-only searches on the **demo tenant** (`817f2330-…`, production Core) through the Platform's own client: with `keeping_snippets()` the five passages of a question were 2, 10, 48, 139 and 172 characters long. A model cannot answer from 2 characters; the quote check needs three words |
| Class | sensitive (Siran approves): what text of a document reaches the model |
| Branch / worktree | `feat/pmvp-20-passages` from `origin/feat/platform-mvp`, worktree `.worktrees/pmvp-20p` |
| Not here | `src/assistant/check.py`, `service.py` (the hardening works there), `core.py`'s factory and the provider module (PMVP-25) |

## What to build

1. **Find where the text is.** Look at what `POST /secured/query` returns for a hit (`top_chunks`, their fields,
   snippets, chunk text, page and sentence numbers): in the Core's code (KnowledgeBase `origin/master`,
   `knovas-software/app/src/api/secure_api.py` and the search services; read with `git show`, check nothing out),
   and in **read-only** searches on the demo tenant (below). Write the shape down in your notes (field names and
   lengths only).
2. **The passage of a hit** (`_snippet_with_location` and what calls it in `src/knovas_client.py`, inside
   `keeping_snippets()` only): the longest text the response carries **for one chunk** (its full text if the Core
   sends it, else that chunk's snippets joined in order with " … "), cut at a word boundary to a limit
   (`ASSISTANT_PASSAGE_CHARS`, default 1,200). Page and sentence stay those of that chunk (rework 1, item 5: never
   from another chunk). Choose the chunk with the best score that has text. A hit whose text is shorter than three
   words is not a passage.
3. **If the Core sends no text longer than its 240-character snippets**, do not invent a way around it (no second read
   of the document from the Platform: POST-02, "the assistant has no second way to read documents"). Stop, and write
   what the Core would have to send (field, length) as a request for the Core in your notes.
4. **The prompt budget** (`service.compose`) already cuts passages to fit the Core's or the provider's limits; check
   that 8 passages of 1,200 characters fit, and say what is cut first.
5. **Tests** in `tests/test_assistant_knovas_client.py`: chunk text preferred over snippets; snippets of one chunk
   joined; never text from two chunks; the page from the same chunk; the limit cut at a word; too short → not a
   passage; outside `keeping_snippets()` nothing changes for the search page.

## The demo tenant (read only)

Certificate, key and CA: `E:\Knovas\.secrets\demo-tenant\client-cert.pem`, `client-key.pem`, `ca-root.pem` (the key
is readable by this Windows account only; never copy, print or log it). Core: `https://api.knovas.ch:8443`, tenant
`817f2330-3d31-4b56-b363-cc308521bae0`. A client config for the Platform's own `KnovasAPIClient` is in
`C:\Users\siran\AppData\Local\Temp\claude\1838582cff9d51e1\af9716ae-0421-42ac-9fac-6acb192cc6e6\scratchpad\demo-tenant\config.yaml`
(`ConfigLoader(path)`). **Only searches** (`POST /secured/query`, at most one request every 2 seconds; the Core
answers 429 above its rate limit). Never any other POST, PUT, PATCH or DELETE; never the Knovas GmbH tenant. The demo
tenant holds invented law-firm documents; quote at most a few words of them in your notes.

## Done when

1. `tests/test_assistant_*.py` and `tests/test_pmvp20_statements_acceptance.py` green; the frozen check all OK.
   **Do not run the whole suite.**
2. In your notes: the response shape, and for the same two questions the coordinator used ("Wie lange ist die
   Kuendigungsfrist im Mietvertrag?", "Wer vertritt die Gegenpartei?") the passage lengths before and after.
3. Commits `PMVP-20: passages …` ending with the co-author line; `feat/pmvp-20-passages` pushed. No pull request.

## Builder's notes

Builder session, 2026-11-21, branch `feat/pmvp-20-passages` from `origin/feat/platform-mvp` @ `736fe9a`; commit
`cd8a938` (code and tests), then these notes. Nothing here is verified; that is the verifier's.

**Stopped on item 3.** The Core sends no text longer than its snippets, and on the demo tenant not even the snippets
are cut: every snippet is its chunk's whole text, and the chunks are one to five sentences. Item 2 is built and drops
the passages that cannot carry a quote, but a passage stays as long as the Core's chunk (31 to 172 characters for the
two questions). What the Core would have to send is under 3 below. No second read anywhere (POST-02).

**1. What `POST /secured/query` returns.** Read in KnowledgeBase `origin/master` @ `be344ad` (`query_pipeline.py`,
`query_two_stage.py`: the rows ~1655-1730, `attach_chunk_snippets` ~224, `cut_snippet_window` ~184) and in two
read-only searches on the demo tenant (`limit` 10, one request per question, 2.5 s apart, 2026-11-21).

- Top level: `status`, `message`, `query_session_id`, `pointers`, `results`, `result_count`, `no_strong_matches`,
  `relevance_gate_applied`, `truncated`, `meta` (`embed_latency_ms`, `stage1_latency_ms`, `stage2_latency_ms`,
  `degraded_to_bm25`, `routing`, `document_finder`, `snippets` = `requested`, `attached`, `missing`,
  `skipped_auto_summary`).
- A row: `pointer`, `document_uuid`, `acl_folder_id`, `final_score`, `stage2_score`, `document_score`, `score_sum`,
  `stage1_rank_score`, `stage1_max_dense`, `stage1_mean_dense`, `cosine_similarity`, `score_mode`, `cosine_distance`,
  `page_number`, `sentence_number`, `sentence_number_end` (the first chunk's), six `maxsim_*` numbers,
  `ingested_summary` (`present`, `text`), `top_chunks`, `snippet` (a copy of the first chunk's snippet).
- A chunk (`top_chunks[]`, at most 7 per row): `uuid`, `chunk_uuid`, `cosine_similarity`, `page_number`,
  `sentence_number`, `sentence_number_end`, `rerank_score`, and `snippet` on the first three only
  (`QUERY_CHUNK_SNIPPET_CHUNKS_PER_RESULT`). Sorted by `rerank_score`, high first; chunks stage 2 did not score come
  after, in stage-1 order. No chunk carries `original_text` or any other text.
- The snippet (`cut_snippet_window`): the start of the chunk's `original_text`, white space collapsed, cut at a word
  to `QUERY_CHUNK_SNIPPET_MAX_CHARS` (240) with "…" appended (table chunks: whole rows); never the BM25 `text`, never
  an auto summary (GI-QUERY-04). One snippet per chunk.
- Measured: 16 rows, 36 chunks (1 to 4 per row; 28 of one sentence, 8 of two to five), 34 snippets of **2 to 172
  characters** (1 to 22 words), 10 of them under three words (a list number, a date, a file-number line, a firm
  name). **None ends in "…": no snippet was cut, each is its chunk's whole text.** `ingested_summary.text` is empty
  on all 16 rows (and an auto summary is not the document's words). 14 of 16 rows have no page (.docx, .txt, .msg).

**2. The passage** (`_snippet_with_location`, called only where `keeping_snippets()` is on).

- A chunk's text: its `original_text` if one of its entries carries it, else its distinct snippets in the order
  sent, joined with " … ". Entries with the same `chunk_uuid` (else `uuid`) are one chunk; an entry without an id is
  a chunk of its own. Never `text` or `content`.
- The chunk: the best by `rerank_score`, then the chunks without one in the order sent (the Core's own citation
  order; not the stage-1 cosine, which the reranker overrules on purpose); the first whose cut text has at least
  three words. Its page and sentence become `snippet_page_number` / `snippet_sentence_number` (None when it has
  none, never another chunk's).
- The cut: at the last white space within the limit minus one, then "…" (the check reads it as a closing mark);
  inside a word only when the first word alone is longer than the limit.
- A word is counted as the check counts it: a run between white space with a letter or a digit. The client keeps
  its own `MIN_PASSAGE_WORDS = 3`, held equal to `check.MIN_QUOTE_WORDS` by a test: the client does not import the
  check (two other builders work in `src/assistant/`).
- The row's own `snippet` stays the last resort with an unknown location, as before, now also with three words.
- `ASSISTANT_PASSAGE_CHARS`: read per search, only inside the block; a whole number of at least 100
  (`MIN_PASSAGE_CHARS`), anything else gives 1,200 and one warning naming the setting (its value cut to 20
  characters; never text).

*Decided beyond the order* (the verifier should judge): (a) "the chunk with the best score that has text" is read as
"whose passage has three words", so a better ranked two-word chunk ("Aktenzeichen: ...") is passed over for the next
chunk instead of dropping the hit; (b) joining needs several entries with one `chunk_uuid`; the Core sends one
snippet per chunk, so today nothing is joined; (c) the floor of 100 characters for the setting, so a typo cannot
starve every passage.

**Before and after** (the two answers above, each run through the old and the new client's own search path with a
fake session, then `passages_from_rows`; characters per passage in search order):

| Question | Before | After |
|---|---|---|
| Wie lange ist die Kuendigungsfrist im Mietvertrag? (10 hits) | 10 passages: 139, 172, 48, **2**, **10**, 42, **22**, 38, 61, 31 | 8 passages: 139, 172, 48, 42, **39**, 38, 61, 31 |
| Wer vertritt die Gegenpartei? (6 hits) | 6 passages: 57, 57, 61, 61, 95, 95 | the same |

The two hits with only a list number or a date give no passage now; one hit's two-word line is replaced by its next
chunk (39 characters, 5 words). The longest passage is 172 characters before and after.

**3. Request for the Core** (stopped here; nothing built for it).

- Raising `QUERY_CHUNK_SNIPPET_MAX_CHARS` changes nothing on this tenant: no snippet was cut, the chunks are short.
- What the assistant needs: on each served `top_chunks` entry that gets a snippet, a field such as **`context`**:
  the `original_text` of the neighbouring sentence chunks of the same document, in document order around the chunk
  (sentences `sentence_number - k` to `sentence_number_end + k`), at most **1,200 characters**
  (`QUERY_CHUNK_CONTEXT_MAX_CHARS`), cut at words, the chunk's own text always inside; with
  **`context_sentence_number`**, **`context_sentence_number_end`** and **`context_page_number`** (the page of its
  first sentence, null without pages) so a reference opens the right place. The snippet's rules hold:
  `original_text` only, no auto summary, only documents the served row already carries (access is per document,
  so the neighbours give no new access; GI-QUERY-04's "only chunk ids the served rows carry" would need the Core's
  own amendment), counts in `meta.snippets`.
- Or larger chunks at ingest (a least chunk length); that means re-ingesting every tenant.
- The Platform's part is then small: read that field as the chunk's text, before `original_text` and the snippet,
  with its own location.
- Before the Core ships it: FU-09's INFO line (`knovas_client.py` ~3596, "no page/sentence in first hits") logs raw
  `top_chunks` and would log up to 1,200 characters per chunk. FU-09 should land first.

**4. The prompt budget** (`service.compose`, read only; 1,200 characters of German are about 1,254 bytes):

- 8 passages of 1,200 characters, no history: 12,662 of the 14,000 bytes, nothing cut.
- 10 passages (the search asks for 10): each cut to about 1,090 characters.
- With history the passages get 60 % of what is left after the instruction and the question (`PASSAGE_SHARE`):
  8 passages about 818 characters each, 10 about 654; then history turns are dropped, oldest first.
- What is cut first: `fit_texts` keeps short passages whole and cuts the longest to equal shares, at a character
  (not a word) with "…". The check still looks for a quote in the whole passage, so a quote from a part the model
  did not see would pass (rework 2, decision 4).
- Tokens: the budget is in bytes, so 14,000 bytes (about 5,000 to 6,000 tokens) plus 1,536 for the answer stays
  within 8,192. Today's passages use about 1 KB of it; passages of 1,200 characters would use all of it, so FU-06's
  margin starts to matter.

**Tests** (`tests/test_assistant_knovas_client.py`, 10 new): full text before snippets; snippets of one chunk joined
in order, a repeat dropped; never two chunks' text (also entries without an id); the best ranked chunk with three
words, by `rerank_score` and not by list order or cosine, unscored after scored; the row's page from the passage's
chunk; under three words, no passage; the cut at a word (also at a line break, a word longer than the limit, a
1,200 cut of a long chunk); `ASSISTANT_PASSAGE_CHARS` (300; four wrong values give the default and a warning; no
text in a log line); outside the block the setting is not read and the rows are as before; `MIN_PASSAGE_WORDS`
equals `MIN_QUOTE_WORDS`. Two old tests used two-word texts and now use three words.

**Evidence** (from `P`, Windows, Python 3.13.5):

- `py -3 -m pytest tests/test_assistant_*.py tests/test_pmvp20_statements_acceptance.py -q -p no:cacheprovider` ->
  449 passed (check 265, core 13, knovas_client 49, was 39, service 34, frozen file 88). The whole suite was not run.
- The frozen check prints OK for all five files. `py -3 scripts/check_ascii_py.py 736fe9a..HEAD` -> 0 lines.
- Ten single changes to the passage code (three-word rule off, `rerank_score` ignored, snippet before full text,
  cut inside a word, two chunks joined, page of another chunk, no limit, setting ignored, repeat kept, row fallback
  without the rule) each make an own test fail.
- Demo tenant: two `POST /secured/query` and nothing else, through `KnovasAPIClient` with the order's config file;
  the answers stayed in my scratchpad for the counts above, never in a log or the repository.
