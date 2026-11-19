# Work order PMVP-01: cards of the components the MVP touches, and the new domain L1-WORKSPACE

| | |
|---|---|
| Item | PMVP-01 (Jira SS-725), plan `FEAT-2026-11-19-platform-mvp` § 7 (contracts) and its start brief; DEC-0068 rule 1 ("cards first") |
| Founder decision | 2026-11-19, on the coordinator's question about MAP-K5: **a new top-level domain `L1-WORKSPACE`** holds the Platform's user-facing parts: the assistant, the Brain views, the shell (navigation and look) and the Admin pages. Admin may move later if it grows. |
| Class | routine (cards and registration; no code) |
| Skill | `map-components` (KnowledgeBase `.claude/skills/map-components/SKILL.md`); owner page `docs/Docs/04_COMPONENTS/software_map.md` |
| Branches | KnovasComponents: `feat/pmvp-01` from `feat/platform-mvp`, worktree `.worktrees/pmvp-01`. KnowledgeBase: `steering/pmvp-01-workspace-domain` from `origin/master`, worktree `E:\Knovas\KnowledgeBase-worktrees\pmvp-01` |

## Precedent

`L2-BRAIN-BUILDER` (domain `L1-BUILDER`) is the first KnovasComponents component: its card is
`docs/components/L2-brain-builder.card.yaml` here, `docs/components/boundaries.yaml` and `map-check.yaml` sit beside
it, and the KnowledgeBase manifest (`docs/ModernDocs/alloy/component-manifest.yaml`) registers it with a comment
block for its domain, `card:` pointing here and `source_paths` that follow the card (no folder under
`docs/ModernDocs/components`). Do the same for the new domain.

## What to build

**KnowledgeBase**
1. A decision record for the founder's decision above (next free `DEC-` number in
   `docs/ModernDocs/client-zero/ontology/decisions/`, in the style of DEC-0081/DEC-0082; decided by the founder,
   recorded by AGENT-claude-code as coordinator; it closes MAP-K5 for these parts). Mark MAP-K5 decided where the
   Software Map plan (`docs/ModernDocs/plans/2026-11-06-knovas-software-map.md`) and its open questions list it.
2. The domain `L1-WORKSPACE` wherever domains are declared (find every place `L1-BUILDER` is declared or indexed,
   and do the same).
3. Manifest entries for the four components below, `card:` pointing to their KnovasComponents cards.
4. Fix the drift on `L2-BRAIN-BUILDER`: its card lists migration `0025_*`, the manifest stops at `0024_*` (S11: the
   manifest follows the card).

**KnovasComponents** (cards in `docs/components/`, rendered pages beside them, `boundaries.yaml` updated)

