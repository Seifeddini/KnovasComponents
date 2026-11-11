-- Brain Builder: an approval binds to everything a publish changes (BB-07, finding B1; FEAT-2026-11-05-knovas-brain-v1).
--
-- An approval held the draft's content hash only, and the review page showed an import as "Paket importiert" with
-- no content, while publish also wrote the package's access groups and folder rules onto the tenant and its role
-- mapping into the Platform's settings. The review now shows the whole diff against the published version, the dry
-- run shows what publish writes beside the ontology, and the approval keeps the digest of all of it
-- (builder.review.review_hash): a publish whose fresh digest differs -- the draft, the published state, the tenant's
-- access groups or rules, or the Platform's role mapping changed since -- is not the approved one and is refused.
--
-- ``own_import`` marks an approval by the person whose import (or creation) the draft started from. Self-approval
-- stays allowed (decision of 2026-11-06); it is recorded and shown as such.
--
-- Additive only: existing rows keep NULL and false. An approval without a digest no longer lets a draft be published.

ALTER TABLE builder_approval ADD COLUMN IF NOT EXISTS review_hash CHAR(64)
    CHECK (review_hash IS NULL OR review_hash ~ '^[0-9a-f]{64}$');
ALTER TABLE builder_approval ADD COLUMN IF NOT EXISTS own_import BOOLEAN NOT NULL DEFAULT false;
