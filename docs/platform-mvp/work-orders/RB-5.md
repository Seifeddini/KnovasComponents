# Work order RB-5: a role's data; the dry run; four eyes; adopting a group; direct grants into roles

**Draft for the coordinator** (2026-11-21). Everything under "Decisions this order takes" is a proposal until the
coordinator (and, where marked, the founder) confirms it and the test file is frozen.

| | |
|---|---|
| Item | RB-5 of the role builder (Jira: filed by the coordinator), plan `FEAT-2026-11-19-platform-mvp` § 4 Admin A-01 to A-10 (KnowledgeBase `docs/ModernDocs/plans/2026-11-19-platform-mvp.md`); follow-ups **FU-41** (direct grants into roles) and **FU-45** (adopting an existing Core group, moved here from RB-4) in `docs/platform-mvp/follow-ups.md` |
| Design | `docs/platform-mvp/design/role-builder.md`: § 2.2 (the Core's groups, folder rules and document rights; when a rule takes effect), § 3 (one role, one Core group; the Platform keeps no copy; a walled folder and an open one; adopting a group), § 4 (FU-41: only roles carry data to agents), § 6 rules 1, 2, 4, 5 and 7, § 7 (RB-5's row; cut order 2), § 9 |
| Core requests | `docs/platform-mvp/core-requests.md`: **CR-R1** (`brokered`), **CR-R2** (folder-rule routes take the signed claim), **CR-R3** (a new folder rule reaches documents already read in). Someone else builds them. The data act only with CR-R1 and CR-R2; a new folder over documents already read in only with CR-R3 |
| Mock-up | `docs/platform-mvp/reference-screens/07-rollen.html` (approved): the part «Darf lesen» with «+ Daten hinzufügen», one row per folder («Ordner · N Dokumente») or document («Dokument») |
| Bet | `BET-2026-11-platform-mvp` (approved 2026-11-19) |
| Founder decisions | 2026-11-21 (design § 9): **no deny**; granting oneself a role needs no second person (roles); data changes keep **four eyes** (design rule 5: "Data changes keep the four-eyes path `acl_change`") |
| Class | **sensitive** (Siran approves before `main`): this item writes folder rules and document rights to the tenant's Core, which decide who reads what once enforcement is «Ein». **Not critical** under this repository's CLAUDE.md: **no migration** (the data live in the Core and are read back each time; adopting a group and moving direct grants use RB-1's tables, `user_access_groups` and `approval_requests` as they are), no change to tenancy, mTLS, certificates, secrets, CI or a Golden Invariant, no gate or decorator change. The move of direct grants (FU-41) changes rows of existing tables on an administrator's action, keeps every person's claim identical (checked inside its transaction) and writes nothing to the Core: a data change, not a schema migration. **If the work needs a migration (for example role memberships with an end date, or a table of pending role data), it becomes critical: stop and write why under "Builder's notes".** |
| Depends on | RB-1/RB-2 and RB-3/RB-4 merged into `feat/platform-mvp` (`identity.roles.RoleStore`, the Rollen page, frozen `test_rb12_roles_acceptance.py`, `test_rb34_roles_page_acceptance.py`); **RB-6/RB-7 merged** (they add the agents' rows to the same page and `admin_roles.py`, and FU-41's purpose needs RB-6: a role created later gets the Knovas Assistent, `RoleStore.create_role` → `agents.join_new_role`); PMVP-40 (the «Zugriff» pages and `execute_acl_change`) |
| Branch / worktree | `feat/rb-5` from `feat/platform-mvp` **after RB-6/RB-7 is merged**, worktree `.worktrees/rb-5` |
| Frozen tests | `P/tests/test_rb5_role_data_acceptance.py` (95 tests), blob id in `docs/platform-mvp/frozen-tests.txt` (the coordinator adds the line when it freezes the file; the check command is in that file). `P` = `KnovasPlatform/components/docbridge_integration` |
| Card | `docs/components/L2-platform-admin.card.yaml` (or the card «Rollen» if RB-FIX-1 has split it, FU-44), its page, `docs/components/boundaries.yaml` (new files); `L2-PLATFORM-ACCESS` («Zugriff») for the change to `execute_acl_change` |

## Goal

An administrator opens a role and sees, under «Darf lesen», what the role reads: the folders whose rule names the
role's group and the documents whose own rights name it, **as the Core holds them, read back each time**. She adds a
source, folder or document with «+ Daten hinzufügen» and removes one from its row. **Before each change a dry run**
says who will gain sight and who will lose it, and warns when an open folder would be walled for everyone else. The
change goes the existing four-eyes path `acl_change`. A role can take over an existing Core group, «Mitglied»
included (FU-45), and the people's direct group grants move into roles without changing anybody's sight (FU-41), so
that under «Ein» the Knovas Assistent reads what each person reads. **Until the tenant is `brokered` (CR-R1, CR-R2)
the data are saved in the Core, hide nothing, and the page says «wirkt mit «Ein»».**

## What RB-5 does not do

- **Switching enforcement** («Aus / Probe / Ein») or reading what each role would hide (RB-9, CR-R4). RB-5 never asks
  the Core for a posture change.
- **Agents' rows** on the role page (RB-6, RB-7). RB-5 keeps to the part «Darf lesen», the dry runs and its own routes;
  the region RB-3/RB-4 marked for the members (`=== the role's members ===`) is RB-6/RB-7's.
- **The people page's groups field** (`POST /admin/people/groups`, direct grants). It stays as it is (decision 9).
- **The three readers of direct grants** of FU-42 and the client's own log lines of FU-43 (RB-FIX-1); see rule 6 for
  what RB-5 relies on.
- **Splitting `admin`** (RB-10).

## What to build (the frozen tests pin it)

### 1. The routes

All in the console's blueprint (`admin`), each behind the console's own `require_admin` (its view carries
`allowed_roles == frozenset({"admin"})`; signed out 302 to the sign-in; every other function and an account without
one 403, nothing changed, nothing sent). **Every POST reads the form field `csrf_token` first** and answers 400 when it
is missing or wrong, before anything else is read or sent: nothing changed, no Core call, no audit row, no approval
request. Then the role (404 for an unknown, foreign or malformed id, before any Core call), then the checks (400),
then the Core. Suggested home: a new module `P/src/web_interface/admin_role_data.py` with
`attach_role_data_routes(bp, gate, *, csrf_valid, csrf_token, page_context, client_factory, require_admin, …)`,
attached from `attach_role_routes`; the answers are the Rollen page (RB-3/RB-4's `_page`) with a dry run, a notice or
a refusal in `role="alert"`. Endpoint names are yours.

| Method and path | Form fields | What it does | Answers |
|---|---|---|---|
| `POST /admin/roles/<id>/data/preview` | `op` (`add` \| `remove`), `kind` (`source` \| `folder` \| `document`), `pointer` | **the dry run** of adding or removing (§ 3); writes nothing anywhere | 200 with the dry run; refused 400; Core unreadable 502 |
| `POST /admin/roles/<id>/data/add` | `kind`, `pointer`, `confirm` (`1` when the dry run warns) | plans again, then the change through `acl_change` (§ 5) | 200 done or queued; refused 400 (also: a warning without `confirm=1`); Core refused or unreadable 502 |
| `POST /admin/roles/<id>/data/remove` | `kind`, `pointer`, `confirm` | as above, removing | as above |
| `POST /admin/roles/<id>/adopt/preview` | `group_id` | **the dry run** of adopting the group (§ 7); writes nothing | 200; refused 400; Core unreadable 502 |
| `POST /admin/roles/<id>/adopt` | `group_id` | the role takes over that Core group (§ 7) | 200; refused 400 (also the 32-group cap); Core unreadable 502 |
| `POST /admin/roles/direct-grants/preview` | | **the dry run** of the move (§ 8); writes nothing | 200; Core unreadable 502 |
| `POST /admin/roles/direct-grants/move` | | moves the direct grants into roles (§ 8) | 200; Core unreadable 502 (nothing moved) |
| `POST /admin/approvals/<id>/approve`, `…/execute` (exist, `require_approver`) | | the second person; carries out an approved role-data request through `execute_acl_change` (§ 5) | as today |

`data/add` and `data/remove` never trust the dry run the browser sends back: they plan again from what the Core holds
now, and the change sent is the one planned then.

### 2. The part «Darf lesen» (`data-role-part="data"`)

The role page's part stays one `<section data-role-part="data">` with no `<section>` inside (RB-3/RB-4's tests read
it that way). It holds:

- **The rows, read from the Core on every page** (the Platform keeps no copy, design § 3; the group cache is "never
  authoritative"): each folder rule from `client.folder_rules()` whose `access_groups` name the role's Core group, an
  element with `data-rule-id="<rule id>"` showing its prefix; each document from `client.documents(group=<the
  role's group>)` whose own rights name the group (its `acl_folder_id` empty or `__acl_folder_none__`, and its
  `access_groups` naming the group), an element with `data-document-uuid="<document_uuid>"` showing its pointer. A
  document governed by a folder rule is shown through its rule, not as a row. Every prefix and pointer is escaped
  (no `|safe`).
- **A removal per row, through its dry run**: a form posting to `…/data/preview` with the hidden fields `op=remove`,
  `kind` (`folder` for a rule, `document` for a document) and `pointer`. No row posts to `…/data/remove` directly.
- **«+ Daten hinzufügen»** (the tests read «Daten hinzufügen»): exactly one form posting to `…/data/preview` with
  the hidden field `op=add`, a `<select name="kind">` with the options `source`, `folder`, `document` (labels yours, e.g. «Quelle»,
  «Ordner», «Dokument») and a field `pointer`. How the administrator picks the pointer (typed, or chosen from the
  inventory of `/admin/documents`) is yours; the field's name is pinned.
- **«wirkt mit «Ein»»** (pinned words) while the Platform does not know that the tenant is `brokered`; see § 6.
- **The dry run** (§ 3), when the answer carries one.
- **«Gruppe übernehmen»** on the page of Mitglied and of the tenant's own roles (anywhere in the card): a form posting
  to `…/adopt/preview` with a `<select name="group_id">` offering the Core's groups except the administrator group,
  every role's group (this role's included), as `client.access_groups()` lists them (§ 7).
- **Administration has no data** (its group is the tenant's administrator group, design § 3): no «+ Daten
  hinzufügen», no adopt form; its preview, add, remove and adopt answer 400.
- A role **without a Core group** (Mitglied before «Mit Knovas verbinden», a role of RB-1's move): no «+ Daten
  hinzufügen» (its preview and add answer 400); the adopt form is its way to a group.
- When the Core cannot be read, the part says so in its own words and the page still renders.

**The offer to move direct grants** (§ 8): while any person holds a direct grant without an end date, the page (in
`<main>`, outside the card is fine) offers a form posting to `/admin/roles/direct-grants/preview`.

Opening any role page writes nothing anywhere (no Core write, no audit row, no approval request).

### 3. The dry run

The dry run is an element with `data-dry-run="data"` (for the move `data-dry-run="grants"`, § 8) carrying
`data-gains="<n>"` and `data-losses="<n>"`. Every person who gains sight is an element with
`data-gains-person="<user id>"` whose text is the person's display name; every person who loses it one with
`data-loses-person="<user id>"`. A long list may sit inside a `<details>`; every id is on the page. **Names and ids
are for the page only: never in a log line, and the audit row holds the counts** (§ 9). Below the counts the dry run
draws the form that carries the change out (posting to `…/data/add` or `…/data/remove` with the same `kind` and
`pointer`, and an unticked checkbox `confirm` value `1` when it warns, § 4).

**Who sees what** is computed as the data will act under «Ein», whatever the posture is today: a person sees a folder
or document when it is **open** (no group) or when one of the person's **claim groups** (`roles.claim_groups`: their
running direct grants plus their roles' groups) **is one of its groups or above one of them** in the Core's tree
(`client.access_groups()`, the Builder's `builder.access.Tree`; design § 2.2: "a document's readers are its group
and that group's ancestors"). People are the active accounts. Gains = readers after − readers before; losses =
readers before − readers after. While the tenant is not `brokered`, the part says «wirkt mit «Ein»» (§ 6), so the
administrator reads the dry run as "what this will do once «Ein» is on".

**What is planned** (the groups before and after, and the one Core call). `G` is the role's group. A folder's rule
is the **live rule whose prefix equals the pointer**; the folder's governing rule is the longest live rule whose
prefix the pointer starts with (`folder_rule_service.py`'s resolution; it may be the folder's own rule):

| op, kind | before | after | Core call |
|---|---|---|---|
| add, `source` or `folder`, the folder has its own rule | its rule's groups | before ∪ {G} | `update_folder_rule(rule_id, after)`: applies at once (`secure_api.py` PATCH) |
| add, `source` or `folder`, no rule of its own | the governing rule's groups (none: open) | before ∪ {G}: **the new rule keeps the groups that govern the folder today, so nobody loses sight** | `create_folder_rule(pointer, after)`; reaches only documents read in later until CR-R3 (§ 4) |
| add, `document` | its current groups (`document_access(pointer)`) | before ∪ {G} | `set_document_access(pointer, after)`: replaces the set and takes the document out of its folder rule (the dry run may say so) |
| remove, `source` or `folder` | its own rule's groups (which must name G, else 400) | before − {G} | `update_folder_rule(rule_id, after)` when `after` is not empty; **`delete_folder_rule(rule_id)` when it is**: the rule's successor then governs (`after` = the successor's groups, none: open). **Never an empty set to `update_folder_rule`** |
| remove, `document` | its own rights (which must name G, else 400) | before − {G} | `set_document_access(pointer, after)` (an empty `after` opens the document: § 4) |

`source` and `folder` are the same Core call (a folder rule on the source's prefix); the words differ only on the page.
Adding what the role already holds is "no change" (a notice, nothing sent, no row). Refused before any Core write
(400): `op` or `kind` outside the lists; a `pointer` empty after trimming, over 1,024 characters
(`FOLDER_RULE_PREFIX_MAX_LEN`) or holding a control character; a document the Core does not know; removing a folder
whose own rule does not name G or a document whose own rights do not; Administration; a role without a Core group.
A Core read that fails (groups, rules, documents) is 502 with a message naming «Knovas», nothing planned.

### 4. The warnings and the confirmation

| Warning (`data-warning=`) | When | Confirmation |
|---|---|---|
| `walls-open` | **adding** to a folder or document that is **open** today (no governing rule, or one with no group; a document with no group): with «Ein» everyone outside the role loses sight; the dry run's losses are exactly those people | `confirm=1` required; without it 400 and nothing sent |
| `opens` | **removing** leaves a folder or document with no group (the rule is deleted and nothing walled governs it, or a document's rights become empty): with «Ein» everyone sees it | `confirm=1` required |
| `already-read-in` | **adding** creates a **new** folder rule and the Core lists documents under the prefix that are not governed by rights of their own: those documents keep their present rule until Knovas backfills (CR-R3) | none (information) |

The words are yours; the attribute values are pinned. The confirmation is re-checked when the change is carried out
(§ 5): a request planned without a warning that warns by the time a second person confirms it is not carried out.

### 5. Four eyes

**The existing approvals module carries it.** `identity/approvals.py` already has the guarded kind `acl_change`, the
administrators' recorded bypass (`approvals.admin_bypass`, on by default since 14 August 2026), self-approval refused
in code and by the database (`approval_requests.four_eyes`), and the Freigaben page with its executor registry
(`admin.py`: `"acl_change": execute_acl_change`). RB-5 adds no guarded kind and no table:

- `…/data/add` and `…/data/remove` call `run_guarded(ApprovalService(…), me, kind="acl_change",
  target_ref=f"role:{role_id}", payload={...}, execute=…)`.
  - **Bypass on** (the default): the administrator acts alone; the Core is written at once and `approval.bypassed`
    records it (target `acl_change`, `role:<role id>`).
  - **Bypass off**: an approval request (kind `acl_change`, status `pending`, `target_ref` `role:<role id>`) and
    **nothing reaches the Core** until a second person confirms. The requester cannot confirm her own.
- The payload: `{"action": "role_data_add" | "role_data_remove", "role_id", "core_group_id", "kind", "pointer",
  "confirm"}`. `execute_acl_change` (`admin_documents.py`) hands these two actions to RB-5's executor; its other
  actions stay as they are. The payload is stored in `approval_requests.payload` (not an audit row) and shown on the
  Freigaben page (`_summary` may name the role and the folder: a page, not a log).
- **The executor plans again** from what the Core holds when it runs (§ 3), refuses (raises, nothing sent) when the
  role's Core group is no longer the payload's `core_group_id`, or when the plan now warns (§ 4) and the payload's
  `confirm` is not true; then it sends the one call and writes the audit row (§ 9). Its exceptions carry fixed words
  only (`admin_approvals._execute` logs them).
- **Only an administrator carries a role's data change out** (design rule 1: only Administration attaches data; under
  «Ein» the Core checks that the caller dominates the groups, `secure_api.py:4677-4689`). An approver who is not an
  administrator may confirm: the request is then `approved` and nothing is sent; an administrator carries it out with
  «Ausführen» (`/admin/approvals/<id>/execute`). The executor checks `"admin" in actor.roles` before any Core call.
- **Adopting a group and moving direct grants need no second person** (decision 2): neither changes a wall at the
  Core; adopting only grants sight, like adding a member (founder: no second person for roles), and the move changes
  nobody's sight.

### 6. The Core calls, and «wirkt mit «Ein»» before «Ein»

- **Through the client's existing calls only**: `access_groups`, `folder_rules`, `create_folder_rule`,
  `update_folder_rule`, `delete_folder_rule`, `document_access`, `set_document_access`, `documents`,
  `delete_access_group`. No new client call is needed; `knovas_client.py` does not change (rule 6).
- **Never `acting_as`.** The Core takes the caller from the person's signed claim once CR-R2 lets the folder-rule
  routes read it under `brokered`. Under `disabled` the Core checks nothing; under `enforcing` it then refuses the
  Platform's writes (no assertion: `access_policy.can_assign` "no_assertion"), which is right: under `enforcing` the
  Core ignores the Platform's claim (design § 2.3), so a wall written then would hide documents from every Platform
  user at once.
- **What is written before «Ein»** (decision 3): **everything, to the Core.** Under «Aus» (`disabled`) the Core stores
  folder rules and document rights and hides nothing (design rule 7), so writing them today has no effect on anybody's
  sight; at «Ein» they act. The Platform keeps no copy: the part reads them back (§ 2), and a rule changed at the Core
  directly shows as the Core holds it. **What waits** is the effect, not the write: (a) every data change acts only with
  CR-R1 and CR-R2; (b) a **new** folder rule reaches documents already read in only with CR-R3 (`already-read-in`);
  (c) under `brokered` before CR-R2 the Core refuses the folder-rule writes (they build the caller from `acting_as`
  only), and the page says Knovas refused. Document rights already take the signed claim under `brokered`
  (`secure_api.py` `_acting_principal` passes `principal_assertion` through), so they need CR-R1 only.
- **«wirkt mit «Ein»»** stays in the part on every role (rows or none) **unless the client reports the tenant as
  `brokered`**: `getattr(client, "rbac_posture", None)` (the name `builder/access_backend.py` already asks for),
  called, answering a dict whose `mode` is `"brokered"`. The real client has no such call today (the Core has no
  secured posture read: decision 4), so the words always show until CR-R1 brings one. A posture of `disabled` or
  `enforcing`, no answer, or an error all keep the words.
- **A Core refusal** (the client's `None`, `False` for a delete, or an exception) is 502 with a message naming
  «Knovas»; the page claims nothing; the audit row has outcome `error` (§ 9); no `approval.bypassed` row (nothing ran).
- **A change reaches the Core before it counts** (design rule 5): the page after a change is drawn from what the Core
  reads back.

### 7. Adopting a group (FU-45)

A role takes over an existing Core group as its group (design § 3: "A tenant with a tree may *adopt* an existing
group"). This is FU-45's way out: «Mit Knovas verbinden» cannot create a second group «Mitglied» when the Core has one;
Mitglied adopts it, and «Mit Knovas verbinden» then links Administration alone (it leaves a linked role alone, RB-4).

- **`…/adopt/preview`**: the dry run (`data-dry-run="data"`), gains = the role's active members who do not read the
  group today (they hold neither it nor a group above it), losses 0; it draws the form posting to `…/adopt` with the
  same `group_id`. Writes nothing.
- **`…/adopt`**: in one transaction, `RoleStore.set_core_group(role, group)` (its 32-group cap, and RB-6's agent
  cap, refuse with 400, nothing changed) and the audit row; then, when the role had a group, **delete the old group at
  the Core** (`delete_access_group`, best effort; its outcome in the audit log, e.g. RB-4's `role.core_group_abandoned`).
  No Core group is created.
- **Refused (400, nothing changed, nothing sent), in the preview and in the adopt:** Administration (its group is the
  tenant's administrator group, linked by «Mit Knovas verbinden»); the administrator group; a group the Core does not
  list; an empty `group_id`; a group that is another role's (one role, one group: a role's delete deletes its group);
  **a role whose present group still carries data** (a folder rule names it, or the Core lists a document for it):
  its data would be readable by nobody (remove them first, each with its dry run).

### 8. Moving direct grants into roles (FU-41)

The smallest form that keeps every person's sight unchanged and changes nothing under the current posture
(decision 5): **one role per directly granted group.**

For every running direct grant (`user_access_groups`, `ends_on` null or not past):

| The grant | What the move does |
|---|---|
| no end date, a group the Core lists, not the administrator group, **no role has it** | **moved**: one new role per such group (kind `custom`, **no functions**, `core_group_id` = the group, named like the group at the Core: trimmed to 80, " (2)" … when taken), its holders its members; their direct grants of it go |
| no end date, a group **a role already has**, the person **is a member** of that role | **goes** (the role gives it already) |
| a group a role has, the person is **not** a member | **left** (joining that role would give its functions) |
| **with an end date** (an access window, B14) | **left** (a role membership has no end date) |
| the **administrator group** | **left** (it carries ACL authority, not reading) |
| a group the **Core does not list** | **left** |

- In one transaction: the role locks (`lock_all`), each new role (`create_role`, which on RB-6's branch also makes the
  Knovas Assistent a member), each member added **before** their direct grant is deleted (so the 32-group cap never
  sees a new group), the redundant grants deleted, the audit rows; then **every touched person's claim groups and
  functions are compared with before, and any difference undoes it all** (500, nothing moved).
- **Nothing is written to the Core.** Under the current posture nothing changes for anyone: the claims are identical,
  and the assistant's narrowing acts only under `brokered` (design § 4).
- **`…/direct-grants/preview`** (`data-dry-run="grants"`, `data-gains="0"`, `data-losses="0"`): the new roles and their
  people, and what stays and why; it draws the form posting to `…/direct-grants/move`. Writes nothing.
- A second move changes nothing (what is left stays left).
- The Core's groups are read first (`access_groups`): unreadable, 502, nothing moved.

### 9. The audit rows (design rule 4)

Ids, function keys and counts only: **never a pointer or prefix, a folder or document name, a role's or group's
name, a person's name or address.** Pinned:

| Action | `target_type`, `target_id` | `detail` holds at least | When |
|---|---|---|---|
| `role.data_added`, `role.data_removed` | `role`, the role | `core_group_id`; `kind`; `rule_id` (source, folder; the new rule's id after a create) or `document_uuid` (document); `gains`, `losses` (counts) | after the Core answers: outcome `ok`; a Core refusal: outcome `error` (best effort). Actor: the person who carried it out (the confirming administrator after an approval) |
| `approval.requested`, `approval.bypassed`, `approval.approved`, `approval.executed` (the approvals module's own) | `acl_change`, `role:<role id>` | the module's keys; the executor's result holds ids and counts only (`detail.result` of `bypassed` and `executed`) | as today |
| `role.core_group_adopted` | `role`, the role | `core_group_id`, `previous_core_group_id` (null when none) | in the adopt's transaction (`record_or_raise`) |
| `role.direct_grants_moved` | `role`, null | `roles` (the new roles' ids), `people` (the ids of everyone whose direct grants changed), `grants_moved` (grants that went), `grants_left` (running grants that stayed) | in the move's transaction; none when nothing moved |
| `role.created` (RB-3/RB-4's) | `role`, each new role of the move | `core_group_id`, `functions` (`[]`) | in the move's transaction |

Data changes write their row after the Core answers (design rule 4; the Core cannot be rolled back with the
Platform's transaction). Use `record_or_raise`; if it fails after the Core has changed, the page says the change stands
at Knovas without its row and the log names the role by id (not pinned).

### 10. The management page

`docs/platform-mvp/management/administration.md`: a section «Was eine Rolle lesen darf» (adding and removing data,
the dry run and its warnings, four eyes and the bypass, «wirkt mit «Ein»» and what waits for CR-R1 to CR-R3,
adopting a group, moving direct grants and what stays), and the status table in `management/README.md`.

## Rules

1. **No customer text in logs** (FU-14): ids, counts and error classes only, DEBUG included. Never a pointer or prefix,
   a document's title, a role's or group's name, a person's name or address, a Core error's text (it can quote the
   address). **This includes the approvals module's bypass line**, which logs the administrator's e-mail today
   (`approvals.py` `record_bypass`): log her id instead (the one change allowed in `approvals.py`).
2. **Ids only in audit rows** (§ 9). The approval request's `target_ref` is `role:<role id>`, never a pointer.
3. **The dry run before each change; the change planned again when it is made** (§ 3, § 5). The browser's copy of
   the dry run is never trusted.
4. **Never wall an open folder or open a walled one without the confirmation** (§ 4); never an empty set to
   `update_folder_rule`.
5. **No deny** (founder): roles only grant; a wall is a folder rule that leaves a role out.
6. **The Core is written only on an administrator's POST** (and by the executor on a confirmed request), only through
   the client's existing calls, never with `acting_as`, never at start, never on a GET. The real client's
   `_rbac_request` and `_make_request` can log a Core response body (FU-43, RB-FIX-1); RB-5's paths must not add to
   that, and RB-5 should not go to a client tenant before RB-FIX-1 is merged.
7. **Nothing is written to any tenant in tests**: a recording fake Core, invented people, folders and documents.
8. **Escaping and addresses**: every prefix, pointer, name escaped (no `|safe`); every href and form action from
   `url_for` (FU-34); a pointer travels in a form field, never in a URL the console builds.
9. **No gate changes**: no route's gate or decorator, no `allowed_roles` other than the console's admin gate; the
   approvals routes keep `require_approver`.
10. **The look**: PMVP-12's parts and PMVP-40's Admin look, brand tokens only, nothing scrolls sideways at 390 px,
    text contrast 4.5 to 1, a focus ring on every control. A long list of people in a dry run opens on request.

**Where the frozen file pins each rule** (section numbers of the file; 95 tests):

| What | Tests |
|---|---|
| Gates and tokens | § 1 (24): `allowed_roles` of the seven routes, signed out 302 (7), member / approver / ingestion manager / no function 403 with nothing changed (4), each of the seven forms 400 without or with a wrong token and nothing changed (7), an unknown or malformed role 404 on the five role forms (5) |
| The part «Darf lesen» | § 2 (12): rows read from the Core (rules and own-rights documents naming the group), no copy kept, escaping, «Daten hinzufügen» and its fields, a removal per row through the dry run, «wirkt mit «Ein»» for no posture, `disabled`, `enforcing` and an error (4), gone once `brokered`, opening writes nothing, Administration has no data |
| The dry run | § 3 (15): gains and losses with ids and names and nothing written, a group above the folder's group, a removal's losses, a document's current groups, a new rule keeping the governing groups (and the CR-R3 warning), an own rule without it, `source` as a folder rule, seven refusals, a role without a group |
| The open-folder warning | § 4 (7): `walls-open` with everyone else's losses, walled only with `confirm=1` (as drawn without it: 400), an empty rule is open, an open document, a removal that would open deletes the rule and needs `confirm=1`, never an empty rule, a walled folder needs no confirmation |
| Four eyes | § 5 (8): bypass on (alone, recorded, `role:<id>`), bypass off (queued, nothing sent), no self-confirmation, a second administrator (sent once, executed, her row), an approver (approved, nothing sent; an administrator carries it out), planned again (an open folder by then: nothing sent), a removal queued too, adopt and move need no second person |
| The Core calls | § 6 (8): the folder's own rule updated and read back with «wirkt mit «Ein»», a document's groups kept, a removal keeps the other groups, never `acting_as`, a Core refusal (3: 502, «Knovas», no `ok` row, no bypass row), an unreadable Core plans nothing |
| Adopting a group (FU-45) | § 7 (10): Mitglied adopts the Core's «Mitglied» after connect failed, then connect links Administration; the dry run; the old group deleted; six refusals; the 33rd group |
| Moving direct grants (FU-41) | § 8 (7): the dry run (0, 0, nothing written), the offer, every person's claim groups and functions unchanged and no Core write, one role per group without functions, what it takes and what it leaves, a second move changes nothing, an unreadable Core moves nothing |
| Audit | § 9 (3): every action's row with its keys and counts, no pointer, name or address; the four-eyes rows name the role, never the folder; a Core refusal's row with outcome `error` |
| Logs | § 10 (1): every path of RB-5 at DEBUG, the bypass, an approver's refusal and an administrator's execution included |

## Decisions this order takes (draft: for the coordinator to confirm before the tests are frozen)

1. **Class sensitive, no migration** (see the header). The move of direct grants is a runtime data change with its own
   proof (claims compared inside the transaction), not a schema change.
2. **Four eyes: data changes keep `acl_change` with the installation's bypass** — the design's line ("Data changes
   keep the four-eyes path `acl_change`") read literally, and the same rule the «Zugriff» page follows, so the role
   page is neither a way around it nor stricter than it. With the bypass on (the default since 14 August) an
   administrator acts alone and `approval.bypassed` records it; with it off a second person confirms. **Founder to
   check:** if "four eyes" for role data must hold even with the bypass on, that is one change in
   `ApprovalService.requires_approval` (a kind or payload the bypass does not cover), and a firm with one
   administrator could then not change a role's data at all. **Only an administrator carries a role's data change
   out** (design rule 1; the Core's dominance check under «Ein»); an approver's confirmation counts as the second
   person. **Adopting a group and moving direct grants need no second person**: neither changes a wall; adopting only
   grants sight, like adding a member, which the founder freed from a second person; the move changes no one's
   sight. Coordinator to check adopt: if it should be guarded too, it becomes a third `acl_change` action.
3. **Before «Ein» everything is written to the Core** (it hides nothing under «Aus», design rule 7) and read back;
   nothing is kept in the Platform. What waits is the effect (CR-R1, CR-R2) and, for a new folder over documents
   already read in, CR-R3. The alternative, keeping pending data in the Platform until «Ein», needs a table (a
   migration, critical) and a second source of truth the design rejects.
4. **The posture is read through `client.rbac_posture()` when the client has it.** The Core has no secured route that
   tells a tenant its own posture (only the Knovas-internal one), so today the words «wirkt mit «Ein»» always show.
   **Coordinator: add to CR-R1 a read of the tenant's posture on a secured route** (e.g. `GET /secured/rbac/posture`
   → `{"mode": …, "backfill_complete": …}`); the client method that calls it maps the answer to `{"mode": …}`.
5. **FU-41: one role per directly granted group**, not a personal role per person (a role has exactly one Core group,
   so a "personal role" would be one role per person and group: more roles, same effect) and not the group's folders
   as another role's data (that changes walls at the Core and therefore sight). Grants with an end date, of the
   administrator group, of a group the Core does not list, and of a role's group for a non-member stay direct:
   **the founder or coordinator decides whether those people must be handled before «Ein»** (the assistant will not
   read through them). Role memberships with an end date would close the access-window gap but need a migration.
6. **Removal never empties a rule**: when the role's group is the rule's only group, the rule is deleted and its
   successor governs (the Core's tombstone semantics, `folder_rule_service.py`); the dry run shows what that opens.
7. **Administration has no data** and **a role adopts only while its present group carries no data**, so no data
   become readable by nobody.
8. **Pointers are typed or chosen; the pin is the field `pointer`.** A picker from `/admin/documents` would be nicer;
   the builder may add one within the look.
9. **The people page's direct-grants field stays.** After the move an administrator can still grant groups directly
   there; the role page offers the move again whenever open-ended direct grants exist. Whether the people page should
   stop offering direct grants before «Ein» is a separate decision (coordinator).
10. **The approvals module's bypass log line names the actor by id** (rule 1), the one change to `approvals.py`.

**Found while drafting** (for the coordinator; not this item's to fix):

- RB-3/RB-4's frozen log test sets DEBUG on the root logger only, while the app holds `web_interface` and
  `knovas_client` at `LOG_LEVEL` (`app.py` `_configure_logging_for_wsgi`), so a DEBUG line of those modules is never
  made in that test. RB-5's log test lowers them too (a pointer logged at DEBUG survived the first version).
- The «Zugriff» page's own routes put customer text where RB-5 may not: `execute_acl_change` logs a pointer on a
  failed document change and its audit rows carry pointers and prefixes as `target_id` (`document.acl_changed`,
  `folder_rule.saved`), and the approval's `target_ref` is the pointer. A candidate for the second log sweep (FU-22,
  FU-31).
- A pytest run that is killed can leave a test schema holding `citext`, after which every test in that database errors
  with `type "citext" does not exist`; recreate the database (`dropdb`/`createdb`) rather than reading it as a failure.

## Files you may change

- new: `P/src/web_interface/admin_role_data.py` (suggested), `P/tests/test_web_admin_role_data_*.py` (your own tests)
- `P/src/web_interface/admin_roles.py`: attaching the routes and handing the page what the part «Darf lesen» needs;
  nothing in RB-3/RB-4's or RB-6/RB-7's routes
- `P/src/web_interface/templates/admin_roles.html`: the part «Darf lesen», the dry runs, the adopt form, the move
  offer; nothing in RB-6/RB-7's agents' region
- `P/src/web_interface/admin_documents.py`: only `execute_acl_change` handing the two role actions to RB-5's executor
- `P/src/web_interface/admin_approvals.py`: only `_summary` (a line for the two role actions; a page, not a log)
- `P/src/identity/approvals.py`: only the bypass log line (rule 1)
- `P/src/identity/roles.py`: only additions; no signature or behaviour that `test_rb12_roles_acceptance.py` or RB-6's
  tests read changes
- `P/src/web_interface/static/css/admin.css`: rules of the part «Darf lesen» and the dry runs
- existing tests that pin the console's routes: `tests/test_web_admin_gates.py` (add the seven routes to its lists,
  FU-17), `tests/test_web_admin_approvals.py` or `tests/test_identity_approvals.py` only if they pin the bypass log
  line; list each with its reason. A test that fails for another reason is a finding, not something to adapt.
- `docs/components/…` (the card, its page, `boundaries.yaml`; `card check`, `card render`, `map-check --scope touched`
  as in `work-orders/PMVP-01.md`), `docs/platform-mvp/management/administration.md`, `management/README.md`
- `docs/platform-mvp/work-orders/RB-5.md`: only the "Builder's notes" section

## Files you must not change

- the frozen test files (`docs/platform-mvp/frozen-tests.txt`); `P/tests/conftest.py`; every migration (no new one:
  see the class)
- `P/src/knovas_client.py` (its calls as they are; FU-43 is RB-FIX-1's), `P/src/identity/users.py`, `principal.py`,
  `assertion.py`, `agents.py` (RB-6/RB-7's)
- any route's gate or decorator, `_require_roles`, `GUARDED_KINDS`, `APPROVER_ROLES`, `requires_approval`
- `P/src/web_interface/templates/_parts.html`, `_sidebar.html`, `static/css/parts.css`, `shell.css`
- `docs/platform-mvp/design/**`, `docs/platform-mvp/reference-screens/**`, `docs/platform-mvp/core-requests.md`
  (decision 4 is the coordinator's to add)

If the work needs one of these, stop and write the reason under "Builder's notes" instead of changing it.

## Done when

1. `cd P && py -3 -m pytest tests/test_rb5_role_data_acceptance.py -q -p no:cacheprovider` is green and not skipped,
   on a test database of your own (`docker exec pmvp-platform-test-db createdb -U platform knovas_platform_test_rb5`,
   `PLATFORM_DB_TEST_DSN=postgresql://platform:testpw@127.0.0.1:55433/knovas_platform_test_rb5`,
   `PLATFORM_DB_REQUIRED=true`). One pytest process at a time. The frozen check in `frozen-tests.txt` prints all OK.
2. RB-1/RB-2's, RB-3/RB-4's and RB-6/RB-7's frozen files stay green. Your own tests are green. **Do not run the whole
   Platform suite.** Run, each file in a process of its own: `tests/test_web_admin_*.py`, `tests/test_identity_*.py`,
   `tests/test_web_guarded.py`, `tests/test_shell_*.py`, and every file that names `execute_acl_change`,
   `acl_change`, `record_bypass` or `/admin/roles` (say which). Compare the failures with
   `docs/platform-mvp/baseline-failing-ids.txt`: no failing id the base does not have.
3. Screenshots outside the repository (say where): a role with folders and documents, the dry run of a walled folder,
   of an open folder (the warning), of a removal that opens, the adopt dry run on Mitglied, the move's dry run, a
   queued change on the Freigaben page, at 1440 and 390 px beside `07-rollen.png`. Invented data, a fake Core.
4. Card checks clean. Commits on `feat/rb-5` name the item (`RB-5: …`) and end with the co-author line; the branch is
   pushed. No pull request: the coordinator integrates after a verifier session passes it.

## Builder's notes

(the builder writes here: what was decided, what is open, what the verifier should look at)

**Built** (`feat/rb-5` from `origin/feat/platform-mvp` @ `127fe2f5`; builder session of 2026-11-21): `81bc357c` (code and tests), `917d1b98` (cards, management pages), `38153495` (map-check: the Builder's tree declared, the executor's promise), `2941a631` (no k- class in admin.css), `783c1155` (two texts) and the commit of these notes. No
migration, no new guarded kind, no gate or decorator change, no change to `knovas_client.py`, `users.py`,
`principal.py`, `assertion.py`, `agents.py`, `_parts.html`, `_sidebar.html`, `parts.css`, `shell.css`, `conftest.py`,
the design, the reference screens, `core-requests.md` or any frozen file. RB-FIX-1: **not merged in**: `origin/feat/platform-mvp` was still at `127fe2f5` at the last check (2026-11-22, before pushing), so this branch carries none of RB-FIX-1's changes; the merge (card «Rollen», `clean_name`, the client's logging, the group readers) is the integrator's or a follow-up of this branch.

| File | What |
|---|---|
| new `P/src/web_interface/admin_role_data.py` | `attach_role_data_routes(bp, gate, *, csrf_valid, client_factory, require_admin, page)` with the seven routes (endpoints `admin.preview_role_data`, `admin.add_role_data`, `admin.remove_role_data`, `admin.preview_adopt_group`, `admin.adopt_group`, `admin.preview_direct_grants`, `admin.move_direct_grants`); `plan_data` (§ 3, § 4), `execute_role_data` (§ 5), `plan_adopt` (§ 7), `plan_move` and the move (§ 8), `role_data_part` (§ 2), `tenant_brokered` (§ 6), `Sight` (who reads what under «Ein») |
| `P/src/web_interface/admin_roles.py` | `_page` takes `dry_run`, `grants`, `data_form` and hands the template `role_data` (read from the Core on every page); `attach_role_routes` attaches RB-5's routes. Nothing in RB-3/RB-4's or RB-6/RB-7's routes |
| `P/src/web_interface/templates/admin_roles.html` | the part «Darf lesen» (rows, «+ Daten hinzufügen», the dry run, «Gruppe übernehmen», «wirkt mit «Ein»»), the move offer and its dry run above the row of roles, one sentence on the connect line (FU-45); nothing in the agents' region |
| `P/src/web_interface/admin_documents.py` | `execute_acl_change` hands `role_data_add` / `role_data_remove` to `execute_role_data`; its other actions unchanged |
| `P/src/web_interface/admin_approvals.py` | `_summary`: one line for the two role actions (kind, path, whether the warning was confirmed, "only Administration carries it out") |
| `P/src/identity/approvals.py` | the bypass log line names the administrator by id (rule 1) |
| `P/src/identity/roles.py` | addition only: `active_claim_groups(conn)` (every active person's claim groups in one query, for the dry runs) |
| `P/src/web_interface/static/css/admin.css` | one new section at the end, «Rollen: Darf lesen» |
| new `P/tests/test_web_admin_role_data_rules.py` | own tests, 21 (below) |
| `P/tests/test_web_admin_gates.py` | the seven routes (FU-17); see "Existing tests changed" |
| cards | `L2-platform-admin.card.yaml`: `admin_role_data.py`, R-7, the seven routes with N-1 and P lines, `execute_role_data` with a promise, the term «dry run», uses L2-BRAIN-BUILDER (`builder.access.Tree`), the EXT-BRAIN-API method, a review entry; `L2-platform-access.card.yaml`: `execute_acl_change`'s hand-off and its use of `execute_role_data`; `boundaries.yaml`; pages rendered (`L2-brain-builder.md` re-rendered for the new consumer) |
| management | `administration.md` («Was eine Rolle lesen darf», status row, decision row, open points), `README.md` (status row; RB-5 out of "not started"), `data-protection.md` (a paragraph and a status row) |

**Decisions beyond the order** (all two-way, in new code unless said):

1. **"No change" covers what the role already reads through a rule above.** Adding a subfolder (or a document) whose
   governing rule already names the role's group sends nothing and writes no row (a notice says the role reads it through
   the folder above), instead of creating a rule identical to the one above. Adding a folder whose own rule names the
   group is the order's "no change".
2. **A document's "before"**: when the Core's listing says a live rule governs the document (`acl_folder_id` is a live
   rule), that rule's groups (the Core's own "groups governing it today", `secure_api.py` PUT); otherwise
   `document_access(pointer)`'s groups (own rights; for a tombstoned rule id too). With the frozen fake both agree.
3. **"Own rights"** is the Core's backfill definition (`acl_backfill_worker._is_explicit_override`): `acl_folder_id`
   none and a non-empty group set. A document opened by a removal (own rights set to `[]`) is therefore no row any more.
4. **`already-read-in`** counts a document under the prefix that has no own rights and is not governed by a rule deeper
   than the new one (those keep their deeper rule for good). An unfinished walk warns (it only informs).
5. **Walking the Core's document list** is bounded: 50 pages of 200 for one question (a document's entry, the backfill
   warning, adopt's "the Core lists a document for it"), 5 pages for the part on every page view, which then says that
   not every own-rights document may be listed. Adopt's check that cannot finish is 502 (the Core could not say); a
   listing that answers `unavailable` (the client's 404 on the inventory) counts as unreadable.
6. **Adopting**: the role's own group again is "no change" (200, notice); the adopt holds every role's row
   (`lock_all`) and checks again that the role still has the group the dry run saw and that no other role took the new
   one; the previous group's deletion reuses RB-4's `role.core_group_abandoned` with `after_adopt: true`. The audit row
   also carries `gains` (a count).
7. **The move**: holds the roles (`lock_all`), then every person with a direct grant (`FOR NO KEY UPDATE`, id order),
   then plans; writes `role.created` (with `from_direct_grants: true`), a `role.member_added` row per new member (as
   RB-4's form does) and `role.direct_grants_moved` with a `grants` list (person, group, role: ids) beside the pinned
   keys. A deactivated person's open-ended grant moves like any other (the claim is compared all the same). A role name
   taken meanwhile (`RoleError`) is 400 and nothing moved; any other failure 500. Role names: the group's name without
   control characters, white space collapsed, at most 80 characters, «Gruppe» when empty, " (2)" … when taken
   (case-insensitive, as citext).
8. **The dry run on the page**: a panel in the part «Darf lesen» (the move's above the row of roles): title, the counts in
   a sentence, each warning in its words with `data-warning`, notes (a document leaves its folder rule; a rule is deleted
   and the one above governs), the people in a `<details>` per side (open up to six), and the form. The confirmation is
   an unticked checkbox with `required`, so a browser does not send the form without it; the server checks anyway.
   «Entfernen …» on a row posts to the dry run; «+ Daten hinzufügen» is a `<details>` at the part's corner, a refused
   preview opens it again with what was typed.
9. **The approver's path**: an approver who is not an administrator confirms; `admin_approvals._execute` then shows its
   generic "Freigegeben, aber die Ausfuehrung ist fehlgeschlagen" (only `_summary` may change there), and the summary
   line says that only Administration carries it out. The summary names the kind and the path, not the role's name (the
   payload is pinned and has none; the target column shows `role:<id>`).
10. **The rows show no document count per folder** (the mock-up's «Ordner · N Dokumente»): one more Core read per row on
    every page view, and under «Ein» it would be the administrator's own count.
11. **The part when the Core cannot be read** says so in plain text (no `role="alert"`: it is not the answer to a form);
    the adopt form then says Knovas did not name its groups.
12. **`builder.access.Tree`** (as the order names it) is imported for the reader closure; the card declares the use of
    L2-BRAIN-BUILDER (map-check `declared` asked for it).

**What is written to the Core before «Ein»** (decision 3): on an administrator's data form with the bypass on (the
default), or on a request a second person confirmed and an administrator carried out, exactly one call through the
client's existing calls and never with `acting_as`: `update_folder_rule` (never with an empty set), `create_folder_rule`,
`delete_folder_rule` or `set_document_access`; after an adopt, `delete_access_group` of the role's previous group (best
effort). Nothing at start, on a GET, by a dry run, by the move or with the bypass off before the second person. Under
`disabled` it hides nothing; under `enforcing` the Core refuses the Platform's writes (no assertion) and the page says
Knovas refused (502); under `brokered` before CR-R2 the folder-rule writes are refused the same way.

**Existing tests changed** (one file; each change because the order asks for the seven routes in its lists, FU-17):

- `tests/test_web_admin_gates.py`: the seven routes in `FORMS` with the smallest form each accepts; a new placeholder
  `{grouped}` (a role of the tenant's own with the Core group `g-lit`, made by `_ids`) for the three data routes, whose
  forms need a role with a group; `NO_ROLES` fills both placeholders where the test used `_fill(path, role=NO_ROLE)`
  (four places: the paths now carry `{grouped}`); the test's fake Core lists one folder rule of `g-lit`
  (`rc-sync/rolle/`, for the removal) and one more group (`g-team`, for the adopt). Adding an open folder and removing a
  rule's only group warn, so those two forms carry `confirm=1`; with the bypass off they are queued, never sent. The
  docstring says so. No other existing test changed.

**Own tests** (`tests/test_web_admin_role_data_rules.py`, 21, 25 after the fixes below; they reuse the frozen file's fake Core, world and scene):
`active_claim_groups` equals `claim_groups` for every active person (ended and running windows, a deactivated person);
a subfolder read through its folder's rule is no change; a deleted rule leaves the rule above governing (no `opens`, its
gains and losses); a deeper rule's documents do not count for the backfill warning; a pointer of 1,025 characters, a
C1 control character and DEL are refused, 1,024 is planned; the executor refuses when the role's group changed after
the request; a change whose row cannot be written says so (500) with the Core changed; the page after a change is read
back; adopting the role's own group is no change; a failed adopt (the 33rd group) deletes no old group; role names from
group names; a group named like a role becomes «Bau (2)»; a move whose claims would differ is undone; the page renders
when folder rules, documents or groups cannot be read (three); no speaking group id or pointer in a log line; the
Freigaben summary.

**Checks** (own database `knovas_platform_test_rb5`, `PLATFORM_DB_REQUIRED=true`, one pytest process at a time):

- **Frozen file `test_rb5_role_data_acceptance.py`: 95 passed, 0 skipped**, on the first commit and again on the final
  code (`783c1155`, 5.4 min). RB-1/RB-2's 66, RB-3/RB-4's 106 and RB-6/RB-7's 27 passed (one process, first commit);
  on the final code again RB-3/RB-4's 106, the own 21 and `test_web_admin_approvals.py` 22.
  `test_pmvp11_navigation_acceptance.py` 40, `test_pmvp21_chats_acceptance.py` 13 passed.
- Each file in its own process, one after the other: `tests/test_identity_*.py` (20 files) all passed but
  `test_identity_broker_key.py::test_private_key_is_created_readable_only_by_its_owner` (failed) and one skip there
  (POSIX mode bits on Windows); `tests/test_shell_*.py` (8 files) passed, after one fix: the first run failed
  `test_shell_parts_css.py::test_no_other_stylesheet_has_a_k_class[admin.css]` (a selector `.role-part > p.k-small` of
  mine; now `.role-part[data-role-part="data"] > p`; 86 passed on the rerun); `tests/test_web_admin_*.py` (10 files):
  approvals 22, doc_fields 124, documents 66, **gates 331**, ingestion 161, people 28, roles_rules 13, system_doc_fields
  14, system_extractor 32, own 21; `tests/test_web_guarded.py` 5. The files that name `execute_acl_change`,
  `acl_change`, `record_bypass` or `/admin/roles` (search over `tests/`): `test_identity_agents_pages.py`,
  `test_identity_approvals.py`, `test_identity_audit_recent.py`, `test_web_admin_approvals.py`,
  `test_web_admin_documents.py`, `test_web_admin_gates.py`, `test_web_admin_roles_rules.py`, `test_web_guarded.py`
  and the four frozen role files: all in the runs above. The one failing id is in `baseline-failing-ids.txt`: no
  failing id the base does not have.
- Frozen check from `frozen-tests.txt`: all nine OK.
- `card check`: 18 cards valid, 0 errors, 0 warnings. `card render --root docs/components --check`: 18 cards, 0 not
  current. `map-check --scope touched` (base `origin/feat/platform-mvp`, settings `docs/components/map-check.yaml`,
  head `38153495`): problems 0; declared 0 findings (the first run found `admin_role_data.py` pulling in
  L2-BRAIN-BUILDER undeclared: now declared); compatible 7 (`need_added`: the seven new routes' N-1); complete 5
  `no_contract` (`create_admin_blueprint`, `run_guarded`, and L2-PLATFORM-ACCESS's `attach_document_routes`,
  `execute_acl_change`, `execute_corpus_purge`, all as before; `execute_role_data` has its promise); touched without a
  card: `src/identity` (`approvals.py`, `roles.py`), as for RB-1 to RB-4; **budget: L2-PLATFORM-ADMIN 53,280 tokens
  against 16,000** (open 5).
- **Screenshots**, outside the repository, in
  `C:\Users\siran\AppData\Local\Temp\claude\1838582cff9d51e1\af9716ae-0421-42ac-9fac-6acb192cc6e6\scratchpad\rb5\shots\`:
  `rb5-<page>-<1440|390>.png` for `roles-bau` (a role with two folders and a document), `dry-walled` (adding a walled
  folder), `dry-open` (adding an open folder: `walls-open` and `already-read-in`), `dry-remove-opens` (removing a
  rule's only group: `opens`), `adopt-mitglied` (Mitglied adopting the Core's «Mitglied»), `grants-dry` (the move's dry
  run) and `freigaben-queued` (a change queued with the bypass off, seen by a second administrator); `beside-*` puts
  each beside `07-rollen.png`; `rb5-facts.json` what the browser measured. The real app (headless Chrome), invented
  people, groups, folders and documents, an in-memory fake Core (tools in `..\rb5\tools\`); its schema was dropped.
  At 390 px no Rollen page scrolls sideways (the row of roles scrolls inside itself, as `parts.tabs` does); lowest text
  contrast 4.76 to 1; a keyboard walk shows a ring on every control of `<main>` (14 to 25 per page, none without); no
  request left the installation; the fake Core recorded no write (dry runs only, and the queued change). **The
  Freigaben page scrolls sideways at 390 px** (863 px): it is the old approvals page, not reworked by PMVP-40 (FU-23);
  its empty tables already overflow, and the target column (`role:<id>`, monospace) is as wide as a pointer was. The
  server's own log over all of this (INFO) holds no path, name, group id or address of RB-5's (one bootstrap line of
  the app's own start).

**Fixes after the verification** (coordinator, 2026-11-22; the verification passed with notes):

- **F1 (low):** a request stored `confirm=1` whenever the form sent it, also when its dry run gave no warning; a folder
  that became open before the second person confirmed was then walled without anyone having seen a warning, and the
  Freigaben line said «Warnung bestaetigt». Now `_change` stores `confirm` only when the plan needed it
  (`confirm and plan.needs_confirm`); the executor plans again as before and refuses (`_WARNS_NOW`, the page's words)
  when the new plan needs a confirmation the request lacks. Tests: `test_a_confirmation_counts_only_for_the_warning_it_answered`
  (bypass off, `confirm=1` on a walled folder, the rule emptied, a second administrator approves: nothing sent, the
  request stays approved, no «bestaetigt» on the page) and `test_a_confirmation_the_dry_run_asked_for_is_stored`.
- **Adopting checks for data under the lock:** the adopt now takes every role's row (`lock_all`) first and runs all
  its checks (the group, another role's group, the present group's data at the Core) inside that transaction; the Core
  is read while the rows are held. For the lock to protect anything, a data change now holds its role's row too:
  `execute_role_data` takes it in share mode (`RoleStore.share`, an addition to `roles.py`) from the check of the
  role's group to its audit row, the Core call in between; a refusal's `error` row is written after that transaction
  is undone. Two-connection tests, both ways: `test_an_adopt_checks_for_data_only_once_it_holds_the_rows` (a data
  change in flight writes a rule for the present group: the adopt waits, then refuses, nothing changed) and
  `test_a_data_change_waits_for_an_adopt_and_then_refuses` (an adopt holds the row and switches the group: the data
  change, bypass on, waits, then sends nothing). The three new tests fail on the code before the fix (tried).
- Cost: an adopt holds every role's row while it reads the Core (other role changes wait up to that long); a data
  change holds one role's row while it writes (an adopt or a change of that role waits).

**Merge of RB-FIX-1** (coordinator, 2026-11-22; `origin/feat/platform-mvp` at `d655226f`, merged in `68da849f`):

- Conflicts resolved keeping both sides. `roles.py`: RB-FIX-1's `CLAIM_GROUP_ROWS` (the claim's rows for the readers
  of many people) and its NUL refusal in `clean_name` stay; RB-5's `active_claim_groups` now reads `CLAIM_GROUP_ROWS`, so
  the claim's rule has one SQL definition (its own test still holds it to `claim_groups`); `RoleStore.share` stays. The
  move's role names already drop control characters (NUL included) before `create_role`. `administration.md`: both
  status rows and both work orders. RB-FIX-1's `knovas_client.py` logging comes in unchanged.
- **Cards:** the admin card and `boundaries.yaml` take RB-FIX-1's split; RB-5's lines moved to
  **L2-PLATFORM-ROLES** (`L2-platform-roles.card.yaml`) with `admin_role_data.py`: R-2 (was R-7 on my branch), the
  seven routes and `execute_role_data` with their lines, the term «dry run», the hides line, the Core method, the
  review entry, and uses of L2-PLATFORM-ADMIN (`run_guarded`, which this card's code now imports) and L2-BRAIN-BUILDER
  (`builder.access.Tree`). They were never on the base, so they carry no aliases. L2-PLATFORM-ACCESS's
  `execute_acl_change` now uses L2-PLATFORM-ROLES's `execute_role_data` (expecting its P-1). The admin card holds
  nothing of RB-5 and is within its budget.
- **Open (for the coordinator):** map-check reports **L2-PLATFORM-ROLES over budget, 33,953 tokens against 16,000**
  (card 13,877, code 19,934: `admin_roles.py`, its page and `admin_role_data.py`). A card of its own for a role's data
  would hold about 18,000 (the module alone is about 10,000) and still be over unless the module is cut down or split
  (the planning, which needs no Flask, could leave the routes' module). Not split here: the order was "the Rollen card",
  and a split is a decision of its own. Every other map-check line in scope is as before: problems 0, declared 0,
  compatible 7 (`need_added`: the seven routes' N-1), complete 6 `no_contract` (the five known ones and RB-FIX-1's
  `attach_role_routes`), touched without a card `src/identity`.
- **Checks after the merge** (one process per file, own database):
  the nine frozen files 497 passed, 0 skipped (PMVP-03 29, PMVP-10 33, PMVP-11 40, PMVP-20 88, PMVP-21 13, RB-1/2 66,
  RB-3/4 106, **RB-5 95**, RB-6/7 27); `tests/test_web_admin_*.py` (11 files) 835 passed (gates 331, own 25, RB-FIX-1's
  input checks 19); `tests/test_identity_*.py` (21 files) 334 passed, 1 failed, 1 skipped (the failure is
  `test_identity_broker_key.py::test_private_key_is_created_readable_only_by_its_owner`, in the baseline; the skip is
  POSIX mode bits on Windows); `tests/test_shell_*.py` (8 files) 403 passed; `tests/test_knovas_client_*.py` (6 files)
  287 passed. Frozen check: all nine OK. `card check` 20 cards valid, 0 errors, 0 warnings; `card render --check` 0 not
  current.

**Split «Rollendaten»** (coordinator, 2026-11-22): RB-5's part has a card of its own,
**L2-PLATFORM-ROLE-DATA** (`docs/components/L2-platform-role-data.card.yaml`), the way RB-FIX-1 split Rollen and
Agenten: `admin_role_data.py` and a new template `templates/_role_data.html` (RB-5's parts of the Rollen page and their
words, which `admin_roles.html` imports with its context and calls: the move offer above the row of roles, the content of
`<section data-role-part="data">`). R-1, the seven routes and three calls (`attach_role_data_routes`, `role_data_part`,
`execute_role_data`), each with a gate line (routes) and a promise; uses of L2-PLATFORM-ADMIN (`run_guarded`),
L2-BRAIN-BUILDER (`builder.access.Tree`) and EXT-BRAIN-API; L2-PLATFORM-ROLES uses its two calls, L2-PLATFORM-ACCESS
its executor; `boundaries.yaml` has the component. The Rollen card is back to the base plus that use and a pointer. To fit
the budget, `admin_role_data.py` was made shorter (53,573 to 39,790 characters: the invariants live on the card, the
page's words in the template; behaviour unchanged, the views pass codes the template words) and the card's evidence
lists name at most five checks each. **Sizes (map-check, `--worktree` on the final tree):** L2-PLATFORM-ROLE-DATA
15,700 tokens (card 5,603, code 9,947), L2-PLATFORM-ROLES about 13,000, the admin and access cards within the budget;
budget 0 findings in scope, declared 0, compatible 0, complete 6 `no_contract` (the five known ones and RB-FIX-1's
`attach_role_routes`), problems 0. `card check` 21 cards valid, `card render --check` 0 not current.

**Open, for the coordinator and the verifier:**

1. **Founder to check (decision 2):** with the bypass on (the default) an administrator changes a role's data alone,
   recorded as `approval.bypassed`. Four eyes even with the bypass on is one change in `requires_approval`; a firm with
   one administrator could then not change a role's data at all.
2. **Direct grants that stay** after the move (end dates, the administrator group, groups the Core does not list,
   groups of roles the person is not in) reach no agent under «Ein»; whether they must be handled first is the
   founder's or coordinator's (decision 5). The people page still grants groups directly (decision 9).
3. **A Core filter for own-rights documents would help**: the part finds them by walking the group's listing (which
   also holds every folder-governed document and those of groups below), bounded at 5 pages per page view. A
   `GET /secured/documents?…explicit=true` (or similar) is a candidate Core request.
4. **The real client puts a pointer in a query string** (`document_access(pointer)`, `documents(prefix=pointer)`), as
   the «Zugriff» pages do; urllib3 logs a request line with its query at DEBUG when its logger is at DEBUG (the app
   holds it quiet). Not RB-5's to change (`knovas_client.py`, FU-43).
5. **The cards' budget:** resolved by the split «Rollendaten» above (every card in scope within 16,000).
6. Verifier: the frozen file takes about 9 minutes; run in a database of your own. A killed run leaves schemas behind
   (recreate the database).
