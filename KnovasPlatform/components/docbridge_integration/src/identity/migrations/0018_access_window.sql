-- The access window (Brain Builder step B14).
--
-- A grant of an access group may end on a day: ends_on, NULL for no end.
-- The principal assertion reads only the grants that still run
-- (identity.users.UserRepository.access_groups_of), so an expired grant
-- stops widening what the person sees on their next request, by itself.
--
-- Switching the tenant's RBAC enforcement (clients.rbac_enforcement:
-- disabled | enforcing) takes a second administrator: one asks, another
-- approves, never the same person. The switch itself is Knovas-internal
-- (it needs the ACL backfill complete); the request records who asked and
-- who approved, and what the Brain answered.

ALTER TABLE user_access_groups ADD COLUMN IF NOT EXISTS ends_on DATE;

CREATE TABLE IF NOT EXISTS access_posture_request (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant        TEXT NOT NULL,
    mode          TEXT NOT NULL CHECK (mode IN ('disabled', 'enforcing')),
    requested_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    requested_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_by   UUID REFERENCES users(id) ON DELETE SET NULL,
    approved_at   TIMESTAMPTZ,
    outcome       TEXT,
    CHECK (approved_by IS NULL OR approved_by <> requested_by)
);

CREATE INDEX IF NOT EXISTS access_posture_request_tenant ON access_posture_request (tenant, requested_at DESC);
