-- Brain Builder: sync components under connector protocol v1.
--
-- A sync component is a kind of its own, and ``check`` a command: without them here, the database refuses both.
--
-- What a component reports about reading its sources: per source a code and counts, the latest only -- whether it can
-- reach it, with this credential, with this folder mounted. Never a file name, an address or a value (GI-DATA-02/04).

ALTER TABLE builder_component DROP CONSTRAINT IF EXISTS builder_component_kind_check;
ALTER TABLE builder_component ADD CONSTRAINT builder_component_kind_check
    CHECK (kind IN ('github_runner', 'knovas_connector', 'http_json', 'sync_component'));

ALTER TABLE builder_component_command DROP CONSTRAINT IF EXISTS builder_component_command_command_check;
ALTER TABLE builder_component_command ADD CONSTRAINT builder_component_command_command_check
    CHECK (command IN ('run', 'pause', 'resume', 'approve_plan', 'reject_plan', 'reload_config', 'check'));

ALTER TABLE builder_component ADD COLUMN IF NOT EXISTS source_checks JSONB NOT NULL DEFAULT '{}'::jsonb;
