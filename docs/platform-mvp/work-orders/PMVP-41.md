# Work order PMVP-41: «Was sieht …?» in Admin

**Draft for the coordinator** (2026-11-22; the founder's answer on names written in the same day). Everything under
"Decisions this order takes" is a proposal until the coordinator confirms it and the test file is frozen.

| | |
|---|---|
| Item | PMVP-41 (Jira: filed by the coordinator), plan `FEAT-2026-11-19-platform-mvp` § 4 Admin **A-05** ("pick a person and a document and get yes or no with the reason; run a search as that person"; "Exists in the Builder; move to Admin"), spec item SPEC-platform-mvp-13; follow-up **FU-20** ("Was sieht …?" is still a Builder file). KnowledgeBase `docs/ModernDocs/plans/2026-11-19-platform-mvp.md` on `origin/steering/platform-mvp-provider` |
| Design | `docs/platform-mvp/design/role-builder.md` § 2.2 (a document's readers are its group and that group's ancestors; one rule governs a document), § 2.3 (the claim; the Core verifies it only under `brokered`), § 4 (one claim for every agent), § 6 rule 7 (Aus, Probe, Ein) |
| Builds on | RB-2 (`identity.roles.claim_groups`), RB-5's planning (`web_interface/admin_role_data.py`: `Sight`, the readers of rules and documents, `tenant_brokered`), RB-6's claim rule (`identity.agents.AgentStore.claim_for`, `AgentPrincipal`), PMVP-34's cap (`brain_views.limit.bounded`, in build) |
| Core requests | none new. The answers become Knovas's own once the tenant is `brokered` (switched by an administrator through **CR-R8**, PMVP-42, or by Knovas through **CR-R1**) and the Platform can read that (**CR-R1a**, its client call comes with PMVP-42). Until then every answer is computed and says so |
| Mock-up | `docs/platform-mvp/reference-screens/05-zugriff.html` (and `out/05-zugriff.png`): the card «Prüfen» («Darf <Person> das Dokument <…> lesen?» – «Nein. Die Gruppe … » – «Als … suchen») on the page «Zugriff» |
| Bet | `BET-2026-11-platform-mvp` (approved 2026-11-19) |
| Class | **sensitive** (plan § 6.5 names "Was sieht …?"; Siran approves before `main`): an administrator sees what another person, a role or an agent reads, and searches as them. No migration, no gate change, no Core write |
| Depends on | PMVP-40 (the Admin look, integrated), RB-1 to RB-7 and RB-5 (integrated), **PMVP-34** (the 25 cap; if it is not integrated when you start, start from `feat/platform-mvp` and merge it in once it is; never write a second cap) |
| Branch / worktree | `feat/pmvp-41` from `origin/feat/platform-mvp` (`74478d1a` or later), worktree `.worktrees/pmvp-41` |
| Frozen tests | `P/tests/test_pmvp41_what_sees_acceptance.py` (31 tests), blob id in `docs/platform-mvp/frozen-tests.txt` once the coordinator freezes it. `P` = `KnovasPlatform/components/docbridge_integration` |
| Card | `docs/components/L2-platform-access.card.yaml` («Zugriff») if it stays within its budget, else a card of its own («Zugriff prüfen») the way RB-5 split «Rollendaten»; `boundaries.yaml`; the pages rendered |

## Goal

On the Admin page «Zugriff» an administrator picks a person, a role, the Knovas Assistent (for a person) or an
external agent, and asks three questions: **what does X read** (the reach), **may X read this document** (yes or no
with the reason), and **what does a search find as X**. Each answer is built from a few per-thing reads of the Core,
never from the whole tenant, and says whether it is **computed** ("what X would read under «Ein»") or **Knovas's own
answer** (once the tenant is `brokered`).

## What PMVP-41 does not do

- Show or switch the enforcement position (PMVP-42; it adds its part to the same page).
- Probe (RB-9, CR-R4).
- Change who reads what: it writes nothing to the Core and nothing to the Platform's tables but one audit row.
- Remove the Builder's «Zugriff erklären» (`POST /builder/api/access/explain`): it stays until a later clean-up
  (it reads only direct grants, see "Found while drafting").

## What to build (the frozen tests pin it)

### 1. The page and the routes

All in the console's blueprint (`admin`), each behind the console's own `require_admin` (`allowed_roles ==
frozenset({"admin"})`); signed out 302 to the sign-in; every other function 403 and nothing asked. **Every POST reads
`csrf_token` first** (400, nothing asked, no audit row). Suggested home: a new module `P/src/web_interface/admin_access.py`
with `attach_access_routes(bp, gate, *, csrf_valid, csrf_token, page_context, client_factory, require_admin)`,
attached from `create_admin_blueprint`, and a template `admin_access.html` in PMVP-40's look.

| Method and path | Form fields | Answers |
|---|---|---|
| `GET /admin/access` | | the page «Zugriff»: the three forms; writes nothing, asks Knovas nothing it does not need for the forms |
| `POST /admin/access/reach` | `wer`, `fuer`, `seite` (1 when missing) | 200 with what X reads; 400 refused |
| `POST /admin/access/check` | `wer`, `fuer`, `pointer` | 200 with yes or no and the reason; 400 refused (also an unknown or empty document) |
| `POST /admin/access/search` | `wer`, `fuer`, `query` | 200 with the hits; 400 refused (also a role, or an empty query) |

- **`wer`** is `person:<user id>`, `role:<role id>` or `agent:<agent id>`; the page offers every person, every role
  and every agent that is not revoked in one `<select name="wer">`. **`fuer`** is a person's id (`<select
  name="fuer">`), needed for the Knovas Assistent (it always acts for a person) and ignored otherwise. Anything else,
  an unknown id, or the assistant without an existing person: 400, nothing asked.
- The menu: `app.py`'s `admin_menu` gains `('access', 'Zugriff', 'admin.access', False)` (after «Agenten»); the
  template sets `admin_tab = 'access'`. The menu reads the gate, so only administrators see it (PMVP-11 unchanged).
- A pointer and a query travel in form fields only, never in a URL the console builds (RB-5 rule 8).

### 2. Whose sight: the claim rule

X's groups are exactly what the Core would receive in X's claim:

| X | Groups | Principal for `asserting_for` |
|---|---|---|
| a person | `claim_groups(conn, id)` (running direct grants plus her roles' Core groups, RB-2) | the person (`gate.users().get(id)`) |
| a role | its `core_group_id` (none: nothing walled) | none: a role has no claim |
| an agent | `AgentStore(conn).claim_for(id, on_behalf_of=fuer or None).groups` (its roles' groups, narrowed to its person, never a person's direct grant: FU-41) | `AgentPrincipal(id, claim.on_behalf_of)` |

**X reads nothing** (`data-reads-nothing`, no rows, «Nein» for every document, no search sent) when the person is not
active, the agent is revoked or its person is not active (`claim_for` raises `AgentError`), or the claim would carry
more than 32 groups (`TooManyGroupsError`): the Platform signs nothing for them, so Knovas would answer nothing.

### 3. Who reads what: RB-5's rule, read per thing

A document's groups are its own rights, else the groups of the longest folder rule its pointer starts with, else none
(open). X reads it when it is open or when one of X's groups is one of its groups **or above one of them** in the
Core's tree (`builder.access.Tree`; the closure `Sight.closure` of RB-5). Reuse RB-5's readers and `Sight` (import
them, or move them into a shared module both use without changing RB-5's behaviour: its frozen file stays green).

**The reach** (`POST …/reach`), never from the whole tenant:

- **Folder rows**: every live folder rule with at least one group that X reads (`client.folder_rules()`, the rules are
  configuration, not documents), `data-reads="rule:<rule id>"`, showing the prefix and the groups by name. An open
  rule is no row.
- **Document rows**: every document with **rights of its own** (the Core's definition: `acl_folder_id` none or
  `__acl_folder_none__` and a group set) that X reads, `data-reads="document:<document_uuid>"`, found with
  `client.documents(group=<g>)` for each of X's groups **and each group below them** in the tree, bounded as RB-5's
  walk is (at most 5 pages of 200 per group; when a walk cannot finish, the answer says the list may be incomplete). A
  document governed by a folder rule shows through its rule, not as a row.
- **The cap (B-01, PMVP-34):** at most **25 rows** on the first answer (`bounded`), the rest a count `data-more="<n>"`
  that opens the list: `POST …/reach` with `seite=2, 3, …`, at most 50 rows a page (`paged`), every row exactly once.
- One sentence says that X also reads everything open (no rule, or a rule without a group, and no rights of its own).

**One document** (`POST …/check`): `client.document_access(pointer)` (unknown: 400), the governing rule from
`folder_rules()`, and whether the document has rights of its own from `client.documents(prefix=pointer)` (a filtered,
per-thing listing). The answer is an element with `data-answer="ja"|"nein"` and `data-source`; the reason names the
governing rule (`data-reason-rule="<rule id>"`, its prefix shown) when a rule gives the document its groups, and each
of its groups (`data-reason-group="<group id>"`, its name shown); an open document has none.

**Search as X** (`POST …/search`): `client.search_documents(query, limit=…)` **inside `client.asserting_for(<principal>)`**.
Each hit is shown by name only (title and pointer, `data-hit="<pointer>"`): **never passage text** (`snippet`,
`top_chunks`, `ingested_summary`), at most 25.

### 4. Before «Ein» and after

`ein` = the client reports the tenant as `brokered` (`tenant_brokered(client)`, RB-5: `getattr(client,
"rbac_posture", None)` called, a dict whose `mode` is `"brokered"`). The real client has no such call until PMVP-42
adds it, and the Core has no route until CR-R1a, so today every answer is a preview.

| | Before «Ein» (`data-effect="vorschau"`) | After «Ein» (`data-effect="ein"`) |
|---|---|---|
| Words | «wirkt mit «Ein»» (RB-5's pinned words) and that today, under «Aus», everyone reads everything | none of them |
| Reach | computed (§ 3) | computed (§ 3): the same rule the Core applies; labelled as computed |
| One document | computed, `data-source="berechnet"`; **never Knovas's `document_readable`** (under `disabled` it says yes to everything, under `enforcing` it ignores the Platform's claim) | **Knovas's own answer**: `document_readable(pointer)` inside `asserting_for(<principal>)`, `data-source="knovas"`; it counts even when it differs from the computation (the page may say they differ). A role stays computed |
| Search | all hits Knovas returned, each with `data-would="ja"|"nein"` computed from `document_access(<its pointer>)` (at most 25 per-thing reads) | Knovas's hits for X as returned, no mark |

The answer sits in one element `data-what-sees="<wer>"` with `data-effect`.

### 5. Audit and the management pages

- One audit row per answered question: action `access.what_sees`, actor the administrator, `target_type` `user`,
  `role` or `agent`, `target_id` the id; `detail` holds `question` (`reach` | `document` | `search`), `effect`, and
  counts or the yes/no (and `fuer` by id for the assistant). **Never a pointer, query, title, name, group id or
  group name.** A refused question writes none. (`audit.record`, best effort, is enough: the row records a look, not
  a change.)
- `management/administration.md`: «Was sieht …?» from "Planned" into what exists, with what it shows before and after
  «Ein»; its status row; `management/README.md`'s row; `data-protection.md` (an administrator sees the names of the
  documents another person reads, and every look is recorded by id).

## Rules

1. **Per-thing reads only** (L2-PLATFORM-BRAIN-VIEWS POST-03 and B-02, applied to Admin): one starting point (X);
   never `graph_export`, `graph_nodes`, `graph_edges`, `iter_documents` or a `documents()` call without `group` or
   `prefix`.
2. **Knovas decides once it can** (`knovas_client.document_readable`'s docstring): after «Ein» the yes or no is
   Knovas's; the computation is the reason, never an override.
3. **Never a second copy of the policy for enforcement.** The computation only previews and explains; nothing is
   filtered or hidden on its strength.
4. **No customer text in logs** (FU-14, FU-46): ids, counts and error classes only, DEBUG included.
5. **Names, never passages**: a search as X shows titles and pointers, no passage, summary or chunk text.
6. **Nothing written** to the Core, and nothing to the Platform but the audit row.
7. **Escaping and addresses**: every prefix, pointer, title and name escaped (no `|safe`); every href and form action
   from `url_for` (FU-34).
8. **No gate changes**; the look is PMVP-12's parts and PMVP-40's Admin look, brand tokens only, nothing scrolls
   sideways at 390 px, contrast 4.5 to 1, a focus ring on every control.

**Where the frozen file pins each rule** (31 tests): § 1 gates and tokens, the menu, the page's offer (6); § 2 the
claim rule for people, roles, the assistant and external agents, reading nothing, refused subjects (8); § 3 the cap
and its list, escaping (2); § 4 one document before and after «Ein», open documents, groups above, rights of their
own, an unknown document, a role without principal, every posture but `brokered` (7); § 5 search as X before and after «Ein», the
assistant's claim, names only, a role refused, the cap (5); § 6 no whole-tenant read and no write (1); § 7 audit (1);
§ 8 logs at DEBUG (1). Reference run: 31 passed; 18 mutants, all killed (see the coordinator's draft report).

## Decisions this order takes (draft: for the coordinator to confirm before the tests are frozen)

1. **Names under «Ein»: decided by the founder on 2026-11-22: names only, every look audited.** «Was sieht …?»
   shows the titles and pointers another person reads (reach, search as X), also documents the administrator
   cannot read herself (the administrator group "grants no read access to walled content", Core `models.py`);
   never passage text, and every answered question writes its audit row by id (§ 5).
2. **Computed before «Ein», Knovas's after.** The only honest answer before `brokered`: under `disabled` Knovas says
   yes to everything, under `enforcing` it ignores the Platform's claim (design § 2.3).
3. **The reach is always computed**, also after «Ein»: Knovas has no "what does X read" route, and listing documents
   inside `asserting_for(X)` would make the Core filter the administrator's inventory as X (a second meaning for one
   route). The single-document answer and the search are Knovas's own after «Ein».
4. **A role has no principal**: its answers stay computed (no claim is ever minted for a role).
5. **The assistant needs a person** (`fuer`): it never acts alone (RB-6).
6. **Audit with `audit.record`** (best effort): the row records a look; it must not block the answer.
7. **The page is «Zugriff» (`/admin/access`)**, as in `05-zugriff`; PMVP-42 adds the position and the switch to it.

**Found while drafting** (for the coordinator; not this item's to fix):

- The Builder's `POST /builder/api/access/explain` explains with a person's **direct grants only**
  (`_running(person["grants"])`), not her roles' groups (RB-2), so its path can be wrong since the role builder. A
  candidate for the clean-up that retires it (FU-20).
- The Core's `/secured/documents` filters by the caller's own groups ("Gefiltert wird mit der eigenen Freigabe des
  Aufrufers"). Under «Ein» the administrator's claim carries Administration's group; whether the Core lists other
  groups' documents for it (the administrator group "may read, change or clear any document's ACL") must be checked
  on the demo tenant once CR-R1 lands, for this page and for RB-5's part «Darf lesen» alike.

## Files you may change

- new: `P/src/web_interface/admin_access.py` (suggested), `P/src/web_interface/templates/admin_access.html`,
  `P/tests/test_web_admin_access_*.py` (your own tests)
- `P/src/web_interface/admin.py`: attaching the routes only
- `P/src/web_interface/app.py`: the one `admin_menu` line only
- `P/src/web_interface/admin_role_data.py`: only to make its readers and `Sight` importable (moved or renamed), no
  behaviour change
- `P/src/web_interface/static/css/admin.css`: rules of the new page
- `P/tests/test_web_admin_gates.py`: the four routes in its lists (FU-17); say so
- `docs/components/…` (the card, its page, `boundaries.yaml`; `card check`, `card render`, `map-check --scope touched`
  as in `work-orders/PMVP-01.md`), `docs/platform-mvp/management/administration.md`, `README.md`, `data-protection.md`
- this order: only the "Builder's notes" section

## Files you must not change

- the frozen test files (`docs/platform-mvp/frozen-tests.txt`); `P/tests/conftest.py`; every migration (none is
  needed: if one seems needed, stop and write why)
- `P/src/knovas_client.py` (PMVP-42 adds `rbac_posture`), `P/src/identity/**`, `builder/**`,
  `web_interface/builder_routes.py`
- any route's gate or decorator, `_require_roles`, the approvals module
- `_parts.html`, `_sidebar.html`, `parts.css`, `shell.css`; `brain_views/limit.py` (PMVP-34's)
- `docs/platform-mvp/design/**`, `reference-screens/**`, `core-requests.md`

If the work needs one of these, stop and write the reason under "Builder's notes" instead of changing it.

## Done when

1. `cd P && py -3 -m pytest tests/test_pmvp41_what_sees_acceptance.py -q -p no:cacheprovider` is green and not
   skipped, on a test database of your own (`docker exec pmvp-platform-test-db createdb -U platform
   knovas_platform_test_pmvp41`, `PLATFORM_DB_TEST_DSN=postgresql://platform:testpw@127.0.0.1:55433/knovas_platform_test_pmvp41`,
   `PLATFORM_DB_REQUIRED=true`). One pytest process at a time. The frozen check prints all OK.
2. Every frozen file stays green; each file in a process of its own: `tests/test_web_admin_*.py`,
   `tests/test_shell_*.py`, `tests/test_pmvp11_navigation_acceptance.py`, the RB frozen files. **Do not run the whole
   suite.** No failing id the base (`baseline-failing-ids.txt`) does not have.
3. Screenshots outside the repository (say where): the page; a reach before «Ein» with a long list and its «weitere»;
   a «Nein» with its reason; a search as a person before «Ein» (marks) and after (a fake reporting `brokered`); at 1440
   and 390 px beside `05-zugriff.png`. Invented data, a fake Core.
4. Card checks clean. Commits `PMVP-41: …` ending with the co-author line; `feat/pmvp-41` pushed. No pull request.

## Builder's notes

(the builder writes here: what was decided, what is open, what the verifier should look at)
