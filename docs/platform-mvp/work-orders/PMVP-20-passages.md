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

(the builder writes here)
