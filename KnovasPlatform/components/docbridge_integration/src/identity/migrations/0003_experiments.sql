-- Experiments module (Experimente): experiments of any domain, their variants,
-- runs, measurements, notes, evaluations and decisions.
--
-- Everything lives in platform-db because the module needs what platform-db
-- already offers: per-user identity, roles, the audit log, transactions and
-- one store every gunicorn worker shares. The tables exist whether or not
-- EXPERIMENTS_ENABLED is on; empty tables cost nothing, and switching the
-- module on later must not need a migration step of its own.
--
-- Design and plan:
--   docs/superpowers/specs/2026-09-28-experiment-platform-design.md
--   docs/superpowers/plans/2026-09-28-experiments-module.md

-- ── roles ─────────────────────────────────────────────────────────────────

-- The module is invisible to everyone without one of these roles (or admin),
-- which is what keeps the standard search view uncluttered.
INSERT INTO roles (key, label, description, is_builtin) VALUES
    ('experimenter', 'Experimente',
     'Sees the Experiments module; creates experiments, records measurements and decisions.', TRUE),
    ('experiments_manager', 'Experimente verwalten',
     'Maintains domains, experiment types, metrics and evaluators; may delete experiments.', TRUE)
ON CONFLICT (key) DO NOTHING;

-- ── configuration: domains, types, metrics, evaluators ───────────────────

