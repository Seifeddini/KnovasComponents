# Work order PMVP-42: the enforcement position shown, and one administrator switching it

**Draft for the coordinator** (2026-11-22, reworked the same day after the founder's answers). Everything under
"Decisions this order takes" is a proposal until the coordinator confirms it and the test file is frozen.

| | |
|---|---|
| Item | PMVP-42 (Jira: filed by the coordinator), plan `FEAT-2026-11-19-platform-mvp` § 4 Admin **A-06** and **§ 8.1**, both as the founder answered them on 2026-11-22 (the coordinator records it in the plan): **"An admin alone should be able to switch such things"**; **RBAC is handled in the Core**, `brokered` is the posture of a tenant with RBAC enabled, and enabling it may be conditioned on two green runs, **checked by the Core**. Spec item SPEC-platform-mvp-14 (new text proposed below); **DEC-0082** (never cut; its point 3, "a second administrator", changes with the founder's answer); follow-up **FU-20** |
| Design | `docs/platform-mvp/design/role-builder.md` § 2.3 (the Core verifies the Platform's claim only under `brokered`; under `enforcing` it ignores it), § 6 rule 7 (Aus = `disabled`, Probe needs CR-R4, Ein must be `brokered`) |
| Core requests | **CR-R1a** (the tenant reads its posture), **CR-R7** (the rights test under `brokered`; two green runs are the Core's own precondition), **CR-R8** (the tenant sets its own posture; refusal codes). All in `core-requests.md`. Someone else builds them |
| Mock-up | `docs/platform-mvp/reference-screens/05-zugriff.html`: the state line at the top of «Zugriff» with its one action |
| Bet | `BET-2026-11-platform-mvp` |
| Class | **critical** (plan § 6.5: "the enforcement switch"). **No migration**: the Platform keeps nothing of the switch but its audit rows |
| Depends on | **PMVP-41** integrated (it makes the page «Zugriff»; if PMVP-42 must start first, it makes the page with its own part only, and each test file reads only its own part). For the switch to act: CR-R1a and CR-R8; for «Ein» also CR-R7's two green runs |
| Branch / worktree | `feat/pmvp-42` from `origin/feat/platform-mvp` after PMVP-41 is integrated, worktree `.worktrees/pmvp-42` |
| Frozen tests | `P/tests/test_pmvp42_enforcement_switch_acceptance.py` (18 tests), blob id in `docs/platform-mvp/frozen-tests.txt` once frozen. `P` = `KnovasPlatform/components/docbridge_integration` |
| Card | the card that holds «Zugriff» after PMVP-41; `L2-brain-builder.card.yaml` (its posture lines); `EXT-brain-api.card.yaml` (the two new client calls); `boundaries.yaml` |

## Goal

The page «Zugriff» says in one sentence which position the firm's Knovas is in, as Knovas reports it. **One
administrator** switches it to «Ein» or «Aus» after confirming what that does; the Platform asks the Core, and the Core
switches or refuses with a reason the page puts in plain German. The Core alone decides whether «Ein» is allowed: a
broker key, a complete backfill and two green end-to-end rights runs under `brokered` are its own preconditions. The
Platform checks no gate and keeps no record of runs.

## What the Platform can read and set today

| | Where | Usable by the Platform? |
|---|---|---|
| The posture (`disabled` / `enforcing` / `brokered`) | Core, Knovas-internal `GET /admin/clients/<id>/principal_broker_key` | **no**: an employee login; the Platform has none and gets none |
| A secured posture read | none on KnowledgeBase `master` | **no**: **CR-R1a** |
| Setting the posture | Core, Knovas-internal `PUT /admin/clients/<id>/rbac-enforcement` (`enforcing` / `disabled`; `brokered` not accepted: CR-R1) | **no**: an employee login |
| The tenant setting its own posture | none | **no**: **CR-R8** |
| The Builder's request record (B14) | `access_posture_request`: `disabled` / `enforcing` requests with a second approver | a record of requests, not the position, and it asks for `enforcing`, which the Platform must never be in |

**So today the page says «unbekannt» and its switch waits for Knovas.** This order adds both client calls now
(`rbac_posture()`, `set_rbac_posture()`; a 404 answers None), so the page, RB-5's «wirkt mit «Ein»» and PMVP-41's
answers switch on by themselves once the Core has the routes.

## What PMVP-42 can do before the Core changes, and after

- **Before** (today): the position sentence «unbekannt»; the switch shown, disabled, «wartet auf Knovas»; a POST sends
  nothing. Everything else (the confirmation, the audit, the refusal words, retiring the Builder's request) is built
  and tested against a fake Core.
- **After CR-R1a**: the real position, and why «Ein» is not possible yet (`not_ready`); the switch still waits until
  the Core reports `tenant_switch: true`.
- **After CR-R8**: one administrator switches; «Aus» always works; «Ein» is refused by the Core with
  `rights_runs_missing` until CR-R7's two green runs are on the Core's record (and with `no_broker_key` or
  `backfill_incomplete` before those hold).

## What to build (the frozen tests pin it)

1. **The client calls** (`P/src/knovas_client.py`, through `_rbac_request`, log lines with the status only, RB-FIX-2's
   rules):
   - `rbac_posture()`: `GET /secured/rbac/posture` (CR-R1a) → the answer's `{"mode", "backfill_complete",
     "broker_key_registered", "not_ready", "tenant_switch"}`; `None` on 404; other failures raise.
   - `set_rbac_posture(mode)`: `PUT /secured/rbac/posture` with `{"mode": "brokered" | "disabled"}` (CR-R8) →
     `{"mode": <new>}`; a 409 → `{"refused": <the Core's error_code>}`; a 404 → `None`; other failures raise. It
     refuses any other mode itself (never `enforcing`).
2. **The position sentence** on «Zugriff», one element `data-enforcement-position`:

   | Knovas reports | Value | Sentence (words yours; the guillemets pinned for two) |
   |---|---|---|
   | `brokered` | `ein` | «Ein»: Knovas shows each person only what their roles allow |
   | `disabled` | `aus` | «Aus»: Knovas hides nothing from anyone; roles and rules act with «Ein» |
   | `enforcing` | `teilweise` | an exception mark: Knovas filters but does not check the Platform's requests, so every search here sees only what is open |
   | no call, an error, anything else | `unbekannt` | Knovas does not report the position yet |

   Below it, while the Core reports `not_ready` codes, why «Ein» is not possible yet, in the page's words per code
   (the same words as a refusal, item 4), never the bare code. «Probe» is never shown or offered until CR-R4 (RB-9).
   Opening the page writes nothing.
3. **The switch** (`POST /admin/access/enforcement`, `mode` = `ein` | `aus`, `bestaetigt` = `1`), in the console's
   blueprint behind `require_admin`, reading `csrf_token` first (400):
   - **The form**: the position it would switch to (`aus` → «Ein»; `ein` → «Aus»; `teilweise` both), the consequence
     spelled out (for «Ein»: from now on each person, the assistant and every agent read only what their roles allow,
     and a document no role reaches is hidden from everyone; for «Aus»: everyone reads everything again), a checkbox
     `bestaetigt`, one button. While the Core does not report `tenant_switch: true`, the form is shown **disabled**
     (`disabled` on the fieldset or button) with «wartet auf Knovas».
   - **Refused before the Core, nothing sent**: a mode other than `ein` / `aus` (400; never Probe, never `enforcing`);
     no `bestaetigt=1` (400); the Core does not offer the switch (409, «wartet auf Knovas»); the position already
     active (409).
   - **Then** `set_rbac_posture("brokered")` for «Ein», `"disabled"` for «Aus», once. The answer is the page, drawn
     from what the Core reports after the call. **No second administrator, no approvals module, no bypass setting**
     takes part.
4. **The Core's answers**: switched → 200 and the new position; refused (`{"refused": code}`) → 409 with the reason in
   `role="alert"`, in German words of its own per code (`no_broker_key`, `backfill_incomplete`, `rights_runs_missing`,
   and one sentence for a code the Platform does not know), never the bare code; no route (`None`) → 409, «wartet auf
   Knovas»; a failure → 502. In every case but a switch the position stays what the Core reports.
5. **Audit** (ids only; `audit.record_or_raise` after the Core answered, as RB-5 writes its data changes):
   `enforcement.switched` (`detail`: `from`, `to`, the Core modes) and `enforcement.switch_refused` (`detail`: `to`,
   `code`); `target_type` `enforcement`, `target_id` the Platform's tenant id; actor the administrator.
6. **The Builder's old request** (B14, FU-20): `POST /builder/api/access/posture` and `…/approve` answer 410 with a
   pointer to Verwaltung › Zugriff and record nothing; the Builder's «Durchsetzung» section shows the position read
   only (`rbac_posture`) and links there. `builder/store.py` and the table `access_posture_request` stay.
7. **The management pages**: `administration.md` (the position, the switch, the Core's preconditions, what waits for
   which CR), `data-protection.md` (one administrator decides who reads what, recorded by id; the Core holds the
   gate), `README.md`.

## Rules

1. **The Core decides.** The Platform checks no precondition of «Ein» and keeps no record of runs; it shows what the
   Core reports and says what the Core refused.
2. **One administrator, a deliberate step**: the confirmation is checked on the server; nothing reaches the Core
   without it.
3. **Never `enforcing`** from the Platform (it ignores the Platform's claims, design § 2.3), never Probe.
4. **The page reports, it does not assume**: the position is what the Core reports after the call.
5. **No customer text in logs**; audit rows name people by id.
6. **No gate changes** beyond the new route behind `require_admin`; the look as PMVP-41's page.

**Where the frozen file pins each rule** (18 tests): § 1 the position for every report, the not-ready reasons in
words of their own (2); § 2 the form (the other position, the checkbox, no Probe), one administrator to «Ein» and to
«Aus» with their audit rows and no approval or bypass, both ways from `teilweise`, no confirmation, bad modes, the
active position (7); § 3 waiting for Knovas, a Core without the route (2); § 4 refusals in words of their own with
their audit rows, a failing Core (2); § 5 gates and tokens (3); § 6 the Builder records nothing (1); § 7 audit and logs
(1). Reference run: 18 passed; 20 mutants, all killed.

## SPEC-platform-mvp-14: proposed new text

```yaml
  - key: SPEC-platform-mvp-14
    ears: "When an administrator confirms a switch of enforcement, Knovas shall switch it in the Core if the Core's own
      prerequisites hold (a broker key, a complete backfill, two green end-to-end rights runs under brokered) and
      otherwise say why not; the Admin page shall say in one sentence which position is active."
    example: {input: "admin A confirms «Ein» while the Core has no two green rights runs",
      expected: "nothing switched; the page says why («… zwei grünen Rechteläufe …») and «Durchsetzung: «Aus»»"}
    class: critical
```

## Decisions this order takes (draft, for the coordinator)

1. **Both directions with the same confirmation**, and «Aus» always accepted by the Core (CR-R8): switching off must
   never be blocked by the gate.
2. **The switch is disabled until the Core says it offers it** (`tenant_switch` in CR-R1a's answer), so the page never
   offers a click that cannot work; a Core that still answers 404 says the same.
3. **The refusal words** are the Platform's, per code (the Core's codes are pinned in CR-R8).
4. **The Builder's B14 request is retired** (it asks for `enforcing`, which breaks every Platform search); its two
   posture tests in `tests/test_builder_access.py` change with it.
5. **No migration**: no request table, nothing kept but the audit rows.

## Files you may change

- `P/src/web_interface/admin_access.py` and `templates/admin_access.html` (PMVP-41's): the part «Durchsetzung» and its
  route; nothing in PMVP-41's questions
- `P/src/web_interface/admin.py`: attaching the route only
- `P/src/knovas_client.py`: the two calls `rbac_posture()` and `set_rbac_posture()` only
- `P/src/web_interface/builder_routes.py`: the two posture routes and the view's posture only;
  `templates/builder/access.html`: the «Durchsetzung» section only; `tests/test_builder_access.py`: its posture tests
  only (list each change)
- new: `P/tests/test_web_admin_enforcement_*.py`, `P/tests/test_knovas_client_posture.py` (your own tests)
- `P/src/web_interface/static/css/admin.css`: rules of the new part
- `P/tests/test_web_admin_gates.py`: the route in its lists (FU-17)
- `docs/components/…`, `docs/platform-mvp/management/administration.md`, `data-protection.md`, `README.md`
- this order: only the "Builder's notes" section

## Files you must not change

- the frozen test files; `P/tests/conftest.py`; every migration (none is needed: if one seems needed, stop)
- `P/src/identity/**` (the approvals module takes no part), `web_interface/guarded.py`
- `P/src/builder/store.py`, `builder/access_backend.py`; RB-5's and PMVP-41's routes
- any other route's gate or decorator; `_parts.html`, `_sidebar.html`, `parts.css`, `shell.css`
- `docs/platform-mvp/design/**`, `reference-screens/**`, `core-requests.md`

If the work needs one of these, stop and write the reason under "Builder's notes" instead of changing it.

## Done when

1. `cd P && py -3 -m pytest tests/test_pmvp42_enforcement_switch_acceptance.py -q -p no:cacheprovider` green and not
   skipped, on a database of your own (`knovas_platform_test_pmvp42`, `PLATFORM_DB_REQUIRED=true`); every frozen file
   of `frozen-tests.txt` green, each in a process of its own (RB-6/7's counts what the real client sends on the wire:
   the posture read must not ride on the agents' delivery; RB-5's and PMVP-41's switch on `rbac_posture`); the frozen
   check all OK. One pytest process at a time.
2. Each file in a process of its own: `tests/test_web_admin_*.py`, `tests/test_builder_access.py`,
   `tests/test_builder_store.py`, `tests/test_knovas_client_*.py`, `tests/test_shell_*.py`,
   `tests/test_pmvp11_navigation_acceptance.py`. **Do not run the whole suite.** No failing id the base does not have.
3. Screenshots outside the repository: the position in each of the four values; the switch waiting for Knovas; the
   confirmation for «Ein»; each refusal; at 1440 and 390 px beside `05-zugriff.png`. A fake Core.
4. Card checks clean. Commits `PMVP-42: …` ending with the co-author line; `feat/pmvp-42` pushed. No pull request.
   Management approves before `main` (critical).

## Builder's notes

(the builder writes here: what was decided, what is open, what the verifier should look at)
