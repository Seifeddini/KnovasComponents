-- Saved views from the tenant's profiles (KC-2): a profile view is a shared view whose source is
-- the profile view it was seeded from, ``profile`` + ``<profile id>/<view id>`` (the definitions
-- bundle, KnowledgeBase KB-2/KB-7). The definition stays in the bundle; the row stores how to draw.
--
-- * source_kind gains 'profile'; render_kind gains the profile render kinds 'list' and 'map'.
-- * A profile view is unique by its source (seeding is idempotent by profile id + view id), not
--   by name: two profiles may both have a "Feature trace". Other shared views keep one name each.
--
-- Plan: WP KC-2 (views from the tenant's profiles).

DO $$
DECLARE
    c RECORD;
BEGIN
    FOR c IN
        SELECT conname FROM pg_constraint
        WHERE conrelid = 'saved_views'::regclass AND contype = 'c'
          AND (pg_get_constraintdef(oid) LIKE '%render_kind%' OR pg_get_constraintdef(oid) LIKE '%source_kind%')
    LOOP
        EXECUTE format('ALTER TABLE saved_views DROP CONSTRAINT %I', c.conname);
    END LOOP;
END $$;

ALTER TABLE saved_views
    ADD CONSTRAINT saved_views_source_kind
        CHECK (source_kind IN ('process', 'spine', 'profile')),
    ADD CONSTRAINT saved_views_render_kind
        CHECK (render_kind IN ('trace', 'board', 'tree', 'matrix', 'doc', 'list', 'map')),
    -- Processes draw as trace or board, spines as tree, matrix or documentation; a profile view as
    -- its profile says.
    ADD CONSTRAINT saved_views_source_render
        CHECK ((source_kind = 'process' AND render_kind IN ('trace', 'board'))
            OR (source_kind = 'spine' AND render_kind IN ('tree', 'matrix', 'doc'))
            OR source_kind = 'profile'),
    -- A profile view is always shared: it belongs to the tenant's configuration, not to a person.
    ADD CONSTRAINT saved_views_profile_shared
        CHECK (source_kind <> 'profile' OR audience = 'shared');

DROP INDEX IF EXISTS idx_saved_views_shared_name;
CREATE UNIQUE INDEX IF NOT EXISTS idx_saved_views_shared_name
    ON saved_views (lower(name)) WHERE audience = 'shared' AND source_kind <> 'profile';
CREATE UNIQUE INDEX IF NOT EXISTS idx_saved_views_profile_source
    ON saved_views (source_key) WHERE source_kind = 'profile';
