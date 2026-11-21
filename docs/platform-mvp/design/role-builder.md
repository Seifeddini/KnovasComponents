# Role builder ("Rollen"): design

State on 21 November 2026, with the founder's answers of the same day (§ 9). Plan `FEAT-2026-11-19-platform-mvp`
(KnowledgeBase `docs/ModernDocs/plans/2026-11-19-platform-mvp.md`: § 4 Admin A-01 to A-10, § 9 PMVP-40 to PMVP-43,
§ 10). Mockup: [`../reference-screens/07-rollen.html`](../reference-screens/07-rollen.html), rendered as
`out/07-rollen.png`. Core changes: [`../core-requests.md`](../core-requests.md). **Everything marked "proposed" is a
design. None of it is built.**

**The founder's request.** Create only the roles that are minimally required: one for administration and one for
everyone else. The page lets a tenant build its own access control: create roles and assign them to people, resources
and agents. The role builder is part of the pilot. One role decides both **functions** (pages and actions) and
**data** (sources, folders, documents), built on the two mechanisms that exist today, so the Core keeps enforcing data
access. Members are people, the Knovas Assistent and external AI agents. External agents are treated the same as the
Knovas Assistent, and some clients will have only external agents. Connectors and sync are out of scope.

**Citations.** Platform paths are under `KnovasPlatform/components/docbridge_integration/src/` on `feat/platform-mvp`.
`KB/` is KnowledgeBase `master` (`be344ad23`), and `S/` is `KB/knovas-software/app/src/`. Every claim comes from
reading the code; nothing was run.

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

- The internal MCP server uses the tenant's certificate and is locked to the Knovas tenant
  (`KB/knovas-software/client/knovas_mcp/mtls.py:1-16`). It allows only the search, `GET` reads and the facts batch
  (`:13-15`).
- The model chooses `access_groups` on each call (`knovas_mcp/server.py:79-80`). There is no signed claim, so under
  `brokered` every call is refused (`principal_resolver.py:165-167`).
- DEC-0077 (branch `dec-0077-signed-group-claims`) says "a signed claim names a person" and leaves an agent's identity
  to AGB-01 (`:73-74`). AGB-01 asks that agents never hold the tenant's certificate
  (`KB/docs/ModernDocs/steering/bets/BET-2026-11-agents-on-the-brain.yaml:85`).

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
**Its requests carry a claim the Platform signs: `sub` = the agent's opaque id, `grp` = the groups of the agent's
roles, narrowed to the person's groups when the agent acts for a person.** The request also declares
`{kind: agent, id, on_behalf_of}` for the Core's log. The agent's functions gate which tools it may use, the same way
a person's functions gate pages.

- **The assistant** always acts for the signed-in person. After the migration it is a member of every role, so
  nothing changes until an administrator takes it out of a role, for example "Personal" (A-10, "may be used by AI").
- **An external agent** acts either for one person, fixed when its key is issued, or on its own (only its roles'
  groups). It starts with no role. The first case never reads more than that person, and deactivating the person stops
  the agent.

Why a claim and not a filter on returned passages:

1. **The Core stays the only judge.** A filter would need a second copy of the policy, which the code rejects by name
   (`knovas_client.py:3326-3331`).
2. **It never shows more.** Fewer groups never read more, because visibility is monotone (`access_policy.py:32-33`).
3. **It needs no Core code change.** The verifier accepts any subject that is not empty.

**Limit.** The narrowing acts only under `brokered` (CR-R1). With enforcement "Aus", agents, like people, read
everything the tenant holds, and the role page says so.

## 5. External agents: through the Platform (recommended)

**Recommended: external agents reach the Brain through the Platform, the way the assistant does.**

- The agent authenticates to the Platform with a key issued in the role builder.
- The Platform checks the agent's functions, signs the claim of § 4 and calls the Core over its own certificate.
- The Platform serves a remote MCP endpoint with the reads the internal server allows today (`mtls.py:13-15`), so
  Claude Code or any other MCP client connects with a URL and a key.

| | **Through the Platform** (recommended) | Platform-signed claim, agent calls the Core directly |
|---|---|---|
| Credentials per agent | one key at the Platform; the tenant certificate never leaves the Platform (meets AGB-01) | a Platform key plus its own Core credential, since the tenant certificate is ruled out (AGB-01) |
| Enforcement | the assistant's code path and rule | the same claim, minted on request |
| Revocation | one place, effective within one claim lifetime | two places |
| Core work | none in code (CR-R5 becomes a decision text; CR-R6 falls away) | CR-R5 (agent credential, verified actor) and CR-R6 (MCP attaches the claim) |
| Clients with external agents only | works: the gateway does not depend on the assistant's switch | works, after the Core work |
| Cost | the Platform proxies agents' reads (it already proxies every person's) and must be reachable from where the agent runs; new dependency `mcp` (not in `requirements.txt` today) | no proxy |
| By 15 December | yes, Platform work only | no |

