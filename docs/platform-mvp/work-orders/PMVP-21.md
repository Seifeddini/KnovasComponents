# Work order PMVP-21 — Chat storage: tables, owner-only reads, delete

| | |
|---|---|
| Item | PMVP-21 (Jira SS-733), plan `FEAT-2026-11-19-platform-mvp` § 5.2, § 7, § 8.3, § 9 (KnowledgeBase `docs/ModernDocs/plans/2026-11-19-platform-mvp.md`) |
| Bet | `BET-2026-11-platform-mvp` (approved 2026-11-19) |
| Requirements | W-07; contract L2-PLATFORM-ASSISTANT POST-03, POST-04 |
| Class | **critical** under this repository's CLAUDE.md (migrations); proposed by the agent, approved by management before it reaches `main` |
| Branch / worktree | `feat/pmvp-21` from `feat/platform-mvp`, worktree `.worktrees/pmvp-21` |
| Frozen tests | `KnovasPlatform/components/docbridge_integration/tests/test_pmvp21_chats_acceptance.py`, blob id in `docs/platform-mvp/frozen-tests.txt` (the check command is in that file) |

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
   `PLATFORM_DB_TEST_DSN`, port 55433, is running), the file unchanged (its blob id in `docs/platform-mvp/frozen-tests.txt`).
2. `PLATFORM_DB_REQUIRED=true py -3 -m pytest -q` for the whole Platform suite is green, or every failure is shown to
   fail on the base commit too (`tests/test_identity_migrations.py` above all).
3. Commits on `feat/pmvp-21` name the item (`PMVP-21: …`) and end with the co-author line; the branch is pushed.
   No pull request: the coordinator integrates after a verifier session passes it.

## Builder's notes

(the builder writes here: what was decided, what is open, what the verifier should look at)

**Built.** `P/src/identity/migrations/0026_assistant_chats.sql` (three `CREATE TABLE IF NOT EXISTS`, three
`CREATE [UNIQUE] INDEX IF NOT EXISTS`, nothing else), `P/src/identity/chats.py`, `P/tests/test_identity_chats_store.py`
(52 tests of my own). No existing file changed.

**Decisions beyond the work order** (all two-way, in new code only):

- Order column `assistant_messages.ordinal` (0, 1, 2 … per chat), unique per chat; that unique index is the
  "messages of a chat in order" index. "My chats, newest first" is `(owner_user_id, updated_at DESC)`.
- `add_message` runs in `conn.transaction()`: first `UPDATE assistant_chats SET updated_at = now() … WHERE id AND
  owner_user_id` (holds the chat's row; no row → `ChatNotFound`, nothing changed), then the message with
  `max(ordinal) + 1`, then its references. Two writers on one chat queue instead of colliding (tested with two
  connections, 15 messages each). Every one of these statements filters on `owner_user_id` itself (rule 1).
- `get_chat` reads the chat, then its messages, then their references: three statements, each owner-filtered.
- Returned objects are frozen dataclasses: `Chat(id, title, created_at, updated_at, messages)`,
  `Message(id, role, text, created_at, references)`, `ChatSummary(id, title, created_at, updated_at)` from
  `list_chats` (ordered `updated_at DESC, created_at DESC, id`). A reference is a dict with always the five keys
  `number, pointer, page, title, text` (`title` `""` and `text` `None` when not given); keys beyond these are ignored.
- References come back **by number**; a number is unique within its message (unique index) and starts at 1; `pointer`
  is non-empty text, `page` an int or None. Violations raise `ValueError` before the database.
- Title: one line (white space collapsed, ends trimmed), may be empty (stored `''`), at most 200 characters
  (`VARCHAR(200)`; longer → `ValueError`, never cut). Message text has no limit and is stored exactly as given.
- A malformed chat id (not a UUID) raises the same `ChatNotFound` ("Chat nicht gefunden.") as a missing or foreign
  one; a malformed owner raises `ValueError` (the owner comes from the session, so that is a programming error).
- Logs: `delete_chat` logs at INFO with the chat and user ids; create/add at DEBUG with ids and the reference count.
  No title, text, pointer or passage reaches a log line or an exception message (tested at DEBUG).

**Not found.** No Platform database document lists the tables (searched `KnovasPlatform/docs`, the component
README, `docs/`); `KnovasPlatform/docs/deployment/knovas-tenant.md` § 4.10 describes the Builder's store in prose
only, so I changed no documentation. No component card for L2-PLATFORM-ASSISTANT exists yet (PMVP-01 registers it).

**For the verifier.**

- Concurrent pytest runs on **one** database break each other: `CREATE EXTENSION citext` lands in the first test's
  schema, and a run killed mid-test leaves it in an abandoned schema; every later run then fails with `type "citext"
  does not exist`. I hit this on `knovas_platform_test` (extensions in leftover schema `t6592805a7d14`, no session
  connected) and dropped that one abandoned schema to restore the default DSN; 963 other leftover `t…` schemas remain.
  Run in a database of your own (`PLATFORM_DB_TEST_DSN=…/knovas_platform_test_<name>`) when another session tests.
- Whole suite, `PLATFORM_DB_REQUIRED=true`, own database each: branch 106 failed, 6241 passed, 43 skipped, 8 errors;
  base `87afe42` 106 failed, 6176 passed, 43 skipped, 9 errors. The failing and erroring test ids are the same set
  on both except the base's collection error of `test_pmvp21_chats_acceptance.py` (gone on the branch). The rest are
  Windows-environment failures (e.g. `socketserver.UnixStreamServer`) and PMVP-20's not-yet-built acceptance file.
  `tests/test_identity_migrations.py` passes (16).
