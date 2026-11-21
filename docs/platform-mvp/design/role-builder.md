# Role builder ("Rollen"): design

State on 21 November 2026, with the founder's answers of the same day (§ 9); answers 6 and 7 ("Core gateway only",
"only after «Ein»") came after the first draft of RB-6 to RB-8. Plan `FEAT-2026-11-19-platform-mvp` (KnowledgeBase
`docs/ModernDocs/plans/2026-11-19-platform-mvp.md`: § 4 Admin A-01 to A-10, § 9 PMVP-40 to PMVP-43, § 10).
Mockup: [`../reference-screens/07-rollen.html`](../reference-screens/07-rollen.html), rendered as `out/07-rollen.png`.
Core changes: [`../core-requests.md`](../core-requests.md). **Everything marked "proposed" is a design. None of it is
built.**

**The founder's request.** Create only the roles that are minimally required: one for administration and one for
everyone else. The page lets a tenant build its own access control: create roles and assign them to people, resources
and agents. The role builder is part of the pilot. One role decides both **functions** (pages and actions) and
**data** (sources, folders, documents), built on the two mechanisms that exist today, so the Core keeps enforcing data
access. Members are people, the Knovas Assistent and external AI agents. External agents are treated the same as the
Knovas Assistent, and some clients will have only external agents. Connectors and sync are out of scope.

**Citations.** Platform paths are under `KnovasPlatform/components/docbridge_integration/src/` on `feat/platform-mvp`.
`KB/` is KnowledgeBase `master` (`be344ad23`; for § 2.4 and § 5 `1d08d383f`), `S/` is `KB/knovas-software/app/src/`,
and `G/` is `KB/knovas-software/client/knovas_mcp/gateway/`. Every claim comes from reading the code; nothing was run.

## 1. What a role is (proposed)

| Part | What it holds | Where it is enforced |
|---|---|---|
| Name, description | plain words; the name is unique | — |
| **Darf tun** (functions) | a closed list: today's ten role keys, in plain words | the Platform's route gates |
| **Darf lesen** (data) | sources, folders, documents | the Core: the role's own access group, named in folder rules and document rights |
| **Mitglieder** | people, the Knovas Assistent, external agents | people: their signed claim on every request. Agents (the assistant and external ones): **one rule**, the claim the Platform signs with the groups of the agent's roles, narrowed to the person's groups when the agent acts for a person (§ 4, § 5) |

A new installation has exactly two roles, **Administration** and **Mitglied**, and they cannot be deleted. Every other
role is created by the tenant.

## 2. What exists, and what is missing

### 2.1 Functions: the Platform's route gates

**Exists.** The table `roles` holds ten keys (`identity/migrations/0001_identity.sql:50-67`,
`0003_experiments.sql:18-23`, `0016_builder_team.sql:19-24`), granted in `user_roles` (`0001:69-75`). The schema
says they decide what a user may *do* and mean nothing to Knovas (`0001:8-14`). Gates check them:

- the console's `_require_roles` (`web_interface/admin.py:54-85`), whose set the menu reads as `allowed_roles`
  (`web_interface/app.py:1768-1778`);
- the Builder's permission map (`builder/team.py:27-35`, `web_interface/builder_routes.py:58-88`);
- Experimente (`experiments/permissions.py:17-21`);
- settings, the schema editor and the styleguide (`web_interface/settings_routes.py:53-64`, `graph_routes.py:54-69`,
  `shell_routes.py:190-202`).

Search and the assistant need only a sign-in (`identity/webauth.py:140-163`, `web_interface/assistant_routes.py:177-180`);
no gate checks `member`. The people page offers the keys as roles (`web_interface/admin.py:35-38`,
`templates/admin_people.html:149`). A person's keys are read in one place, `UserRepository.roles_of`
(`identity/users.py:166, 325-331`).

**Missing in the Platform (proposed).**

- Roles as bundles: their tables and their page.
- **The ten keys become functions only. They are never roles of their own and never offered on the people page,
  which offers roles.**
- `roles_of` returns the keys of the person's roles, so **no gate changes** and the menu works as it is.
- Names for the functions: Fragen und suchen (`member`), Verwaltung (`admin`), Freigaben bestätigen (`approver`),
  Einlesen (`ingestion_manager`), Experimente, Experimente verwalten, and Builder lesen, bearbeiten, prüfen,
  veröffentlichen (the four `builder_*` keys).
- `admin` stays one coarse function until it is split (RB-10).

**Missing in the Core.** Nothing. The Core parses the claim `rol` and never uses it (`S/services/rbac/assertion.py:216-222`).

