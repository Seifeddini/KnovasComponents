# Follow-ups found during the build

The coordinator records here what a verifier or builder found that is not a defect of the item itself, and which
later item or work order takes it up. An entry is closed when that item's work order names it.

| Id | Found in | What | Taken up by |
|---|---|---|---|
| FU-01 | PMVP-21 verification | Some bad input reaches the database before it is refused (`identity/chats.py` ~117-129, ~156): a NUL byte in text, title or pointer raises psycopg `DataError`; a number or page above 2^31 raises `NumericValueOutOfRange`. Rolled back, no content in the message, but a route that doesn't catch it answers 500. | PMVP-22a, PMVP-23 (routes catch it or strip NUL first) |
| FU-02 | PMVP-21 verification | A chat title over 200 characters raises `ValueError`; nothing is shortened. | PMVP-22a (shorten the first question before `create_chat`) |
| FU-03 | PMVP-21 verification | `list_chats` has no limit or paging. | PMVP-23 |
| FU-04 | PMVP-21 verification | `KnovasPlatform/docs/deployment/knovas-tenant.md` § 4.12 (rollback loses "what was done in the Builder", now also chats) and § 4.10 (restore count query, ~line 719, doesn't count chats) are out of date; `chats.py` and migration 0026 belong to no component until PMVP-01 creates the L2-PLATFORM-ASSISTANT card (`source_paths`, `boundaries.yaml`). | PMVP-01 (card); docs with PMVP-62 or the first ops item |
| FU-05 | PMVP-21 verification | The whole-suite command needs `--continue-on-collection-errors`; without it pytest stops on the Windows collection errors and runs nothing. | every later work order |
