-- Brain Builder: enrolling and monitoring sync components (build plan step B13).
--
-- A component registered in step B7 is shown through an adapter over what it
-- reports today (transport 'adapter': the KnovasConnector's /health and
-- /sync/status, the Platform's own GitHub sync). A component enrolled here
-- speaks connector protocol v1 (transport 'v1'): it calls out to the Platform
-- only, with its own client certificate and a control token. The enrolment
-- token (one-time, 24 h) and the control token are secrets: the settings
-- registry holds them sealed, as hashes; nothing here can be shown again.
--
-- builder_component grows the state the registry shows; runs, events and the
-- plans the deletion guard holds are kept per component. Events carry codes
-- and counts only, never document names or content (GI-DATA-02/04).

ALTER TABLE builder_component DROP CONSTRAINT IF EXISTS builder_component_state_check;
ALTER TABLE builder_component ADD CONSTRAINT builder_component_state_check
    CHECK (state IN ('registered', 'pending', 'enrolled', 'revoked', 'retired'));
ALTER TABLE builder_component ADD COLUMN IF NOT EXISTS transport TEXT NOT NULL DEFAULT 'adapter'
    CHECK (transport IN ('adapter', 'v1'));
ALTER TABLE builder_component ADD COLUMN IF NOT EXISTS paused BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE builder_component ADD COLUMN IF NOT EXISTS schedule JSONB NOT NULL DEFAULT '{"mode": "manual"}'::jsonb;
ALTER TABLE builder_component ADD COLUMN IF NOT EXISTS version TEXT NOT NULL DEFAULT '';
ALTER TABLE builder_component ADD COLUMN IF NOT EXISTS capabilities JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE builder_component ADD COLUMN IF NOT EXISTS enrol_expires_at TIMESTAMPTZ;
ALTER TABLE builder_component ADD COLUMN IF NOT EXISTS enrolled_at TIMESTAMPTZ;
ALTER TABLE builder_component ADD COLUMN IF NOT EXISTS certificate_fingerprint TEXT;
ALTER TABLE builder_component ADD COLUMN IF NOT EXISTS last_heartbeat_at TIMESTAMPTZ;
ALTER TABLE builder_component ADD COLUMN IF NOT EXISTS reported_state TEXT;
ALTER TABLE builder_component ADD COLUMN IF NOT EXISTS lag_seconds INTEGER;
ALTER TABLE builder_component ADD COLUMN IF NOT EXISTS open_errors INTEGER NOT NULL DEFAULT 0;
ALTER TABLE builder_component ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS builder_component_run (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    component     UUID NOT NULL REFERENCES builder_component(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL CHECK (kind IN ('dry_run', 'apply')),
    status        TEXT NOT NULL CHECK (status IN ('running', 'ok', 'held', 'failed')),
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,
    duration_ms   INTEGER,
    added         INTEGER NOT NULL DEFAULT 0,
    changed       INTEGER NOT NULL DEFAULT 0,
    retired       INTEGER NOT NULL DEFAULT 0,
    errors        INTEGER NOT NULL DEFAULT 0,
    requested_by  UUID REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS builder_component_run_latest ON builder_component_run (component, started_at DESC);

CREATE TABLE IF NOT EXISTS builder_component_event (
    id         BIGSERIAL PRIMARY KEY,
    component  UUID NOT NULL REFERENCES builder_component(id) ON DELETE CASCADE,
    at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    code       TEXT NOT NULL CHECK (code ~ '^[a-z][a-z0-9_.]{1,60}$'),
    counts     JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS builder_component_event_latest ON builder_component_event (component, at DESC);

CREATE TABLE IF NOT EXISTS builder_held_plan (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    component    UUID NOT NULL REFERENCES builder_component(id) ON DELETE CASCADE,
    mapping      TEXT NOT NULL,
    held_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    retire       JSONB NOT NULL DEFAULT '{}'::jsonb,
    share        REAL,
    state        TEXT NOT NULL DEFAULT 'held' CHECK (state IN ('held', 'approved', 'rejected')),
    decided_by   UUID REFERENCES users(id) ON DELETE SET NULL,
    decided_at   TIMESTAMPTZ,
    reason       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS builder_held_plan_component ON builder_held_plan (component, state);

CREATE TABLE IF NOT EXISTS builder_component_command (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    component     UUID NOT NULL REFERENCES builder_component(id) ON DELETE CASCADE,
    command       TEXT NOT NULL CHECK (command IN ('run', 'pause', 'resume', 'approve_plan', 'reject_plan', 'reload_config')),
    args          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by    UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at  TIMESTAMPTZ,
    done_at       TIMESTAMPTZ,
    result        TEXT
);
CREATE INDEX IF NOT EXISTS builder_component_command_open ON builder_component_command (component, delivered_at);
