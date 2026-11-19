# Work order PMVP-21 — Chat storage: tables, owner-only reads, delete

| | |
|---|---|
| Item | PMVP-21 (Jira SS-733), plan `FEAT-2026-11-19-platform-mvp` § 5.2, § 7, § 8.3, § 9 (KnowledgeBase `docs/ModernDocs/plans/2026-11-19-platform-mvp.md`) |
| Bet | `BET-2026-11-platform-mvp` (approved 2026-11-19) |
| Requirements | W-07; contract L2-PLATFORM-ASSISTANT POST-03, POST-04 |
| Class | **critical** under this repository's CLAUDE.md (migrations); proposed by the agent, approved by management before it reaches `main` |
| Branch / worktree | `feat/pmvp-21` from `feat/platform-mvp`, worktree `.worktrees/pmvp-21` |
| Frozen tests | `KnovasPlatform/components/docbridge_integration/tests/test_pmvp21_chats_acceptance.py`, SHA-256 in `docs/platform-mvp/frozen-tests.sha256` (check: `sha256sum -c docs/platform-mvp/frozen-tests.sha256`) |

## Goal

Past chats of the assistant, stored in the Platform's own database, visible to their owner only, deletable. No routes
or pages here (PMVP-23 builds the list); no assistant logic (PMVP-20).

## What to build (the frozen tests pin the interface)

- Migration `P/src/identity/migrations/0026_assistant_chats.sql`: three **new** tables, nothing else.
  - `assistant_chats` (id UUID PK, `owner_user_id` → `users(id) ON DELETE CASCADE`, title, created_at, updated_at)
  - `assistant_messages` (id, `chat_id` → chats `ON DELETE CASCADE`, role `CHECK IN ('user','assistant')`, text,
    created_at, an order column)
  - `assistant_references` (id, `message_id` → messages `ON DELETE CASCADE`, number, pointer, page, title, optional
    passage text)
  - Header comment naming the plan and item, in the style of `0005_saved_views.sql`. Indexes for "list my chats,
    newest first" and "messages of a chat in order".
- `P/src/identity/chats.py`: `ChatStore(conn)` with `create_chat(*, owner, title) -> UUID`,
  `add_message(chat_id, *, owner, role, text, references=()) -> UUID`, `list_chats(*, owner)`,
  `get_chat(chat_id, *, owner)`, `delete_chat(chat_id, *, owner)`, and `ChatNotFound`. Rows mapped like the rest of
  `identity/` (tuples by position). `add_message` bumps the chat's `updated_at`.

## Rules

1. **Owner only, in SQL.** Every read, write and delete filters on `owner_user_id` in the same statement; a chat of
   another person raises `ChatNotFound` with the same message as a missing chat, and the message holds no id.
2. **Additive only** (§ 8.3): no change to any existing table, function or migration. The migration runs once, in
   the runner's transaction; `CREATE TABLE IF NOT EXISTS` like the others.
3. **No text in logs** (POST-04): ids and counts only.
4. Roles other than `user` and `assistant` raise `ValueError` before touching the database.
5. Store the text a person typed and the answer as shown; nothing is encrypted or summarised here.

## Files you may change

- new: `P/src/identity/migrations/0026_assistant_chats.sql`, `P/src/identity/chats.py`,
  `P/tests/test_identity_chats_*.py` (your own tests)
- the Platform's database documentation, if one lists the tables (find it; add the three tables)
- `docs/platform-mvp/work-orders/PMVP-21.md`: only the "Builder's notes" section at the end

## Files you must not change

- the frozen test file above; `P/tests/conftest.py`; every existing migration; `P/src/web_interface/app.py`;
  anything under `P/src/assistant/` (PMVP-20)

If the work needs one of these, stop and write the reason under "Builder's notes" instead of changing it.

## Done when

1. `cd P && py -3 -m pytest tests/test_pmvp21_chats_acceptance.py -q` is green (not skipped: the test database at
   `PLATFORM_DB_TEST_DSN`, port 55433, is running), the file unchanged (SHA-256).
2. `PLATFORM_DB_REQUIRED=true py -3 -m pytest -q` for the whole Platform suite is green, or every failure is shown to
   fail on the base commit too (`tests/test_identity_migrations.py` above all).
3. Commits on `feat/pmvp-21` name the item (`PMVP-21: …`) and end with the co-author line; the branch is pushed.
   No pull request: the coordinator integrates after a verifier session passes it.

## Builder's notes

(the builder writes here: what was decided, what is open, what the verifier should look at)
