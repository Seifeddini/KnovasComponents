# Role builder ("Rollen"): design

State on 21 November 2026. Plan `FEAT-2026-11-19-platform-mvp` (KnowledgeBase
`docs/ModernDocs/plans/2026-11-19-platform-mvp.md`: § 4 Admin A-01 to A-10, § 9 PMVP-40 to PMVP-43, § 10). Mockup:
[`../reference-screens/07-rollen.html`](../reference-screens/07-rollen.html), rendered as `out/07-rollen.png`.
**Everything marked "proposed" is a design. None of it is built.**

**The founder's request (21 November).** Create only the roles that are minimally required (one for administration,
one for everyone else). The page lets a tenant build its own access control: create roles and assign them to people,
resources and agents. The role builder is part of the pilot. One role decides both **functions** (pages and actions)
and **data** (sources, folders, documents). It is built on the two mechanisms that exist today, so the Core keeps
enforcing data access. Members are people, the Knovas Assistent, and external AI agents that read the Brain through
the API. Connectors and sync are out of scope.

**Citations.** Platform paths are under `KnovasPlatform/components/docbridge_integration/src/` on `feat/platform-mvp`
(`e7c15215`). `KB/` is KnowledgeBase `master` (`be344ad23`), and `S/` is `KB/knovas-software/app/src/`. Every claim
comes from reading the code; nothing was run.

## 1. What a role is (proposed)

| Part | What it holds | Where it is enforced |
|---|---|---|
| Name, description | plain words; the name is unique | — |
| **Darf tun** (functions) | a closed list: today's ten role keys, in plain words | the Platform's route gates |
| **Darf lesen** (data) | sources, folders, documents | the Core: the role's own access group, named in folder rules and document rights |
| **Mitglieder** | people, the Knovas Assistent, external agents | people: their signed claim on every request; the assistant: the person's claim narrowed to the assistant's roles (§ 4); agents: a signed claim of their own, which needs the Core (§ 5) |

Two roles are built in and cannot be deleted: **Administration** and **Mitglied**. All other roles are the tenant's.

## 2. What exists, and what is missing

### 2.1 Functions: the Platform's route gates

**Exists.** The table `roles` holds ten keys (`identity/migrations/0001_identity.sql:50-67`,
`0003_experiments.sql:18-23`, `0016_builder_team.sql:19-24`), granted in `user_roles` (`0001:69-75`). The schema
says they decide what a user may *do* and mean nothing to Knovas (`0001:8-14`). Gates check them: the console's
`_require_roles` (`web_interface/admin.py:54-85`), whose set the menu reads as `allowed_roles`
(`web_interface/app.py:1768-1778`); the Builder's permission map (`builder/team.py:27-35`,
`web_interface/builder_routes.py:58-88`); Experimente (`experiments/permissions.py:30-34`); settings, schema editor
and styleguide (`web_interface/settings_routes.py:53-64`, `graph_routes.py:54-69`, `shell_routes.py:190-202`). The
Builder maps view profiles to role keys (`builder/review.py:537-546`). Search and the assistant need only a sign-in
(`identity/webauth.py:140-163`, `web_interface/assistant_routes.py:177-180`); no gate checks `member`. Six keys have
plain words (`web_interface/templates/admin_people.html:23-30`). A person's keys are read in one place,
`UserRepository.roles_of` (`identity/users.py:166, 325-331`).

**Missing in the Platform (proposed).** Roles as bundles: tables and a page. **The ten keys become the list of
functions, and no gate changes:** `roles_of` returns the person's direct keys plus the keys of her roles, so every gate
and the menu work as they are. Names: Fragen und suchen (`member`), Verwaltung (`admin`), Freigaben bestätigen
(`approver`), Einlesen (`ingestion_manager`), Experimente and Experimente verwalten, Builder lesen, bearbeiten, prüfen,
veröffentlichen (the four `builder_*`). `admin` stays one coarse function until it is split (RB-10).

**Missing in the Core.** Nothing. The Core parses the claim `rol` and never uses it (`S/services/rbac/assertion.py:216-222`).

### 2.2 Data: the Core's access groups and document rights

**Exists in the Core.**
- Groups form a tree; a document's readers are its group and that group's ancestors
  (`S/services/rbac/access_policy.py:8-9`). One administrator group per tenant may change every ACL but grants no
  reading (`S/services/rbac/models.py:63-69`).
- A folder rule is a prefix with a set of groups; the longest prefix wins and governs each document alone
  (`S/services/rbac/folder_rule_service.py:22-24`); an empty set means open (`models.py:346-347`). Document rights
  replace a document's whole set (`knovas_client.py:3352-3376`) and take it out of its folder rule (`:3367-3368`).
