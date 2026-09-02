# Task 4 Report: Attach the assertion to every outbound call

**Status:** DONE_WITH_CONCERNS  
**Branch:** `feat/section-b-buildout`  
**HEAD before:** `118665c`  
**Commit:** `31b924e` (pushed to `origin/feat/section-b-buildout`)

## What I implemented

- `ASSERTION_FIELD = "principal_assertion"` on the Knovas client; injected as a **JSON body field** via `_with_principal`, called from `_make_request` (retrying path; this tree has no `_request`) and `_request_no_retry`. `_get_headers()` is unchanged.
- `KnovasAPIClient(..., *, principal_broker=None)` — unsigned construction sites keep working when the broker is `None`.
- Fail closed: no authenticated user → `PermissionError` (no unsigned call).
- App startup: when `identity.enabled`, load/create the broker key, wrap `IdentityGate` in `_RequestScopedBroker` (`current_user()` + per-request `gate.users()`), pass `api.client_id` as tenant. Missing `identity.broker_key_dir` or `api.client_id` refuses to start.
- Config: `identity.broker_key_dir` and `api.client_id` in `identity_app`, `knovas.env.example`, `KnovasPlatform/docs/setup.md`, and production `config.yaml`.

## TDD evidence

### RED

```
pytest tests/test_knovas_client_assertion.py -v

ERROR collecting tests/test_knovas_client_assertion.py
ImportError: cannot import name 'ASSERTION_FIELD' from 'knovas_client'
```

Expected: feature missing, not a typo.

### GREEN

```
pytest tests/test_knovas_client_assertion.py -v

tests/test_knovas_client_assertion.py .....                              [100%]
======================== 5 passed, 1 warning in 1.65s =========================
```

Sanity after touching `_make_request`:

```
pytest tests/test_knovas_client_hardening.py tests/test_web_login_identity.py tests/test_identity_principal.py -v

======================= 42 passed, 1 warning in 12.82s ========================
```

(`urllib3` RequestsDependencyWarning is pre-existing.)

## Files changed

| File | Action |
|------|--------|
| `KnovasPlatform/components/docbridge_integration/src/knovas_client.py` | Modified — field, broker kwarg, `_with_principal` on both request paths |
| `KnovasPlatform/components/docbridge_integration/src/web_interface/app.py` | Modified — `_RequestScopedBroker`, startup wiring |
| `KnovasPlatform/components/docbridge_integration/tests/test_knovas_client_assertion.py` | Created — five wire tests |
| `KnovasPlatform/components/docbridge_integration/tests/conftest.py` | Modified — `identity_app` keys, Dummy kwarg, assertion fixtures |
| `KnovasPlatform/components/docbridge_integration/config/config.yaml` | Modified — `api.client_id`, `identity.*` |
| `knovas.env.example` | Modified — `PLATFORM_BROKER_KEY_DIR`, `KNOVAS_CLIENT_ID` |
| `KnovasPlatform/docs/setup.md` | Modified — same keys beside admin email |

## Spec coverage

- Body field name `principal_assertion` (not a header).
- Both outbound choke points attach via one helper.
- No broker → body unchanged.
- No user → `PermissionError`.
- Groups come from `user_access_groups`; subject is the opaque user id; no `@` in the token; distinct `jti`s per call.
- `identity_app` has `identity.broker_key_dir` (empty subdir of `tmp_path`) and `api.client_id: "tenant-a"`.
- Production APIs not renamed: `UserRepository.create`, `set_access_groups`, `KnovasAPIClient`.

## Self-review

- Transport matches KnowledgeBase (`services/rbac/assertion.py:65`).
- `_RequestScopedBroker` uses `gate.users()` per mint (IdentityGate connection is request-scoped; `gate.users` is a method, not a repo).
- `client_with_broker` is a thin wrapper around `KnovasAPIClient` with `.search()` → `search_documents`, because the brief’s Flask client cannot `.search()`.
- Subject assertion uses the created `user.id` (`create()` does not take `user_id`).
- `captured_requests` patches `_make_request` (production retrying path) and records the body **after** `_with_principal`.
- `graphify-out/graph.json` does not exist; no `graphify update`.

## Concerns

- **Public health probes:** `/api/health` calls `api_client.health_check()` with no signed-in user. With identity on, `_with_principal` raises `PermissionError`, which health_check swallows as “API down”. Fail-closed as specified; operators may see a false unhealthy until a session exists or health is exempted later.
- **Cert auto-renew** (`_attempt_certificate_renewal_legacy` / `_validate_renewed_certificate`) talks to `_session.request` directly and does not go through `_with_principal`. Out of this task’s two choke points.
- **Default `PLATFORM_BROKER_KEY_DIR=/app/data/broker_keys`** lives on the data volume but the subdirectory is not created. `load_or_create_signer` fails closed if the directory is missing — document and mkdir before enabling identity.
- **`_make_request` vs brief `_request`:** this tree’s retrying method is `_make_request`. Injection is on that method; tests patch it.

