# Platform MVP (FEAT-2026-11-19-platform-mvp)

Working folder of the Platform MVP build on branch `feat/platform-mvp`. The plan, the bet
(`BET-2026-11-platform-mvp`) and the decisions (DEC-0081, DEC-0082) live in KnowledgeBase; the Jira epic is SS-723.

- `work-orders/PMVP-<item>.md`: the coordinator's work order per item, written before the build.
- `frozen-tests.txt`: the acceptance tests the coordinator froze, by git blob id; a builder never edits them.

Each item is built on `feat/pmvp-<item>` from this branch in its own worktree, checked by a fresh verifier session,
then integrated here. `feat/platform-mvp` reaches `main` only through a pull request a person reviews and merges.
