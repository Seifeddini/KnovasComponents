-- The firm's own identity store.
--
-- This database lives on the customer's hardware and holds what Knovas
-- deliberately never sees: who works at the firm, what they may open, and who
-- approved the destructive things. Knovas receives a signed opaque subject id
-- and a group list; none of the columns below cross that boundary.
--
-- Two concepts share this schema and must never be conflated:
--
--   platform roles   what a user may DO inside the Platform (admin, approver,
--                    ingestion_manager, member). Local, and meaningless to Knovas.
--   access groups    Knovas-side RBAC group ids from GET /secured/access_groups.
--                    They govern what a user may SEE. `user_access_groups` is the
--                    join the B2 principal assertion signs.
--
-- Plan: docs/superpowers/plans/2026-08-14-section-b-buildout.md (KC-F2)

CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()

-- ── people ────────────────────────────────────────────────────────────────

-- email is citext, not text-with-a-lower()-index: a firm's addresses are
-- case-insensitive in every mail system they use, and two rows differing only
-- by case would be two accounts for one person — the exact ambiguity an audit
-- must not contain.
CREATE TABLE IF NOT EXISTS users (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email                CITEXT      NOT NULL UNIQUE,
    display_name         TEXT        NOT NULL,
    -- NULL for federated-only accounts: an OIDC user has no local verifier,
    -- and storing a dummy hash would make "can this account log in locally?"
    -- unanswerable.
    password_hash        TEXT,
    idp_subject          TEXT UNIQUE,
    status               TEXT        NOT NULL DEFAULT 'active'
                         CHECK (status IN ('active', 'disabled', 'locked')),
    must_change_password BOOLEAN     NOT NULL DEFAULT FALSE,
    mfa_secret_enc       BYTEA,
    mfa_enrolled_at      TIMESTAMPTZ,
    failed_attempts      INTEGER     NOT NULL DEFAULT 0,
    locked_until         TIMESTAMPTZ,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by           UUID REFERENCES users(id) ON DELETE SET NULL,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    disabled_at          TIMESTAMPTZ,
    disabled_by          UUID REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS roles (
    id          SERIAL PRIMARY KEY,
    key         TEXT NOT NULL UNIQUE,
    label       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    is_builtin  BOOLEAN NOT NULL DEFAULT FALSE
);

INSERT INTO roles (key, label, description, is_builtin) VALUES
    ('admin', 'Administrator',
     'Manages accounts, access groups, walls and ingestion.', TRUE),
    ('approver', 'Approver',
     'May be the second confirmer on a destructive action. Cannot approve their own request.', TRUE),
    ('ingestion_manager', 'Ingestion manager',
     'May edit and run ingestion profiles.', TRUE),
    ('member', 'Member',
     'Searches and reads, within their access groups.', TRUE)
ON CONFLICT (key) DO NOTHING;

CREATE TABLE IF NOT EXISTS user_roles (
    user_id UUID    NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    granted_by UUID REFERENCES users(id) ON DELETE SET NULL,
    PRIMARY KEY (user_id, role_id)
);

-- ── the mapping B2 signs ──────────────────────────────────────────────────

-- group_id is a Knovas identifier and therefore text, not a foreign key: the
-- authoritative tree lives in the tenant's Knovas database, and a stale local
-- copy must never be able to hold a grant hostage. The Secure API resolves
-- these inside the caller's own tenant and fails the request closed if one is
-- unknown (GI-ACCESSROLES-04), so a deleted group degrades to a refusal there
-- rather than to a silent widening here.
CREATE TABLE IF NOT EXISTS user_access_groups (
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    group_id   TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'sso')),
    granted_by UUID REFERENCES users(id) ON DELETE SET NULL,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, group_id)
);

CREATE INDEX IF NOT EXISTS idx_user_access_groups_group ON user_access_groups (group_id);

