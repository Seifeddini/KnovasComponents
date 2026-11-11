-- Brain Builder as a team tool (build plan step B11).
--
-- Four builder roles, granted like any Platform role (user_roles) for every
-- workspace of the tenant, or for one workspace only through an access grant:
--
--   builder_author     edits drafts
--   builder_reviewer   approves a draft (never one they changed)
--   builder_publisher  applies an approved draft to the tenant
--   builder_viewer     reads
--
-- builder_access holds the grants for one workspace: an outside consultant
-- invited by e-mail, with the roles granted and the day the access ends. It
-- ends by itself: every request checks ends_on against today. builder_comment
-- holds review comments on a change-set item or a canvas element (the target
-- names the element as builder_change.path does), open until resolved.
-- builder_access_log records every request of a grant's holder and every
-- write of anyone: who, when, which action and its status -- never content.

INSERT INTO roles (key, label, description, is_builtin) VALUES
    ('builder_author', 'Builder author', 'Edits drafts in the Brain Builder.', TRUE),
    ('builder_reviewer', 'Builder reviewer', 'Approves a Brain Builder draft; never one they changed.', TRUE),
    ('builder_publisher', 'Builder publisher', 'Applies an approved Brain Builder draft to the tenant.', TRUE),
    ('builder_viewer', 'Builder viewer', 'Reads Brain Builder workspaces without changing them.', TRUE)
ON CONFLICT (key) DO NOTHING;

CREATE TABLE IF NOT EXISTS builder_access (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace   UUID NOT NULL REFERENCES builder_workspace(id) ON DELETE CASCADE,
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    email       TEXT NOT NULL,
    roles       TEXT[] NOT NULL CHECK (cardinality(roles) >= 1 AND roles <@ ARRAY[
                    'builder_author', 'builder_reviewer', 'builder_publisher', 'builder_viewer']::TEXT[]),
    ends_on     DATE NOT NULL,
    invited_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    invited_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    revoked_at  TIMESTAMPTZ,
    UNIQUE (workspace, user_id)
);

CREATE INDEX IF NOT EXISTS builder_access_user ON builder_access (user_id);

CREATE TABLE IF NOT EXISTS builder_comment (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace    UUID NOT NULL REFERENCES builder_workspace(id) ON DELETE CASCADE,
    target       TEXT NOT NULL CHECK (char_length(target) BETWEEN 1 AND 300),
    body         TEXT NOT NULL CHECK (char_length(btrim(body)) BETWEEN 1 AND 4000),
    author       UUID REFERENCES users(id) ON DELETE SET NULL,
    at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    resolved_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS builder_comment_target ON builder_comment (workspace, target, at);

CREATE TABLE IF NOT EXISTS builder_access_log (
    id              BIGSERIAL PRIMARY KEY,
    workspace       UUID,
    user_id         UUID,
    email_snapshot  TEXT,
    at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    method          TEXT NOT NULL,
    action          TEXT NOT NULL,
    status          INTEGER NOT NULL,
    via             TEXT NOT NULL CHECK (via IN ('role', 'grant'))
);

CREATE INDEX IF NOT EXISTS builder_access_log_workspace ON builder_access_log (workspace, at DESC);
