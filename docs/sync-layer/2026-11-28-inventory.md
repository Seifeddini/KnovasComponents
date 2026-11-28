# Sync layer: inventory as built (SS-954)

**Date:** 2026-11-28. **Epic:** SS-953. **Written by:** Claude (model-written, not yet accepted by the founder).

**What this is.** This document lists every part of the sync layer and every backend as the code stands today. For each one it gives the component, its path, the protocol version, the state of its switch, how its data reaches the Brain, what starts it, and which tests cover it. Every statement points to its evidence.

**How to read the evidence.**

| Prefix | Means |
|---|---|
| `KC:` | KnovasComponents `origin/main` at `a9609b49` (the merge of PR #59, SS-950). The path is from the repository root. |
| `KB:` | KnowledgeBase `origin/develop` at `8c25ae1a3` (2026-11-26). |
| `KB@master:` | KnowledgeBase `origin/master` at `5594e90e5` (2026-11-22). |
| `KGB:` | The working tree of `E:\Knovas\Knovas GmbH Brain`, read only. |
| `Brain:` | The Knovas Brain over MCP. Readiness was checked on 2026-11-28 at 13:07Z: ready. The last document write was 2026-11-18. |

**Shortened paths.** Where a path is shortened with `…`, it means:

| Short | Full path |
|---|---|
| `KC:…/sync_component/` | `KC:KnovasPlatform/components/sync_component/src/sync_component/` |
| `KC:…/builder/`, `KC:…/brain_sync/`, `KC:…/docbridge_integration/` | `KC:KnovasPlatform/components/docbridge_integration/src/builder/`, `…/src/brain_sync/`, `KC:KnovasPlatform/components/docbridge_integration/` |
| `KC:…/knovas-tenant.md` | `KC:KnovasPlatform/docs/deployment/knovas-tenant.md` |
| `KB:…/Brain_Mapping_From_CI.md`, `KB:…/Brain_Store_Operations.md` | `KB:docs/archive/Docs/08_RUNBOOKS/Semantix_Runbooks/Ops/` plus the file name |
| `KB:…/source_to_brain_mapping.md` | `KB:docs/archive/Docs/04_COMPONENTS/source_to_brain_mapping.md` |
| `KB:…/mappings/`, `KB:…/case-study-1/` | `KB:docs/ModernDocs/client-zero/mappings/`, `KB:docs/ModernDocs/client-zero/case-study-1/` |
| `KB:…/api/`, `KB:…/services/`, `KB:…/config/` | `KB:knovas-software/app/src/api/`, `…/src/services/`, `KB:knovas-software/app/config/` |
| `KB:…/app-config-prod.yaml` | `KB:infra/kubernetes/components/app-config-prod/app-config-prod.yaml` |

A `path:line` is that line at that ref. Nothing here was taken from memory. Where the repositories cannot show something (for example, what runs on server01 today), the text says **not verifiable**.

## 1. The picture in one table

| Backend | Who reads it | Protocol | How data reaches the Brain | Who starts it | Inside the protocol (Link + kit)? |
|---|---|---|---|---|---|
| GitHub (the KnowledgeBase repo) | brain-runner over a checkout. A 1.1 stream inside the runner. | 1.1 stream; 1.x jobs | The runner calls `secured/graph/*` with the tenant certificate | A person: `brain-mapping.yml` (`workflow_dispatch`) or `brain_mapping_local.sh plan/apply/sync` | **No.** Link is not on the read path. |
| GitHub (fetch / write-back) | sync component behind Link | 1.x `fetch`/`write`. The v2 write path sits behind `SYNC_PROTOCOL_V2`. | Work channel through Link | A Brain job (a person's accept, or a fetch of an original) | Yes (1.x); v2 behind its switch |
| Jira | Jira connector, on the kit | 2.0 record stream and actions | `dump-stream` writes a file. A person runs brain-runner on server01 with the tenant certificate. | A person: `brain_mapping_local.sh jira`, with an interactive `y` | **Reading: no.** Writing: yes (v2, behind the switches) |
| File share (as documents) | Knovas Connector | Control plane 1.x (`platform_link`) or none (legacy). Its data plane has its own routes. | Direct mTLS upload to `secured/init_document_transmission` | Platform `run apply` (platform_link), or a continuous loop (legacy) | **No** |
| File share (as records) | sync component (`knovas_connector` fetcher plus `ShareSource`) | 1.1 stream inside the runner | The runner's graph writes, through Link's relay | Platform schedule or command; a dry run, then a person's apply | Yes (1.x) |
| Microsoft 365 (OneDrive/SharePoint) | Knovas Connector (`m365/`) | Same as file share as documents | Direct mTLS upload | Same as file share as documents | **No** |
| HTTP/JSON | sync component fetcher; also an in-Platform reader for test and sample | 1.x | The runner's graph writes, through Link's relay | Platform schedule or command | Yes (1.x). No tenant uses it (not verifiable). |
| PostgreSQL | **not built** | — | — | — | — |

## 2. Shared parts

### 2.1 Knovas Link

| Aspect | As built | Evidence |
|---|---|---|
| Component, path | One component per site, the only process there that talks to Knovas | `KC:KnovasLink/README.md:2-10`; `KC:KnovasLink/src/knovas_link/` |
| Version | Code says 1.2.0. The package metadata still says 1.1.0 and "protocol 1.1". | `KC:KnovasLink/src/knovas_link/__init__.py:10` `VERSION = "1.2.0"`; `KC:KnovasLink/pyproject.toml:7-8` |
| Protocol 1.x | Local API, work channel, relay, reports, sync status (1.2). Contracts are pinned at 1.2.0. | `KC:KnovasLink/contracts/contracts.lock` `"version": "1.2.0"`; `local_api.py:247-260` (routes) |
| Protocol 2.0 | Site declaration, permit keys, permit checks, used-permit store, write log, v2 job routing | `KC:KnovasLink/src/knovas_link/protocol_v2.py:1-16`; `write_path.py:1-31`; `jobs.py:272-414` |
| Switch | `LINK_PROTOCOL_V2`, **off** by default. With it off, a v2 job is answered `unsupported protocol_unsupported`. | `KC:KnovasLink/src/knovas_link/settings.py:27-31,117`; `link.py:133`; `jobs.py:277-280` |
| Switch at sites | **On** in the Jira site's compose file | `KC:KnovasConnectorJira/deploy/docker-compose.jira.yml:29` `LINK_PROTOCOL_V2: "true"` |
| Switch at sites | Absent from the root compose file and `knovas.env.example`. Whether server01 runs it on is **not verifiable**. | — |
| Job kinds | 1.x: `fetch`, `write`, `probe`. v2: `preview`, `act`, `reread`, `review_outcome`, `unstage`. **No read or stream job.** | `KC:KnovasLink/src/knovas_link/registry.py:27-29` |
| Relay to the Brain | An allow-list of exact paths, plus everything below `secured/graph/` **with every method, DELETE included** | `KC:KnovasLink/src/knovas_link/relay.py:20-25`; `local_api.py:40`; test `KC:KnovasLink/tests/test_relay.py:56` `test_every_method_is_relayed` |
| Default components | Link writes tokens for `sync-component` and `knovas-connector`, plus `LINK_COMPONENTS` | `KC:KnovasLink/src/knovas_link/settings.py:24-27,47` |
| Tests | 937 tests pass on Linux. CI job `knovas-link`. | `KC:.github/workflows/ci.yml:204-222`; §6 |

### 2.2 Connector kit

| Aspect | As built | Evidence |
|---|---|---|
| Component, path | `knovas-connector-kit` 0.1.0, protocol 2.0. Since 2026-11-23 it is the only copy: the vendored copies were removed. | `KC:KnovasConnectorKit/pyproject.toml:7-9`; `KC:KnovasConnectorKit/README.md:3-9`; commit `a9609b49` |
| Modules | contracts, canonical, permits, `Connector` base, `LinkGate` reference, example connector, conformance suite | `KC:KnovasConnectorKit/README.md:15-23` |
| Contracts | A byte-for-byte copy of `KnovasLink/contracts/v2`, checked against its lock on first use | `KC:KnovasConnectorKit/src/knovas_connector_kit/contracts.py:23-29` |
| Contracts | The copy is compared to Link's in the tests | `KC:KnovasConnectorKit/tests/test_contracts_and_permits.py:16` |
| Conformance | 7 properties. Read: `replay_changes_nothing`, `deletion_guard`, `no_credential_leaves_site`. Write: `preview_equals_receipt`, `exactly_once`, `permit_refusals`. Plus `review_outcome`. | `KC:KnovasConnectorKit/src/knovas_connector_kit/conformance/suite.py:35-37` |
| Users | Jira connector; the sync component's GitHub v2 connector | `KC:KnovasConnectorJira/pyproject.toml:11`; `KC:KnovasPlatform/components/sync_component/src/sync_component/github_v2.py:45` |
| Tests | 65 tests, plus a mutant per property. CI job `connector-kit`. | `KC:KnovasConnectorKit/tests/test_conformance_suite.py`; `KC:.github/workflows/ci.yml:227-245` |

### 2.3 Sync component

| Aspect | As built | Evidence |
|---|---|---|
| Component, path | `sync_component` 0.3.0 ("under connector protocol v1") | `KC:KnovasPlatform/components/sync_component/src/sync_component/__init__.py:1,3` |
| Modes | **Link mode** when `SYNC_LINK_URL` is set: its Brain calls go through Link's relay. **Direct mode** otherwise: it enrols itself and uses its own certificate. | `__main__.py:121,145-166`; `__main__.py:18-32` |
| Fetchers | `http_json`, `github`, `knovas_connector` (files) | `fetchers.py:211-212` |
| Engine | Runs brain-runner as a subprocess: plan on a dry run, apply exactly that plan | `engine.py:1,150,198` |
| Engine | Auto-apply only when the plan is clean | `engine.py:53-91`; `agent.py:229-271` |
| Jobs | 1.x: `fetch` and `write` for GitHub only. v2: `preview`, `act`, `reread`, `review_outcome`, behind its switch. | `jobs.py:26-34,63-70` |
| Switch | `SYNC_PROTOCOL_V2`, **off** by default | `__main__.py:94-97` |
| Switch | The tenant runbook tells operators to set it on | `KC:KnovasPlatform/docs/deployment/knovas-tenant.md:988`; `KB:…/Brain_Mapping_From_CI.md:771` |
| Tests | No test folder of its own. Its tests are in `docbridge_integration/tests` (`test_sync_component*.py`, `test_github_jobs.py`, `test_github_write.py`, `test_connector_kinds.py`). CI installs brain-runner 0.3.3, pinned at `c618c7bed`. | `KC:.github/workflows/ci.yml:90-113` |

### 2.4 brain-runner (the Brain's one writer for mappings)

| Aspect | As built | Evidence |
|---|---|---|
| Component, path | `brain-runner` 0.3.3, the same on develop and master | `KB:scripts/brain_runner/__init__.py:7` |
| Sources | `local`, `github`, `folder`, `records`. Records come from a connector's v2 stream file via `--stream`. | `KB:scripts/brain_runner/__main__.py:387-390`; `KB:scripts/brain_runner/sources/__init__.py:1-2` |
| Streams | It reads every source as a 1.1 stream (`document`, `tombstone`, `generation`, `state`) and takes v2 `record`. It refuses the permission types (`unsupported_type`) and a stream without a complete generation (`incomplete_stream`). | `KB:scripts/brain_runner/streams.py:1-42,62,289-306`; `KB:scripts/brain_runner/runner.py:41-42` |
| Graph writes | HTTP to `secured/graph/nodes`, `…/facts`, `…/edges`, PATCH and DELETE on facts and edges. The tenant certificate is used directly, or Link's relay with `BRAIN_LOCAL_TOKEN`. | `KB:scripts/brain_runner/api.py:3-4,19-24`; `apply.py:108,275,305,442,466` |
| HTTP 500 | **Never retried** (`RETRY_STATUSES = {429, 503}`). It is counted as an error and the apply goes on. The mapping ends Degraded/WriteErrors and the run exits 1. | `KB:scripts/brain_runner/api.py:37`; `apply.py:118-119,314-316,447,469,639-640`; `__main__.py:688` |
| HTTP 500 | Only node creates are re-read after a non-2xx | `apply.py:310-317` |
| HTTP 500 | Failed run-record facts are never planned again | `KB:docs/ModernDocs/client-zero/case-study-1/STATUS.md:70-72` |
| Tests | 76 test files in `scripts/brain_runner/tests/`. CI runs them in `ci.yml` job `definitions-bundle` (on path changes) and in `client-zero.yml` (master pushes). | `KB:.github/workflows/ci.yml:1044,1085`; `KB:.github/workflows/client-zero.yml:64-65` |

### 2.5 Deletion guard

| Aspect | As built | Evidence |
|---|---|---|
| Rule | Per type, it trips when `retire >= 3 and share > 0.2`. A mapping may override with `deletion_guard.max_retire_share`; 75 of 76 mapping files set it. | `KB:scripts/brain_runner/guard.py:7-8,15-23`; e.g. `KB:…/mappings/jira-issues.yaml:45-46` |
| Plan side | A tripped mapping is planned out (`blocked`/`unguarded`). Document removes go through the same check. A mapping that is no longer configured always has its removes held. | `KB:scripts/brain_runner/diff.py:262-287,736,766`; `documents.py:382-392` |
| Approval | Only `apply --approve-guard REASON`. The reason is kept in `guard_approvals`. | `KB:scripts/brain_runner/__main__.py:403`; `apply.py:231-249` |
| **Where it does not apply** | It runs only inside brain-runner. Nothing on the server side checks it. | — |
| — Server routes | `DELETE /secured/delete_information_object`, `DELETE /secured/delete_all_documents` (a typo guard only), `DELETE /secured/graph/nodes/<id>`, `/edges/<id>`, `/facts/<id>` | `KB:knovas-software/app/src/api/secure_api.py:2868,3137,3146-3147`; `KB:knovas-software/app/src/api/graph_api.py:1581,1718,2363` |
| — Link's relay | Relays all of these | `KC:KnovasLink/src/knovas_link/relay.py:20-25` |
| — Knovas Connector | Deletes on its own in legacy mode (`delete_on_remove`, default true) | `KC:KnovasConnector/src/sync/knovas_uploader.py:523-530`; `KC:KnovasConnector/src/sync/sync_executor.py:991-993,1026` |
| — Old loader and cleanup | The client-zero loader (when `BRAIN_WRITER != runner`) and `p8_cleanup.py` | `KB:scripts/client_zero/load_ontology.py:702,1020,1046,1068,1074`; `KB:.github/workflows/client-zero.yml:146-147`; `KB:scripts/client_zero/p8_cleanup.py:61` |
| Tests | 15 or more runner tests | e.g. `KB:scripts/brain_runner/tests/test_runner.py:187`, `test_claims_and_guard.py:118,127,146`, `test_documents_apply.py:461`, `test_retire_entries.py:298` |
| Tests | Run today: 118 passed, 1 skipped (§6) | — |

### 2.6 The Brain's connector routes (secure-api) and the Platform's protocol side

| Aspect | As built | Evidence |
|---|---|---|
| Work channel | `GET /secured/connector/jobs`, `…/part`, `…/result`, `/probe`, `/jobs/stats`. No flag. The tenant comes from the certificate. Lease 60 s. 429 past the caps. | `KB:knovas-software/app/src/api/secure_api.py:5004,5032,5066,5092,5112`; `KB:…/services/connector_jobs.py:132,235,736-760` |
| v2 routes | `PUT /secured/connector/v2/site`, `GET /secured/connector/v2/permit-keys`: 404 unless `write_back_v2_enabled(tenant)`. `act` is queued only for a site that declared protocol 2. | `KB:…/api/secure_api.py:5130,5153-5154,5169,5191-5192`; `KB:…/services/connector_jobs.py:54-64,1560-1562`; `KB:…/services/connector_sites.py:17-18,70,78` |
| Brain-side v2 switch | `WRITE_BACK_V2_ENABLED`: default false. **Prod: true, for tenant af639810 only, since 2026-11-13** (commit `d08d9f3e2`; also on master). Dev overlays: unset, so false. | `KB:knovas-software/app/config/defaults.toml:1181,1185`; `KB:infra/kubernetes/components/app-config-prod/app-config-prod.yaml:139-140` |
| Originals, proposals | `GET /secured/documents/original`, `PUT /secured/documents/source_ref`, `/secured/source-writes` (+ accept/reject) | `KB:…/api/secure_api.py:5266,5318,5494,5534,5556`; `KB:…/services/fetch_relay.py`; `KB:…/services/source_write_proposals.py` |
| Document upload | `POST /secured/init_document_transmission`, `/secured/transmit_document_part`. Certificate, rate limit, quota and billing only; no protocol check. | `KB:…/api/secure_api.py:1782,1063` |
| Record stream route | Plan I4's `POST /secured/sync/records`: **not built** | not found in `KB:knovas-software/app/src` |
| Platform (Builder) | `/api/connectors/v1`: enrolment, heartbeat, config, package, runs, events, checks. Config comes from the published version only. | `KC:KnovasPlatform/components/docbridge_integration/src/builder/protocol.py:1-63,113-226,355-376,676-705` |
| Platform v2 switch | `BUILDER_PROTOCOL_V2`, off by default | `KC:…/builder/protocol_v2.py:57` |
| Tests | secure-api: `test_connector_jobs.py`, `test_connector_jobs_act.py`, `test_connector_sites.py`, `test_connector_v2_results.py`, `test_fetch_relay.py`, `test_source_write_proposals.py`, in KB `ci.yml` jobs `test-fast` / `test-full`. **Not run today** (they need the database). | `KB:.github/workflows/ci.yml:309,392,621,872` |

## 3. Backends

### 3.1 GitHub

| Aspect | As built | Evidence |
|---|---|---|
| Reading, KnowledgeBase repo (path A) | brain-runner over a checkout. The CI workflow is started by hand only. It applies only on master/develop and only when the repository variable `BRAIN_WRITER=runner`. | `KB:.github/workflows/brain-mapping.yml:3,23-24,101-108,232-233` |
| Reading, KnowledgeBase repo (path B) | `brain_mapping_local.sh plan|apply|sync` on server01 (`/home/master/kb-mapping`) | `KB:scripts/client_zero/brain_mapping_local.sh:1-18`; `KB:…/Brain_Mapping_From_CI.md:284,859` |
| Reading: which writer runs today | CI runner or the old loader, depending on `BRAIN_WRITER`. **Not verifiable** from git. | `KGB:sources/software/modules/MOD-CONNECTION.card.yaml:298-301` |
| Reading, GitHub API source | `--source github` exists, for a runner without a checkout (behind Link). No KnowledgeBase automation uses it. | `KB:scripts/brain_runner/sources/github.py` |
| Reading, sync component | GitHub fetcher plus «bei neuen Commits» polling. Not deployed for af639810 (not verifiable). | `KC:…/sync_component/fetchers.py:159-166`; `agent.py:316-350` |
| Reading, in the Platform | "Sync von GitHub" (KC-0.2) reads the repo in the Platform and writes the Brain with the tenant's certificate: **outside Link** | `KC:…/docbridge_integration/src/brain_sync/runner.py:4-6`; `builder/sources.py:48-49` (`github_runner`, "in der Plattform") |
| Protocol | Reading: a 1.1 stream inside the runner. Fetch of originals and write proposals: 1.x jobs through Link. Writing direct or by pull request: v2 behind `SYNC_PROTOCOL_V2`. | `KC:…/sync_component/github_jobs.py:1-17`; `github_v2.py:1-20` |
| Flags | `SYNC_PROTOCOL_V2` off (code) | §2.3 |
| Flags | Brain side `WRITE_BACK_V2_ENABLED` on in prod for af639810 | §2.6 |
| Flags | `BUILDER_PROTOCOL_V2` off (code). The runbook says to set it. | `KC:KnovasPlatform/docs/deployment/knovas-tenant.md:976` |
| How it reaches the Brain | Reading: the runner's graph writes with the tenant certificate. Writing: the Brain's change sets, then a commit or pull request; the Brain's fact changes at the next read (GI-CONN-04). | §2.4; `KC:KnovasPlatform/docs/connector-protocol-v2.md:38` |
| Trigger | Reading: a person (workflow dispatch, or server01) | `KB:.github/workflows/brain-mapping.yml:3` |
| Trigger | Fetch and write: a Brain job | — |
| Deletions | Tombstones and generations under the guard (runner) | §2.5 |
| Tests | `test_github_jobs.py` 22, `test_github_write.py` 22, `test_sync_component_github_v2.py` 13 (it runs the kit's suite on the GitHub connector), `test_front_matter_edit.py` 27 | `KC:KnovasPlatform/components/docbridge_integration/tests/` |

### 3.2 Jira

| Aspect | As built | Evidence |
|---|---|---|
| Component, path | `knovas-connector-jira` 0.1.0, on kit 0.1.0, protocol 2.0 | `KC:KnovasConnectorJira/pyproject.toml:7-11`; `src/knovas_connector_jira/connector.py:65` |
| Reading | A `record` per issue, `tombstone` for vanished keys, `generation` (complete), `state` | `KC:KnovasConnectorJira/src/knovas_connector_jira/connector.py:245-285`; `records.py:128` |
| Writing | `set_assignee`, `set_due_date`, `transition_issue` (direct), under permits | `connector.py:82-83,289,368,443` |
| Talks to | Link's local API only: register, heartbeat, config, checks, jobs. Its job kinds are `probe`, `preview`, `act`, `reread`. | `KC:KnovasConnectorJira/src/knovas_connector_jira/site.py:11,49,56,123-148` |
| How records reach the Brain | **Through a file, not through Link.** `dump-stream` writes JSONL. On server01, a person runs `brain_mapping_local.sh jira`: dump, then `brain-runner plan --source records --stream`, the summary, an interactive `y`, then the ontology and `brain-runner apply` with the tenant certificate. | `KC:…/site.py:21-23,239`; `KC:…/dump.py:1-2,99,108-126`; `KB:scripts/client_zero/brain_mapping_local.sh:151-218,189` |
| Trigger | A person only. No workflow, CronJob or script invokes it. | grep of `KB:.github`, `KB:infra` for `brain_mapping_local`, `dump-stream`, `jira-connector`: empty |
| Trigger: when last run | 2026-11-19 by the brief. **Not verifiable** from the repositories. The bet's CV-8 still waits for the founder to run it. | `KB:docs/ModernDocs/steering/bets/BET-2026-11-client-zero.yaml:165` |
| Flags | Site compose: `LINK_PROTOCOL_V2: "true"`, `LINK_COMPONENTS: jira-connector`. The image digest is required. | `KC:KnovasConnectorJira/deploy/docker-compose.jira.yml:29-32` |
| Deletions | Tombstone or generation, under the runner's guard (the mapping's share is 0.2) | `KB:…/mappings/jira-issues.yaml:45-46` |
| Tests | 110 test functions (8 files) against a fake Jira and a fake Link. The conformance suite passes 6 of 6 applicable properties (`review_outcome` n/a). | `KC:KnovasConnectorJira/tests/`; §6 |
| Tests | `test_brain_mapping*.py` and `test_jira_issues.py` run **only** on master pushes (`client-zero.yml`), not on PRs | `KB:.github/workflows/client-zero.yml:15-17,60-61` |

### 3.3 File share

| Aspect | As built | Evidence |
|---|---|---|
| As documents | Knovas Connector: bounded discovery under `RC_WATCH_ROOTS`. SMB is only a mounted folder. | `KC:KnovasConnector/src/discover/filesystem.py:1`; `KC:KnovasConnector/docs/operations.md:153` |
| As records | sync component `knovas_connector` fetcher plus `ShareSource` (brain-runner over a mounted folder) through Link. An empty folder is refused (`fetch.folder_empty`). | `KC:…/sync_component/fetchers.py:186-206`; `share.py:1` |
| Folder source | The runner's `folder` source, e.g. the Knovas GmbH Brain folder via `brain_mapping_local.sh brain` | `KB:scripts/client_zero/brain_mapping_local.sh:220-286` |
| Version (Connector) | `pyproject.toml` 0.3.0. The images are tagged 0.1.1. The last CHANGELOG entry is 0.1.1. | `KC:KnovasConnector/pyproject.toml:7`; `KC:KnovasConnector/docker-compose.yml:10`; `KC:docker-compose.yml:42`; `KC:KnovasConnector/CHANGELOG.md:83` |
| Control plane (Connector) | With `RC_PLATFORM_URL`: protocol v1 `POST /api/connectors/v1/enrol` with its own key and CSR (not Link's). Without it: a hand-installed tenant certificate that renews itself, and an inbound `/sync` from the Platform. | `KC:KnovasConnector/src/platform_link/__init__.py:1,9`; `client.py:10,50-51`; `identity.py:68`; `bootstrap.py:67-78`; `src/sync/semantix_cert.py:118`; `src/routes/sync.py:97` |
| Data plane (Connector) | Direct mTLS to `secured/init_document_transmission` and `transmit_document_part`. **No Link client** (grep for `knovas_link`, `knovas_connector_kit` and `stream-message`: empty). No source reference is sent. | `KC:KnovasConnector/src/sync/knovas_uploader.py:52,357-370,413,479` |
| Deletions | Legacy mode: `DELETE /secured/delete_information_object`, default on, skipped on a truncated scan, **no guard** | `knovas_uploader.py:523-530`; `sync_executor.py:991-993,1751-1759` |
| Deletions | platform_link: forced off, so removed files stay in the Brain | `KC:KnovasConnector/src/platform_link/operations.py:97`; `KC:KnovasConnector/docs/platform-link.md:18-23` |
| Permissions | Access groups configured by hand per source (`alle` for everyone). None are taken from the source. | `KC:KnovasConnector/src/platform_link/operations.py:43-50`; `KC:KnovasConnector/docs/configuration.md:112-120` |
| Incremental state | SQLite with mtime and size | `KC:KnovasConnector/src/sync/sync_state_db.py:1,15-21` |
| Trigger | platform_link: the schedule runs dry runs only; apply only on a person's `run apply` | `KC:KnovasConnector/src/platform_link/agent.py:135-140,164-165`; `bootstrap.py:80` |
| Trigger | Legacy: a continuous loop, default 60 s | `KC:KnovasConnector/src/sync/sync_scheduler.py:487-488,521-532` |
| Tests | 879 test functions across 73 files. Among them: `test_sync_delete.py` 8, `test_platform_link.py` 23, `test_knovas_uploader.py` 17, `test_sync_access_groups.py` 5. CI job `knovas-connector`. **Not run today.** | `KC:KnovasConnector/tests/`; `KC:.github/workflows/ci.yml:408-519` |

### 3.4 Microsoft 365 (OneDrive, SharePoint)

| Aspect | As built | Evidence |
|---|---|---|
| Component, path | Knovas Connector `m365/`: one folder in OneDrive or SharePoint takes the place of the watch root. A legacy OneDrive mirror runs when `M365_FOLDER_URL` is unset. | `KC:KnovasConnector/src/m365/source.py:1,10-14`; `KC:KnovasConnector/src/onedrive_mirror/__init__.py:1`; `KC:KnovasConnector/src/app.py:74-82` |
| Incremental | Graph delta link, refreshed at the start of each cycle. No webhooks. | `KC:KnovasConnector/src/m365/inventory.py:1,168-178`; `sync_executor.py:1152-1154` |
| Permissions | **None.** The delta `select` carries no permissions. SS-582 needs the permission stream types, which v1 and v2 only reserve. | `KC:KnovasConnector/src/m365/inventory.py:36-39`; `KC:KnovasPlatform/docs/connector-protocol-v2.md:524` |
| Upload, deletions, trigger | As for the file share as documents (§3.3) | — |
| Tests | `test_m365_inventory.py` 13, `test_m365_location.py` 6, `test_m365_resolver.py` 8, `test_m365_sync.py` 21, `test_onedrive_mirror*.py` 33 | `KC:KnovasConnector/tests/unit/` |

### 3.5 HTTP/JSON

| Aspect | As built | Evidence |
|---|---|---|
| Component | sync component fetcher: GET with an optional Bearer token, HTTPS only unless `SYNC_ALLOW_HTTP`, at most `SYNC_FETCH_MAX_BYTES` (50 MB) | `KC:…/sync_component/fetchers.py:45,67-91` |
| In-Platform reader | `http_json` "in der Plattform", for test and sample only (`adapter_only`) | `KC:…/builder/sources.py:53-54`; `KC:…/builder/components.py:36` |
| Protocol | 1.x. No v2 connector, no stream of its own. The runner reads `response.json`. | `fetchers.py:67-91` |
| Reaches the Brain | Through the runner's graph writes, through Link's relay | §2.3 |
| Deployed | Not verifiable; no tenant configuration in the repositories names one | — |
| Tests | `test_sync_component.py`, `test_connector_kinds.py` | `KC:KnovasPlatform/components/docbridge_integration/tests/` |

### 3.6 PostgreSQL

| Aspect | As built | Evidence |
|---|---|---|
| Component | **Not built.** No `postgres`, `psycopg` or SQL reader in the sync component, Link, the kit, the Jira connector or the Knovas Connector. | grep over `KC` |
| Plan | Only "planned" entries exist, and their text is stale: "wartet auf das Connector-Protokoll v1 (SS-534)" | `KC:…/builder/components.py:35`; `KC:…/builder/sources.py:61` |
| Plan | Plan item I4 = SS-581: `POST /secured/sync/records` plus record components for HTTP/JSON, PostgreSQL and Jira. The route was not built. | `KB:docs/archive/superpowers/plans/2026-11-05-connection-layer-implementation.md:721-727` |
| Related analysis | NVIDIA Auto Ontology (text-to-SQL over relational databases) | `KB:docs/archive/ModernDocs/reports/2026-11-06-nvidia-auto-ontology-analysis.md` |

## 4. Switches in one place

| Switch | Code default | Compose / env example | KB infra | Runbook says | Live state |
|---|---|---|---|---|---|
| `LINK_PROTOCOL_V2` | off (`KC:KnovasLink/src/knovas_link/settings.py:117`) | on in `docker-compose.jira.yml:29`; absent elsewhere | not set | on at the server01 site (`KB:…/Brain_Mapping_From_CI.md:745-747`) | not verifiable |
| `SYNC_PROTOCOL_V2` | off (`KC:…/sync_component/__main__.py:94-97`) | absent | not set | on (`KC:…/knovas-tenant.md:988`; `KB:…/Brain_Mapping_From_CI.md:771`) | not verifiable |
| `BUILDER_PROTOCOL_V2` | off (`KC:…/builder/protocol_v2.py:57`) | absent | — | on (`KC:…/knovas-tenant.md:976`) | not verifiable |
| `WRITE_BACK_V2_ENABLED` / `_TENANTS` | false / "" (`KB:…/config/defaults.toml:1181,1185`) | — | **prod: true, af639810** (`KB:…/app-config-prod.yaml:139-140`); dev: unset | — | on in prod since 2026-11-13 (from the repository) |
| `auto_apply` (per source) | false (`KC:KnovasLink/contracts/local-api.md:75-77`) | — | — | — | not used: no sync component serves af639810 (not verifiable) |
| `RC_PLATFORM_URL` (Connector) | unset, so legacy mode (`KC:KnovasConnector/src/platform_link/__init__.py:9`) | absent from `knovas.env.example` | — | — | not verifiable |

## 5. Manual steps found (input for SS-961)

| # | Step | Who | Evidence |
|---|---|---|---|
| M1 | Plan and apply the KnowledgeBase mappings: workflow dispatch, or server01 `brain_mapping_local.sh plan/apply/sync` | founder | `KB:.github/workflows/brain-mapping.yml:3`; `KB:…/Brain_Mapping_From_CI.md:442` ("You run every command") |
| M2 | After a cut apply, plan again and apply again | founder | `KB:.github/workflows/brain-mapping.yml:323,349-351` |
| M3 | Jira: `brain_mapping_local.sh jira` on server01, answer `y`, run again on failure, "whenever the Brain should follow Jira" | founder | `KB:scripts/client_zero/brain_mapping_local.sh:189,215`; `KB:…/Brain_Mapping_From_CI.md:300` |
| M4 | Knovas GmbH Brain folder: `brain_mapping_local.sh brain` from a local checkout | founder | `KB:scripts/client_zero/brain_mapping_local.sh:220-286` |
| M5 | Restart the graph engine after each certificate renewal: 720 h certificate renewed 240 h early, so about every 20 days | founder | `KB:…/Brain_Store_Operations.md:794-797`; `KB:infra/kubernetes/base/brain-graph/brain-graph-certificate.yaml:9-10,19-20` |
| M6 | Re-run after HTTP 500 write failures (SS-960). There is no re-run-failed mode; a new plan and apply repairs it. | founder | `KB:scripts/brain_runner/api.py:37`; `KB:scripts/brain_runner/tests/test_review.py:24` |
| M7 | relink-facts: "rerun the same command once" | founder | `KB:…/Brain_Mapping_From_CI.md:738-739` |
| M8 | Site certificate: `certificate-request`, a Knovas admin signs it, `install-certificate` | admin + site | `KC:KnovasPlatform/docs/connector-protocol-v1.md:33,123` |
| M9 | Knovas Connector documents (platform_link): a person's `run apply` after every dry run | person | `KC:KnovasConnector/src/platform_link/agent.py:135-140` |
| M10 | Knovas Connector access groups per source, set by hand | person | `KC:KnovasConnector/src/platform_link/operations.py:43-50` |

## 6. Tests run today (2026-11-28)

| What | Command (from the package folder) | Result |
|---|---|---|
| Connector kit | `py -3 -m pytest -q tests -p no:cacheprovider` (Windows, Python 3.13.5) | **65 passed** (also 65 on Linux) |
| Conformance suite, example connector | `PYTHONPATH=src py -3 -m knovas_connector_kit.conformance knovas_connector_kit.example:make_harness` | 7/7 passed, report sha256 `9d519e02…c508146`, exit 0 |
| Conformance suite, Jira connector | in `KnovasConnectorJira`: `PYTHONPATH="src;tests;../KnovasConnectorKit/src" py -3 -m knovas_connector_kit.conformance harness:make_harness` | 6 passed, `review_outcome` not applicable, report sha256 `f1fbcecc…dc486`, exit 0 |
| Knovas Link | `PYTHONPATH=src py -3 -m pytest -q tests` | Windows: 928 passed, 8 failed, 2 errors, **all Windows-only** (POSIX 0600 mode asserts, `/data` vs `\data`, an env var over 32767 chars) |
| Knovas Link | on Linux (WSL, Python 3.12.3, from `git archive HEAD`) | **937 passed** |
| Jira connector | `py -3 -m pytest -q tests` | Windows: 151 passed, 1 failed (the 0600 mode assert) |
| Jira connector | on Linux | **152 passed** |
| sync component + Builder protocol (14 files in `docbridge_integration/tests`: `test_sync_component*.py`, `test_github_jobs.py`, `test_github_write.py`, `test_builder_protocol_v2.py`, `test_ci_runner_and_link_jobs.py`, `test_connector_kinds.py`, `test_builder_autosync.py`, `test_brain_sync.py`) | `PYTHONPATH=<brain-runner 0.3.3 from KB c618c7bed> py -3 -m pytest -o addopts="" -q …` | **379 passed, 1 skipped, 1 failed.** The failure is Windows-only: the Platform's copy of `declaration-v2.schema.json` is checked out CRLF, while the index is LF. Without brain-runner on the path, the runner-backed tests fail; CI installs it. |
| brain-runner guard and streams (`test_claims_and_guard.py`, `test_streams.py`, `test_record_streams.py`, `test_retire_entries.py`, `test_documents_apply.py`) | from `git archive KB origin/develop scripts/brain_runner` | **118 passed, 1 skipped** |
| Not run | Knovas Connector (Flask and extraction dependencies), secure-api connector tests (they need PostgreSQL and Redis) | — |

## 7. Where the 2026-11-23 picture is wrong now

1. **"Protocol v2 off everywhere": wrong on the Brain side.** `WRITE_BACK_V2_ENABLED: "true"` for af639810 in prod, on develop and master, since commit `d08d9f3e2` (2026-11-13) (`KB:…/app-config-prod.yaml:139-140`). The site compose for Jira turns `LINK_PROTOCOL_V2` on (`KC:…/docker-compose.jira.yml:29`). The runbook tells the operator to turn `SYNC_PROTOCOL_V2` and `BUILDER_PROTOCOL_V2` on. Only the code defaults are off. Several KB docs still say "off" (`KB:knovas-software/app/src/services/write_back/feature.py:24`; `KB:…/source_to_brain_mapping.md:507`).
2. **"The founder runs plan and apply on server01": only half.** The KnowledgeBase repo is also written by a by-hand CI workflow on GitHub runners (`KB:.github/workflows/brain-mapping.yml`). server01 is needed for `jira` and `brain` (`KB:…/Brain_Mapping_From_CI.md:258-259`). Which writer runs depends on the repository variable `BRAIN_WRITER`, which git does not show.
3. **"GitHub reading: brain-runner reads the repo, no stream": the runner does read it as a 1.1 stream**, inside itself (`KB:scripts/brain_runner/runner.py:41-42`). What is missing is a stream route at Knovas, and Link on the read path. Two more GitHub paths exist outside Link: the Platform's own "Sync von GitHub" (`KC:…/brain_sync/runner.py:4-6`) and the sync component's direct mode.
4. **"The Knovas Connector has its own enrolment": half.** With `RC_PLATFORM_URL` its control plane speaks protocol v1 against `/api/connectors/v1/enrol`, like Link. Only its data plane is outside (direct mTLS uploads). Link's README claims the Connector talks only to Link (`KC:KnovasLink/README.md:5,21`); the code has no Link client.
5. **File share has two paths, not one:** documents through the Connector, and records through the sync component behind Link. A third is the runner's folder source.
6. **HTTP 500s are not lost silently.** Each one is counted, the run exits 1, and the mapping becomes Degraded. But a 500 is never retried, a write answered 500 may have landed anyway, and failed run-record facts are never planned again (`KB:…/case-study-1/STATUS.md:70-72`).
7. **"Deletions only under the deletion guard": false on every path but the runner's** (§2.5).
8. **Jira "last run 2026-11-19": not verifiable** from the repositories. The Brain itself shows its last document write on 2026-11-18 (Brain readiness, 2026-11-28).
9. **SS-581 (PostgreSQL) is plan item I4, whose record route `POST /secured/sync/records` was never built.** Jira took a file-dump path instead.
10. Confirmed as stated: the kit is one package (`a9609b49`), Jira is on the kit with a record stream, HTTP/JSON is a sync-component fetcher, PostgreSQL is not built, and the permission stream types are reserved (SS-582 needs a protocol change).

## 8. Drift between documents and code (input for SS-956, SS-959)

| Where | Says | Code says |
|---|---|---|
| `KC:KnovasLink/contracts/local-api.md:3` | "Status: contract, not built" | Link is built and tested (§2.1) |
| `KC:KnovasLink/pyproject.toml:7-8` | 1.1.0, "protocol 1.1" | `VERSION = "1.2.0"`, lock 1.2.0 |
| `KC:KnovasLink/README.md:5,21` | the Knovas Connector talks only to Link | no Link client in `KnovasConnector` |
| `KC:KnovasConnectorJira/src/knovas_connector_jira/__init__.py:5` | "Nothing ships it yet: no image, deployment …" | image, compose and release notes exist |
| `KC:KnovasConnectorKit/src/knovas_connector_kit/contracts.py:4` | `tests/test_vendored_contracts.py` checks the copy | that file does not exist; the check is in `test_contracts_and_permits.py:16` |
| `KC:…/builder/sources.py:61` | the database connector waits for protocol v1 (SS-534) | v1 and v2 exist |
| `KC:KnovasPlatform/docs/connection-layer.md:105` | "No image is released yet" | release workflow and notes exist (`KC:RELEASE_NOTES.md`) |
| `KC:KnovasLink/contracts/v2/contracts.lock` | `2.0.0-draft`, "nothing of v2 is released" | Brain-side v2 is on in prod for af639810 |
| `KC:KnovasConnector/pyproject.toml:7` vs images | 0.3.0 | image tag 0.1.1 |
| `KGB:…/MOD-CONNECTION.card.yaml:6-12,271-276,16-21` | `status: live`; code lists only Link, Connector, Jira, kit | approval `draft`; the sync component and brain-runner are missing (the card's own review says so at `:302-303`) |
| `KC:…/docbridge_integration/src/builder/contracts/declaration-v2.schema.json` | a byte copy of Link's (test `test_builder_protocol_v2.py:121-125`) | no LF rule, so a Windows checkout fails the drift test |
| Brain | SS-953 and its children | not in the Brain (`knovas_find_node key=SS-953`: no match); the Brain's data is from 2026-11-18 |
