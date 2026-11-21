# Work order PMVP-25: the assistant's switch, and the language model the customer chooses

| | |
|---|---|
| Item | PMVP-25 (Jira SS-738), plan `FEAT-2026-11-19-platform-mvp` § 5.2 ("Switches"), widened by the founder's decision below |
| Founder decisions | 2026-11-21, on the coordinator's questions: **the Platform calls the language model itself** (not through the Core's `/secured/llm/generate`); **the customer chooses in the Platform which provider to use**; the pilot (Knovas) chose **Anthropic**, so passages from its documents may be sent there. Probe the same day: Claude (`claude-opus-5-5`) answered 8 of 8 invented cases right in the statement form (`tools/claude_probe.py`, follow-up FU-32) |
| Class | **critical** under this repository's CLAUDE.md (it stores and uses a secret, the provider's API key); management approves before `main` |
| Branch / worktree | `feat/pmvp-25` from **`origin/feat/pmvp-20`** (the assistant service, rework 2, in verification), worktree `.worktrees/pmvp-25`; merge `origin/feat/platform-mvp` in once |
| Frozen tests | none new; the five in `docs/platform-mvp/frozen-tests.txt` stay unchanged, `test_pmvp20_statements_acceptance.py` stays green |

## Goal

An administrator switches the assistant on, chooses the provider and the model, and enters the provider's key, on
the Platform's settings page. The assistant then answers through that provider. Nothing else about the assistant
changes: it still searches as the person and sends only those passages (POST-02), and the check stays as it is.

## What to build

1. **Settings, in the existing registry** (`src/platform_settings.py`, `REGISTRY`; read its header first: values
   from the deployment pin a key, then the page, then the default; secrets are encrypted with
   `PLATFORM_SETTINGS_KEY`, never returned to the browser, never logged). Installation-wide keys:
   - `assistant.enabled` (on/off, **off** by default; `ASSISTANT_ENABLED` in the environment pins it);
   - `assistant.provider` (choice: `knovas` = the Core's `/secured/llm/generate` as built, `anthropic`; default
     `knovas`);
   - `assistant.model` (choice for `anthropic`: `claude-opus-5-5` (default), `claude-sonnet-5-5`,
     `claude-haiku-5-5`; German help text says what each costs per million tokens and that Opus is the default);
   - `assistant.anthropic_api_key` (secret).
   Labels and help in German, like the other entries. Only the roles that may change installation settings today
   may change these (find how the page decides; do not add a role).
2. **The Anthropic call** in `src/assistant/` (a module of its own, e.g. `providers.py`), behind the same
   `generate(*, system_instruction, messages, max_tokens) -> str` the service already takes, so `service.py` and
   the check do not change. With the official SDK (`anthropic`, pinned in `requirements.txt` to `2.12.0`;
   the one new dependency):
   - `client.beta.messages.create(model=…, max_tokens=…, system=system_instruction, messages=…,
     output_config={"effort": "medium"}, betas=["server-side-fallback-2026-07-01"], fallbacks="default")`; no
     `temperature` (the current models refuse it), no thinking setting (adaptive is the default and cannot be
     switched off on Opus 5.5);
   - `max_tokens` at least 8,000 on this path (thinking counts against it; the probe used about 200 tokens per
     answer, but a cut answer loses statements);
   - the text is the joined `text` blocks; `stop_reason == "refusal"` → `CoreFailed`; `max_tokens` → the text as
     it is (the check drops a cut last line);
   - errors: `RateLimitError` and HTTP 529 → `CoreBusy` ("gerade ausgelastet"); `AuthenticationError`,
     `PermissionDeniedError`, other status errors, connection errors and timeouts → `CoreFailed`; class names only
     in logs;
   - client `timeout` 60 seconds and `max_retries` 1, so a question never outlasts nginx's 180 seconds (FU-08);
   - the key is passed to the client from the resolved setting, never read from a file by the assistant, never
     logged, never in an exception message, never in a page; a missing key with `anthropic` chosen → `CoreFailed`
     and the settings page says the key is missing.
   With `knovas` chosen, the existing Core call stays exactly as it is.
3. **The switch.** With `assistant.enabled` off the assistant answers nothing and its routes (PMVP-22a) will
   refuse; for this item: the service factory returns "off", and a test shows it. The setting is read through the
   `Resolver` (changes apply without a restart).
4. **A "Verbindung testen" action** on the settings page, administrators only: one call with an invented passage
   and question (no customer text), showing "funktioniert" or the plain failure ("Schlüssel abgelehnt",
   "ausgelastet", "keine Verbindung"). It logs the outcome class only.
