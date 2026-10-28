-- Change requests proposed from the Ansichten TRACE view (Step 7).
--
-- The brain is never written: a change becomes a pull request on the plan
-- file in KnowledgeBase, and the client-zero loader updates the tenant after
-- the merge. This table remembers which PR belongs to which instance so the
-- view can say "Änderung beantragt: PR #n" until the loader has caught up.
-- The audit log carries the same event (plan.change.proposed).
--
-- node_id has no foreign key: it names a node in the brain, across the mTLS
-- boundary (same reasoning as node_grants).
--
-- Plan: docs/superpowers/plans/2026-10-27-process-and-spine-views.md (Step 7)

CREATE TABLE IF NOT EXISTS change_requests (
    id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    node_id       UUID         NOT NULL,
    process_key   VARCHAR(80)  NOT NULL,
    instance_key  VARCHAR(200) NOT NULL,
    plan_path     VARCHAR(500) NOT NULL,
    change_class  VARCHAR(12)  NOT NULL CHECK (change_class IN ('minor', 'substantive')),
    fields        JSONB        NOT NULL DEFAULT '[]'::jsonb,
    expected      JSONB        NOT NULL DEFAULT '{}'::jsonb,
    pr_number     INTEGER      NULL,
    pr_url        VARCHAR(500) NULL,
    branch        VARCHAR(200) NOT NULL,
    state         VARCHAR(12)  NOT NULL DEFAULT 'open'
                  CHECK (state IN ('open', 'merged', 'closed', 'applied')),
    created_by    UUID         NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_change_requests_node ON change_requests (node_id, state);
