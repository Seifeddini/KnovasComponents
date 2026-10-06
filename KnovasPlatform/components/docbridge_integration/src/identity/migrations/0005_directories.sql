-- Local presentation settings for Knowledge Graph node types.
-- Graph data and read ACLs remain in Knovas; these ids intentionally have no
-- foreign key across the mTLS boundary.

CREATE TABLE IF NOT EXISTS node_type_views (
    node_type_id TEXT         PRIMARY KEY CHECK (length(node_type_id) BETWEEN 1 AND 200),
    slug         VARCHAR(64)  NOT NULL UNIQUE
                 CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
    title        VARCHAR(200) NOT NULL CHECK (length(btrim(title)) > 0),
    subtitle     VARCHAR(200) NOT NULL DEFAULT '',
    active       BOOLEAN      NOT NULL DEFAULT TRUE,
    position     INTEGER      NOT NULL DEFAULT 0,
    columns      JSONB        NOT NULL DEFAULT '[]'::jsonb
                 CHECK (jsonb_typeof(columns) = 'array'),
    updated_by   UUID         NULL REFERENCES users(id) ON DELETE SET NULL,
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS node_type_cards (
    node_type_id TEXT         PRIMARY KEY CHECK (length(node_type_id) BETWEEN 1 AND 200),
    layout       JSONB        NOT NULL CHECK (jsonb_typeof(layout) = 'object'),
    updated_by   UUID         NULL REFERENCES users(id) ON DELETE SET NULL,
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);
