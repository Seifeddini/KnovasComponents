-- Change sets proposed from the Ansichten views (Step 7).
--
-- The brain is never written. A user collects link fixes and field edits in one open
-- change set; on submit the Platform opens ONE pull request on KnowledgeBase, and the
-- client-zero loader updates the tenant after the merge. This table holds the open set
-- (operations, the base commit they apply to, the user's reasons for decision records)
-- and, once submitted, which pull request carries it, so the views can say
-- "Änderung ausstehend: PR #n" until the loader has caught up. The audit log carries
-- every submit (plan.changeset.submitted).
--
-- Operations name brain nodes (instance ids) across the mTLS boundary, so there is no
-- foreign key to them (same reasoning as node_grants).
--
-- Plan: docs/superpowers/plans/2026-10-27-process-and-spine-views.md (Step 7)

CREATE TABLE IF NOT EXISTS change_sets (
    id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    state         VARCHAR(12)  NOT NULL DEFAULT 'open'
                  CHECK (state IN ('open', 'submitted', 'merged', 'closed', 'applied', 'discarded')),
    base_branch   VARCHAR(200) NOT NULL,
    base_commit   CHAR(40)     NOT NULL,
    operations    JSONB        NOT NULL DEFAULT '[]'::jsonb,
    reasons       JSONB        NOT NULL DEFAULT '{}'::jsonb,
    pr_number     INTEGER      NULL,
    pr_url        VARCHAR(500) NULL,
    branch        VARCHAR(200) NULL,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    submitted_at  TIMESTAMPTZ  NULL
);

-- one open change set per user
CREATE UNIQUE INDEX IF NOT EXISTS idx_change_sets_open ON change_sets (user_id) WHERE state = 'open';
-- "which submitted sets touch this instance": operations @> '[{"instance": "<id>"}]'
CREATE INDEX IF NOT EXISTS idx_change_sets_ops ON change_sets USING gin (operations jsonb_path_ops);
