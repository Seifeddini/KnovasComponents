-- Saved views may show a spine as its documentation (Step 8, render kind "doc").
--
-- 0005 created the two render-kind checks without names, so they are found by what
-- they say rather than by a generated name, dropped, and added again, named.
--
-- Plan: docs/superpowers/plans/2026-10-27-process-and-spine-views.md (Step 8)

DO $$
DECLARE
    c RECORD;
BEGIN
    FOR c IN
        SELECT conname FROM pg_constraint
        WHERE conrelid = 'saved_views'::regclass AND contype = 'c'
          AND pg_get_constraintdef(oid) LIKE '%render_kind%'
    LOOP
        EXECUTE format('ALTER TABLE saved_views DROP CONSTRAINT %I', c.conname);
    END LOOP;
END $$;

ALTER TABLE saved_views
    ADD CONSTRAINT saved_views_render_kind
        CHECK (render_kind IN ('trace', 'board', 'tree', 'matrix', 'doc')),
    -- Processes draw as trace or board, spines as tree, matrix or documentation.
    ADD CONSTRAINT saved_views_source_render
        CHECK ((source_kind = 'process' AND render_kind IN ('trace', 'board'))
            OR (source_kind = 'spine' AND render_kind IN ('tree', 'matrix', 'doc')));
