-- Brain Builder: health reviews (build plan step B10).
--
-- Each type of a workspace's health section has an owner role and a review
-- rhythm (review_every). When the owner has looked at a type's three numbers,
-- the Betrieb stage records it here; the next review is due review_every
-- later. Without a review the last publish counts as the start.

CREATE TABLE IF NOT EXISTS builder_review (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace    UUID NOT NULL REFERENCES builder_workspace(id) ON DELETE CASCADE,
    node_type    TEXT NOT NULL CHECK (char_length(node_type) BETWEEN 1 AND 200),
    reviewed_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS builder_review_latest ON builder_review (workspace, node_type, at DESC);
