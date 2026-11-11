-- Brain Builder: the workspace model, the builder's only record of a company's
-- configuration (build plan step B1).
--
-- A workspace is one company. Its model lives in versions: at most one draft
-- the editors write, and the published versions before it, each kept whole as
-- JSON with its content hash. Every change to a draft is a row in
-- builder_change: who, where in the model, before and after.
--
-- tenant stays empty until the workspace is linked to the company's tenant
-- (the certificate is held in the settings registry); it is never taken from
-- a request field.

CREATE TABLE IF NOT EXISTS builder_workspace (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant      TEXT,
    name        TEXT NOT NULL CHECK (char_length(btrim(name)) BETWEEN 1 AND 120),
    created_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS builder_version (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace     UUID NOT NULL REFERENCES builder_workspace(id) ON DELETE CASCADE,
    number        INTEGER NOT NULL CHECK (number >= 1),
    state         TEXT NOT NULL CHECK (state IN ('draft', 'published')),
    model         JSONB NOT NULL,
    content_hash  CHAR(64) NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at  TIMESTAMPTZ,
    published_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    UNIQUE (workspace, number),
    -- A published version says when; a draft has not been published.
    CHECK ((state = 'published') = (published_at IS NOT NULL))
);

-- One draft per workspace at a time.
CREATE UNIQUE INDEX IF NOT EXISTS builder_version_one_draft
    ON builder_version (workspace) WHERE state = 'draft';

CREATE TABLE IF NOT EXISTS builder_change (
    id         BIGSERIAL PRIMARY KEY,
    workspace  UUID NOT NULL REFERENCES builder_workspace(id) ON DELETE CASCADE,
    version    UUID NOT NULL REFERENCES builder_version(id) ON DELETE CASCADE,
    actor      UUID REFERENCES users(id) ON DELETE SET NULL,
    path       TEXT NOT NULL,
    before     JSONB,
    after      JSONB,
    at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS builder_change_latest
    ON builder_change (workspace, at DESC, id DESC);
