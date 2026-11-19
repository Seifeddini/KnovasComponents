-- Past chats of the assistant ("Knovas Assistent"): what a person asked, the
-- answer as it was shown, and the references under it.
--
-- A chat belongs to one person and is read, continued and deleted by that
-- person only (requirement W-07; contract L2-PLATFORM-ASSISTANT POST-03): the
-- store (identity/chats.py) filters every statement on owner_user_id, and
-- another person's chat answers like a missing one. Deleting a chat removes its
-- messages and their references; removing a person removes their chats. The
-- text is stored as typed and as shown, neither encrypted nor summarised, and
-- never written to a log line (POST-04).
--
-- Messages keep their order in ``ordinal`` (0, 1, 2 … per chat); a reference
-- carries the number the answer cites it by, unique within its message.
--
-- New tables only; no existing table changes (plan § 8.3).
--
-- Plan: KnowledgeBase docs/ModernDocs/plans/2026-11-19-platform-mvp.md (PMVP-21, § 5.2, § 7)

CREATE TABLE IF NOT EXISTS assistant_chats (
    id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id UUID         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title         VARCHAR(200) NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- "My chats, newest first"; it also serves the cascade from users.
CREATE INDEX IF NOT EXISTS idx_assistant_chats_owner_updated
    ON assistant_chats (owner_user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS assistant_messages (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    chat_id    UUID        NOT NULL REFERENCES assistant_chats(id) ON DELETE CASCADE,
    ordinal    INTEGER     NOT NULL CHECK (ordinal >= 0),
    role       VARCHAR(9)  NOT NULL CHECK (role IN ('user', 'assistant')),
    text       TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- "The messages of a chat in order"; one place per message.
CREATE UNIQUE INDEX IF NOT EXISTS idx_assistant_messages_chat_ordinal
    ON assistant_messages (chat_id, ordinal);

CREATE TABLE IF NOT EXISTS assistant_references (
    id         UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id UUID    NOT NULL REFERENCES assistant_messages(id) ON DELETE CASCADE,
    number     INTEGER NOT NULL CHECK (number >= 1),
    pointer    TEXT    NOT NULL CHECK (length(pointer) > 0),
    page       INTEGER NULL,
    title      TEXT    NOT NULL DEFAULT '',
    text       TEXT    NULL
);

-- The references of a message by number; one reference per number.
CREATE UNIQUE INDEX IF NOT EXISTS idx_assistant_references_message_number
    ON assistant_references (message_id, number);