- A caller may assign only groups at or below her own (`S/api/secure_api.py:4677-4689`); any holder of the tenant
  certificate may create a group (`:4236-4270`). A group in use cannot be deleted
  (`S/services/rbac/group_service.py:235-255`); groups cannot be moved
  (`KB/docs/ModernDocs/components/L1-identity/L2-rbac/README.md:76`). The Core stores no members.
- **There is no deny.** The filter is a disjunction without negation (`S/services/rbac/acl_filter_builder.py:24-26`);
  deny rules were out of scope (`KB/docs/ModernDocs/plans/2026-07-27-client-rbac-access-groups.md:47`).
- A **new** folder rule reaches documents already read in only through a backfill that staff run from the command line
  (`S/services/rbac/acl_backfill_worker.py:1-8`); changing an existing rule's groups applies at once
  (`secure_api.py:4706-4711`).

**Exists in the Platform.** Who holds which group, with an end date (`identity/migrations/0001_identity.sql:85-92`,
`0018_access_window.sql:14`); the console's routes for groups, folder rules and document rights, behind the admin gate
and the four-eyes path `acl_change` (`web_interface/admin_documents.py:151-206, 325-364, 696-803`).

**Missing in the Platform (proposed).** One Core group per role (§ 3); "Daten hinzufügen" through the existing calls;
a dry run that shows who gains and who loses sight, which the Builder's access window already computes
(`builder/access.py:11-12`).

**Missing in the Core.** CR-R2 and CR-R3 (§ 7).

### 2.3 People: the signed claim

**Exists.** For every request the Platform signs `sub`, `tid`, `grp`, `rol` with the tenant's key, for 120 seconds,
at most 300 (`identity/assertion.py:65-66, 157-197`), with groups read fresh from the database
(`identity/principal.py:22-24, 54-69`), attached to every secured request (`knovas_client.py:1815-1843`). The Core
verifies it **only in the posture `brokered`** (`S/services/rbac/principal_resolver.py:158-190`). In `enforcing` it
reads the body field `access_groups` instead (`:192-200`), which the Platform never sends
(`identity/principal.py:37-39`), so every Platform search sees open documents only (`principal_resolver.py:51-53`).
The posture route is staff-only and accepts `enforcing` and `disabled`, not `brokered`
(`S/api/internal_api.py:2103-2142`). A claim holds at most 32 groups (`models.py:53`).

**Missing in the Platform (proposed).** `access_groups_of` (`identity/users.py:360-367`) returns the direct grants
plus the groups of the person's roles; a 33rd group is refused.

**Missing in the Core.** CR-R1, a way to switch a tenant to `brokered`. Without it "Ein" cannot work for the Platform,
with roles or without.

### 2.4 The Knovas Assistent

**Exists.** It searches through the search page's path, as the signed-in person (`assistant/core.py:17-27, 94-112`);
`/secured/query` takes no filters and the Platform filters answers itself (`knovas_client.py:2251-2253`). It has no
limit of its own. **Missing:** § 4, Platform only.

### 2.5 External agents

**Exists.** The MCP server uses the tenant's certificate and is locked to the Knovas tenant
(`KB/knovas-software/client/knovas_mcp/mtls.py:1-16`). It declares `{kind: agent, id, on_behalf_of}`
(`knovas_mcp/actor.py:1-12`), which the Core records and decides nothing on (`S/services/request_actor.py:1-8`). The
model chooses `access_groups` on each call (`knovas_mcp/server.py:79-80`); there is no signed claim. DEC-0077 (branch
`dec-0077-signed-group-claims`) leaves an agent's identity to AGB-01 (`:73-74`); AGB-01 and AGB-02 are open
(`KB/docs/ModernDocs/steering/bets/BET-2026-11-agents-on-the-brain.yaml:85-86`). **Missing:** § 5, both sides.

## 3. How a role becomes the Core's groups, and the migration (proposed)

- **One role, one Core group.** "Rolle anlegen" first creates a root group in the Core (`knovas_client.py:3284-3290`);
  the role is saved only when the Core answers. A rename renames the group; its id stays (`knovas_client.py:3297-3298`).
  Roles do not nest, since groups cannot move. A tenant with a tree may *adopt* an existing group as a role's group.
- **Data** are the folder rules and document rights naming the role's group. The Platform keeps no copy and reads them
  from the Core, as its group cache is "never authoritative" (`0001_identity.sql:96-98`). Adding a **walled** folder
  adds the role's group to the folder's rule: one write, nobody loses sight. Adding an **open** folder would wall it off
  from everyone else: the dry run says so and asks.
