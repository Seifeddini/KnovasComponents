# Follow-ups found during the build

The coordinator records here what a verifier or builder found that is not a defect of the item itself, and which
later item or work order takes it up. An entry is closed when that item's work order names it.

| Id | Found in | What | Taken up by |
|---|---|---|---|
| FU-01 | PMVP-21 verification | Some bad input reaches the database before it is refused (`identity/chats.py` ~117-129, ~156): a NUL byte in text, title or pointer raises psycopg `DataError`; a number or page above 2^31 raises `NumericValueOutOfRange`. Rolled back, no content in the message, but a route that doesn't catch it answers 500. | PMVP-22a, PMVP-23 (routes catch it or strip NUL first) |
| FU-02 | PMVP-21 verification | A chat title over 200 characters raises `ValueError`; nothing is shortened. | PMVP-22a (shorten the first question before `create_chat`) |
| FU-03 | PMVP-21 verification | `list_chats` has no limit or paging. | PMVP-23 |
| FU-04 | PMVP-21 verification | `KnovasPlatform/docs/deployment/knovas-tenant.md` § 4.12 (rollback loses "what was done in the Builder", now also chats) and § 4.10 (restore count query, ~line 719, doesn't count chats) are out of date; `chats.py` and migration 0026 belong to no component until PMVP-01 creates the L2-PLATFORM-ASSISTANT card (`source_paths`, `boundaries.yaml`). | PMVP-01 (card); the two doc sections with PMVP-62 (upgrade note, way back) |
| FU-05 | PMVP-21 verification | The whole-suite command needs `--continue-on-collection-errors`; without it pytest stops on the Windows collection errors and runs nothing. | every later work order |
| FU-06 | PMVP-20 verification | The prompt budget (14,000 bytes for an 8,192-token model) holds for prose, but the model counts one token per digit; a digit-heavy chat history can overflow, and every follow-up in that chat then answers `failed`. | PMVP-23 |
| FU-07 | PMVP-20 verification | `_fit_history` (`assistant/service.py` ~463) sends no history at all when the turns are long, instead of the newest turns that fit. | PMVP-23 |
| FU-08 | PMVP-20 verification | Search retries a timeout 3 x 120 s, then generate waits up to 120 s; nginx `proxy_read_timeout` is 180 s, so the person can get a 504 instead of the plain "failed" message (POST-06). | PMVP-24 |
| FU-09 | PMVP-20 builder + verifier | Two log lines older than this build can carry customer text (GI-DATA-04): `knovas_client.py` ~1811 logs up to 600 characters of an error body, ~3477-3485 logs raw `top_chunks` (which hold snippet text whenever the Core sends snippets). Not made worse by PMVP-20. | maintenance branch `maint/platform-logs-no-customer-text` from `main`, all of the Platform web app swept (founder, 2026-11-19: "no customer text in the logs") |
| FU-10 | PMVP-20 verification | In the demo profile (legacy API, `KnovasPlatform/docs/demo.md`) the assistant answers `failed`; with `SEARCH_USE_TEST_RESULTS` it still searches the real Core. The pilot setup (secured API) works. | PMVP-02 (the mock profile as fallback) |
| FU-11 | PMVP-20 verification | Sentence text keeps Markdown and link leftovers ("**Ergebnis:**", "(http://...)"); the page must escape it and decide what Markdown it renders. | PMVP-22a |
| FU-12 | PMVP-20 verification | Each passage uses 1 of up to 3 snippets per hit, at most 240 characters; passages may be too short to support an answer. | PMVP-03 measures it; PMVP-22b |
| FU-13 | PMVP-20 verification | The check works per sentence: text after a mid-sentence reference followed by a comma ("... [1], und der Vermieter darf ...") is shown under that reference. Stops, semicolons and line breaks after a reference are handled by rework 1. Accepted limit for now. | PMVP-03 watches for it |
| FU-14 | PMVP-20 verification | Plan § 9.1 proposes a small Alloy model for POST-02 "written with PMVP-20"; none exists. The founder decides whether it is required before approval. | closed: the founder decided on 2026-11-19 it is not needed, because the assistant gets document content only through the Brain's search, which enforces access |