| Card (working id) | Holds | Contract lines from |
|---|---|---|
| `L2-PLATFORM-ASSISTANT` | `src/assistant/` (built on `feat/pmvp-20`, not yet integrated: list the path, add a `review` entry "operations follow when PMVP-20 is integrated"), `src/identity/chats.py`, migration `0026_assistant_chats.sql` | plan § 7, L2-PLATFORM-ASSISTANT POST-01 to POST-06; evidence = the frozen tests `tests/test_pmvp20_*_acceptance.py` and `tests/test_pmvp21_chats_acceptance.py` |
| `L2-PLATFORM-BRAIN-VIEWS` | the existing code the Brain-view items (PMVP-30a to PMVP-34) will change; find it from the plan | plan § 7 (views' POST lines) |
| `L2-PLATFORM-SHELL` | base templates, navigation, shared styles and page parts (PMVP-10 to PMVP-12) | plan § 7 (SHELL POST-01) |
| `L2-PLATFORM-ADMIN` | the existing people, groups and access pages (PMVP-40 to PMVP-42) | plan § 7 where it names Admin; otherwise `review` entries |

- `L2-BRAIN-BUILDER`: update its card where the plan's Builder items (PMVP-50 to PMVP-54, on BET-2026-11-brain-builder)
  change it. If they change nothing yet, say so in your notes and leave it.
- A file two cards would claim: one owner, in the same change; if you can't justify one, stop (skill, "Stop instead of
  guessing").
- Owners: a role key you see used for this code; otherwise a provisional owner with a `review` entry (K4). No owner,
  risk class or evidence you didn't see.

## Checks

`card check` 0 errors for every new and changed card; `card render` with no stale page; `map-check --scope touched`
on the KnovasComponents change with no problems about the cards (findings inside the scope listed in your notes).
`py -3 scripts/steering/validate.py --no-git` 0 errors on the KnowledgeBase change. Commands run from KnowledgeBase
`scripts/` (`py -3 -m brain_runner …`), with `--root`/`--repo` pointing at the KnovasComponents worktree where the
card lives (see how L2-BRAIN-BUILDER was checked: KnovasComponents commit `81e9c92`).

## Never

- Nothing reaches the Knovas tenant (DEC-0066, K2/K3): no card mapping in a configuration, no plan or apply with the
  card templates.
- No product code, no test, no migration; don't touch `BET-2026-11-platform-mvp.yaml` (the coordinator writes it).

## Done when

Both branches pushed, commits named per the skill (`software map: cards of … (…, for PMVP-01)`) and ending with the
co-author line. No pull requests: the coordinator opens them after a verifier session passes the work. Notes under
"Builder's notes" below.

## Builder's notes

Builder session of 2026-11-19 (AGENT-claude-code). KnovasComponents `feat/pmvp-01` from `7343abe`; KnowledgeBase
`steering/pmvp-01-workspace-domain` from `origin/master` `69bbb83b2`. Commands ran from KnowledgeBase `scripts/` with
`--repo`/`--root` on this worktree, as for `81e9c92`.

### What was built

| Card | Source paths | Operations | Lines | Review entries |
|---|---|---|---|---|
| `L2-PLATFORM-ASSISTANT` | `src/assistant` (not on this branch), `identity/chats.py`, migration `0026_assistant_chats.sql` | 0 (review: they follow PMVP-20's integration) | 10 on `*`: N-1/N-2 = PRE-01/02, P-1..P-6 = POST-01..06, L-1 (the Core's limits), L-2 (title and input checks); R-1 = RESP-01, R-2 (past chats, W-07) | 13 |
| `L2-PLATFORM-BRAIN-VIEWS` | `src/brain_views` (planned, § 5.2; nothing there yet) | 0 | 4 on `*`: N-1 = PRE-01, P-1..P-3 = POST-01..03; R-1 = RESP-01 | 8 |
| `L2-PLATFORM-SHELL` | `templates/_sidebar.html`, `static/css/style.css`, `static/fonts`, `static/img` | 0 (no Python) | 1 on `*`: P-1 = SHELL POST-01 (alias `POST-01`) | 6 |
| `L2-PLATFORM-ADMIN` | `admin.py`, `admin_documents.py`, `admin_approvals.py`, `guarded.py`, five templates, two scripts, `admin.css` | 28 (26 routes, 2 calls), code_map's ids | 26: each route's N-1, its role gate (the server half of SHELL POST-01) | 7 |

`boundaries.yaml` has the four entries with their reasons, and `not_carded` says what of `web_interface/`, `identity/`
and `graph_views/` stays outside. Pages are rendered; the Builder's and EXT-BRAIN-API's pages gain only Used by rows.

### What I decided, and why (the verifier should look here first)

1. **Brain views hold no existing code.** The plan builds them as a new module `src/brain_views/` (§ 5.2). The
   existing views (Ansichten, Cortex, custom views) read the tenant's whole edge list (`graph_views/topology.py`) and
   have no 25-node limit, so on this card they would break P-1 and P-3 at once. If PMVP-32 takes the plan's cut (2)
   (the existing Ablauf in the new look), `graph_views/process_views.py` and its page join this card or get their own:
   **the coordinator decides then.** This reads "the existing code the Brain-view items will change" narrowly; the
   work order may have meant `graph_views/`.
2. **The shell holds no Python.** Which links a person sees is computed in `app.py` (`_sidebar_context`,
   `_console_url`, `_builder_nav`), which stays with the uncarded web app; PMVP-11 decides whether that logic moves to
   the shell. `style.css` is here as the one stylesheet every page loads (PMVP-10's tokens go there), though it also
   styles pages no card holds.
3. **Admin is the work order's scope**: people, documents, access groups, folder rules, approvals. `guarded.py` joins
   because only console modules call it. `admin_ingestion.py`, `admin_doc_fields.py` and `admin_system.py` stay
   outside; `GET /admin/system` is Admin's anyway because its route is in `admin.py`.
4. **"Was sieht …?" and the enforcement posture are the Builder's files today** (`builder/access.py` `explain`,
   `builder_routes.py` `access_explain` and `access_posture`). The Admin card does not claim them; PMVP-41 and PMVP-42
   move them and change both cards then.
5. **No file is claimed by two cards.** `chats.py` and 0026 sit in `identity/`, but only the assistant uses them.
6. **Owners**: `ROLE-ENGINE-OWNER`, proposed, as on every card in this repository (a K4 review entry on each).
   **Risk classes**: `standard` everywhere, from the plan's change classes (§ 9: PMVP-11, 20 and 21 sensitive, 30 to 34
   and 40 routine) through K6. POST-02 is a rights property; K8 may want it critical.

