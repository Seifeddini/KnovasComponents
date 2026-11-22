# Work order PMVP-42: the enforcement position shown, and the switch with a second administrator

**Draft for the coordinator** (2026-11-22). Everything under "Decisions this order takes" is a proposal until the
coordinator (and, where marked, the founder) confirms it and the test file is frozen.

| | |
|---|---|
| Item | PMVP-42 (Jira: filed by the coordinator), plan `FEAT-2026-11-19-platform-mvp` § 4 Admin **A-06** ("Enforcement per customer in three positions: Aus, Probe, Ein. Switching to Ein needs a second administrator"), **§ 8.1** ("Until the Core's end-to-end rights test has passed twice, no customer is switched to «Ein», and the Admin page says in one sentence which position is active"), spec item SPEC-platform-mvp-14; **DEC-0082** (the pilot runs with «Ein»; PMVP-42 is on the MVP's path and **never cut**); follow-up **FU-20** (the position is still a Builder file) |
| Design | `docs/platform-mvp/design/role-builder.md` § 2.3 (the Core verifies the Platform's claim only under `brokered`; under `enforcing` it ignores it), § 6 rule 7 (Aus = `disabled`, Probe needs CR-R4, Ein must be `brokered`) |
| Core requests | **CR-R1** (the posture route accepts `brokered`), **CR-R1a** (a secured read of the tenant's posture; its detail added with this draft), **CR-R7** (new with this draft: the end-to-end rights test under `brokered`; what counts for § 8.1). Someone else builds them. CR-R8 only if the founder chooses decision 1 B |
| Mock-up | `docs/platform-mvp/reference-screens/05-zugriff.html`: the state line at the top of «Zugriff» («Die Regeln laufen zur Probe … Einschalten») |
| Bet | `BET-2026-11-platform-mvp` |
| Class | **critical** (plan § 6.5: "the enforcement switch"; and a migration): proposed by an agent, approved by management |
| Depends on | **PMVP-41** integrated (it makes the page «Zugriff»; if PMVP-42 must start first, it makes the page with its own part only and PMVP-41 adds the rest; each test file reads only its own part). For the switch to act: CR-R1, CR-R1a, CR-R7 and two green runs |
| Branch / worktree | `feat/pmvp-42` from `origin/feat/platform-mvp` after PMVP-41 is integrated, worktree `.worktrees/pmvp-42` |
| Frozen tests | `P/tests/test_pmvp42_enforcement_switch_acceptance.py` (26 tests), blob id in `docs/platform-mvp/frozen-tests.txt` once frozen. `P` = `KnovasPlatform/components/docbridge_integration` |
| Card | the card that holds «Zugriff» after PMVP-41; `L2-brain-builder.card.yaml` (its posture lines); `EXT-brain-api.card.yaml` (the new client call); `boundaries.yaml` |

## Goal

The page «Zugriff» says in one sentence which position the firm's Knovas is in, as Knovas reports it. An
administrator asks for «Ein» (or «Aus»); a **second administrator** approves; **Knovas staff carry it out**. The
Platform refuses to ask for «Ein» before two green end-to-end rights runs of the Core are on record, never switches
anything itself, and the administrators' four-eyes bypass does not apply.

## What the Platform can show today, and what it cannot

| Read | Where | Usable by the Platform? |
|---|---|---|
| `clients.rbac_enforcement` (`disabled` / `enforcing` / `brokered`) | Core, Knovas-internal `GET /admin/clients/<id>/principal_broker_key` (answers the posture beside the keys) | **no**: an employee login (JWT); the Platform has none and must not get one |
| A secured posture read | none on KnowledgeBase `master` | **no**: that is **CR-R1a** |
| `document_readable` | `GET /secured/document_readable` | **no**: it answers for one document and cannot tell `disabled` from a person who may read |
| The Builder's request record | `access_posture_request` (B14): `disabled` / `enforcing` requests with a second approver, outcome "Knovas schaltet den Mandanten um" | a record of requests, not the position; and it asks for `enforcing`, which the Platform must never be in (design § 2.3) |
| `BrainAccess.posture()` | `getattr(client, "rbac_posture", None)` | the real client has no such call: today it shows nothing |

**So today the page can only say «unbekannt»**; with CR-R1a it says the real position. This order adds the client call
now (`rbac_posture()`, a 404 answers None), so the page, RB-5's «wirkt mit «Ein»» and PMVP-41's answers switch on by
themselves once the Core has the route.

## Who sets the posture

- **Today: Knovas staff only**, through the Core's internal admin API `PUT /admin/clients/<id>/rbac-enforcement`
  (employee JWT, `_require_knovas_admin`; it accepts `enforcing` and `disabled`, `enforcing` only after a completed
  backfill), with `scripts/ops/rbac_switch.py` (weekend release, step P7). `brokered` cannot be set by any route
  (**CR-R1**).
- **The Platform has no route to the posture and gets none** in this order. Its switch is a **request**: recorded,
  approved by a second administrator, and carried out by Knovas staff, who check it before they switch.
- **The bypass does not apply.** `approvals.admin_bypass` lets an administrator act alone on the guarded kinds of
  `identity/approvals.py`; the switch is **not** one of them and never goes through `run_guarded`. Its requests live in
  a table of their own whose database constraint refuses an approval by the requester, so no setting can turn the
  second person off.

## What the switch does

1. **Request** (`POST /admin/access/enforcement/request`, `mode` = `ein` | `aus`): an administrator. Refused, nothing
   stored: another request is open (409); the Core already reports that position (409); for `ein`, the § 8.1 gate is not
   met (409) or the Core reports its backfill incomplete (409); any other mode, Probe included (400).
2. **Approval** (`POST /admin/access/enforcement/<id>/approve`): **another administrator** (function `admin`; an
   approver who is not an administrator is refused like a member: the switch is an administrators' decision). Never
   the requester (403; and the table's `CHECK`). The gate is checked again for `ein` (409). Approved, the request says
   that Knovas carries it out; **nothing is sent to the Core**.
3. **Carrying it out**: Knovas staff, on the firm's word (for the pilot: the founders see the page; for a client: the
   administrator sends Knovas the request's id), check the request and the two runs, and switch through the internal
   API. Once the Core reports the requested position (CR-R1a), the next POST of these routes closes the approved
   request as done (`enforcement.carried_out`), and the page shows it as carried out without writing anything on a GET.
4. **Withdrawal** (`POST /admin/access/enforcement/<id>/cancel`): any administrator withdraws a waiting or approved
   request.
5. **«Aus»** goes the same way, without the gate: turning enforcement off is also a decision of two administrators.
   In an emergency Knovas staff can always switch off through the internal API.

## The § 8.1 gate: recorded where, checked how

- **What counts** (**CR-R7**): a green run of the Core's end-to-end rights test **under `brokered`**, with claims signed
  as the Platform signs them. The job `rbac-wall` (`scripts/ci/rbac_wall_stack.py`) runs today under `enforcing` with
  body groups (run record `docs/ModernDocs/reports/2026-11-rbac-phase-1-run-record.md`, first green run 18 Nov): a
  posture the Platform never uses, so it does not count for the Platform's «Ein».
- **Recorded**: (1) in KnowledgeBase's run record, one line per run (run id, date, commit, posture, result), as today;
  (2) copied by Knovas into the installation's configuration at deployment (PMVP-62's release note):

  ```yaml
  enforcement:
    rights_runs:
      - {run: "<Actions run id>", date: "2026-12-..", commit: "<Core commit>", posture: brokered, result: green}
      - {run: "...", date: "...", commit: "...", posture: brokered, result: green}
  ```

- **Checked**: the Platform counts the entries with a `run`, a `commit`, `posture: brokered` and `result: green`,
  distinct by `run`; fewer than two: no request for «Ein» (409) and no approval of one (409). The page lists them
  (`data-rights-run`), so the second administrator sees what she approves, and the request stores them. Knovas staff
  check them against the run record before switching. **Recommended, Core side** (part of CR-R7): the switch step
  refuses `brokered` unless given two such runs, so the gate holds where the switch happens and not only on a page the
  firm configures.

## What is Core work, and what is Platform work

| Part | Who | State |
|---|---|---|
| `brokered` settable at all (posture route, key registered, backfill complete) | Core: **CR-R1** | not built |
| The tenant reads its own posture (`GET /secured/rbac/posture`) | Core: **CR-R1a** (detail in `core-requests.md`) | not built |
| The end-to-end rights test under `brokered`; two green runs recorded; optionally the switch refuses without them | Core: **CR-R7** (new) | not built |
| The tenant's second administrator carries the switch out herself (a secured route verifying the Platform's dual-control token) | Core: **CR-R8**, only if the founder chooses decision 1 B | not requested |
| `KnovasAPIClient.rbac_posture()`; the position sentence; request, approval, withdrawal; the gate check; the table; retiring the Builder's request; audit; pages | **Platform: this order** | buildable now |

## What PMVP-42 can do before the Core changes, and after

- **Before** (today): the page says «Durchsetzung: unbekannt» (Knovas does not report it yet); the request, approval
  and withdrawal work end to end and are audited; «Ein» is refused while fewer than two qualifying runs are
  configured (none exist: CR-R7), so the only request possible is «Aus». Nothing reaches the Core.
- **After CR-R1a**: the page shows the real position (`aus`, `teilweise` for `enforcing`, `ein`); a request for the
  position already active is refused; «Ein» waits for a complete backfill.
- **After CR-R1 and CR-R7 with two green runs**: «Ein» can be requested and approved, and Knovas staff can carry it out.
- **After CR-R8** (only if decided): the approved request is carried out by the second administrator from the page.

## What to build (the frozen tests pin it)

1. **The table** (critical: a migration; new tables only, plan § 8.3): e.g. `enforcement_requests` (`id`, `mode` `ein` |
   `aus`, `status` `pending` | `approved` | `cancelled` | `done`, `rights_runs` JSONB, `requested_by`, `requested_at`,
   `approved_by`, `approved_at`, `cancelled_by`, `cancelled_at`; `CHECK (approved_by IS NULL OR approved_by <>
   requested_by)`; a partial unique index so at most one request is `pending` or `approved`). No tenant column (one
   installation per database, as in `identity/`). No column for a key, secret, hash or token. No existing table changes
   (`access_posture_request` and `approval_requests` stay as they are).
2. **The client call** `KnovasAPIClient.rbac_posture()`: `GET /secured/rbac/posture` through `_rbac_request` (CR-R1a);
   answers `{"mode": …, "backfill_complete": …}`, `None` on 404 (the Core without CR-R1a); other failures raise. Its log
   lines carry the status, never a body (RB-FIX-2's rules).
3. **The position sentence** on «Zugriff», one element `data-enforcement-position`:

   | Knovas reports | Value | Sentence (words yours; the guillemets pinned for two) |
   |---|---|---|
   | `brokered` | `ein` | «Ein»: Knovas shows each person only what their roles allow |
   | `disabled` | `aus` | «Aus»: Knovas hides nothing from anyone; roles and rules act with «Ein» |
   | `enforcing` | `teilweise` | an exception mark: Knovas filters but does not check the Platform's requests, so every search here sees only what is open; tell Knovas |
   | no call, an error, anything else | `unbekannt` | Knovas does not report the position yet |

   «Probe» is never shown or offered until CR-R4 (RB-9). Opening the page writes nothing.
4. **The request part**: a form posting `mode` to `…/request` (`ein` and `aus` while the position is unknown; never
   Probe); the configured runs (`data-rights-run="<run>"`); the open request as one element
   `data-enforcement-request="<id>" data-mode="ein|aus" data-status="wartet|freigegeben"`, naming who asked (and who
   approved), and, when approved, that Knovas carries it out; «Freigeben» for another administrator and «Zurückziehen».
5. **The routes** of § "What the switch does", in the console's blueprint, behind `require_admin`, each reading
   `csrf_token` first (400, nothing changed); unknown or malformed id 404; a request not `pending` 409 on approve.
6. **Audit** (`record_or_raise`, in the change's transaction): `enforcement.requested` (detail: `mode`, the run ids
   for `ein`), `enforcement.approved`, `enforcement.cancelled`, `enforcement.carried_out`; `target_type`
   `enforcement`, `target_id` the request's id; people by id only (the approvals module's `requested_by_email` is not
   copied).
7. **The Builder's old request** (B14, FU-20): `POST /builder/api/access/posture` and `…/approve` answer 410 with a
   pointer to Verwaltung › Zugriff and record nothing; the Builder's «Durchsetzung» section shows the position read
   only (through the same client call) and links there. `builder/store.py` and the table `access_posture_request` stay.
8. **The management pages**: `administration.md` (the position, the switch, who carries it out, the gate, what waits
   for which CR), `data-protection.md` (the switch decides who reads what; the gate), `README.md`.

## Rules

1. **The Platform never switches.** No call to the Core changes a posture; no employee credential on the Platform.
2. **Four eyes without a bypass**: two different administrators, checked in code and by the database.
3. **«Ein» only with two qualifying runs on record**, checked at the request and at the approval.
4. **The page reports, it does not assume**: the position is what Knovas reports; an approved request is not a
   position.
5. **No customer text in logs**; audit rows name people by id.
6. **No gate changes** beyond the new routes behind `require_admin`; the look as PMVP-41's page.

**Where the frozen file pins each rule** (26 tests): § 1 new tables only, no credential column (1); § 2 the position
for every report, no Probe, opening writes nothing (2); § 3 the gate (6 cases), a waiting request with its runs and
audit row, no bypass, one open request, the active position and an incomplete backfill, bad modes (11); § 4 not the
requester, not an approver or a member, the second administrator and Knovas, the gate again at approval, «Aus»
without runs, withdrawal, unknown ids (7); § 5 gates and tokens (3); § 6 the Builder records nothing (1); § 7 audit and
logs (1). Reference run: 26 passed; 18 mutants, all killed.

## Decisions this order takes (draft)

**For the founder:**

1. **Who carries out the switch.** **A (recommended for the pilot):** Knovas staff, through the Core's internal API,
   after the firm's two administrators approved on the page; no new Core route beyond CR-R1. **B (later):** the
   tenant's second administrator, from the page, through a secured route that verifies a dual-control token the
   Platform signs with its broker key (requester and approver, bound to the action and the tenant:
   `PrincipalBroker.dual_control_token` exists) and checks the backfill and the § 8.1 runs (CR-R8). B makes «Ein»
   self-service for client firms; A keeps Knovas in the loop for every customer's first «Ein».
2. **What counts as the § 8.1 gate.** Recommended: two green runs of the end-to-end rights test **under `brokered`**
   (CR-R7), recorded in KnowledgeBase's run record and copied into the installation's configuration by Knovas; the
   Platform refuses «Ein» without them, Knovas re-checks them before switching. (The green run of 18 Nov was under
   `enforcing` and would not count.)

**For the coordinator:**

3. **«Aus» also needs a second administrator** (one rule for every change of the position; Knovas can switch off
   alone in an emergency). The alternative: one administrator may ask for «Aus», and Knovas carries it out.
4. **Only administrators approve**, not the approvers of `APPROVER_ROLES` (A-06 says "a second administrator").
5. **The Builder's B14 request is retired** (it asks for `enforcing`, which breaks every Platform search, design
   § 2.3); its two posture tests in `tests/test_builder_access.py` change with it.
6. **The position's words** for `enforcing` («teilweise») and the unknown state.
7. **SPEC-platform-mvp-14's example** («Durchsetzung: Probe» while waiting) cannot hold before CR-R4: the page says
   the position Knovas reports, «unbekannt» while it reports none. The spec's example should change with this order.

## Files you may change

- new: a migration (new tables only), `P/tests/test_web_admin_enforcement_*.py` (your own tests)
- `P/src/web_interface/admin_access.py` and `templates/admin_access.html` (PMVP-41's): the part «Durchsetzung»; nothing
  in PMVP-41's questions
- `P/src/web_interface/admin.py`: attaching the routes and handing them the configured runs
- `P/src/knovas_client.py`: the one call `rbac_posture()` only
- `P/src/web_interface/builder_routes.py`: the two posture routes and the view's posture only;
  `templates/builder/access.html`: the «Durchsetzung» section only; `tests/test_builder_access.py`: its posture tests
  only (list each change)
- `P/src/web_interface/static/css/admin.css`: rules of the new part
- `P/tests/test_web_admin_gates.py`: the three routes in its lists (FU-17)
- `P/config/config.template.yaml`: the commented `enforcement.rights_runs` section
- `docs/components/…`, `docs/platform-mvp/management/administration.md`, `data-protection.md`, `README.md`
- this order: only the "Builder's notes" section

## Files you must not change

- the frozen test files; `P/tests/conftest.py`; every existing migration
- `P/src/identity/approvals.py`, `GUARDED_KINDS`, `requires_approval`, `web_interface/guarded.py` (the switch is not a
  guarded kind)
- `P/src/builder/store.py`, `builder/access_backend.py`; RB-5's and PMVP-41's routes
- any other route's gate or decorator; `_parts.html`, `_sidebar.html`, `parts.css`, `shell.css`
- `docs/platform-mvp/design/**`, `reference-screens/**`, `core-requests.md`

If the work needs one of these, stop and write the reason under "Builder's notes" instead of changing it.

## Done when

1. `cd P && py -3 -m pytest tests/test_pmvp42_enforcement_switch_acceptance.py -q -p no:cacheprovider` green and not
   skipped, on a database of your own (`knovas_platform_test_pmvp42`, `PLATFORM_DB_REQUIRED=true`); every frozen file
   of `frozen-tests.txt` green, each in a process of its own (RB-6/7's counts what the real client sends on the wire:
   the new posture read must not ride on the agents' delivery; RB-5's and PMVP-41's switch on `rbac_posture`); the
   frozen check all OK. One pytest process at a time.
2. Each file in a process of its own: `tests/test_web_admin_*.py`, `tests/test_builder_access.py`,
   `tests/test_builder_store.py`, `tests/test_identity_*.py`, `tests/test_knovas_client_*.py`, `tests/test_shell_*.py`,
   `tests/test_pmvp11_navigation_acceptance.py`. **Do not run the whole suite.** No failing id the base does not have.
3. Screenshots outside the repository: the position in each of the four values; a waiting «Ein» request; an approved
   one; the refusal without runs; at 1440 and 390 px beside `05-zugriff.png`. A fake Core.
4. Card checks clean. Commits `PMVP-42: …` ending with the co-author line; `feat/pmvp-42` pushed. No pull request. Siran
   and Jannik approve before `main` (critical).

## Builder's notes

(the builder writes here: what was decided, what is open, what the verifier should look at)
