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

Builder session of 2026-11-22 (AGENT-claude-code). `feat/pmvp-41` from `origin/feat/platform-mvp` (`1871df4c`), test
database `knovas_platform_test_pmvp4142`.

### What was built

| File | What |
|---|---|
| `P/src/web_interface/admin_access.py` (new) | `attach_access_routes` (the page and its three questions, behind the console's `require_admin`, `csrf_token` first); `resolve` (X from `wer`/`fuer`, the claim rule of § 2); `reach_rows`, `reach_page`, `check_document`, `search_as` (§ 3, § 4); `clean_pointer`, `clean_query`; one audit row `access.what_sees` per answered question |
| `P/src/web_interface/templates/admin_access.html` (new) | the page «Zugriff» in PMVP-40's look: the card «Prüfen» with three forms, the answer below in a card of its own (`data-what-sees`, `data-effect`) |
| `P/src/web_interface/admin_role_data.py` | one block of three lines: public names for RB-5's readers (`read_core`, `groups_of`, `live_rules`, `governing`, `core_groups`, `walk`, `own_rights`, `WalkUnfinished`, `PAGE_WALK`); the same functions, nothing else changed (RB-5's frozen file green) |
| `P/src/web_interface/admin.py` | attaching the routes (after the agents') |
| `P/src/web_interface/app.py` | `('access', 'Zugriff', 'admin.access', False)` after «Agenten» |
| `P/src/web_interface/static/css/admin.css` | the rules of the new page (brand tokens only) |
| `P/tests/test_web_admin_access_rules.py` (new, 27, no database) and `…_routes.py` (new, 4) | below |
| cards | a new card `L2-PLATFORM-ACCESS-CHECK` «Zugriff prüfen» (`docs/components/L2-platform-access-check.card.yaml`, its page, `boundaries.yaml`); `L2-PLATFORM-ACCESS` points at it; `L2-PLATFORM-ADMIN` declares its use; pages rendered |
| management | `administration.md` (a section «Was sieht …?», the planned line, the status row, two limits, where the detail is), `README.md`'s row, `data-protection.md` (a paragraph, its row, the founder's decision of 22 Nov) |

### What I decided, and why (the verifier should look here first)

1. **PMVP-34 merged in, not copied.** PMVP-34 is built on `origin/feat/pmvp-34` but not integrated. The order says
   never a second cap, so I merged `origin/feat/pmvp-34` (`db060a06`) into `feat/pmvp-41` (commit `4f424931`, no
   conflict) and draw through its `bounded`, `paged`, `page_number` and `more_text`. **Consequence for the
   coordinator:** integrating `feat/pmvp-41` also integrates PMVP-34's three commits; integrate (or verify) PMVP-34
   first. If PMVP-34 changes after its verification, a later merge of `feat/platform-mvp` brings the change.