## Review fix: observe the real wire body

- Replaced the `_make_request` test double with interception of
  `requests.Session.request`, so assertions now inspect the actual `json=`
  argument emitted by the production `_make_request` path.
- Added a POST regression using a non-empty `{"documents": ...}` body. It
  verifies both the original payload and `principal_assertion` survive the
  production merge.
- Added an explicit transport assertion that `principal_assertion` is absent
  from request headers.
- RED proof: temporarily removing `data = self._with_principal(data)` from
  `_make_request` produced `6 failed, 1 passed`; notably the POST body reached
  the recorder as `{"documents": [...]}` without `principal_assertion`.
  Restoring the production line returned the suite to green.

### Covering tests

```text
$ .venv/Scripts/python.exe -m pytest tests/test_knovas_client_assertion.py -v
============================= test session starts =============================
platform win32 -- Python 3.13.5, pytest-9.1.1, pluggy-1.6.0
rootdir: E:\Knovas\KnovasComponents\.worktrees\section-b-buildout\KnovasPlatform\components\docbridge_integration
configfile: pyproject.toml
plugins: anyio-4.14.2
collected 7 items

tests\test_knovas_client_assertion.py .......                            [100%]

============================== warnings summary ===============================
.venv\Lib\site-packages\requests\__init__.py:113
  E:\Knovas\KnovasComponents\.worktrees\section-b-buildout\KnovasPlatform\components\docbridge_integration\.venv\Lib\site-packages\requests\__init__.py:113: RequestsDependencyWarning: urllib3 (2.7.0) or chardet (7.5.1)/charset_normalizer (3.5.0) doesn't match a supported version!
    warnings.warn(

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
======================== 7 passed, 1 warning in 2.23s =========================
```

### Full pytest output

```text
$ .venv/Scripts/python.exe -m pytest -v
============================= test session starts =============================
platform win32 -- Python 3.13.5, pytest-9.1.1, pluggy-1.6.0
rootdir: E:\Knovas\KnovasComponents\.worktrees\section-b-buildout\KnovasPlatform\components\docbridge_integration
configfile: pyproject.toml
testpaths: tests
plugins: anyio-4.14.2
collected 451 items

tests\test_autodoc_path.py ......                                        [  1%]
tests\test_context_store.py .......                                      [  2%]
tests\test_csrf_enforcement.py .......                                   [  4%]
tests\test_enrichment_lookup.py ........                                 [  6%]
tests\test_identity_approvals.py .........................               [ 11%]
tests\test_identity_assertion.py .......................                 [ 16%]
tests\test_identity_bootstrap.py ................                        [ 20%]
tests\test_identity_broker_key.py .s.....                                [ 21%]
tests\test_identity_db.py ............                                   [ 24%]
tests\test_identity_migrations.py ................                       [ 28%]
tests\test_identity_passwords.py ............                            [ 30%]
tests\test_identity_principal.py ...........                             [ 33%]
tests\test_identity_sessions.py ...............                          [ 36%]
tests\test_identity_users.py .............................               [ 43%]
tests\test_ingestion_compiler.py ................................        [ 50%]
tests\test_knovas_client_assertion.py .......                            [ 51%]
tests\test_knovas_client_hardening.py ...............                    [ 54%]
tests\test_knovas_client_secured_api.py ....                             [ 55%]
tests\test_knovas_extract_upload.py .                                    [ 56%]
tests\test_knovas_query_parse.py ................                        [ 59%]
tests\test_ontology_api.py ................                              [ 63%]
tests\test_ontology_filters.py ........                                  [ 64%]
tests\test_ontology_graph.py ...................                         [ 69%]
tests\test_ontology_store.py .................                           [ 72%]
tests\test_open_tokens.py .....                                          [ 74%]
tests\test_platform_health.py .........................sss               [ 80%]
tests\test_preview_endpoint.py ........                                  [ 82%]
tests\test_preview_extract.py .........                                  [ 84%]
tests\test_search_test_results.py ........                               [ 85%]
tests\test_security_hardening.py s...........                            [ 88%]
tests\test_transmit_location.py ..                                       [ 88%]
tests\test_unc_path.py ....                                              [ 89%]
tests\test_web_admin_people.py .......................                   [ 94%]
tests\test_web_login.py .......                                          [ 96%]
tests\test_web_login_identity.py ................                        [100%]

============================== warnings summary ===============================
.venv\Lib\site-packages\requests\__init__.py:113
  E:\Knovas\KnovasComponents\.worktrees\section-b-buildout\KnovasPlatform\components\docbridge_integration\.venv\Lib\site-packages\requests\__init__.py:113: RequestsDependencyWarning: urllib3 (2.7.0) or chardet (7.5.1)/charset_normalizer (3.5.0) doesn't match a supported version!
    warnings.warn(

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
============ 446 passed, 5 skipped, 1 warning in 81.49s (0:01:21) =============
```
