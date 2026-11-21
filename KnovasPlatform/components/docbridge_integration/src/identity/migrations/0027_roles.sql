-- Roles as bundles of functions: the role builder's items RB-1 and RB-2.
--
-- A role is a name, a description, functions, at most one Core group and members:
--
--   functions     keys of the table ``roles`` (what a person may DO in the Platform; the route gates check them).
--                 A person's functions are the union of their roles' functions (identity.users.roles_of).
--   core group    one Knovas access group id (what the role's members may SEE). A person's claim groups are their
--                 running direct grants plus their roles' Core groups, at most 32 (identity/roles.py).
--   members       people. Agents come with RB-6 and RB-7, in tables of their own.
--
-- Two roles always exist, exactly one of each kind, and neither can be deleted: «Administration», which alone holds
-- the function ``admin`` (an administrator is exactly a member of it), and «Mitglied», with the function ``member``.
-- Every other role is the tenant's own (kind ``custom``). This file links no Core group and writes nothing to the
-- tenant: RB-4 creates or adopts the groups on an administrator's action, never at start-up.
--
-- The move of an existing installation (design § 3), in SQL below, so that nobody loses a function:
--
--   * every person (every row of ``users``) joins Mitglied;
--   * everyone holding ``admin`` in ``user_roles`` joins Administration;
--   * for each distinct set of other keys (all but ``admin`` and ``member``; an administrator's other keys count,
--     because some checks name a key exactly and ``admin`` does not pass them) one role is created and exactly the
--     people holding that set join it. It is named after its functions in the words and the order of design § 2.1,
--     joined by " + ", for example «Freigaben bestätigen» or «Experimente + Experimente verwalten». A name longer
--     than 80 characters keeps its leading words and ends in " + … (n)", n counting those roles one by one, so
--     every name stays unique.
--
-- A new installation has nobody yet: the move finds no one and leaves the two roles without members. Its first
-- administrator joins Administration at the first start (identity.bootstrap, through grant_role).
--
-- The way back. ``user_roles`` is kept untouched for one release and is read by nothing after this migration. The
-- previous image reads ``user_roles`` as the move left it, so role changes made after the move are not carried back.
--
-- New tables only: no existing table, column, index, constraint or trigger changes, and no row of ``roles``.
-- One installation per database, as in the rest of identity/: no tenant column.
--
-- Plan: KnowledgeBase docs/ModernDocs/plans/2026-11-19-platform-mvp.md (§ 4 Admin A-01 to A-10), items RB-1, RB-2.
-- Design: docs/platform-mvp/design/role-builder.md (§ 1, § 2.1, § 2.3, § 3, § 6); work order
-- docs/platform-mvp/work-orders/RB-1-2.md.

CREATE TABLE IF NOT EXISTS tenant_roles (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Unique regardless of case; trimmed and with white space collapsed by the store.
    name          CITEXT      NOT NULL UNIQUE CHECK (char_length(name::text) BETWEEN 1 AND 80),
    description   TEXT        NOT NULL DEFAULT '',
    kind          TEXT        NOT NULL DEFAULT 'custom'
                  CHECK (kind IN ('administration', 'mitglied', 'custom')),
    -- A Knovas access group id: text and no foreign key, like user_access_groups.group_id, because the tree lives
    -- in the tenant's Knovas database.
    core_group_id TEXT        NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by    UUID        REFERENCES users(id) ON DELETE SET NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- At most one Administration and one Mitglied; the kind, not the name, identifies them.
CREATE UNIQUE INDEX IF NOT EXISTS idx_tenant_roles_one_per_kind
    ON tenant_roles (kind) WHERE kind <> 'custom';

CREATE TABLE IF NOT EXISTS tenant_role_functions (
    role_id  UUID NOT NULL REFERENCES tenant_roles(id) ON DELETE CASCADE,
    function TEXT NOT NULL REFERENCES roles(key),
    PRIMARY KEY (role_id, function)
);

CREATE TABLE IF NOT EXISTS tenant_role_members (
    role_id  UUID        NOT NULL REFERENCES tenant_roles(id) ON DELETE CASCADE,
    user_id  UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    added_by UUID        REFERENCES users(id) ON DELETE SET NULL,
    PRIMARY KEY (role_id, user_id)
);

-- "The roles of a person": the gates read it on every request (roles_of); it also serves the cascade from users.
CREATE INDEX IF NOT EXISTS idx_tenant_role_members_user ON tenant_role_members (user_id);

-- ── the two roles ─────────────────────────────────────────────────────────

INSERT INTO tenant_roles (name, description, kind) VALUES
    ('Administration', 'Verwaltet die Installation: Personen, Rollen, Zugriff und Einstellungen.', 'administration'),
    ('Mitglied', 'Jede Person der Installation: fragen und suchen.', 'mitglied')
ON CONFLICT DO NOTHING;

INSERT INTO tenant_role_functions (role_id, function)
SELECT id, CASE kind WHEN 'administration' THEN 'admin' ELSE 'member' END
FROM tenant_roles WHERE kind IN ('administration', 'mitglied')
ON CONFLICT DO NOTHING;

-- ── the move of an existing installation ──────────────────────────────────

-- Every person joins Mitglied.
INSERT INTO tenant_role_members (role_id, user_id)
SELECT r.id, u.id FROM tenant_roles r CROSS JOIN users u WHERE r.kind = 'mitglied'
ON CONFLICT DO NOTHING;

-- Everyone holding admin joins Administration.
INSERT INTO tenant_role_members (role_id, user_id)
SELECT r.id, ur.user_id
FROM tenant_roles r
CROSS JOIN roles k
JOIN user_roles ur ON ur.role_id = k.id
WHERE r.kind = 'administration' AND k.key = 'admin'
ON CONFLICT DO NOTHING;

-- One role per distinct set of other keys, and exactly the people holding that set join it. One statement: the
-- roles, their functions and their members are written together, matched by the role's unique name.
WITH held AS (
    -- Each person's other keys as a sorted array: the set that decides their role.
    SELECT ur.user_id, array_agg(k.key ORDER BY k.key) AS keys
    FROM user_roles ur
    JOIN roles k ON k.id = ur.role_id
    WHERE k.key NOT IN ('admin', 'member')
    GROUP BY ur.user_id
),
words (key, word, place) AS (
    -- The words of design § 2.1, in its order ("Fragen und suchen" and "Verwaltung" are member and admin: not here).
    VALUES ('approver', 'Freigaben bestätigen', 3),
           ('ingestion_manager', 'Einlesen', 4),
           ('experimenter', 'Experimente', 5),
           ('experiments_manager', 'Experimente verwalten', 6),
           ('builder_viewer', 'Builder lesen', 7),
           ('builder_author', 'Builder bearbeiten', 8),
           ('builder_reviewer', 'Builder prüfen', 9),
           ('builder_publisher', 'Builder veröffentlichen', 10)
),
parts AS (
    -- The words of each set in that order; a key without words (none today) is named by itself, after the others.
    SELECT s.keys, coalesce(w.word, x.key) AS word,
           row_number() OVER (PARTITION BY s.keys ORDER BY coalesce(w.place, 99), x.key) AS place
    FROM (SELECT DISTINCT keys FROM held) s
    CROSS JOIN LATERAL unnest(s.keys) AS x(key)
    LEFT JOIN words w ON w.key = x.key
),
spans AS (
    -- The length of the name up to and including each word.
    SELECT keys, word, place,
           sum(char_length(word) + 3) OVER (PARTITION BY keys ORDER BY place) - 3 AS upto
    FROM parts
),
full_names AS (
    SELECT keys, string_agg(word, ' + ' ORDER BY place) AS name FROM parts GROUP BY keys
),
long_names AS (
    -- Over 80 characters: a tail numbering these roles one by one keeps every shortened name unique.
    SELECT keys, ' + … (' || row_number() OVER (ORDER BY keys) || ')' AS tail
    FROM full_names WHERE char_length(name) > 80
),
named AS (
    SELECT f.keys,
           CASE WHEN l.tail IS NULL THEN f.name
                ELSE (SELECT string_agg(s.word, ' + ' ORDER BY s.place) FROM spans s
                      WHERE s.keys = f.keys AND s.upto + char_length(l.tail) <= 80) || l.tail
           END AS name
    FROM full_names f
    LEFT JOIN long_names l ON l.keys = f.keys
),
made AS (
    INSERT INTO tenant_roles (name, description, kind)
    SELECT name, 'Bei der Umstellung auf Rollen angelegt, für die Personen mit genau diesen Funktionen.', 'custom'
    FROM named
    RETURNING id, name::text AS name
),
given AS (
    INSERT INTO tenant_role_functions (role_id, function)
    SELECT m.id, unnest(n.keys) FROM made m JOIN named n ON n.name = m.name
    RETURNING role_id
)
INSERT INTO tenant_role_members (role_id, user_id)
SELECT m.id, h.user_id
FROM held h
JOIN named n ON n.keys = h.keys
JOIN made m ON m.name = n.name;