### 2.2 Data: the Core's access groups and document rights

**Exists in the Core.**

- **Groups.** They form a tree; a document's readers are its group and that group's ancestors
  (`S/services/rbac/access_policy.py:8-9`). One administrator group per tenant may change every ACL but grants no
  reading (`S/services/rbac/models.py:63-69`).
- **Folder rules.** A rule is a prefix with a set of groups. The longest prefix wins and alone governs each document
  (`S/services/rbac/folder_rule_service.py:22-24`). An empty set means open (`models.py:346-347`).
- **Document rights.** Setting them replaces a document's whole set (`knovas_client.py:3352-3376`) and takes the
  document out of its folder rule (`:3367-3368`).
- **Who may assign.** A caller may assign only groups at or below her own (`S/api/secure_api.py:4677-4689`).
- **Limits.**
  - A group that is in use cannot be deleted (`S/services/rbac/group_service.py:235-255`).
  - Groups cannot be moved (`KB/docs/ModernDocs/components/L1-identity/L2-rbac/README.md:76`).
  - The Core stores no members.
  - There is no deny: the filter has no negation (`S/services/rbac/acl_filter_builder.py:24-26`).
- **When a rule takes effect.** A **new** folder rule reaches documents already read in only through a backfill that
  staff run (`S/services/rbac/acl_backfill_worker.py:1-8`). Changing an existing rule applies at once
  (`secure_api.py:4706-4711`).

**Exists in the Platform.**

- Who holds which group, with an end date (`0001_identity.sql:85-92`, `0018_access_window.sql:14`).
- The console's routes for groups, folder rules and document rights. They sit behind the admin gate and the four-eyes
  path `acl_change` (`web_interface/admin_documents.py:151-206, 325-364, 696-803`).

**Missing in the Platform (proposed).**

- One Core group per role (§ 3).
- "Daten hinzufügen" through the existing calls.
- A dry run that shows who gains sight and who loses it, as the Builder's access window already computes
  (`builder/access.py:11-12`).

**Missing in the Core.** CR-R2 and CR-R3.

### 2.3 People and agents: the signed claim

**Exists.**

- **The claim.** For every request the Platform signs `sub`, `tid`, `grp` and `rol` with the tenant's key, for 120
  seconds and at most 300 (`identity/assertion.py:65-66, 157-197`). The groups are read fresh each time
  (`identity/principal.py:22-24, 54-69`).
- **Who is signed.** The claim is attached to every secured request, for the signed-in person or for whomever
  `asserting_for` names (`knovas_client.py:1759-1776, 1815-1843`).
- **The subject.** The Core requires only that `sub` is not empty (`S/services/rbac/assertion.py:211-212`). It
  records a declared actor `{kind: agent, id, on_behalf_of}` on `/secured/query` and the graph reads
  (`S/services/request_actor.py:59-69`) and decides nothing on it (`:1-8`).
- **When the Core verifies the claim.** Only in the posture `brokered` (`S/services/rbac/principal_resolver.py:158-190`).
  - In `enforcing` it reads the body field `access_groups` instead (`:192-200`).
  - The Platform never sends that field (`identity/principal.py:37-39`), so every Platform search would see open
    documents only (`principal_resolver.py:51-53`).
  - No route can switch a tenant to `brokered` (`S/api/internal_api.py:2103-2142`).
- **The cap.** A claim holds at most 32 groups (`models.py:53`).

**Missing in the Platform (proposed).**

- `access_groups_of` (`identity/users.py:360-367`) returns the groups of the person's roles.
- A 33rd group is refused.
- The claim for an agent (§ 4).

**Missing in the Core.** CR-R1. Without it "Ein" cannot work for the Platform, with roles or without.

### 2.4 External agents today

- **The internal MCP server** (stdio, Claude Code at Knovas) holds the Knovas tenant's certificate
  (`KB/knovas-software/client/knovas_mcp/mtls.py:1-16`); the model chooses `access_groups` on each call
  (`server.py:79-80`); under `brokered` every call is refused (`principal_resolver.py:165-167`).