5. **The cards**: `L2-platform-assistant.card.yaml` (the provider module, the new dependency `EXT`-style: name the
   Anthropic API as an external dependency in the way the cards name others; the operation "Verbindung testen";
   its source paths now that `src/assistant` exists on the branch) and `boundaries.yaml`; the checks as in
   `work-orders/PMVP-01.md`.

## Rules

1. **The key**: encrypted at rest by the existing `SecretBox`; never in a log line, an exception, a page, the
   audit log (the registry writes `geändert`), a test's output or a commit. Tests use an obviously fake key.
2. **No real call in a test.** Tests use a fake Anthropic client (inject it). One optional live check may exist
   only behind an environment variable that is off by default; do not run it.
3. **No customer text in a log line**: counts, reasons, class names, the model name and timings only.
4. POST-02 stays: the provider receives only the system instruction, the passages the person's search returned
   and the question with its history.

## Files you may change

- `src/assistant/**` (a new provider module; `core.py` only where the factory chooses the provider);
  `src/platform_settings.py` (the four registry entries); the settings page's template and route only for the
  test action and the help texts; `requirements.txt` (the one pin); `tests/test_assistant_*.py`,
  `tests/test_settings_*.py` (your own tests); the two cards, their pages, `docs/components/boundaries.yaml`;
  `docs/platform-mvp/work-orders/PMVP-25.md` (notes only)

## Files you must not change

- the frozen test files; `tests/conftest.py`; `src/assistant/check.py` and the reply's shape; any migration (the
  settings table exists, migration 0009); `src/identity/**`; other routes' gates

## Done when

1. Your tests green; `tests/test_pmvp20_statements_acceptance.py`, `tests/test_assistant_*.py` and the settings
   page's existing tests green; the frozen check all OK. **Do not run the whole suite.** Test database of your own
   (`knovas_platform_test_pmvp25`) if a test needs one.
2. The card checks clean.
3. Commits `PMVP-25: …` ending with the co-author line; `feat/pmvp-25` pushed. No pull request.

## Builder's notes

Builder session, 2026-11-21, branch `feat/pmvp-25` (from `origin/feat/pmvp-20` `e1d92a4`, `origin/feat/platform-mvp`
`f11927b` merged in once: `669667a`). Commits `fc56495` (code and tests), `be34cd8` (the page names its two entries),
`39464f9` (cards) and the commit that carries these notes. Nothing here is verified; that is the verifier's. Nothing
was sent to Anthropic: every test uses a fake client or the real SDK over an in-process transport.

**Built.**

- `src/platform_settings.py`: four installation-wide entries, German labels and help: `assistant.enabled` (choice
  `aus`/`an`, `aus` by default, `ASSISTANT_ENABLED` pins it), `assistant.provider` (`knovas` default, `anthropic`),
  `assistant.model` (`claude-opus-5-5` default, `claude-sonnet-5-5`, `claude-haiku-5-5`; the help gives each price per
  million tokens and says Opus is the default), `assistant.anthropic_api_key` (secret, no file, no environment).
- `src/assistant/providers.py` (new): `make_anthropic_generate` (the service's own `generate` signature; the call
  exactly as point 2; `max(max_tokens, 8000)`; client `timeout` 60, `max_retries` 0 (decision 1 below), built per
  call and closed;
  text blocks joined; refusal -> `CoreFailed`; `max_tokens` -> the text as it is; 429/529 -> `CoreBusy`, everything
  else `CoreFailed`), `connection_test`, `settings_problems`, `is_enabled`, `chosen_provider`. ASCII-only like the
  rest of the package; the SDK is imported at call time, so the settings page can import the module.
- `src/assistant/core.py`: `OFF` and `make_service(settings, client, *, split, user_key, anthropic_client)`, the
  switch and the provider, read through the Resolver on every call. `make_search` and `make_generate` unchanged.
- The settings page: `settings_routes.py` (the key's Test is "Verbindung testen"; `problems` in the page's answer),
  `static/js/platform_settings.js` (the button's label, the `message` of a result, the problem line next to the key).
- `requirements.txt`, `config/config.yaml`, `src/assistant/README.md` (a new section "The switch and the provider",
  the knobs, the wiring for PMVP-22a).
- Tests (all new, 120): `test_assistant_providers.py` (86), `test_assistant_switch.py` (14),
  `test_settings_assistant.py` (20, three of them through the real app against the test database).
- Cards: `L2-PLATFORM-ASSISTANT` (operations `make_service`, `connection_test`, `settings_problems`, `CoreBusy`,
  `CoreFailed` with contracts; N-2 now has evidence; P-2, P-4, P-6 gain the provider path's; new P-7 (the key,
  critical) and P-8 (the chosen provider); uses `EXT-ANTHROPIC-API`; RISK-03), new `EXT-ANTHROPIC-API`
  (`POST /v1/messages` through the SDK), `boundaries.yaml`, both pages rendered.

