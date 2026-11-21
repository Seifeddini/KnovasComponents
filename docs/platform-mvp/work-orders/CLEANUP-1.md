# Work order CLEANUP-1: remove what the Platform no longer needs

| | |
|---|---|
| Item | Maintenance on the Platform MVP's branch, on the founder's word of 2026-11-21: "while you're working on knovas components, make sure that in your branch you clean everything old up that we will not need anymore. Of course the hidden views like the custom views can stay for now" |
| Survey | A read-only survey of `feat/platform-mvp` @ `5b0175b` (scripts and vulture output in the coordinator's scratchpad `survey\`); its findings are the list below |
| Founder decisions | 2026-11-21. **Remove too:** the Cortex marketing demo; the sample search hits (`SEARCH_USE_TEST_RESULTS`); the Open Companion; the typed-node workbench foundation; the legacy certificate renewal; `pyotp`; the legacy (non-secured) API path and the `mock` mode. **Keep:** the shared company login (`identity.enabled: false`); the hidden views (Cortex, Experimente, Eigene Ansichten, Einstellungen, the Builder) |
| Class | sensitive (Siran approves): it removes features, dependencies and image packages |
| Branch / worktree | `maint/pmvp-cleanup-1` from `origin/feat/platform-mvp`, worktree `.worktrees/cleanup-1` |

## Remove

1. **Dead files** (nothing refers to them): `P/src/web_interface/static/cortex-bestand/` (5 files);
   `KnovasPlatform/knovas-docs/` (four "moved" stubs; fix the two mentions in `RELEASE_NOTES` and `Secure_API.md`);
   `P/src/web_interface/static/img/knovas-mark.svg`; `P/src/web_interface/DESIGN-CANVAS.md`, the root `README.md`
   banner (lines 3-5), the comment at `test_experiments_frontend.py:1638` and the "Blank canvas" header comment in
   `ontology.css` (they say the design was taken out; it came back on 2026-10-10).
2. **Dead CSS**: `style.css` `.site-header-bar`, `.site-brand`, `.feedback-link--footer`, the logout and footer rules,
   `.login-brand`, `.mt-20`, `body.preview-open`, `.site-nav`, `.corpus-status`; `ontology.css` `.graph-add-type*`,
   `.galaxy-danger` (and their entries in selector lists). Before deleting a rule, search templates **and** JS for
   its class (also class names built from strings); leave rules built from template variables.
3. **Dead code**: in `src/knovas_client.py` `BatchProcessor`, `sync_document_batch`, `sync_single_document`,
   `_sync_single_document_secured`, `format_document_payload`, `_cap_path_keep_suffix`, `_chunk_text_from_hit`,
   `graph_delete_filter`, `graph_restore_placement`; `src/file_utils.py` `get_file_info`, `find_autodoc_files`,
   `validate_file`, `get_relative_path`; `stats._normal_sf`, `store.is_uuid`, `plan_file.set_links`,
   `node_grants.nodes_for_user`, `sessions.list_for_user`, `users.UnknownUserError`, `ontology_graph.assign_document`,
   `app._clamp_search_limit`; in `static/js/app.js` `firstSentencesExcerpt`, `hasContextPreview`, `formatDate`,
   `formatFileSize`. Re-check each for callers (also by string, `getattr`, templates, other components, tests)
   before deleting it.
4. **Unused dependencies**: from `P/requirements.txt` `pyodbc`, `watchdog`, `python-dotenv`, `python-dateutil`,
   `python-json-logger`, and (founder) `pyotp`; from `P/Dockerfile` the apt packages `unixodbc*`, `odbcinst`,
   `mdbtools`. Confirm none is imported anywhere under `P/` (also `import` inside functions and test helpers).
5. **The Cortex marketing demo**: `P/serve_cortex_demo.py`, `P/demo_data/`, `P/tests/test_cortex_demo_client.py`, and
   what only they use. Cortex itself stays.
6. **The sample search hits**: `SEARCH_USE_TEST_RESULTS` (`app.py` ~197-655), `tests/test_search_test_results.py`,
   the sample files under `docbridge_test_data/AutoDoc/corpus` that only they use, and their mentions in docs and env
   examples. `docbridge_test_data/ontology/` stays (compose mounts it for the Cortex fixture).
7. **The Open Companion**: `KnovasPlatform/components/knovas_open_companion/`, `semantix_open_companion/`,
   `P/src/open_tokens.py`, the `/api/open-tokens/*` routes, the companion branches in `static/js/app.js` and the
   search page, `docs/integration/open-tokens-api.md`, the config keys and their tests. Opening a document from the
   search page must keep working the way it does without the companion (the document view and the original's link).
   Also `POST /api/document/<id>/open` and `open.allow_server_side_startfile` (survey: off by default, and
   `os.startfile` does not exist in the Linux container) with the `app.js` fallback and the two lines in
   `test_csrf_enforcement.py` that pin it.
8. **The typed-node workbench foundation**: `P/src/graph_model.py`, its test, and the `graph_*` fact methods of
   `knovas_client.py` only it uses (check each: the dossier, PMVP-30a, uses `graph_facts` and `graph_node`; keep what
   any kept code calls).
9. **The legacy certificate renewal** method `'legacy'` (the default `csr` stays).
10. **The legacy API path and the mock mode**: in `knovas_client.py` `use_secured_api=false`, the legacy GET search,
    `allow_legacy_api_fallback`; the compose `mock` profile and `docs/demo.md`'s mock instructions; config keys and env
    examples; tests that only cover the legacy path. **Keep `KnovasPlatform/mock_knovas_api/`** if anything outside the
    Platform's mock mode uses it (the survey found a CI step, the Connector's and the Platform's contract tests): CI
    definitions are critical and this item does not change them. If removing the mock mode would need a CI change,
    stop and report what.

## Do not touch

The shared company login (`identity.enabled: false`) and everything it needs; the hidden views; the assistant, chats,
shell, Admin, search and document view, views, harness; migrations; nginx files, Helm, CI definitions; the frozen test
files; `conftest.py` except where a fixture only serves removed code (say which).

## Rules

1. One commit per numbered point, `CLEANUP-1: …`, ending with the co-author line, so a point can be reverted alone.
2. After every point, run the tests that touch what it removed plus `tests/test_pmvp11_navigation_acceptance.py`
   and the search page's tests. A test that only tests removed code is removed with it; any other test that breaks
   means the code was not dead: put it back and say so.
3. Docs that describe removed features are updated in the same commit.
4. **Do not run the whole suite** (one shared database server); the coordinator runs it when integrating.
5. Build the image once at the end (`docker build -f P/Dockerfile P` or the compose build), to show the removed
   packages are not needed. Do not push or tag an image.

## Done when

All points done or reported as stopped; the frozen check all OK; the image builds; commits pushed on
`maint/pmvp-cleanup-1`; no pull request. In your notes below: per point the files and lines removed, what you kept
that the survey listed and why.

## Builder's notes

Builder, 2026-11-21, branch `maint/pmvp-cleanup-1` from `cdfc6ab`. One commit per point; counts are `git show --shortstat`
(insertions / deletions, binary files not counted).

| Point | Commit | Files | +/− | Removed |
|---|---|---|---|---|
| 1 | `a190799` | 16 | +8 / −2210 | `static/cortex-bestand/` (5), `KnovasPlatform/knovas-docs/` (4), `img/knovas-mark.svg`, `DESIGN-CANVAS.md`; the README banner, the test comment, the ontology.css header; mentions in `RELEASE_NOTES.md` and `Secure_API.md` reworded |
| 2 | `79b8a7b` | 2 | +5 / −164 | every rule the order lists, in `style.css` and `ontology.css` with their selector-list entries |
| 3 | `a61ce63` | 14 | +2 / −596 | every symbol the order lists; also `autodoc.max_discovery_entries` (read only by `find_autodoc_files`) and the imports only the removed code used; the C3 test that called `_sync_single_document_secured` |
| 4 | `9f53152` | 2 | +3 / −28 | the six pins and the four apt packages; the `anthropic` line untouched |
| 5 | `dcd5b97` | 3 | −1974 | `serve_cortex_demo.py`, `demo_data/`, `test_cortex_demo_client.py` |
| 6 | `8120a00` | 9 | +16 / −576 | the flag, the fixtures and helpers (`app.py` ~446 lines), the route branches, `test_search_test_results.py`, two tests of the sample payload, `docbridge_test_data/AutoDoc/corpus/` (4 files); no doc or env example named the flag |
| 7 | `cba4126` | 35 | +139 / −1317 | both companion apps, `open_tokens.py`, the three `/api/open-tokens/*` routes and their login/CSRF/identity exemptions, `POST /api/document/<id>/open` with `_open_file_external` and `web.document_handler`, the app.js companion and server-open branches, the config keys, `open-tokens-api.md`, the tests of those routes; docs updated |
| 8 | `1cc6cb7` | 4 | +3 / −427 | `graph_model.py`, its test, `graph_create_fact` / `graph_update_fact` / `graph_delete_fact` and their client tests |
| 9 | `56d67e4` | 5 | +19 / −76 | the `legacy` renewal, `_extract_customer_id_from_cert`, `api.cert_renew_method`, the `generate_certificate` endpoint key |
| 10 | `388b848` | 19 | +41 / −220 | `use_secured_api`, `allow_legacy_api_fallback`, the legacy GET search and endpoint keys, the compose `knovas-mock` service, `KnovasPlatform/docs/demo.md` and the links to it, spec section 2.9 |

**Kept from the survey's list, and why**

- `KnovasPlatform/mock_knovas_api/` (all of it, its Dockerfile included): the CI step "Mock Knovas API", the Platform's
  `test_doc_fields_mock_contract.py` / `test_doc_fields_render.py` and the Connector's contract test use it. With the
  compose service gone, its Dockerfile has no user; removing it would not need a CI change, but the order keeps the folder.
- `graph_facts`, `graph_node` (the dossier); `graph_neighbors` and the schema-attribute methods (not fact methods, tested).
- `search_plan(enabled=…)` and `_fetch_search_page(over_fetch=…)`: `src/assistant/core.py` passes the first, a kept test the second.
- `search_documents(filters=…)` and the app's `knovas_filters`: `run_search` passes them on, also for the assistant; the
  client now documents that it does not send them.
- `OPEN_PUBLIC_BASE_URL`: nothing in the Platform reads it now (the key left `config.yaml`), but
  `scripts/lib/expand_knovas_env.sh` and `stack_identity.sh` write it and the CI contract `test_expand_knovas_env.sh`
  checks it. Removing it changes a CI contract, so it stays (stop, not done).
- The `api.auth_type` / `api.api_key` header ("legacy authentication" in `config.yaml`): not on the list, and tests use
  `api.api_key` as their config-loader example.

**Left on purpose, for the coordinator**

- Newly dead after point 3: `AutoDocFileHandler.parse_filename`, `calculate_file_hash`, `is_supported_file` (only
  `autodoc_path` is read), and `_secured_transmit_parts_from_document` with its helpers (no production caller since
  `_sync_single_document_secured` went; `test_knovas_extract_upload.py` still calls it). Not on the list.
- `tests/conftest.py` is untouched: `FakeGraphApi` still has the three fact writes, and several test configs carry an
  inert `companion_enabled: false`. Both only serve removed code; left so the dossier's and other branches' conftest
  edits merge cleanly.
- `src/assistant/core.py` and `src/assistant/README.md` still say "the legacy GET search"; the assistant is not mine
  to touch. `test_assistant_core.py` passes unchanged.
- KnovasConnector's `semantix_cert.py` still names a `legacy` renewal it does not implement (out of this order).

**Tests changed rather than removed** (they test kept code through a removed door): the two C4 certificate-install
tests now drive the CSR call; the 204-delete test deletes an edge; `test_web_login`'s path-confinement test keeps
its download half; the client-path test of `test_open_tokens.py` lives on as `test_web_client_path.py`; the
legacy-mode refusal test checks that a client without mTLS sends nothing.

**Runs** (per point, own database `knovas_platform_test_cleanup1`, compared with `baseline-failing-ids.txt`): every
failure was a baseline id. Two more, `test_shell_nav_styles` / `test_shell_parts_css` import-order tests, fail on
`cdfc6ab` itself and were fixed on `feat/platform-mvp` by `fe91aba`. Also green: `mock_knovas_api/tests` (233),
`scripts/lib/test_expand_knovas_env.sh`, `scripts/ci/check_knovas_extract_pin.sh`, `docker compose config` with and
without the experiments profile. Frozen check: all OK.

**Image:** `docker build -f P/Dockerfile P` at `388b848` built (local name `cleanup1-throwaway:local`, not pushed,
removed again); knovas-extract 0.4.0a1 from the pinned git ref, brain-runner skipped without the build secret as
designed. In the image `knovas_client`, `web_interface.app`, `identity.webauth` and `document_grants` import, and none
of pyodbc, watchdog, dotenv, pythonjsonlogger, pyotp, dateutil nor the ODBC/mdbtools packages is installed.