### Where the work order and the skill disagree (noted, not stopped on)

- **Evidence.** The work order says the frozen tests; the skill says a test you have seen pass, else a review entry.
  The PMVP-21 tests pass here (65 passed, 0 skipped: `test_pmvp21_chats_acceptance.py` and
  `test_identity_chats_store.py` against the test database on 55433). The PMVP-20 frozen tests cannot run here
  (`ModuleNotFoundError: assistant`). So the PMVP-20 tests are named as evidence **and** each such line has a review
  entry "not seen passing" (P-1, P-2, P-5, P-6, L-1, and P-4's service half).
- **Admin lines.** The work order says § 7 where it names Admin, otherwise review entries; the skill wants needs,
  promises, always and limits for each operation the project changes. I wrote only each route's role gate (SHELL
  POST-01 names Verwaltung and says the server refuses), seen in passing tests; everything else is a review entry. The
  8 `admin.py` routes other than `GET /admin/people` and `POST /admin/people/create` carry the same decorator, but no
  test counts them.

### Checks

- `card check`: all 15 cards valid, 0 errors, 0 warnings. `card render --check`: 15 cards, 0 not current.
- Tests behind the shell's and Admin's lines seen passing: 56 passed (people, documents and approvals tests,
  `test_builder_shell.py` `TestTheGate`).
- `map-check --scope touched`, base `origin/feat/platform-mvp`, head `bbc7833`: 0 problems; declared 0, compatible 0.
  **Findings inside the scope**: `over_budget` L2-PLATFORM-ADMIN (22,442 tokens; the code dominates; split documents
  from approvals if it matters), and `no_contract` on `call.web_interface.admin.create_admin_blueprint` and
  `call.web_interface.guarded.run_guarded` (§ 7 gives no line; review entry). Outside the scope: 319 `unowned` (291 at
  the base: the console now takes names from `identity/` across a boundary) and 5 cards over budget.
- KnowledgeBase: `py -3 scripts/steering/validate.py --no-git`: 0 errors (30 violations, as on master).

### L2-BRAIN-BUILDER

PMVP-50 to PMVP-54 have changed nothing yet (no branch exists), so its card is unchanged; its page gains the shell's
Used by row. The manifest drift is fixed in KnowledgeBase: 0025 added, so the manifest's paths equal the card's 20.

### Open, for the coordinator

- **EXT-BRAIN-API** lists neither `POST /secured/query` nor `POST /secured/llm/generate` (the assistant), nor
  `/secured/document_access` and `/secured/delete_all_documents` (Admin), nor `/secured/brain/readiness` (PMVP-53). I
  did not add them: the code that calls them goes through `knovas_client.py`, which has no card; add them when PMVP-20
  is integrated.
- **FU-04**'s card half is done by this item (`chats.py` and 0026 are the assistant's); `follow-ups.md` is yours.
- **MAP-K5** is decided in part. `BET-2026-11-client-zero.yaml` still has MAP-K5 `open` and CV-0's `next` waiting on
  it; I did not touch the bet card (setting an item's status is not the builder's). DEC-0083 does not place the custom
  views.
- **Plan § 13's glossary** ("to be confirmed in PMVP-01") is not in this work order; the cards' `terms` carry
  reference, passage, chat, dossier, Beleg-Spur, process line and area. No glossary file changed.
- **On merge to master** the Client Zero loader (CI) writes `L1-WORKSPACE` and the four Component nodes into the
  Knovas tenant, as it did for `L1-BUILDER`. No card mapping or template reaches the tenant.
- **Backlog row B33** (Software Map plan, G4): routes attached to a blueprint passed in as a parameter lose its prefix.
- **Seen in passing**: `app.py` `_console_url` logs a person's e-mail address when it hides the Verwaltung link, which
  GI-DATA-04 would not allow; for the `maint/platform-logs-no-customer-text` sweep.