-- A local mirror of GET /secured/access_groups, so the admin console can draw
-- the tree without a round trip and can detect that `epoch` moved under it.
-- Never authoritative: it is refreshed, never edited.
CREATE TABLE IF NOT EXISTS access_group_cache (
    group_id   TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    parent_id  TEXT,
    depth      INTEGER NOT NULL DEFAULT 0,
    epoch      BIGINT  NOT NULL DEFAULT 0,
    synced_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── sessions ──────────────────────────────────────────────────────────────

-- Server-side, because this is what makes "leaver" real. A signed cookie
-- carrying a user id stays valid until it expires, so disabling an account
-- would take effect whenever the cookie happened to lapse. A row that can be
-- deleted takes effect on the next request.
CREATE TABLE IF NOT EXISTS sessions (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,
    revoked_at   TIMESTAMPTZ,
    mfa_passed   BOOLEAN NOT NULL DEFAULT FALSE,
    ip           INET,
    user_agent   TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions (expires_at);

-- ── four eyes ─────────────────────────────────────────────────────────────

-- The CHECK is the point of this table. KC-B5-1 enforces requester ≠ approver
-- in the service as well; both exist so that a service bug cannot permit
-- self-approval. `approved_by` is NULL while pending, which is why the
-- constraint is written to pass on NULL rather than as an inequality alone.
CREATE TABLE IF NOT EXISTS approval_requests (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind             TEXT NOT NULL CHECK (kind IN (
                         'matter_delete', 'acl_change', 'bulk_export',
                         'purge_all_documents', 'ingestion_profile_change')),
    target_ref       TEXT NOT NULL,
    payload          JSONB NOT NULL DEFAULT '{}'::jsonb,
    requested_by     UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    requested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at       TIMESTAMPTZ NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
                         'pending', 'approved', 'rejected', 'expired', 'executed')),
    approved_by      UUID REFERENCES users(id) ON DELETE RESTRICT,
    approved_at      TIMESTAMPTZ,
    decision_reason  TEXT,
    executed_at      TIMESTAMPTZ,
    execution_result JSONB,
    CONSTRAINT four_eyes CHECK (approved_by IS NULL OR approved_by <> requested_by)
);

CREATE INDEX IF NOT EXISTS idx_approval_requests_pending
    ON approval_requests (status, expires_at) WHERE status = 'pending';

-- ── the record ────────────────────────────────────────────────────────────

-- Append-only by convention now and by grant later: nothing in the Platform
-- issues UPDATE or DELETE against it. actor_email_snapshot is denormalised on
-- purpose — an audit entry must stay readable after the account is deleted,
-- and a dangling uuid is not an answer to "who did this?".
--
-- B4 (per-user attributable audit) is out of scope for this plan; this table
-- is its substrate and receives only the events B1, B2, B3 and B5 generate.
CREATE TABLE IF NOT EXISTS audit_log (
    id                   BIGSERIAL PRIMARY KEY,
    occurred_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor_user_id        UUID REFERENCES users(id) ON DELETE SET NULL,
    actor_email_snapshot CITEXT,
    action               TEXT NOT NULL,
    target_type          TEXT,
    target_id            TEXT,
    outcome              TEXT NOT NULL DEFAULT 'ok'
                         CHECK (outcome IN ('ok', 'denied', 'error')),
    request_id           TEXT,
    ip                   INET,
    user_agent           TEXT,
    detail               JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_audit_log_time ON audit_log (occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_actor ON audit_log (actor_user_id, occurred_at DESC);

-- ── ingestion ─────────────────────────────────────────────────────────────

-- Versioned, attributed, and the source of truth. What RemoteController holds
-- is compiled output; this is what a person edited and who edited it. Replaces
-- an unattributed JSON file inside a container volume.
CREATE TABLE IF NOT EXISTS ingestion_profiles (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name         TEXT NOT NULL,
    version      INTEGER NOT NULL DEFAULT 1,
    profile      JSONB NOT NULL,
    is_current   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by   UUID REFERENCES users(id) ON DELETE SET NULL,
    approved_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    pushed_at    TIMESTAMPTZ,
    UNIQUE (name, version)
);

-- One current version per named profile; earlier versions stay for rollback.
CREATE UNIQUE INDEX IF NOT EXISTS idx_ingestion_profiles_current
    ON ingestion_profiles (name) WHERE is_current;

-- ── settings ──────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by UUID REFERENCES users(id) ON DELETE SET NULL
);
