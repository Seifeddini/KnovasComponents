-- An administrator approving a draft they changed themselves (Brain Builder).
--
-- The four-eyes rule stays: whoever changed a draft does not approve it.
-- An administrator may, deliberately: with a reason, and the approval says
-- so wherever approvals are shown (review page, activity feed). The reason is
-- the administrator's own words and stays here, never in a log.

ALTER TABLE builder_approval ADD COLUMN IF NOT EXISTS own_changes BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE builder_approval ADD COLUMN IF NOT EXISTS reason TEXT CHECK (reason IS NULL OR length(reason) <= 500);