CREATE TABLE IF NOT EXISTS exp_domains (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key         TEXT NOT NULL UNIQUE CHECK (key ~ '^[a-z][a-z0-9-]{1,31}$'),
    name        TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
    description TEXT NOT NULL DEFAULT '',
    color       TEXT NOT NULL DEFAULT '#5A6B80' CHECK (color ~ '^#[0-9A-Fa-f]{6}$'),
    -- Experiment keys are <id_prefix>-<n>, e.g. MKT-58.
    id_prefix   TEXT NOT NULL UNIQUE CHECK (id_prefix ~ '^[A-Z][A-Z0-9]{1,7}$'),
    next_seq    INTEGER NOT NULL DEFAULT 1 CHECK (next_seq >= 1),
    pack        TEXT,
    archived_at TIMESTAMPTZ,
    created_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Experiment types are versioned documents. exp_types names the type; each
-- edit appends a row to exp_type_versions, and an experiment pins the version
-- it was created with, so changing a type never rewrites a running experiment.
-- domain_id NULL means the type is offered in every domain.
CREATE TABLE IF NOT EXISTS exp_types (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain_id       UUID REFERENCES exp_domains(id) ON DELETE CASCADE,
    key             TEXT NOT NULL CHECK (key ~ '^[a-z][a-z0-9_-]{1,47}$'),
    name            TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
    description     TEXT NOT NULL DEFAULT '',
    current_version INTEGER NOT NULL DEFAULT 1 CHECK (current_version >= 1),
    archived_at     TIMESTAMPTZ,
    created_by      UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_exp_types_key
    ON exp_types ((COALESCE(domain_id::text, '')), key);

CREATE TABLE IF NOT EXISTS exp_type_versions (
    type_id    UUID NOT NULL REFERENCES exp_types(id) ON DELETE CASCADE,
    version    INTEGER NOT NULL CHECK (version >= 1),
    definition JSONB NOT NULL,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (type_id, version)
);

-- A metric is defined once and reused by every experiment that measures it.
-- kind decides how a measurement row is read (see the plan, "Measurement
-- rows"); it cannot change once measurements exist.
CREATE TABLE IF NOT EXISTS exp_metrics (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain_id   UUID REFERENCES exp_domains(id) ON DELETE CASCADE,
    key         TEXT NOT NULL CHECK (key ~ '^[a-z][a-z0-9_]{1,47}$'),
    name        TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
    kind        TEXT NOT NULL CHECK (kind IN (
                    'proportion', 'mean', 'count', 'duration', 'currency',
                    'ratio', 'ordinal', 'categorical')),
    unit        TEXT NOT NULL DEFAULT '',
    direction   TEXT NOT NULL DEFAULT 'higher'
                CHECK (direction IN ('higher', 'lower', 'none')),
    description TEXT NOT NULL DEFAULT '',
    definition  JSONB NOT NULL DEFAULT '{}'::jsonb,
    version     INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    archived_at TIMESTAMPTZ,
    created_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_exp_metrics_key
    ON exp_metrics ((COALESCE(domain_id::text, '')), key);

-- Evaluators: 'builtin' ones are Python functions inside the Platform (their
-- code column names the function); 'python' and 'julia' ones are user code
-- that only ever runs in the separate experiments-runner sandbox.
CREATE TABLE IF NOT EXISTS exp_evaluators (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key             TEXT NOT NULL UNIQUE CHECK (key ~ '^[a-z][a-z0-9_.-]{1,63}$'),
    name            TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
    language        TEXT NOT NULL CHECK (language IN ('builtin', 'python', 'julia')),
    current_version INTEGER NOT NULL DEFAULT 1 CHECK (current_version >= 1),
    archived_at     TIMESTAMPTZ,
    created_by      UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- builtin.* names the trusted in-process functions; user code may not take them.
    CHECK (language = 'builtin' OR key NOT LIKE 'builtin.%')
);

CREATE TABLE IF NOT EXISTS exp_evaluator_versions (
    evaluator_id  UUID NOT NULL REFERENCES exp_evaluators(id) ON DELETE CASCADE,
    version       INTEGER NOT NULL CHECK (version >= 1),
    code          TEXT NOT NULL DEFAULT '' CHECK (char_length(code) <= 200000),
    description   TEXT NOT NULL DEFAULT '',
    input_kinds   TEXT[] NOT NULL DEFAULT '{}',
    params_schema JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by    UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (evaluator_id, version)
);

-- ── experiments ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS exp_experiments (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key          TEXT NOT NULL UNIQUE,
    domain_id    UUID NOT NULL REFERENCES exp_domains(id) ON DELETE RESTRICT,
    type_id      UUID NOT NULL REFERENCES exp_types(id) ON DELETE RESTRICT,
    type_version INTEGER NOT NULL,
    title        TEXT NOT NULL CHECK (char_length(title) BETWEEN 1 AND 300),
    hypothesis   TEXT NOT NULL DEFAULT '' CHECK (char_length(hypothesis) <= 20000),
    description  TEXT NOT NULL DEFAULT '' CHECK (char_length(description) <= 50000),
    status       TEXT NOT NULL,
    fields       JSONB NOT NULL DEFAULT '{}'::jsonb,
    tags         TEXT[] NOT NULL DEFAULT '{}',
    owner_id     UUID REFERENCES users(id) ON DELETE SET NULL,
    archived     BOOLEAN NOT NULL DEFAULT FALSE,
    started_at   TIMESTAMPTZ,
    ended_at     TIMESTAMPTZ,
    decided_at   TIMESTAMPTZ,
    -- Optimistic concurrency: every update names the row_version it read.
    row_version  INTEGER NOT NULL DEFAULT 1,
    -- Where the Knovas copy stands: pending (queued), indexed, error, off
    -- (indexing disabled for this deployment).
    index_state  TEXT NOT NULL DEFAULT 'pending'
                 CHECK (index_state IN ('pending', 'indexed', 'error', 'off')),
    indexed_at   TIMESTAMPTZ,
    index_error  TEXT,
    created_by   UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (type_id, type_version) REFERENCES exp_type_versions (type_id, version)
);

CREATE INDEX IF NOT EXISTS idx_exp_experiments_domain_status
    ON exp_experiments (domain_id, status);
CREATE INDEX IF NOT EXISTS idx_exp_experiments_updated
    ON exp_experiments (updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_exp_experiments_index_state
    ON exp_experiments (index_state) WHERE index_state <> 'indexed';

CREATE TABLE IF NOT EXISTS exp_variants (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id UUID NOT NULL REFERENCES exp_experiments(id) ON DELETE CASCADE,
    key           TEXT NOT NULL CHECK (key ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$'),
    name          TEXT NOT NULL DEFAULT '' CHECK (char_length(name) <= 120),
    description   TEXT NOT NULL DEFAULT '' CHECK (char_length(description) <= 5000),
    is_control    BOOLEAN NOT NULL DEFAULT FALSE,
    allocation    DOUBLE PRECISION CHECK (allocation IS NULL OR (allocation >= 0 AND allocation <= 1)),
    config        JSONB NOT NULL DEFAULT '{}'::jsonb,
    position      INTEGER NOT NULL DEFAULT 0,
    UNIQUE (experiment_id, key),
    -- Target of the (experiment_id, variant_id) foreign keys below, so a row can
    -- never point at another experiment's variant.
    UNIQUE (experiment_id, id)
);

-- At most one control per experiment.
CREATE UNIQUE INDEX IF NOT EXISTS idx_exp_variants_one_control
    ON exp_variants (experiment_id) WHERE is_control;

CREATE TABLE IF NOT EXISTS exp_experiment_metrics (
    experiment_id   UUID NOT NULL REFERENCES exp_experiments(id) ON DELETE CASCADE,
    metric_id       UUID NOT NULL REFERENCES exp_metrics(id) ON DELETE RESTRICT,
    role            TEXT NOT NULL CHECK (role IN ('primary', 'secondary', 'guardrail')),
    guardrail_op    TEXT CHECK (guardrail_op IN ('max', 'min')),
    guardrail_value DOUBLE PRECISION
                    CHECK (guardrail_value IS NULL OR guardrail_value NOT IN
                           ('NaN'::float8, 'Infinity'::float8, '-Infinity'::float8)),
    position        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (experiment_id, metric_id),
    CHECK (role <> 'guardrail' OR (guardrail_op IS NOT NULL AND guardrail_value IS NOT NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_exp_experiment_metrics_one_primary
    ON exp_experiment_metrics (experiment_id) WHERE role = 'primary';

CREATE TABLE IF NOT EXISTS exp_runs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id UUID NOT NULL REFERENCES exp_experiments(id) ON DELETE CASCADE,
    variant_id    UUID,
    name          TEXT NOT NULL DEFAULT '' CHECK (char_length(name) <= 200),
    status        TEXT NOT NULL DEFAULT 'finished'
                  CHECK (status IN ('running', 'finished', 'failed', 'cancelled')),
    params        JSONB NOT NULL DEFAULT '{}'::jsonb,
    environment   JSONB NOT NULL DEFAULT '{}'::jsonb,
    commit_ref    TEXT NOT NULL DEFAULT '' CHECK (char_length(commit_ref) <= 200),
    source        TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'api', 'csv')),
    started_at    TIMESTAMPTZ,
    ended_at      TIMESTAMPTZ,
    created_by    UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (experiment_id, id),
    FOREIGN KEY (experiment_id, variant_id) REFERENCES exp_variants (experiment_id, id)
        ON DELETE SET NULL (variant_id)
);

CREATE INDEX IF NOT EXISTS idx_exp_runs_experiment
    ON exp_runs (experiment_id, created_at DESC);

-- One row per insert operation (a form entry, a CSV import, an API call, a
-- run). Undoing an import deletes its batch and, through the cascade, its
-- measurements; counts for the UI come from here instead of scanning
-- exp_measurements.
CREATE TABLE IF NOT EXISTS exp_batches (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id UUID NOT NULL REFERENCES exp_experiments(id) ON DELETE CASCADE,
    run_id        UUID,
    source        TEXT NOT NULL CHECK (source IN ('manual', 'api', 'csv')),
    rows          INTEGER NOT NULL CHECK (rows >= 0),
    metric_keys   TEXT[] NOT NULL DEFAULT '{}',
    filename      TEXT CHECK (filename IS NULL OR char_length(filename) <= 255),
    created_by    UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (experiment_id, run_id) REFERENCES exp_runs (experiment_id, id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_exp_batches_experiment
    ON exp_batches (experiment_id, created_at DESC, id DESC);

-- Measurements are stored as sufficient statistics so that one row can be a
-- single observation or a pre-aggregated block (a day of an ad campaign, a CI
-- run). How value/count/denominator/sum_sq are read depends on the metric's
-- kind; the plan's "Measurement rows" section is the contract. Insert-only;
-- an import is undone by deleting its batch.
--
-- variant_id / run_id reference (experiment_id, id), so a measurement can
-- only name a variant or run of its own experiment. Deleting a variant that
-- still has measurements fails (NO ACTION): removing data is a batch delete,
-- never a side effect of editing the variant list.
CREATE TABLE IF NOT EXISTS exp_measurements (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    experiment_id UUID NOT NULL REFERENCES exp_experiments(id) ON DELETE CASCADE,
    metric_id     UUID NOT NULL REFERENCES exp_metrics(id) ON DELETE RESTRICT,
    variant_id    UUID,
    run_id        UUID,
    batch_id      UUID NOT NULL REFERENCES exp_batches(id) ON DELETE CASCADE,
    observed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    value         DOUBLE PRECISION NOT NULL
                  CHECK (value NOT IN ('NaN'::float8, 'Infinity'::float8, '-Infinity'::float8)),
    count         BIGINT NOT NULL DEFAULT 1 CHECK (count >= 1),
    denominator   DOUBLE PRECISION
                  CHECK (denominator IS NULL OR (denominator >= 0 AND denominator NOT IN
                         ('NaN'::float8, 'Infinity'::float8))),
    sum_sq        DOUBLE PRECISION
                  CHECK (sum_sq IS NULL OR (sum_sq >= 0 AND sum_sq NOT IN
                         ('NaN'::float8, 'Infinity'::float8))),
    dims          JSONB NOT NULL DEFAULT '{}'::jsonb,
    source        TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'api', 'csv')),
    created_by    UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (experiment_id, variant_id) REFERENCES exp_variants (experiment_id, id),
    FOREIGN KEY (experiment_id, run_id) REFERENCES exp_runs (experiment_id, id)
        ON DELETE CASCADE
);

-- Covering index: aggregates per (experiment, metric) are index-only scans on
-- this insert-only table, and evaluator rows come back in id order.
CREATE INDEX IF NOT EXISTS idx_exp_measurements_lookup
    ON exp_measurements (experiment_id, metric_id, id)
    INCLUDE (variant_id, run_id, observed_at, value, count, denominator, sum_sq);
CREATE INDEX IF NOT EXISTS idx_exp_measurements_batch
    ON exp_measurements (batch_id);
CREATE INDEX IF NOT EXISTS idx_exp_measurements_run
    ON exp_measurements (run_id) WHERE run_id IS NOT NULL;
-- For the foreign-key checks when a variant or metric is deleted.
CREATE INDEX IF NOT EXISTS idx_exp_measurements_variant
    ON exp_measurements (variant_id) WHERE variant_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_exp_measurements_metric
    ON exp_measurements (metric_id);

CREATE TABLE IF NOT EXISTS exp_notes (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id UUID NOT NULL REFERENCES exp_experiments(id) ON DELETE CASCADE,
    run_id        UUID,
    variant_id    UUID,
    -- 'status' holds the reason given when the experiment changed state, so
    -- "why was this stopped?" is searchable like any other note.
    kind          TEXT NOT NULL DEFAULT 'note'
                  CHECK (kind IN ('note', 'observation', 'interview', 'feedback', 'status')),
    body          TEXT NOT NULL CHECK (char_length(body) BETWEEN 1 AND 50000),
    created_by    UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (experiment_id, run_id) REFERENCES exp_runs (experiment_id, id)
        ON DELETE SET NULL (run_id),
    FOREIGN KEY (experiment_id, variant_id) REFERENCES exp_variants (experiment_id, id)
        ON DELETE SET NULL (variant_id)
);

CREATE INDEX IF NOT EXISTS idx_exp_notes_experiment
    ON exp_notes (experiment_id, created_at DESC);

CREATE TABLE IF NOT EXISTS exp_evaluations (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id     UUID NOT NULL REFERENCES exp_experiments(id) ON DELETE CASCADE,
    evaluator_id      UUID NOT NULL REFERENCES exp_evaluators(id) ON DELETE RESTRICT,
    evaluator_version INTEGER NOT NULL,
    metric_id         UUID REFERENCES exp_metrics(id) ON DELETE SET NULL,
    params            JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Which rows the evaluation looked at: {"runs": "latest" | [ids],
    -- "since", "until", "dims": {...}}; {} = all rows.
    scope             JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- manual (a person), pipeline (automatic after new data), api (CI).
    trigger           TEXT NOT NULL DEFAULT 'manual'
                      CHECK (trigger IN ('manual', 'pipeline', 'api')),
    status            TEXT NOT NULL DEFAULT 'queued'
                      CHECK (status IN ('queued', 'running', 'done', 'failed')),
    output            JSONB,
    error             TEXT,
    logs              TEXT,
    input_digest      TEXT,
    requested_by      UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at        TIMESTAMPTZ,
    finished_at       TIMESTAMPTZ,
    duration_ms       INTEGER,
    FOREIGN KEY (evaluator_id, evaluator_version)
        REFERENCES exp_evaluator_versions (evaluator_id, version)
);

CREATE INDEX IF NOT EXISTS idx_exp_evaluations_experiment
    ON exp_evaluations (experiment_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_exp_evaluations_pair
    ON exp_evaluations (experiment_id, evaluator_id, metric_id, created_at DESC);

CREATE TABLE IF NOT EXISTS exp_decisions (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id UUID NOT NULL REFERENCES exp_experiments(id) ON DELETE CASCADE,
    verdict       TEXT NOT NULL CHECK (verdict IN ('ship', 'iterate', 'stop', 'inconclusive')),
    rationale     TEXT NOT NULL DEFAULT '' CHECK (char_length(rationale) <= 20000),
    learning      TEXT NOT NULL DEFAULT '' CHECK (char_length(learning) <= 20000),
    decided_by    UUID REFERENCES users(id) ON DELETE SET NULL,
    decided_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_exp_decisions_experiment
    ON exp_decisions (experiment_id, decided_at DESC);

-- ── machine access ───────────────────────────────────────────────────────

-- Personal access tokens for CI and scripts. Only a SHA-256 of the token is
-- stored; the plaintext is shown once. A token acts as its user, with that
-- user's roles as they are at request time, never as they were at creation.
CREATE TABLE IF NOT EXISTS exp_api_tokens (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name         TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
    token_hash   TEXT NOT NULL UNIQUE,
    token_hint   TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ,
    expires_at   TIMESTAMPTZ NOT NULL,
    revoked_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_exp_api_tokens_user ON exp_api_tokens (user_id);

-- ── background work ──────────────────────────────────────────────────────

-- A small durable job queue. Every gunicorn worker runs a poller; a job is
-- claimed with FOR UPDATE SKIP LOCKED and a lease, so a crashed worker's job
-- is picked up again once the lease runs out. dedupe_key coalesces repeated
-- requests (ten edits in a minute become one re-index).
CREATE TABLE IF NOT EXISTS exp_jobs (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind         TEXT NOT NULL CHECK (kind IN ('index', 'unindex', 'evaluate', 'pipeline')),
    dedupe_key   TEXT,
    payload      JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Lower runs first: a person's edit (10) before a bulk re-index (200).
    priority     SMALLINT NOT NULL DEFAULT 100,
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'running', 'done', 'dead')),
    attempts     INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 8,
    run_after    TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_by    TEXT,
    locked_until TIMESTAMPTZ,
    last_error   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_exp_jobs_dedupe_pending
    ON exp_jobs (dedupe_key) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_exp_jobs_due
    ON exp_jobs (priority, run_after, id) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_exp_jobs_lease
    ON exp_jobs (locked_until) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS idx_exp_jobs_finished
    ON exp_jobs (finished_at) WHERE status IN ('done', 'dead');

-- Shared rate slots. The Knovas tenant allows few document inits per minute
-- and RemoteController needs most of them; every Platform worker takes its
-- slot from this one row, so two workers cannot double the rate.
CREATE TABLE IF NOT EXISTS exp_rate_slots (
    name    TEXT PRIMARY KEY,
    next_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO exp_rate_slots (name) VALUES ('knovas_init') ON CONFLICT (name) DO NOTHING;

-- Every pointer the module has written to Knovas, until it is deleted there.
-- No foreign key: the row must outlive its experiment so a failed or late
-- deletion can still be found and repeated (purge-index reads this table).
CREATE TABLE IF NOT EXISTS exp_index_documents (
    pointer       TEXT PRIMARY KEY,
    experiment_id UUID,
    indexed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The experiment page reads its activity from audit_log by target.
CREATE INDEX IF NOT EXISTS idx_audit_log_target
    ON audit_log (target_type, target_id, occurred_at DESC);
