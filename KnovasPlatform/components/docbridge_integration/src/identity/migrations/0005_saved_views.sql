-- Saved views of the Ansichten page: presentation settings, nothing else.
--
-- A view is Platform configuration, never a node in the brain (design
-- principle of the plan). It stores only how to draw: name, description,
-- audience, the definition it draws from (a process or a spine key), the
-- render kind and presentation settings (default root, grouping threshold,
-- hidden types, filters, sorting, colours). It holds no brain content, and it
-- grants nothing: what a view shows is still filtered by the brain per caller.
--
-- Personal views belong to one user; shared views are readable by everyone
-- signed in and changed by admins only, every change in audit_log.
--
-- Plan: docs/superpowers/plans/2026-10-27-process-and-spine-views.md (Step 5)

CREATE TABLE IF NOT EXISTS saved_views (
    id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    audience      VARCHAR(8)   NOT NULL CHECK (audience IN ('personal', 'shared')),
    owner_user_id UUID         NULL REFERENCES users(id) ON DELETE CASCADE,
    name          VARCHAR(80)  NOT NULL CHECK (length(btrim(name)) > 0),
    description   VARCHAR(500) NOT NULL DEFAULT '',
    source_kind   VARCHAR(8)   NOT NULL CHECK (source_kind IN ('process', 'spine')),
    source_key    VARCHAR(80)  NOT NULL CHECK (length(source_key) > 0),
    render_kind   VARCHAR(8)   NOT NULL CHECK (render_kind IN ('trace', 'board', 'tree', 'matrix')),
    settings      JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    created_by    UUID         NULL REFERENCES users(id) ON DELETE SET NULL,
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_by    UUID         NULL REFERENCES users(id) ON DELETE SET NULL,
    -- A personal view has an owner, a shared one has none.
    CHECK ((audience = 'personal') = (owner_user_id IS NOT NULL)),
    -- Processes draw as trace or board, spines as tree or matrix.
    CHECK ((source_kind = 'process' AND render_kind IN ('trace', 'board'))
        OR (source_kind = 'spine' AND render_kind IN ('tree', 'matrix')))
);

-- One name per owner for personal views, one name across shared views.
CREATE UNIQUE INDEX IF NOT EXISTS idx_saved_views_personal_name
    ON saved_views (owner_user_id, lower(name)) WHERE audience = 'personal';
CREATE UNIQUE INDEX IF NOT EXISTS idx_saved_views_shared_name
    ON saved_views (lower(name)) WHERE audience = 'shared';
CREATE INDEX IF NOT EXISTS idx_saved_views_owner ON saved_views (owner_user_id);