- **Members** live in the Platform (the Core has none) and reach the Core as `grp` in the next claim.
- **Delete** in this order: data (with a dry run), members, the Core group (the Core refuses one in use), the role.
- **Migration**, new tables only:
  - **Administration**: function `admin`, which passes every gate (`admin.py:83-85`, `builder/team.py:30`); group: the
    tenant's administrator group, which reads nothing walled, so an administrator sees no more than before; members:
    everyone holding `admin` today.
  - **Mitglied**: function `member`; group: a new root group "Mitglied" (or the existing one of that name), made by a
    start-up step, not by SQL, and assigned to nothing, so nothing changes; members: every person and every new person.
  - The other eight keys stay on Personen as direct functions until someone puts them in a role. Group grants stay.

## 4. "Assign a role to the assistant": recommendation

**Recommended: a Core group set the assistant's requests carry — the person's groups, narrowed to the groups of the
roles the assistant is a member of.** "The assistant is a member of a role" means it may use that role's data when it
answers a member of that role. Its search runs as the person with `grp` = person's groups ∩ assistant's groups.

Why not a filter on the passages the search returns:
1. **The Core stays the only judge.** A Platform filter would have to know where each passage lies (which rule, which
   document rights, open or walled): a second copy of the policy, which the code rejects by name
   (`knovas_client.py:3326-3331`).
2. **It never shows more.** Fewer groups never read more; visibility is monotone (`access_policy.py:32-33`). It may
   hide a little more than needed (a document shared by two roles): the safe side.
3. **No Core change.** One context like `asserting_for` (`knovas_client.py:1759-1776`) and one line in `make_search`.
4. **It covers "may be used by AI" (A-10):** take the assistant out of a role, e.g. "Personal".

The narrowing acts only under `brokered`; with "Aus" the assistant uses what the person sees, as today
(`docs/platform-mvp/management/data-protection.md:31-36`). After the migration the assistant is a member of every
role, so nothing changes until an administrator takes it out. Also proposed: its requests declare
`{kind: agent, id: knovas-assistent, on_behalf_of: <person>}` so the Core's log names it.

## 5. "Assign a role to an external agent": recommendation

