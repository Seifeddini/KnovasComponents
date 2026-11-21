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

(the builder writes here: what was decided, what is open, what the verifier should look at)
