-- Brain Builder: the tenant's registered sync components (build plan step B7; B13 grows it).
--
-- A source (B7) is read only through the component that serves it: the
-- GitHub runner, a KnovasConnector (file share, DMS) or the HTTP/JSON reader.
-- The register holds what the Platform knows of each: its kind, name, site,
-- owner and settings without secrets (a connector's address). Secrets live in
-- the settings registry (platform_settings), never here. Enrolment with a
-- one-time token, the component's own certificate, heartbeats and runs come
-- with step B13.

CREATE TABLE IF NOT EXISTS builder_component (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant         TEXT NOT NULL,
    kind           TEXT NOT NULL CHECK (kind IN ('github_runner', 'knovas_connector', 'http_json')),
    name           TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
    site           TEXT NOT NULL DEFAULT '',
    owner          TEXT NOT NULL DEFAULT '',
    settings       JSONB NOT NULL DEFAULT '{}'::jsonb,
    state          TEXT NOT NULL DEFAULT 'registered' CHECK (state IN ('registered', 'retired')),
    registered_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    registered_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant, name)
);

CREATE INDEX IF NOT EXISTS builder_component_tenant ON builder_component (tenant, kind);
