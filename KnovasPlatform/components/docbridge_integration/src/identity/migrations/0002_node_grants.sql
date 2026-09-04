-- Who may EDIT a knowledge-graph node (SS-315).
--
-- Deliberately not a second read model. Read visibility is decided by the
-- backend ACL (GI-GRAPH-12) and this table never narrows a listing; it answers
-- one question, "may this user write this node?", which the backend has no
-- concept of because it has no concept of a user.
--
-- node_id has no foreign key ON PURPOSE. It names a row in kg_nodes, in a
-- different database on the other side of the mTLS boundary. The Platform
-- cannot guarantee referential integrity here and must not pretend to: a grant
-- whose node was deleted is dead data, cleaned by reconciliation, never by a
-- constraint that cannot exist.
--
-- Plan: docs/superpowers/plans/2026-09-02-typed-node-workbench-components.md (C1)

CREATE TABLE IF NOT EXISTS node_grants (
    node_id    UUID        NOT NULL,
    user_id    UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role       VARCHAR(8)  NOT NULL CHECK (role IN ('owner', 'editor')),
    granted_by UUID        NULL REFERENCES users(id) ON DELETE SET NULL,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (node_id, user_id)
);

-- One owner per node. A second owner would make "who may grant editors?" have
-- two answers, and transfer is an admin action rather than a race.
CREATE UNIQUE INDEX IF NOT EXISTS idx_node_grants_one_owner
    ON node_grants (node_id) WHERE role = 'owner';

CREATE INDEX IF NOT EXISTS idx_node_grants_user ON node_grants (user_id);
