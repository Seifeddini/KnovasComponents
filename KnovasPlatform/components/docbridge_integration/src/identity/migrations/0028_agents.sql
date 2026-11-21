-- Agents as members of roles: the role builder's items RB-6 and RB-7.
--
-- An agent is a member of roles that is not a person:
--
--   assistant    the Knovas Assistent. Exactly one. It goes through the Platform and always acts for the signed-in
--                person: every Core call of an answer carries its own claim, narrowed to that person (RB-6).
--   external     an agent an administrator registers (Claude Code, Copilot ...). It goes through the Core's MCP
--                gateway, acts for one person fixed at registration (on_behalf_of) or on its own, and starts with
--                no role. The Platform signs its claims and hands the Core a pool of them (RB-7, CR-R5).
--
-- An agent's claim is the groups and functions of its roles; acting for a person, only what that person also holds
-- (identity/agents.py, AgentStore.claim_for). No agent joins Administration (the store refuses it, and the claim
-- leaves Administration out). A person's direct group grants reach no agent (FU-41): only roles carry data.
--
-- The Platform holds no agent credential: no column here holds a key, a secret, a hash or a token. The agent's key
-- is the Core gateway's, issued by Knovas and bound to the agent's id (CR-R6); the id is what the Platform shows.
--
-- The seed: the Knovas Assistent, a member of every role but Administration (on a new installation: Mitglied; on
-- one moved by 0027: also the roles of the move). Roles created later get it in the same transaction
-- (RoleStore.create_role); an administrator may take it out of a role on the role page.
--
-- New tables only: no existing table, column, index, constraint or trigger changes. Nothing is written to the
-- tenant. One installation per database, as in the rest of identity/: no tenant column.
--
-- Plan: KnowledgeBase docs/ModernDocs/plans/2026-11-19-platform-mvp.md (§ 4 Admin A-01 to A-10), items RB-6, RB-7.
-- Design: docs/platform-mvp/design/role-builder.md (§ 2.3, § 2.4, § 4, § 5, § 6, § 9); Core requests CR-R1, CR-R5,
-- CR-R6 in docs/platform-mvp/core-requests.md; work order docs/platform-mvp/work-orders/RB-6-7.md.

CREATE TABLE IF NOT EXISTS agents (
    -- The id the Core's gateway key names (CR-R6) and the claim's subject.
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Unique regardless of case; trimmed and with white space collapsed by the store.
    name         CITEXT      NOT NULL UNIQUE CHECK (char_length(name::text) BETWEEN 1 AND 80),
    kind         TEXT        NOT NULL CHECK (kind IN ('assistant', 'external')),
    -- The person an external agent acts for, fixed when it is created; NULL: it acts on its own.
    on_behalf_of UUID        NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by   UUID        REFERENCES users(id) ON DELETE SET NULL,
    revoked_at   TIMESTAMPTZ NULL,
    revoked_by   UUID        REFERENCES users(id) ON DELETE SET NULL,
    -- The assistant acts for whoever is signed in, never for one fixed person.
    CONSTRAINT agents_only_external_acts_for_a_fixed_person CHECK (kind = 'external' OR on_behalf_of IS NULL)
);

-- Exactly one Knovas Assistent.
CREATE UNIQUE INDEX IF NOT EXISTS idx_agents_one_assistant ON agents (kind) WHERE kind = 'assistant';

CREATE TABLE IF NOT EXISTS tenant_role_agents (
    role_id  UUID        NOT NULL REFERENCES tenant_roles(id) ON DELETE CASCADE,
    agent_id UUID        NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    added_by UUID        REFERENCES users(id) ON DELETE SET NULL,
    PRIMARY KEY (role_id, agent_id)
);

-- "The roles of an agent": every claim reads it; it also serves the cascade from agents.
CREATE INDEX IF NOT EXISTS idx_tenant_role_agents_agent ON tenant_role_agents (agent_id);

-- ── the seed ──────────────────────────────────────────────────────────────

INSERT INTO agents (name, kind)
SELECT 'Knovas Assistent', 'assistant'
WHERE NOT EXISTS (SELECT 1 FROM agents WHERE kind = 'assistant');

-- A member of every role but Administration.
INSERT INTO tenant_role_agents (role_id, agent_id)
SELECT r.id, a.id
FROM tenant_roles r
CROSS JOIN agents a
WHERE a.kind = 'assistant' AND r.kind <> 'administration'
ON CONFLICT DO NOTHING;