- **The Core's MCP gateway** (`https://mcp.knovas.ch/mcp`, FEAT-2026-11-16-copilot-mcp-gateway, DEC-0078): stateless
  Streamable HTTP on cloudscale, tools `search` and `fetch` (`G/app.py:1-16`).
  - **Authentication:** a company key per tenant (`knovas_gw_<key id>_<secret>`, Bearer or `X-API-Key`), stored as
    `sha256(secret)` in `mcp_gateway_keys` (tenant, at most 365 days, revoke), compared with `hmac.compare_digest`
    (`G/keys.py:1-40`). Knovas staff issue it (`S/CLI/manage_mcp_keys.py`). It names a tenant, not a person or agent.
  - **Groups:** none. Each call is `POST /secured/query` over the tenant's certificate with `Input`, `limit`,
    `context_budget` 0 and the actor `mcp-gateway.<key id>`: **no `access_groups`, no `principal_assertion`, ever**
    (`G/search.py:1-12, 67-69`). So it reads as a caller without groups (GI-ACCESSROLES-06): everything under
    `disabled`, open documents under `enforcing`, and under `brokered` every call is refused (GI-BROKER-02).
- The Core's verifier burns each claim's `jti` and refuses a lifetime over 300 seconds (`S/services/rbac/assertion.py`).
  DEC-0077 says "a signed claim names a person"; AGB-01 asks that agents never hold the tenant's certificate.

## 3. How a role becomes the Core's groups; new and existing installations (proposed)

- **One role, one Core group.** "Rolle anlegen" first creates a root group in the Core (`knovas_client.py:3284-3290`).
  The role is saved only when the Core answers. A rename renames the group, and the group's id stays
  (`knovas_client.py:3297-3298`). Roles do not nest, because groups cannot move. A tenant with a tree may *adopt* an
  existing group as a role's group.
- **Data** are the folder rules and document rights naming the role's group. The Platform keeps no copy; it reads them
  from the Core, as its group cache is "never authoritative" (`0001_identity.sql:96-98`).
  - Adding a **walled** folder adds the role's group to that folder's rule. Nobody loses sight.
  - Adding an **open** folder would wall it off from everyone else. The dry run says so and asks first.
- **Members** live in the Platform and reach the Core in the next claim.
- **Deleting a role** goes in this order: its data (with a dry run), its members, its Core group (the Core refuses to
  delete one in use), then the role.
- **A new installation** starts with exactly two roles:
  - **Administration**: function `admin`, which passes the console's, the Builder's and the Experimente module's gates
    (`admin.py:83-85`, `builder/team.py:30`, `experiments/permissions.py:17-21`). Its group is the tenant's
    administrator group, which reads nothing walled.
  - **Mitglied**: function `member`. Its group is a new root group "Mitglied", made by a start-up step and assigned
    to nothing. Every person is a member.
- **An existing installation** ends in the same state, and nobody loses a function:
  - The two roles are created as above. Everyone holding `admin` joins Administration; every person joins Mitglied.
  - For each **other set of keys** that people hold, one role is created, named after its functions (for example
    «Freigabe»), and those people join it.
  - Administrators' other keys count too, because some checks name a key exactly and `admin` does not pass them
    (`experiments/schema.py:54`, custom views `custom_views/manifest.py:38-41`).
  - An installation where nobody holds another key ends with exactly the two roles.
  - `user_roles` is no longer read; it is kept untouched for one release as the way back.
  - Group grants (`user_access_groups`) and per-workspace Builder grants (`0016_builder_team.sql:11-16`) stay as they
    are.

## 4. One rule for every agent

An agent is a member that is not a person: the Knovas Assistent, or an external agent registered in the role builder.
**Every claim the Core accepts for an agent is signed by the Platform: `sub` = the agent's opaque id, `grp` = the
groups of the agent's roles, narrowed to the person's groups when the agent acts for a person** (founder, 21 November:
"the Platform signs agents' claims"). The agent's functions ride in `rol`; reading needs no function, as for people.

- **The assistant** goes through the Platform and always acts for the signed-in person; each of its Core calls carries
  its own claim and declares `{kind: agent, id, on_behalf_of}`. It is a member of every role but Administration, also
  of roles created later, until an administrator takes it out of one, for example "Personal" (A-10).