**What it needs.**

- **In the Platform:**
  - an agent register, with a hashed key, an end date and a way to revoke it, and optionally the person it acts for;
  - the claim of § 4;
  - the MCP endpoint, with an audit line per call (ids only) and a rate limit.
- **In the Core:**
  - no code;
  - DEC-0077's "a signed claim names a person" extended to "or an agent registered on the Platform";
  - CR-R1 for the narrowing to act.
- **On the pilot tenant:** the internal MCP server stops at `brokered` (§ 2.4), and Claude Code moves to the Platform's
  endpoint.

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
   - Proposed: role, function, member and agent-key changes write their audit row, with before and after, in the same
     transaction. Data changes write it after the Core answers, with the outcome.
5. **A change reaches the Core before it counts.**
   - Data: the Core write comes first, and the page shows what the Core reads back, as `move` does
     (`builder/access_backend.py:42-47`).
   - A new role: its group is created first.
   - Members and keys: a change counts from the next request, at most 120 seconds later.
   - Data changes keep the four-eyes path `acl_change` (`admin_documents.py:341-349`).
6. **Agents never read more than their roles and, when acting for a person, than that person.** An agent's key is
   shown once, stored hashed, ends on a date and can be revoked.
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
| RB-5 | Data: add or remove a source, folder or document; dry run; four eyes | sensitive | 5 | RB-4 | CR-R1, CR-R2; new folders over existing documents: CR-R3 |
| RB-6 | One claim for agents (§ 4); the assistant as a member | sensitive | 3 | RB-2, PMVP-20 | narrowing: CR-R1 |
| RB-7 | Agent register and keys (hashed, end date, revoke, acting for a person) | critical | 5 | RB-4 | — |
| RB-8 | The agent gateway: the Platform's MCP endpoint, audit per call, rate limit | critical | 8 | RB-6, RB-7 | narrowing: CR-R1 |
| RB-9 | Probe view per role | sensitive | 3 | PMVP-42 | CR-R4 |
| RB-10 | Split `admin` into finer functions | sensitive | 5 | RB-2 | — |

**By 15 December:** RB-1 to RB-8, 35 points.

- Functions act at once.
- Data (RB-5) and the narrowing for the assistant and agents (RB-6, RB-8) are built, but act on the pilot only once
  CR-R1 and CR-R2 have landed and the Core's rights test has passed twice (DEC-0082). Until then they are saved and
  shown as "wirkt mit «Ein»".
- RB-9 and RB-10 come later.
- RB-3 and RB-4 start after PMVP-40, which changes the same people page, is merged.

**If a gate is missed, cut in this order:**

1. RB-8 offers search only.
2. RB-5 adds only walled folders and documents, so it never walls an open folder.
3. RB-3 and RB-4 use the current Admin look.

**Never cut:** the last-administrator rule, and agents reading no more than their roles and their person.

## 8. Core changes now needed (details in `../core-requests.md`)

| No. | Status with this design |
|---|---|
| CR-R1 | **Needed for the pilot**: the posture route accepts `brokered`. Without it no role's data and no agent's narrowing act |
| CR-R2 | **Needed for the pilot**: the folder-rule routes take the signed claim under `brokered` |
| CR-R3 | Needed: a new folder rule reaches documents already read in |
| CR-R4 | Later: Probe |
| CR-R5 | **No code.** A decision text instead: DEC-0077 allows a Platform-registered agent as a claim's subject |
| CR-R6 | **Not needed**, because agents go through the Platform |

## 9. The founder's answers (21 November) and what is still open

| No. | Answer |
|---|---|
| 1 | Granting oneself a role needs no second administrator. The last-administrator rule stays |
| 2 | A new installation has only the two roles; the other keys are functions only (§ 3 for existing installations) |
| 3 | No deny |
| 4 | The Platform signs agents' identity |
| 5 | External agents are in the pilot and are enforced like the assistant (§ 4, § 5) |

**Still open, with a recommendation:**

- **The agent's key.** Recommended: a key per agent, sent as a header to the Platform's MCP endpoint, valid for at
  most 90 days. The alternative is a client certificate to the Platform.
- **DEC-0077's text.** Recommended: amend it in KnowledgeBase, which is a decision only (CR-R5).
