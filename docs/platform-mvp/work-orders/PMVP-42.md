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

Builder session of 2026-11-22 (AGENT-claude-code). `feat/pmvp-42` from `feat/pmvp-41` (PMVP-41 is not integrated
yet: this branch carries its commits); `origin/feat/platform-mvp` merged in (`e4dca705`, as the coordinator asked), then
`feat/pmvp-41` again after PMVP-41's audit fix (`9f22b8e6`: every look audited, no row, nothing shown). Test database
`knovas_platform_test_pmvp4142`.

### What was built

| File | What |
|---|---|
| `P/src/knovas_client.py` | `rbac_posture()` (`GET /secured/rbac/posture`, CR-R1a: the five fields of the answer, nothing else; 404 → None; other failures raise) and `set_rbac_posture(mode)` (`PUT`, CR-R8: `brokered` or `disabled` only, anything else ValueError before a request; 200 → `{"mode"}`, 409 → `{"refused": <short error_code or "unknown">}`, 404 → None, others raise); both through `_rbac_request`; the route `/secured/rbac/posture` added to the templates the log lines name (RB-FIX-2) |
| `P/src/web_interface/admin_access.py` | `Position`, `position_of`, `reason_for`; the route `POST /admin/access/enforcement`; `_page` draws the position as the Core reports it (also after a switch or a refusal) |
| `P/src/web_interface/templates/admin_access.html` | the part «Durchsetzung» at the top (the mock-up's state line): the sentence (`data-enforcement-position`), the not-ready reasons, «wartet auf Knovas», one `<details>` per offered position with the consequence, the checkbox `bestaetigt` and one button, the fieldset `disabled` while Knovas does not offer the switch |
| `P/src/web_interface/static/css/admin.css` | the part's rules (brand tokens only) |
| `P/src/web_interface/builder_routes.py` | the two posture routes answer 410 with the address of Verwaltung › Zugriff and record nothing; the view's posture is `rbac_posture` read only (an error: none); `_zugriff_url` (None where the console is not mounted, as in the Builder's own test app) |
| `P/src/web_interface/templates/builder/access.html` | «Durchsetzung»: the position read only and the link; no request buttons, no request list |
| tests (new) | `test_knovas_client_posture.py` (18, no database), `test_web_admin_enforcement_rules.py` (23, no database), `test_web_admin_enforcement_routes.py` (5) |
| cards | `L2-PLATFORM-ACCESS-CHECK`: R-2, the operation and its contract, `GET`'s P-2; `L2-BRAIN-BUILDER`: the two posture lines retired (N-2 of the approval became its P-1, aliased); `EXT-BRAIN-API`: the access routes' line names CR-R1a and CR-R8; `boundaries.yaml`; pages rendered |
| management | `administration.md` (a section, the status row, the limits, where the detail is), `data-protection.md` (a paragraph, the status row, the founder's decision), `README.md`'s row |

No migration; `builder/store.py`, `builder/access_backend.py`, `identity/**`, `guarded.py` unchanged.

### What I decided, and why (the verifier should look here first)

1. **A Core without the route** (`set_rbac_posture` → None) writes `enforcement.switch_refused` with `code`
   `no_route` (outcome `denied`): the attempt reached Knovas, and the frozen test's § 2 says every attempt that reaches
   the Core is audited. **A Core that fails** (an exception) writes no row (no answer, nothing known), logs the error
   class and answers 502; the page reads the position again.
2. **Refusals** are audited with outcome `denied`, a switch with `ok`; `target_id` is the client's `customer_id`
   (the Platform's tenant id), None when the client has none. `detail` holds the Core's modes and code only.
3. **The audit row after the Core answered** (`record_or_raise`, as RB-5): if it cannot be written, the Core's answer
   stands; a switch answers 500 «Knovas hat umgeschaltet. Der Protokolleintrag dazu konnte nicht geschrieben werden.»,
   a refusal keeps its 409 and adds that sentence. The failure is logged by its error code.
4. **What is offered while the position is unknown:** both «Ein» and «Aus», disabled (the order says "the switch
   shown, disabled"); from «teilweise» both, enabled once Knovas offers the switch.
5. **Each position a `<details>`** («Auf «Ein» schalten»): the consequence, the checkbox and the button stay one
   click away, as the mock-up's single button; a refused confirmation (400) opens its form again. No script.
6. **`switchable` only for `tenant_switch is True`** (not a truthy string or 1). The not-ready reasons are shown only
   while the position is not «Ein», once each, in the Core's order; a code the page does not know is one sentence.
7. **The position is read twice on a question of PMVP-41** (`tenant_brokered` for the answer, `position_of` for the
   page): the order forbids changing PMVP-41's questions; one more metadata read per answered question.
8. **A refusal's words** begin with «Knovas hat nicht umgeschaltet:» and then the reason in the page's words (the
   frozen test asks only that each code has words of its own and that no code shows).
9. **The Builder's view** no longer reads the request table; `_platform_tenant()` is no longer called there (it was
   only for the list). The Builder's script still has its handlers; they find no buttons.

### Existing tests changed

- `tests/test_builder_access.py` (predicted by the order, decision 4): `test_switching_enforcement_takes_a_second_
  administrator` and `test_enforcing_waits_for_the_backfill` replaced by `test_the_builder_no_longer_records_a_switch_
  request` (410, nothing recorded) and `test_the_posture_part_shows_the_position_read_only`.
- `tests/test_web_admin_gates.py` (FU-17, named in the order): the route in `FORMS`; its fake `_Client` reports «Aus»
  with the switch offered and answers a switch without changing anything, so the administrator's 200 can be shown.

### Checks

- **Tests**, in the pinned environment (`scratchpad\pinned-venv`, `requirements.txt`'s versions: pytest 7.4.3, tenacity 8.2.3, cryptography 42.0.0; FU-50), each file in a pytest process of its own, one after another, on `knovas_platform_test_pmvp4142`, all exit code 0, nothing failed, skipped or errored:
  - frozen: `test_pmvp42_enforcement_switch_acceptance.py` 18, `test_pmvp41_what_sees_acceptance.py` 31, `test_rb67_agents_acceptance.py` 27, `test_rb5_role_data_acceptance.py` 95, `test_pmvp11_navigation_acceptance.py` 40, `test_rb12_roles_acceptance.py` 66, `test_rb34_roles_page_acceptance.py` 106, `test_pmvp34_view_limit_acceptance.py` 17, `test_pmvp21_chats_acceptance.py` 13, `test_pmvp03_harness_acceptance.py` 29, `test_pmvp10_brand_acceptance.py` 33, `test_pmvp20_statements_acceptance.py` 88, `test_pmvp23_chats_followups_acceptance.py` 33;
  - mine: `test_knovas_client_posture.py` 18, `test_web_admin_enforcement_rules.py` 23, `test_web_admin_enforcement_routes.py` 5;
  - the rest the order names: `test_builder_access.py` 23, `test_builder_store.py` 39, `test_knovas_client_assertion.py` 14, `test_knovas_client_doc_fields.py` 164, `test_knovas_client_errors.py` 21, `test_knovas_client_hardening.py` 23, `test_knovas_client_logs.py` 32, `test_knovas_client_rbac.py` 16, `test_knovas_client_secured_api.py` 38, `test_web_admin_access_rules.py` 27, `test_web_admin_access_routes.py` 4, `test_shell_nav_links.py` 31, `test_shell_nav_partial.py` 52, `test_shell_nav_styles.py` 33, `test_shell_parts_css.py` 86, `test_shell_parts_macros.py` 49, `test_shell_parts_styleguide.py` 52, `test_shell_styleguide.py` 13, `test_shell_tokens.py` 88, `test_web_admin_approvals.py` 22, `test_web_admin_doc_fields.py` 124, `test_web_admin_documents.py` 66, `test_web_admin_ingestion.py` 161, `test_web_admin_input_checks.py` 19, `test_web_admin_people.py` 28, `test_web_admin_role_data_rules.py` 25, `test_web_admin_roles_rules.py` 13, `test_web_admin_system_doc_fields.py` 14, `test_web_admin_system_extractor.py` 32, `test_identity_agents_pages.py` 10, `test_web_admin_gates.py` 376.
  2337 passed in 47 files. None of these files has an id in `baseline-failing-ids.txt`. The whole suite was not run.
  This full run was made on `3891fb29` + the merge of `origin/feat/platform-mvp` (`0fada1e3`), before PMVP-41's audit
  fix was merged in.
- **After merging PMVP-41's fix** (`5743e29b`) and the refusal's words (`6b8bd90e`), again in the pinned environment:
  `test_pmvp42_enforcement_switch_acceptance.py` 18, `test_web_admin_enforcement_routes.py` 5, `test_web_admin_enforcement_rules.py` 23, `test_knovas_client_posture.py` 18, `test_pmvp41_what_sees_acceptance.py` 31, `test_web_admin_access_routes.py` 7, `test_web_admin_access_rules.py` 27, `test_builder_access.py` 23, `test_rb67_agents_acceptance.py` 27, `test_rb5_role_data_acceptance.py` 95, `test_shell_nav_links.py` 31, `test_shell_nav_partial.py` 52, `test_pmvp11_navigation_acceptance.py` 40, `test_web_admin_gates.py` 376; 773 passed in 14 files, all exit code 0.
- **After merging `origin/feat/platform-mvp` once more** (`71323e6f`, PMVP-24; `knovas_client.py`'s route list conflicted and keeps both new routes), pinned: `test_pmvp42_enforcement_switch_acceptance.py` 18, `test_knovas_client_posture.py` 18, `test_knovas_client_logs.py` 32, `test_pmvp41_what_sees_acceptance.py` 31, `test_web_admin_enforcement_routes.py` 5.
- **Frozen check** (header of `frozen-tests.txt`): all 13 OK.
- **Cards** (KnowledgeBase-worktrees `pmvp-01/scripts`, `py -3 -m brain_runner`, read only; nothing reached a tenant):
  `card check` on `L2-PLATFORM-ACCESS-CHECK`, `L2-BRAIN-BUILDER`, `EXT-BRAIN-API`: valid, 0 errors, 0 warnings.
  `card render --root docs/components --check`: 0 not current, 0 problems. `map-check --scope touched --head
  feat/pmvp-42`, base `origin/feat/platform-mvp`: declared 0; **budget**: every Platform card within 16,000
  (L2-PLATFORM-ADMIN 15,559, L2-PLATFORM-ACCESS 14,718, L2-PLATFORM-ACCESS-CHECK 12,021, L2-PLATFORM-ROLE-DATA 15,871,
  EXT-BRAIN-API 2,748), the one finding is L2-BRAIN-BUILDER (271,375, over long before this item); **compatible** 2
  `statement_changed` (EXT-BRAIN-API's access routes, L2-BRAIN-BUILDER's retired posture request), each with a review
  entry: a person marks them compatible or not; complete 19.

### Screenshots

Outside the repository, in `C:\Users\siran\AppData\Local\Temp\claude\1838582cff9d51e1\af9716ae-0421-42ac-9fac-6acb192cc6e6\scratchpad\b4142\shots42\`
(the real app on the test database, a fake Core answering each case; nothing reached a tenant), each at 1440 and 390
px, beside `00-reference-05-zugriff.png`:

- the position in each value: `42-unbekannt` (no posture call: both positions offered, disabled, «wartet auf
  Knovas»), `42-aus-wartet` («Aus», the Core not offering the switch), `42-aus-nicht-bereit` («Aus» with two reasons
  why «Ein» is not possible yet), `42-teilweise` (the exception mark, both positions offered), `42-ein`;
- the confirmation for «Ein»: `42-ohne-bestaetigung` (sent without the tick: 400, the form open again with the
  consequence, the checkbox and the button);
- each refusal: `42-refused-rights_runs_missing`, `42-refused-no_broker_key`, `42-refused-backfill_incomplete`,
  `42-refused-unknown` (409, «Knovas hat nicht umgeschaltet: …», the position still «Aus»);
- `42-switched-ein` (200: the page reads the position again and says «Ein»).

Nothing scrolls sideways at 390 px (`facts-42.json`).

### Fix round after the verification (pass with notes, 2026-11-22)

- **MEDIUM, a lost answer.** `set_rbac_posture` sends the PUT **once** (`_rbac_request(..., once=True)`: one attempt,
  the original error re-raised; the read keeps the usual retries). The route writes `enforcement.switch_attempted`
  (from, to) with `record_or_raise` **before** it sends; if that row cannot be written, nothing is sent (503, «Der
  Versuch konnte nicht protokolliert werden. Es wurde nichts an Knovas gesendet.»). The outcome follows in a second
  row: `enforcement.switched`, `enforcement.switch_refused` (code), `enforcement.switch_failed` (an error status) or
  `enforcement.switch_unknown` (no answer: timeout, lost connection), the last two with outcome `error`, the error's
  class and status. An unknown outcome answers 502 with «Die Antwort von Knovas ist ausgeblieben: Ob umgeschaltet
  wurde, ist unbekannt.» and the position read again. `administration.md` says so.
- **LOW:** the confirmation checkbox has the button's 2 px focus ring.
- **Tests:** `test_the_switch_is_sent_once_and_never_retried` (a ReadTimeout and a ConnectionError: one PUT),
  `test_the_read_keeps_the_usual_retries`, `test_a_switch_whose_answer_is_lost_is_recorded_as_unknown_and_the_page_
  reads_the_position` (the verifier's repro: the fake switches, then raises ReadTimeout), `test_every_attempt_is_
  recorded_before_it_is_sent_and_without_its_row_nothing_is_sent`. Two of my earlier route tests follow the new rule:
  the row-missing test fails only the outcome's row; a failing Core is now recorded as `switch_unknown`.
- `origin/feat/platform-mvp` merged first (PMVP-41 integrated, the Brain-tools mode). `_rbac_request` gained the
  keyword `once` (default off): the one change outside the two calls, made for this fix.

### Open, for the coordinator, the verifier and management

1. **Critical item**: management approves before `main`. It rides on PMVP-41 (and PMVP-34).
2. **The Core's routes do not exist yet** (CR-R1a, CR-R7, CR-R8): until they do the page shows «unbekannt» and the
   switch waits; nothing is sent. Once CR-R1a answers, RB-5's «wirkt mit «Ein»» and PMVP-41's answers switch on
   `rbac_posture` by themselves, and so does the Builder's read-only line.
3. **The access card** «Zugriff prüfen» now holds both items; the admin card took the part's CSS.
4. The founder's decision replaces DEC-0082's point 3 ("a second administrator"); the coordinator records it.
