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

(the builder writes here: what was decided, what is open, what the verifier should look at)
