-- Brain Builder: a component behind Knovas Link may be of a kind the Platform has no kind of its own for, when it
-- declared itself under connector protocol 2.0 (FEAT-2026-11-10-write-back C-8; SPEC-write-back-13: a new source kind
-- needs no Platform change).
--
-- With BUILDER_PROTOCOL_V2 on, the Platform keeps a component Link reports whose connector declaration the protocol's
-- schema allows, whatever its component kind (builder/protocol.py, _keep_site). Its kind is then the connector's own,
-- which 0022's list need not hold. Only such a child takes another kind: a component enrolled on its
-- own (no ``parent_id``) keeps the list, and the kind always has the shape of a heartbeat's kind (components.EVENT_CODE).
--
-- Additive only: every row 0022 allowed is still allowed.

ALTER TABLE builder_component DROP CONSTRAINT IF EXISTS builder_component_kind_check;
ALTER TABLE builder_component ADD CONSTRAINT builder_component_kind_check
    CHECK (kind IN ('github_runner', 'knovas_connector', 'http_json', 'sync_component', 'knovas_link')
           OR (parent_id IS NOT NULL AND kind ~ '^[a-z][a-z0-9_.]{1,60}$'));
