# Platform MVP: management overview

For the founders and the management team. State on **21 November 2026**, from the work orders and verification
records, follow-ups, component cards, plan, bet card and DEC-0081/DEC-0082 only; where they say nothing: **open**.

## What it is for

An ordinary employee has nowhere to use the Knovas Brain today: the Platform has no assistant, and its Brain views draw
whole graphs. The MVP builds what a pilot needs to ask, search, read what the Brain knows and manage access.

- **Pilot:** Knovas GmbH itself (its own tenant, "Client Zero"), on **Tuesday 15 December 2026**, measured with access
  rights enforced (DEC-0082). Launch rows follow on 12 January 2027.
- **Targets on 15 December:** at least 24 of 30 test questions answered correctly with a source; every reference shown
  points to a retrieved passage; "nothing found" in every question the sources cannot answer; no document shown to a
  person without the right; never more than 25 things drawn in a view.
- **Built mainly by AI agents** under one coordinating agent; Siran, Jannik Kranner and Olymp's CTO (about 5 hours a
  week each) review and decide.

## The three areas

| Area | For | Holds |
|---|---|---|
| **Workspace** | every employee | search, the Knovas Assistent, the document view, the Brain views |
| **Verwaltung** (Admin) | administrators, approvers | people and roles (a role bundles what its members may do and, from RB-4, read), access groups and folder rules, approvals, ingestion, system |
| **Builder** | the customer's administrators, Knovas consultants | sources, the Brain's model, health (its MVP items PMVP-50 to 54 are on the Builder's bet) |

## Where each part stands

**Built** = the builder finished; **verified** = a fresh verifier session passed it; **integrated** = merged into the
MVP's build branch; **on main** = in the product after a person approved. **Nothing of the MVP is on main.** The build
branch reaches main only through a pull request a person reviews and merges; the class says who approves each item.

| Part | Item | State | Class: approves |
|---|---|---|---|
| Component cards, domain "Workspace" | PMVP-01 | verified 20 Nov (with notes); integrated | routine: Jannik or Siran |
| Test harness | PMVP-03 | failed verification twice; founder accepted it with a known limit, 21 Nov; integrated | routine: Jannik or Siran |
| Brand look, styleguide | PMVP-10 | verified 21 Nov (with notes); integrated | routine: Jannik or Siran |
| Navigation, three areas | PMVP-11 | verified 21 Nov (with notes); integrated | sensitive: Siran |
| Shared page parts | PMVP-12 | verified 21 Nov (with notes); integrated | routine: Jannik or Siran |
| Assistant service | PMVP-20 | verified 21 Nov (third attempt, second design, with notes); integrated | sensitive: Siran |
| — number check hardened | PMVP-20 | in build; nothing pushed | sensitive: Siran |
| — longer passages | PMVP-20 | built; stopped on one point (the Core sends no longer text); not verified | sensitive: Siran |
| Chat storage | PMVP-21 | verified 19 Nov (with notes); integrated | critical: management |
| Ask page | PMVP-22a | built; no verification verdict yet | routine: Jannik or Siran |
| Switch and provider choice | PMVP-25 | built; no verification verdict yet | critical: management |
| Dossier | PMVP-30a | built; no verification verdict yet | routine: Jannik or Siran |
| Admin in the new look | PMVP-40 | in build (pushed; builder's notes not written) | routine: Jannik or Siran |
| Roles: tables, the two roles, the move of an existing installation; functions and groups read from roles | RB-1, RB-2 | built; no verification verdict yet | critical (a migration): management |
| Cleanup | CLEANUP-1 | in build; nothing pushed | sensitive: Siran |
| No customer text in logs (pull request #55) | maintenance | verified (second verification, with notes); not on main, not in the build branch | open: no class named |
| Not started (no work order) | PMVP-02, 04, 22b, 23, 24, 30b, 31–34, 41–43, 60–62; RB-3 to RB-10 | not started | as the plan and the role builder's design say |

"Management" approves critical items (DEC-0081); who exactly is **open**. Updating the pilot instance (PMVP-62) needs
both founders.

## Gates and dates

| Gate | Date | Met when | Where it stands |
|---|---|---|---|
| G0 | Fri 20 Nov | bet and plan approved; D-A1 to D-A3 decided | met on 19 Nov |
| G1 | Tue 24 Nov | board live; development instance answers a search; harness runs; shell pull request open | harness runs (stub data); development instance not started (waits for the demo law firm); bet card lags; shell pull request: open |
| G2 | Tue 1 Dec | an answer with checked references on the development instance; capacity test | service and ask page built; no model in production (FU-32); capacity test not started |
| G3 | Tue 8 Dec | dossier and process line open from an answer; Admin and Quellen list in the new look | dossier built; process line not started; Admin look in build |
| Freeze / MVP / Launch | 11 Dec / 15 Dec / 12 Jan 2027 | only fixes / targets met on the pilot / launch rows | |

## Founder decisions so far

| Date | Decision |
|---|---|
| 17 Nov | First mockups rejected; screens follow the brand and five design rules; six reference screens are the contract |
| 19 Nov | Bet and plan approved: 80 hours of people's time, 4 weeks, a provisional cap on agent cost (fixed 27 Nov) |
| 19 Nov | A coordinator role for an agent; Jannik merges routine pull requests, Siran the rest (DEC-0081) |
| 19 Nov | A real pilot with access rights enforced (DEC-0082); the pilot is Knovas GmbH itself |
| 19 Nov | A new top-level domain "Workspace" (DEC-0083) |
| 19 Nov | No customer text in the logs; no formal model needed for "the assistant reads only through search" (FU-14) |
| 20 Nov | The assistant paused after two failed verifications, resumed the same day with a different design |
| 20 Nov | Navigation: "Hide, place later": the Workspace menu shows only the search |
| 21 Nov | The harness accepted with its known limit (FU-36) |
| 21 Nov | The Platform calls the language model itself; the customer chooses the provider; the pilot chose Anthropic |
| 21 Nov | Clean up what is not needed; keep the shared company login and the hidden views (CLEANUP-1) |

**Open:** Olymp's CTO signs off the reference screens (D-A5, due 24 Nov); the final cap on agent cost (27 Nov); who
brings a language model to production (FU-32); the provider's region and retention terms, and the decision record for
the provider change (FU-33).

## Top five risks

1. **Access rights not yet proven.** Enforcement must be on for the pilot, after two green end-to-end rights runs of
   the Core; the last real run (10 August) showed a document to a person without the right. Kill criterion: no green
   run by 1 December, and the founder decides the pilot question again.
2. **No language model for the pilot yet.** None in production behind the Core (FU-32); the Anthropic path awaits
   verification and management's approval; terms for law firms are open (FU-33).
3. **Thin or overreaching answers.** The Core sends only short passages (FU-37); the check cannot see a statement that
   claims more than its quote (FU-27). Tripwire: fewer than 18 of 30 correct in the first run after G2.
4. **Review queue.** Seven integrated and four built items wait for people; nothing is on main. Tripwire: more than
   three pull requests waiting over two working days.
5. **Schedule.** The development instance (PMVP-02) has not started; most screen items have no work order yet.

## How quality is ensured

Work order and frozen tests **before** code; a builder in its own branch who may not edit those tests; a **fresh
verifier** per item; after **two failed attempts** the coordinator stops and asks the founder.
See [quality-and-verification.md](quality-and-verification.md).

## Pages

[assistant.md](assistant.md) · [data-protection.md](data-protection.md) ·
[workspace-and-navigation.md](workspace-and-navigation.md) · [brain-views.md](brain-views.md) ·
[administration.md](administration.md) · [quality-and-verification.md](quality-and-verification.md) ·
[cleanup.md](cleanup.md)

**Where the detail is:** `docs/platform-mvp/work-orders/`, `docs/platform-mvp/follow-ups.md`,
`docs/components/L2-platform-*.md` (branch `feat/platform-mvp`); KnowledgeBase
`docs/ModernDocs/plans/2026-11-19-platform-mvp.md`, `docs/ModernDocs/steering/bets/BET-2026-11-platform-mvp.yaml`.