**Recommended: an agent is a registered member in the Platform; its role's groups reach the Core in a claim the
Platform signs for it, with the people's key and format, marked as an agent.** Acting for a person, the claim carries
the person's groups ∩ the agent's (the assistant's rule) and names the person. The agent fetches a short-lived claim
from the Platform with a credential of its own (precedent: the Experimente API's bearer tokens,
`web_interface/experiments_routes.py:817-830`), and the MCP server attaches it instead of choosing groups.

Why: one place decides who holds which role; the Core already verifies these claims; it matches DEC-0077's "the
Platform as signer" (point 1). It needs CR-R5 and CR-R6. The alternative, the Core holding agents and their groups
itself (AGB-01 as written), makes the role builder write members into the Core through a new route, and two places
would hold memberships.

**Consequence for the pilot:** once the Knovas tenant runs `brokered`, the MCP server's calls carry no claim and are
refused (`principal_resolver.py:165-167`). Until CR-R5 and CR-R6, agents stop at "Ein" on the pilot tenant; the page
shows agent members as "Noch nicht wirksam".

## 6. Safety rules (proposed, each with its control)

1. **No role grants more than its creator holds.** Functions: only those she holds (Platform check). Data: the Core's
   dominance check (`secure_api.py:4677-4689`); only Administration attaches data. Granting to oneself (joining a role,
   or changing the data of one's own role) needs a second administrator **even with the bypass on**; today the bypass
   lets an administrator act alone, recorded (`identity/approvals.py:1-24`).
2. **A denial wins (A-04).** The Core cannot: no negation. For the pilot, roles only grant; a wall is made by leaving a
   role out of a folder's rule, and a deeper rule replaces the one above (`folder_rule_service.py:22-24`). The plan's
   "A-01 to A-04: Exist" is not true for "a denial wins" (question 3).
3. **The last administrator keeps the role.** Missing: `set_roles` can remove the last `admin` (`admin.py:242-262`);
   only deactivating oneself is refused (`admin.py:212-219`). Proposed: refuse any change that leaves Administration
   without an active person.
4. **Every change is audited.** `audit.record` exists but is best effort and never blocks (`identity/audit.py:23-45`);
   the Core logs ids only. Proposed: role, function and member changes write their audit row, with before and after,
   in the same transaction (Platform tables); data changes write it after the Core answered, with the outcome.
5. **A change reaches the Core before it counts.** Data: the Core write first; the page shows what it reads back, as
   `move` does (`builder/access_backend.py:42-47`). New role: its group first. Members: from the next request, at most
   one claim lifetime (120 s). Data and member changes go through the four-eyes path `acl_change`
   (`admin_documents.py:341-349`) as new payload actions, so `approval_requests` (`0001:137-139`) does not change.
6. **Aus / Probe / Ein.** **Aus** (`disabled`): functions act; data are saved in the Core and hide nothing, and the page
   says so. **Probe** does not exist in the Core (no shadow mode found); with CR-R4 the page shows what each role would
   hide. **Ein** must be `brokered` (CR-R1); then data and the assistant's narrowing act, agents with CR-R5/R6. The
   Platform's own posture requests know only `disabled|enforcing` (`0018_access_window.sql:19`).

## 7. Core changes (separate dependencies, like `../core-requests.md`)

| No. | What | Why the role builder needs it |
|---|---|---|
| CR-R1 | The posture route accepts `brokered` once a key is registered and the backfill is complete (DEC-0077 point 5; bet item SA-03) | Under `enforcing` the Platform's claim is ignored, so no role's data act |
| CR-R2 | Folder-rule routes take the caller's signed claim under `brokered`. Today they build the caller from `acting_as` only (`secure_api.py:4680-4683, 4759-4762, 4770-4773`), and `brokered` refuses a caller without a claim (`principal_resolver.py:165-167`) | Without it, under "Ein" no folder can be added to or removed from a role |
| CR-R3 | A new folder rule reaches documents already read in: a backfill for one rule, started by the route or a customer route | Without it, a new folder covers only documents read in later |
| CR-R4 | Probe: evaluate without applying; count per day what would be hidden, no names | A-06's middle position; RB-9 |
| CR-R5 | Agent claims (AGB-01 in this variant): accept a Platform-signed claim whose subject is an agent, record it as a verified actor; an agent credential other than the tenant certificate | RB-8 |
| CR-R6 | The MCP server attaches the claim and drops the per-call `access_groups` (`server.py:79-80`) | RB-8 |

## 8. Items

| Item (proposed) | What | Class | Depends on | By 15 Dec |
|---|---|---|---|---|
| RB-1 | Role tables (new only: role, role function, role member); migration with Administration and Mitglied; their Core groups by a start-up step | sensitive | — | yes |
| RB-2 | `roles_of` and `access_groups_of` read roles; 32-group cap; gate tests for every key | sensitive | RB-1 | yes |
| RB-3 | Rollen page, read only: list and one role (`07-rollen`), behind the admin gate | routine | RB-1, PMVP-12, PMVP-40 | yes |
| RB-4 | Create, rename, delete; functions; people as members; rules 1, 3, 4, 5 | sensitive | RB-2, RB-3 | yes |
| RB-5 | Data: add or remove a source, folder or document; dry run; four eyes | sensitive | RB-4; acts under "Ein" with CR-R1, CR-R2; old documents in new folders with CR-R3 | built yes; acts with the Core |
| RB-6 | The assistant as a member (§ 4) | sensitive | RB-2, PMVP-20 | yes; acts with CR-R1 |
| RB-7 | Register of agents; agents as members, "Noch nicht wirksam" | sensitive | RB-4 | yes |
| RB-8 | Agent claims (§ 5) | critical | CR-R5, CR-R6 | no (12 Jan) |
| RB-9 | Probe view per role | sensitive | CR-R4, PMVP-42 | no |
| RB-10 | Split `admin` into finer functions | sensitive | RB-2 | no |

**Must by 15 December:** RB-1 to RB-4. RB-5 to RB-7 fit if started by 1 December (G2). The data part acts on the
pilot only once CR-R1 and CR-R2 have landed and the Core's rights test has passed twice (DEC-0082). PMVP-40 changes the
same Personen page, so RB-3 and RB-4 start after it is merged. A-07 to A-10 stay on 12 January.

## 9. Open questions for the founder

| No. | Question | Recommended answer |
|---|---|---|
| 1 | Does granting oneself a role, or changing one's own role's data, always need a second administrator, even with the bypass on? | Yes |
| 2 | Does the migration create only the two built-in roles, the other eight keys staying direct functions until an administrator builds roles? | Yes |
| 3 | A-04 says "a denial wins", but the Core has no deny. What does the pilot do? | No deny; walls by leaving a role out. A real deny changes the Core's model; decide after the pilot, and correct "Exist" in the plan |
| 4 | Who signs an agent's claim? | The Platform (§ 5), as the shape of AGB-01 |
| 5 | Once the pilot goes to "Ein", Claude Code over MCP is refused on the Knovas tenant until RB-8. Accept for 15 December? | Yes; agents follow on 12 January |
