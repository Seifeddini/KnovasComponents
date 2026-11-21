# Platform MVP (FEAT-2026-11-19-platform-mvp)

Working folder of the Platform MVP build on branch `feat/platform-mvp`. The plan, the bet
(`BET-2026-11-platform-mvp`) and the decisions (DEC-0081, DEC-0082) live in KnowledgeBase; the Jira epic is SS-723.

- `work-orders/PMVP-<item>.md`: the coordinator's work order per item, written before the build.
- `frozen-tests.txt`: the acceptance tests the coordinator froze, by git blob id; a builder never edits them.

Each item is built on `feat/pmvp-<item>` from this branch in its own worktree, checked by a fresh verifier session,
then integrated here. `feat/platform-mvp` reaches `main` only through a pull request a person reviews and merges.

- `management/`: the management documentation (for the founders and the management team; plain words, no code).

**Management pages follow the work.** Every work order's change updates the matching page in `management/` in the
same commit: its state in the page's "Where it stands" table and in the status table of `management/README.md`, and
any new decision, limit, follow-up id or dependency. The coordinator's verification record and integration update them
too. Which page belongs to which item: PMVP-20 (every order of it), PMVP-21, PMVP-22a, PMVP-22b, PMVP-23, PMVP-24 and
PMVP-25 → `assistant.md`; PMVP-10, PMVP-11 and PMVP-12 → `workspace-and-navigation.md`; PMVP-30a, PMVP-30b and PMVP-31
to PMVP-34 → `brain-views.md`; PMVP-40 to PMVP-43 → `administration.md`; PMVP-01, PMVP-03, PMVP-04, the frozen tests and
the baseline of failing tests → `quality-and-verification.md`; CLEANUP-n → `cleanup.md`; PMVP-02, PMVP-60 to PMVP-62
and the gates → `README.md`. A change to what leaves the installation, to access rights, to logs (also the maintenance
branches on logs), to secrets or to which tenant is used also updates `data-protection.md` (among them PMVP-20's
passages, PMVP-25, PMVP-42 and PMVP-43). A new item without a page gets a row in `management/README.md` and, if it is a
new part, a page of its own with the same sections.