**Decided beyond the order, or against its letter (the verifier should look here first).**

1. **`anthropic` was not a new dependency.** `requirements.txt` already pinned `anthropic==1.17.0` for the Brain
   Builder's views assistant (`src/builder/assistant.py`, same call shape plus tools). I changed that pin to
   `2.12.0` as ordered, so the Builder moves to 2.x too. Every SDK name the Builder uses exists in 2.12.0 (read in
   its source) and `test_builder_assistant.py` stays green (11), but that test uses a fake client: the Builder on
   2.12.0 has not run against Anthropic. 2.12.0 sends the key as `authorization` (seen on the in-process transport),
   needs Python >= 3.10 (the image has 3.11) and brings `httpx2`.
2. **`config/config.yaml` changed although the order does not list it.** `test_platform_settings.py`
   (`test_each_settings_environment_variable_is_the_one_config_yaml_reads`) requires every non-secret entry to have
   an environment variable that config.yaml reads. So `assistant:` has `enabled`, `provider` and `model`, and
   `ASSISTANT_PROVIDER` and `ASSISTANT_MODEL` pin like `ASSISTANT_ENABLED` (the order names only the last).
3. **The key has no deployment pin.** Not `ANTHROPIC_API_KEY`: that pins the Builder's key, whose help text promises
   that Claude never gets tenant data, and the SDK reads it by itself when no key is passed. So without a saved key no
   client is built at all (tested with `ANTHROPIC_API_KEY` set).
4. **The switch is a choice `an`/`aus`**: the registry has no on/off kind and the page shows a choice as a text
   field. A deployment's value is read leniently (`true`, `on`, `yes`, `ja`, `1`; anything else is off, so
   `ASSISTANT_ENABLED=true` works). `OFF` is the string `"off"`; the README says to compare with `is`.
5. **A provider or model that is not offered** (only a deployment can set one): every answer is `failed`, no client is
   built. With `anthropic` and no key the person's search still runs before the answer fails (the service searches
   first; it does not change).