2. **Three forms, each with its own `wer` and `fuer`.** One form with three buttons would send the Enter key of the
   document or search field to the first button. After an answer, X (and the assistant's person) stays selected in
   all three. No script.
3. **The reach's pages.** Page 1 is `bounded(rows)` (25 drawn, `data-more` = the rest); `seite` 2, 3, … is
   `paged(rest, seite - 1)` (50 a page), `data-more` the rows after that page; «Zurück» on pages after the first; a
   page past the end is the last (`paged`), a list without a rest has only page 1. Rules first (by prefix), then
   documents (by pointer).
4. **`fuer` counts for the assistant only.** For an external agent it is ignored (`claim_for` without
   `on_behalf_of`, so the agent's own person applies): passing it would raise `AgentError` for an agent acting on its
   own. The assistant needs an existing person (any state, else 400); a person who is not active reads nothing
   (`person-inaktiv`).
5. **One document's groups:** rights of its own (the listing by prefix, at most RB-5's five pages) win; else the
   longest folder rule's; else what `document_access` holds (normally none: open). A rule without a group leaves the
   document open and names no rule. Under «Ein» a subject that reads nothing is not asked about (the Platform signs
   no claim for it): «Nein», computed.
6. **The search's marks** come from `document_access` of each hit (at most 25 reads), as the order says; a hit whose
   rights cannot be read carries no mark («unbekannt»). The search asks Knovas for 25 hits; the page says how many
   Knovas found when there are more.
7. **A Core that cannot be read** (groups, rules, documents, the search, the document's rights): 502 with the page's
   own words and **no audit row** (no question was answered). A refusal writes none either.
8. **The audit row's `detail`:** `question`, `effect`, and for the reach `rows`, `page`, `partial`; for a document
   `answer` and `source`; for a search `hits`; `reads_nothing` (the code) when X reads nothing; `fuer` (an id) for
   the assistant. Ids and counts only.
9. **A card of its own** («Zugriff prüfen», the way RB-5 split «Rollendaten»): with the page, `L2-PLATFORM-ACCESS`
   would be over 20,000 tokens (14,832 now, the page's code alone 5,309). PMVP-42 adds its part to this card.

### Existing tests changed (each predicted by the order's menu line, template and FU-17)

- `tests/test_web_admin_gates.py`: `/admin/access` in `PAGES`, the three questions in `FORMS` (about `{person}`);
  its fake `_Client` gains `document_access` and `asserting_for` so the administrator's 200 can be shown (FU-17, named
  in the order).
- `tests/test_shell_nav_links.py`: `admin.access` («Zugriff») in `ADMIN_PAGES` and `NAMES`, after «Agenten». The test
  pins the menu's exact list; the order adds the entry.
- `tests/test_shell_nav_partial.py`: `admin_access.html` in `ADMIN_TEMPLATES`. The test requires every
  `admin_*.html` to be listed; the order adds the template.

The two shell files are not in "Files you may change"; RB-3 and RB-7 changed the same lists for their pages.

### Checks

- **Tests**, in the pinned environment (`scratchpad\pinned-venv`, `requirements.txt`'s versions: pytest 7.4.3, tenacity 8.2.3, cryptography 42.0.0; FU-50), each file in a pytest process of its own, one after another, on `knovas_platform_test_pmvp4142`, all exit code 0, nothing failed, skipped or errored:
  - frozen: `test_pmvp41_what_sees_acceptance.py` 31, `test_pmvp11_navigation_acceptance.py` 40, `test_rb5_role_data_acceptance.py` 95, `test_rb12_roles_acceptance.py` 66, `test_rb34_roles_page_acceptance.py` 106, `test_rb67_agents_acceptance.py` 27, `test_pmvp34_view_limit_acceptance.py` 17;
  - mine: `test_web_admin_access_rules.py` 27, `test_web_admin_access_routes.py` 4;
  - the rest the order names: `test_shell_nav_links.py` 31, `test_shell_nav_partial.py` 52, `test_shell_nav_styles.py` 33, `test_shell_parts_css.py` 86, `test_shell_parts_macros.py` 49, `test_shell_parts_styleguide.py` 52, `test_shell_styleguide.py` 13, `test_shell_tokens.py` 88, `test_brain_views_limit_unit.py` 28, `test_web_admin_role_data_rules.py` 25, `test_web_admin_approvals.py` 22, `test_web_admin_doc_fields.py` 124, `test_web_admin_documents.py` 66, `test_web_admin_ingestion.py` 161, `test_web_admin_input_checks.py` 19, `test_web_admin_people.py` 28, `test_web_admin_roles_rules.py` 13, `test_web_admin_system_doc_fields.py` 14, `test_web_admin_system_extractor.py` 32, `test_identity_agents_pages.py` 10.
  1359 passed in 29 files. None of these files has an id in `baseline-failing-ids.txt`. The whole suite was not run. The same files had passed before in the machine's own Python (newer libraries).
- **`tests/test_web_admin_gates.py`** (367 tests, the slowest file): 367 passed in the machine's own Python; its run in the pinned environment was still going when this was pushed (the coordinator asked for the push at once) and its result follows in the next commit of this branch.
- **Frozen check** (header of `frozen-tests.txt`): all 13 OK.
- **Cards** (KnowledgeBase-worktrees `pmvp-01/scripts`, `py -3 -m brain_runner`, read only; nothing reached a tenant):
  `card check` on the new card and the three changed ones: valid, 0 errors, 0 warnings. `card render --root
  docs/components`: 6 pages written (the new card, access, admin, and the consumer tables of `EXT-brain-api.md`,
  `L2-platform-brain-views.md`, `L2-platform-role-data.md`); `--check` 0 not current, 0 problems. `map-check --scope touched --head feat/pmvp-41` (the committed files), base
  `origin/feat/platform-mvp`: declared 0, compatible 0, **budget 0** (L2-PLATFORM-ADMIN 15,471, L2-PLATFORM-ACCESS
  14,630, L2-PLATFORM-ACCESS-CHECK 8,267, L2-PLATFORM-ROLE-DATA 15,783, L2-PLATFORM-BRAIN-VIEWS 11,276 tokens; a Windows
  working tree with CRLF line ends counts about 280 more for the Rollendaten card, so measure with `--head`),
  complete 24 (open point 3); touched without a card: `src/web_interface` (`app.py`, as always).

### Screenshots

Outside the repository, in `C:\Users\siran\AppData\Local\Temp\claude\1838582cff9d51e1\af9716ae-0421-42ac-9fac-6acb192cc6e6\scratchpad\b4142\shots41\`
(the real app on the test database, a fake Core with invented people, groups and documents; nothing reached a
tenant), each at 1440 and 390 px, beside `00-reference-05-zugriff.png`:

- `41-page`: the page with its three questions;
- `41-reach-weitere`: what Sara Keller (Steuerrecht) reads: 61 things, 25 rows drawn and «36 weitere»;
- `41-check-nein`: Jonas Meier (Praktikum) and a document of the mandate: «Nein.», the folder rule and its two groups;
- `41-search-vorschau`: a search as Jonas before «Ein»: four hits by name, two marked «würde nicht lesen»;
- `41-search-ein`: the same search with a fake reporting `brokered`: Knovas's own two hits, no marks;
- `41-check-ein-assistent`: the Knovas Assistent for Marco Frei under «Ein»: Knovas's «Ja.» and the computed reason.

Nothing scrolls sideways at 390 px (`facts-41.json`). After a question the answer stands above the forms, so it is
the first thing on the page.

### Open, for the coordinator and the verifier

1. **PMVP-34 rides along** (decision 1).
2. **The Builder's «Zugriff erklären»** stays, as the order says (it reads direct grants only; FU-20's clean-up).
3. **`complete` findings in map-check's scope** (report only): 14 `not_on_card` on `L2-PLATFORM-ROLE-DATA` (the
   public names of its readers and `Sight`, `tenant_brokered`, `CoreUnreadable`, `POINTER_MAX`) and 5 on
   `L2-PLATFORM-BRAIN-VIEWS` (`limit.py`'s functions): in-process imports, named in the new card's `uses` method.
   The Rollendaten card is at 15,783 tokens of 16,000 (committed files), so they are not listed there as operations.
4. **The admin card** is at 15,471 tokens after this item (admin.css and admin.py); PMVP-42 adds to both.
5. From the order's "Found while drafting": whether the Core lists other groups' documents for an administrator under
   «Ein» is still to be checked on the demo tenant once CR-R1 lands (the reach walks `documents(group=…)` as the
   administrator).
