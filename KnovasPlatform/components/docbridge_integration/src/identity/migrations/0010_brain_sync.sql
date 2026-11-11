-- "Sync von GitHub" (WP KC-0.2): one row per Probelauf ("probe") or Anwenden ("apply") of a tenant.
--
-- Counts and hashes only: the commit, who started it, when, how many files were fetched, the counts per
-- mapping, the result (a short German sentence or an exception type, never a message), the duration, the
-- plan's sha256 and a hash of the tenant and its certificate (tenant_ref). No file name, no fact value.
--
-- One sync per tenant at a time: at most one row per tenant is 'running' (the partial unique index), so
-- two workers or two admins cannot start one at once.

CREATE TABLE IF NOT EXISTS brain_sync_runs (
    id           UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant       VARCHAR(80)  NOT NULL,
    kind         VARCHAR(8)   NOT NULL CHECK (kind IN ('probe', 'apply')),
    branch       VARCHAR(200) NOT NULL DEFAULT '',
    commit_sha   CHAR(40)     NULL,
    started_by   UUID         NULL REFERENCES users(id) ON DELETE SET NULL,
    started_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ  NULL,
    status       VARCHAR(10)  NOT NULL DEFAULT 'running'
                 CHECK (status IN ('running', 'ok', 'failed', 'refused')),
    progress     VARCHAR(200) NOT NULL DEFAULT '',
    files_fetched INTEGER     NULL,
    counts       JSONB        NOT NULL DEFAULT '{}'::jsonb,
    result       VARCHAR(500) NOT NULL DEFAULT '',
    duration_ms  INTEGER      NULL,
    plan_sha256  CHAR(64)     NULL,
    tenant_ref   VARCHAR(64)  NOT NULL DEFAULT '',
    probe_id     UUID         NULL REFERENCES brain_sync_runs(id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_brain_sync_one_running ON brain_sync_runs (tenant) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS idx_brain_sync_tenant_started ON brain_sync_runs (tenant, started_at DESC);