6. **"Verbindung testen"** is the key's Test button (every secret has one). It tests the key typed in the field, else
   the saved one, with the saved model, and tests Anthropic whatever the chosen provider (so a key can be checked
   before switching). It sends the real system instruction and one composed message with `TEST_PASSAGE` and
   `TEST_QUESTION`. Texts: "funktioniert", "Schlüssel abgelehnt" (401; 403 adds "keine Berechtigung für dieses
   Modell"), "ausgelastet …", "keine Verbindung" (also a timeout), "kein Schlüssel gesetzt", "Fehler der Claude API
   (HTTP n)". An error outside the SDK (a key the HTTP library refuses as a header) is caught: its text may hold the key.
7. **The page's script changed** (`platform_settings.js`): I read it as part of "the settings page's template … for
   the test action". The section's title "Ansichten und Wissensbasis" is unchanged although it now holds the
   assistant's entries.
8. **The mapped error is raised outside the SDK's `except` block**, so it has neither `__cause__` nor `__context__`
   (the SDK's exception holds the request, whose headers hold the key). Tested for every class.
9. **An Anthropic 503 is `failed`**, as the order says (only 429 and 529 are busy), although the Core's 503 is busy.
10. **`claude-haiku-5-5` and the fallback.** Haiku 5.5 has no server-side fallback: with `fallbacks="default"` a
    decline stays a decline (Anthropic's documentation; with a list of models it would be a 400). I kept the order's
    one call shape for all three models; a declined answer is `failed`.
11. **Prices in the help text** are Anthropic's list prices as of November 2026 (Opus 4/20, Sonnet 2/10, Haiku
    0,10/0,50 USD per million input/output tokens; Haiku's for prompts up to 100K tokens, ours are far smaller), taken
    from the Claude API reference I had, not re-read on Anthropic's pricing page today.
12. **Time.** With the ordered `max_retries` 1, SDK 2.12.0 waits whatever `retry-after` a 429 or 529 names before
    its retry (`_calculate_retry_timeout`, no cap), so a long one would outlast nginx's 180 s. Decided by the
    coordinator (below): no retry.
13. **No live check** was written (the order allows one behind a variable; untested code seemed worse than none).
14. **The card.** `CoreBusy` and `CoreFailed` are PMVP-20's, but on this branch `knovas_client.py` takes them, so
    map-check wanted them listed; they are, with a promise each. The evidence of P-1 … L-1 still names the frozen
    files PMVP-20's rework 2 withdrew; I did not rename PMVP-20's checks, and the review entries now say so.
    `graphify update .` was not run: it writes outside the worktree.

**Tests seen passing** (`py -3`, anthropic 2.12.0, test database `knovas_platform_test_pmvp25`):
`test_pmvp20_statements_acceptance.py` 88, `test_assistant_check.py` 265, `test_assistant_core.py` 13,
`test_assistant_knovas_client.py` 39, `test_assistant_service.py` 34, `test_assistant_providers.py` 86,
`test_assistant_switch.py` 14, `test_settings_assistant.py` 20, `test_platform_settings.py` 55,
`test_builder_assistant.py` 11: 625 passed, none skipped. Frozen check: all five OK. Not mine:
`test_pmvp11_navigation_acceptance.py` fails 8 here (the navigation's `data-area` is PMVP-11's, not on this branch).

**Card checks** (KnowledgeBase `pmvp-01` scripts, `--root`/`--repo` on this worktree): `card check` 16 cards valid,
0 errors, 0 warnings; `card render --check` 0 not current; `map-check --scope touched` (base
`origin/feat/platform-mvp`): declared 0, complete 0 (6 `not_on_card` before the operations were listed),
compatible 2 `need_added` on the new operations `make_service` and `connection_test` (no consumer), budget 1
(`L2-PLATFORM-ASSISTANT` about 29,000 tokens of 16,000; mostly the check's code); touched without a card:
`src/platform_settings.py`, `src/knovas_client.py` (PMVP-20's) and `src/web_interface/settings_routes.py`, the web
app.

**The coordinator's decisions, 2026-11-21** (after the first report):

1. **No retry.** `max_retries` is 0 on the Anthropic client (`providers.ANTHROPIC_MAX_RETRIES`): a 429 or 529 maps
   straight to `CoreBusy` ("gerade ausgelastet") and the person asks again, so no `retry-after` wait can outlast
   nginx's 180 s. The timeout stays 60 s. Tests: `test_the_client_is_built_without_a_retry`, and over the real SDK a
   429 or 529 with `retry-after: 600` is one request and busy at once, a connection error or a timeout one request.
2. **Accepted as decided above**: the `anthropic` pin moved from 1.17.0 to 2.12.0 (point 1; the Builder's tests stay
   green), `config/config.yaml` with `ASSISTANT_PROVIDER` and `ASSISTANT_MODEL` (point 2), and no deployment pin for
   the key (point 3).
3. `origin/feat/platform-mvp` merged in again (it holds PMVP-11's navigation, merge `af19936`, and the harness);
   the tests re-run after it: 666 passed, none skipped (the files listed above, `test_assistant_providers.py` now
   87, and `test_pmvp11_navigation_acceptance.py` 40); frozen check all five OK; `card check` 16 valid,
   `card render --check` 0 not current.

**Open, for the coordinator.**

- PMVP-22a wires `make_service` into the routes and refuses while it returns `OFF`.
- `L2-BRAIN-BUILDER` does not name `EXT-ANTHROPIC-API` (its views assistant calls it); not this item's card.
- The class is critical: management approves before `main`. The pilot's key goes in on the settings page
  (`PLATFORM_SETTINGS_KEY` must be set), then "Verbindung testen", then `an` and `anthropic`.

## Verification (coordinator's record)

- **2026-11-21, fresh verifier session, `feat/pmvp-25` @ `cb86935`: PASS WITH NOTES.** The fake key showed up only in
  the encrypted settings row and the outgoing `Authorization` header: not in 31 page, API and JS responses (failed
  tests included), not in DEBUG logs of every SDK and httpx logger, not in exceptions (`__cause__`/`__context__`
  None), the audit row says "geändert". Only an administrator with the CSRF token can set or test it; the only host
  reached is `api.anthropic.com`. The request body is exactly what the order says (POST-02 holds); 429/529 with a
  long `retry-after` give one request and "busy" in under 5 s. The Builder works on `anthropic` 2.12.0 over the wire.
  Listed tests 315 passed; card checks clean.
- Integrated into `feat/platform-mvp` by the coordinator (merge `5ab173f`). Class critical: management approves
  before `main`.
- Notes, fixed in a follow-up commit before `main` (FU-39): at `LOG_LEVEL=DEBUG` the SDK's own "Request options" line
  logs the whole request body (passages, question), not the key; `base_url` is not pinned, so `ANTHROPIC_BASE_URL`
  from the deployment's environment would be honoured.