- **An external agent** goes through the Core's gateway (§ 5). It acts for one person, fixed when it is registered,
  or on its own (only its roles' groups), and starts with no role. Deactivating its person stops it.
- **No agent joins Administration**, and a person's direct group grants reach no agent: only roles carry data to
  agents (FU-41: RB-5 moves direct grants into roles before «Ein»).

A claim, not a filter on returned passages: the Core stays the only judge (a filter would copy the policy, which
`knovas_client.py:3326-3331` rejects by name), fewer groups never read more (`access_policy.py:32-33`), and the Core's
verifier already accepts any subject that is not empty.

**Limit.** The narrowing acts only under `brokered` (CR-R1); with "Aus", agents, like people, read everything, and the
role page says so. **On client tenants agents are switched on only after CR-R1 and «Ein»** (founder, 21 November); the
Knovas GmbH pilot may use them before, as it uses Claude Code today.

## 5. External agents: through the Core's gateway (founder: "Core gateway only")

The Platform has **no** MCP endpoint; external agents use the Core's gateway (§ 2.4), which must learn the Platform's
roles. The agent is known to both by **one id**, the Platform's agent id: on the Platform it is a member of roles,
perhaps bound to a person (RB-7); at the Core it is written on the agent's gateway key (CR-R6) and is the claim's
`sub`. The Platform holds no credential and decides the groups; the gateway holds the key's hash, as today, and decides
nothing: it carries a Platform-signed claim to the Secure API.

**Chosen: the Platform hands the Core a pool of the agent's signed claims ahead of use.**

1. Every minute, and at once after a change on the agent pages, the Platform signs ten claims per live external agent
   (each 300 seconds, each its own `jti`) and sends them with `PUT /secured/agents/<agent id>/claims`, replacing the
   agent's pool. A revoked agent, an agent whose person is not active and an agent over 32 groups get
   `DELETE …/claims` instead. Behind the switch `AGENTS_CORE_GATEWAY`, off until the Core has CR-R5.
2. Knovas issues the agent's gateway key with the agent's id (`manage_mcp_keys issue --agent <id>`, CR-R6), only for a
   `brokered` tenant.
3. On each call with such a key the gateway takes one unused claim of that agent and sends it as `principal_assertion`
   with the actor `{kind: agent, id: <agent id>}`. The Secure API verifies it exactly as it verifies a person's claim
   today and burns its `jti`. No claim left: the tool says no rights are current, and nothing is read.

Why: the Core's verifier and enforcement stay as they are, the Platform needs no inbound connection, a change counts
at the next push (at once from the agent pages, else within a minute, at most 300 seconds if the Platform is down),
and the pool bounds an agent to about ten calls a minute. Rejected: the gateway signing per call with a key of its own
(it would mint principals, and the verifier would change); the gateway asking the Platform per call (a client's
Platform is not reachable from cloudscale). **Cost:** the Core holds bearer claims for at most 300 seconds, readable
and deletable only by the gateway's role; one push per agent a minute. On the Knovas tenant Claude Code moves from the
internal server, which stops at `brokered`, to the gateway with an agent key.

## 6. Safety rules (proposed, each with its control)

1. **No role grants more than its creator holds.**
   - Functions: a creator may add only those she holds; the Platform checks this.
   - Data: the Core checks that she dominates the group (`secure_api.py:4677-4689`), so only Administration attaches
     data.
   - Granting oneself a role needs no second person (founder, 21 November).
2. **No deny** (founder, 21 November). Roles only grant; a wall is made by leaving a role out of a folder's rule, and a
   deeper rule replaces the one above (`folder_rule_service.py:22-24`). The plan's "A-01 to A-04: Exist" is not true
   for "a denial wins".
3. **The last administrator keeps the role.**
   - Missing today: `set_roles` can remove the last `admin` (`admin.py:242-262`); only deactivating oneself is refused
     (`admin.py:212-219`).
   - Proposed: refuse any change that leaves Administration without an active person.
4. **Every change is audited.**
   - `audit.record` is best effort and never blocks (`identity/audit.py:23-45`), and the Core logs ids only.
   - Proposed: role, function, member and agent changes write their audit row by id, with before and after, in the
     same transaction. Data changes write it after the Core answers, with the outcome.
5. **A change reaches the Core before it counts.**
   - Data: the Core write comes first, and the page shows what the Core reads back, as `move` does
     (`builder/access_backend.py:42-47`).
   - A new role: its group is created first.
   - Members: a change counts from the next request, at most 120 seconds later; for an external agent at the
     next push of its claims (at once from the agent pages, at most 300 seconds later).
   - Data changes keep the four-eyes path `acl_change` (`admin_documents.py:341-349`).
6. **Agents never read more than their roles and, when acting for a person, than that person.** The Platform holds
   no agent credential; the gateway key is the Core's (hashed, an end date, revoke). Revoking an agent on the Platform
   drops its claims at once.
7. **Aus, Probe, Ein.**
   - **Aus** (`disabled`): functions act; data are saved in the Core and hide nothing; the page says so.
   - **Probe** does not exist in the Core; with CR-R4 the page shows what each role would hide.
   - **Ein** must be `brokered` (CR-R1). The Platform's own posture requests know only `disabled` and `enforcing`
     (`0018_access_window.sql:19`).

## 7. Items

Points are an AI first pass on the plan's scale (1, 2, 3, 5, 8).

| Item | What | Class | Pts | Depends on | Acts only with |
|---|---|---|---|---|---|
| RB-1 | Role tables (new tables only); a new installation gets the two roles; the existing-installation migration (§ 3) | sensitive | 3 | — | — |
| RB-2 | `roles_of` and `access_groups_of` read roles; the 32-group cap; gate tests for every function | sensitive | 3 | RB-1 | data part: CR-R1 |
| RB-3 | Rollen page, read only (`07-rollen`), behind the admin gate | routine | 3 | RB-1, PMVP-12, PMVP-40 | — |
| RB-4 | Create, rename and delete roles; functions; people as members; the people page offers only roles; rules 1, 3, 4, 5 | sensitive | 5 | RB-2, RB-3 | — |
| RB-5 | Data: add or remove a source, folder or document; dry run; four eyes; direct grants into roles (FU-41) | sensitive | 5 | RB-4 | CR-R1, CR-R2; new folders over existing documents: CR-R3 |
| RB-6 | One claim for agents (§ 4); the assistant as a member of every role but Administration | sensitive | 3 | RB-2, PMVP-20 | narrowing: CR-R1 |
| RB-7 | External agents as role members (register, pages); their signed claims pushed to the Core behind a switch (§ 5) | critical | 5 | RB-6 | delivery: CR-R5; use: CR-R6; narrowing: CR-R1 |
| RB-9 | Probe view per role | sensitive | 3 | PMVP-42 | CR-R4 |
| RB-10 | Split `admin` into finer functions | sensitive | 5 | RB-2 | — |

RB-8 (a Platform MCP endpoint) is dropped ("Core gateway only"); its work is the Core's CR-R5 and CR-R6.

**By 15 December:** RB-1 to RB-7, 27 points.

- Functions act at once.
- Data (RB-5), the assistant's narrowing (RB-6) and external agents (RB-7) are built, but act on the pilot only once
  CR-R1 and CR-R2 have landed and the Core's rights test has passed twice (DEC-0082); external agents also need CR-R5
  and CR-R6. Until then they are saved and shown as "wirkt mit «Ein»", and `AGENTS_CORE_GATEWAY` stays off.
- RB-9 and RB-10 come later.

**If a gate is missed, cut in this order:**

1. RB-7's own agent page: agents are registered and put into roles from the Rollen page only.
2. RB-5 adds only walled folders and documents, so it never walls an open folder.
3. RB-3 and RB-4 use the current Admin look.

**Never cut:** the last-administrator rule, and agents reading no more than their roles and their person.

## 8. Core changes now needed (details in `../core-requests.md`)

| No. | Status with this design |
|---|---|
| CR-R1 | **Needed for the pilot**: the posture route accepts `brokered`. Without it no role's data and no agent's narrowing act, and no external agent is switched on for a client |
| CR-R2 | **Needed for the pilot**: the folder-rule routes take the signed claim under `brokered` |
| CR-R3 | Needed: a new folder rule reaches documents already read in |
| CR-R4 | Later: Probe |
| CR-R5 | **Needed for external agents**: `PUT` and `DELETE /secured/agents/<agent id>/claims`, the pool of an agent's Platform-signed claims; DEC-0077 amended so a claim may name a Platform-registered agent |
| CR-R6 | **Needed for external agents**: a gateway key bound to an agent id; the gateway sends one pooled claim per call |

## 9. The founder's answers (21 November) and what is still open

| No. | Answer |
|---|---|
| 1 | Granting oneself a role needs no second administrator. The last-administrator rule stays |
| 2 | A new installation has only the two roles; the other keys are functions only (§ 3 for existing installations) |
| 3 | No deny |
| 4 | The Platform signs agents' identity |
| 5 | External agents are in the pilot and are enforced like the assistant (§ 4, § 5) |
| 6 | **Core gateway only**: the Platform gets no MCP endpoint; external agents use the Core's gateway, which learns the Platform's roles (§ 5) |
| 7 | **Only after «Ein»**: on client tenants agents are switched on once CR-R1 has landed and enforcement is on; the Knovas GmbH pilot may use them before, as it uses Claude Code today |

**Still open, with a recommendation:**

- **Who issues an agent's gateway key:** Knovas staff with the agent's id for the pilot; later a self-service route.
- **DEC-0077's text:** amend it in KnowledgeBase (CR-R5).
- **The RCR Copilot pilot** (`BET-2026-11-rcr-copilot`) uses a company key without groups, which answer 7 does not
  cover as written: the founder decides whether it waits for «Ein».
