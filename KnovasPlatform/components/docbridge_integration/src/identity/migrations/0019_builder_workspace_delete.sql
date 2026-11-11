-- Deleting a workspace (Brain Builder).
--
-- A deleted workspace leaves every list and every page, but its record stays:
-- the change log, the approvals and the publish runs say who changed, approved
-- and published what, and a deletion must not erase that. deleted_by is who
-- deleted it; the workspace's own tables keep ON DELETE CASCADE for the day a
-- retention rule removes the row for good.

ALTER TABLE builder_workspace ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE builder_workspace ADD COLUMN IF NOT EXISTS deleted_by UUID REFERENCES users(id) ON DELETE SET NULL;
