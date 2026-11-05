-- Brain Builder: sites and Knovas Link (connection layer MVP-23, KC-7b). Proposed: a critical change, reviewed by
-- management before it merges.
--
-- A site is one machine at a company. Knovas Link runs there, the only process that calls Knovas; the components
-- ticked for the site run beside it and talk only to Link. The Builder makes the site, its install package and Link's
-- one-time enrolment token; Link enrols as a component of kind 'knovas_link' through protocol v1.
--
-- Link's heartbeat reports the site: whether it holds its certificate, whether the work channel is up, the last probe,
-- the components behind it, per source the last good read, and job counts. The Platform keeps that as ``telemetry``
-- on Link's row -- codes, counts, versions and times only, never a name (GI-DATA-02/04) -- and makes one child row per
-- component (``parent_id``, transport 'link', ``local_name`` its name on the site), so a source can be read through it
-- and its runs, events and checks, which Link forwards, are kept per component.
--
-- Additive only: no existing row changes, and every new column has a default or is nullable.

CREATE TABLE IF NOT EXISTS builder_site (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant      TEXT NOT NULL,
    name        TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
    owner       TEXT NOT NULL DEFAULT '' CHECK (length(owner) <= 120),
    -- What the package installs beside Link: option codes such as 'github' or 'share', never an address or a path.
    options     JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(options) = 'array'),
    created_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant, name)
);

ALTER TABLE builder_component DROP CONSTRAINT IF EXISTS builder_component_kind_check;
ALTER TABLE builder_component ADD CONSTRAINT builder_component_kind_check
    CHECK (kind IN ('github_runner', 'knovas_connector', 'http_json', 'sync_component', 'knovas_link'));

ALTER TABLE builder_component DROP CONSTRAINT IF EXISTS builder_component_transport_check;
ALTER TABLE builder_component ADD CONSTRAINT builder_component_transport_check
    CHECK (transport IN ('adapter', 'v1', 'link'));

ALTER TABLE builder_component ADD COLUMN IF NOT EXISTS site_id UUID REFERENCES builder_site(id) ON DELETE SET NULL;
-- A component behind Knovas Link; it goes with Link's row.
ALTER TABLE builder_component ADD COLUMN IF NOT EXISTS parent_id UUID REFERENCES builder_component(id) ON DELETE CASCADE;
ALTER TABLE builder_component ADD COLUMN IF NOT EXISTS local_name TEXT CHECK (local_name IS NULL OR local_name ~ '^[a-z][a-z0-9_.-]{0,59}$');
ALTER TABLE builder_component ADD COLUMN IF NOT EXISTS telemetry JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE builder_component DROP CONSTRAINT IF EXISTS builder_component_child_check;
ALTER TABLE builder_component ADD CONSTRAINT builder_component_child_check
    CHECK ((parent_id IS NULL) = (local_name IS NULL) AND ((parent_id IS NULL) OR transport = 'link'));

CREATE UNIQUE INDEX IF NOT EXISTS builder_component_child ON builder_component (parent_id, local_name)
    WHERE parent_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS builder_component_site ON builder_component (site_id);

-- «Jetzt prüfen» on a site asks Link for a probe of the work channel.
ALTER TABLE builder_component_command DROP CONSTRAINT IF EXISTS builder_component_command_command_check;
ALTER TABLE builder_component_command ADD CONSTRAINT builder_component_command_command_check
    CHECK (command IN ('run', 'pause', 'resume', 'approve_plan', 'reject_plan', 'reload_config', 'check', 'probe'));
