-- Brain Builder: edits on the canvas (build plan step B3).
--
-- One edit can change several elements of the model at once (renaming a
-- type also rewrites the relations and subtypes that name it). The rows of
-- one edit share a batch id, so undo and redo take the edit back as a whole.

ALTER TABLE builder_change ADD COLUMN IF NOT EXISTS batch UUID;

CREATE INDEX IF NOT EXISTS builder_change_batch
    ON builder_change (workspace, batch) WHERE batch IS NOT NULL;
