-- How this installation presents its knowledge-graph node types.
--
-- The types, their fields and every value live in the tenant's Knowledge
-- Graph at Knovas. What lives here is presentation that belongs to THIS
-- installation: two firms can keep the same type and list it differently.
--
-- node_type_id is TEXT and carries no foreign key on purpose: it names a row
-- in kg_node_type, on the other side of the mTLS boundary (same reasoning as
-- node_grants.node_id). A setting whose type was deleted is dead data and
-- simply never matches a live type again.

-- A directory (Verzeichnis): one page in the navigation listing every entry
-- of one type. At most one per type -- the address is the key to the page,
-- and two pages over the same entries would be two truths about one thing.
CREATE TABLE IF NOT EXISTS node_type_views (
    node_type_id TEXT         PRIMARY KEY CHECK (length(node_type_id) BETWEEN 1 AND 200),
    slug         VARCHAR(64)  NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
    title        VARCHAR(200) NOT NULL CHECK (length(btrim(title)) > 0),
    subtitle     VARCHAR(200) NOT NULL DEFAULT '',
    active       BOOLEAN      NOT NULL DEFAULT TRUE,
    position     INTEGER      NOT NULL DEFAULT 0,
    columns      JSONB        NOT NULL DEFAULT '[]'::jsonb
                 CHECK (jsonb_typeof(columns) = 'array'),
    updated_by   UUID         NULL REFERENCES users(id) ON DELETE SET NULL,
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- The layout of an entry's card: header chips, rail, named sections. Kept
-- apart from node_type_views because a type without a directory still has
-- entries, and each of them still has a card.
CREATE TABLE IF NOT EXISTS node_type_cards (
    node_type_id TEXT         PRIMARY KEY CHECK (length(node_type_id) BETWEEN 1 AND 200),
    layout       JSONB        NOT NULL CHECK (jsonb_typeof(layout) = 'object'),
    updated_by   UUID         NULL REFERENCES users(id) ON DELETE SET NULL,
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Suggestions an administrator turned down. A dismissed suggestion never
-- comes back -- the same rule as a rejected sort proposal in the backend. The
-- id encodes the state it was computed from, so a changed state is a new
-- suggestion. Stored as a hash: ids embed field names and enum values.
CREATE TABLE IF NOT EXISTS graph_suggestion_dismissals (
    suggestion_key CHAR(64)    PRIMARY KEY,
    dismissed_by   UUID        NULL REFERENCES users(id) ON DELETE SET NULL,
    dismissed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
