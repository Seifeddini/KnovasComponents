-- Brain Builder: review and publish (build plan step B5).
--
-- A draft is published to its tenant after a dry run, an approval and an
-- apply. The approval is bound to the draft's content hash: any edit after it
-- changes the hash, and the approval no longer counts. The published version
-- keeps the definitions bundle that was applied, which the Platform's views
-- read. Every dry run and apply is a run, kept for the run history.

ALTER TABLE builder_version ADD COLUMN IF NOT EXISTS bundle JSONB;
ALTER TABLE builder_version ADD COLUMN IF NOT EXISTS bundle_hash CHAR(64)
    CHECK (bundle_hash IS NULL OR bundle_hash ~ '^[0-9a-f]{64}$');

CREATE TABLE IF NOT EXISTS builder_approval (
    id            BIGSERIAL PRIMARY KEY,
    workspace     UUID NOT NULL REFERENCES builder_workspace(id) ON DELETE CASCADE,
    version       UUID NOT NULL REFERENCES builder_version(id) ON DELETE CASCADE,
    content_hash  CHAR(64) NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    reviewer      UUID REFERENCES users(id) ON DELETE SET NULL,
    at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS builder_approval_version ON builder_approval (version, content_hash);

CREATE TABLE IF NOT EXISTS builder_run (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace     UUID NOT NULL REFERENCES builder_workspace(id) ON DELETE CASCADE,
    version       UUID REFERENCES builder_version(id) ON DELETE SET NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('dry_run', 'apply')),
    actor         UUID REFERENCES users(id) ON DELETE SET NULL,
    tenant        TEXT,
    content_hash  CHAR(64) NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    bundle_hash   CHAR(64) CHECK (bundle_hash IS NULL OR bundle_hash ~ '^[0-9a-f]{64}$'),
    status        TEXT NOT NULL CHECK (status IN ('ok', 'refused', 'failed')),
    summary       JSONB NOT NULL DEFAULT '{}'::jsonb,
    at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS builder_run_latest ON builder_run (workspace, at DESC);
