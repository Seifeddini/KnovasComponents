# Sync layer: every protocol rule against the code (SS-955)

**Date:** 2026-11-28. **Epic:** SS-953. **Written by:** Claude (model-written, not yet accepted by the founder).

**Refs.** `KC:` is KnovasComponents `origin/main` `a9609b49`. `KB:` is KnowledgeBase `origin/develop` `8c25ae1a3`. The shortened paths are the ones in [the inventory](2026-11-28-inventory.md). The two contract texts are cited by line:

| Short | Means |
|---|---|
| `v1:` | `KC:KnovasPlatform/docs/connector-protocol-v1.md` (versions 1.0–1.2; lock `KC:KnovasLink/contracts/contracts.lock` = `1.2.0`) |
| `la:` | `KC:KnovasLink/contracts/local-api.md` (protocol 1.2) |
| `v2:` | `KC:KnovasPlatform/docs/connector-protocol-v2.md` (v2.0, approved 2026-11-10; lock `KC:KnovasLink/contracts/v2/contracts.lock` = `2.0.0-draft`) |

**Verdicts.**

| Verdict | Means |
|---|---|
| **E+T** | enforced in code, and a test asserts it |
| **E** | enforced, but no test was found that asserts it |
| **NE** | not enforced, or enforced on some paths and not on others |
| **NA** | not applicable: the rule says it is not built, or a decision keeps it open |

Where a rule is enforced on one path and bypassed on another, the verdict is **NE**, and the bypass is named.

**Method.** I enumerated the rules from the three texts, section by section, and mapped every code in `codes.json` (v1, v2) to where the code produces it. I then read the enforcing function and searched the tests by name. I ran the suites listed in §5.

## 1. Protocol 1.x: principles, control plane, data path (`v1:`)

| ID | Rule | Enforcement | Test | Verdict |
|---|---|---|---|---|
| P1-01 | Outbound only: a component calls the Platform, the Platform never calls in; a component opens no port (v1:18, la:13) | Link and the sync component only call out. Link's local API is on the internal network (`KC:KnovasLink/README.md:59`). **The Knovas Connector in its legacy mode takes an inbound `POST /sync`** (`KC:KnovasConnector/src/routes/sync.py:97`; Flask on :5001, `KC:KnovasConnector/Dockerfile:106`). | none for the Connector | **NE** |
| P1-02 | The tenant comes from the certificate; a component never names it (v1:19) | `KB:…/services/connector_jobs.py:736-760` (every statement names the certificate's tenant); `KB:…/services/connector_sites.py:11`; Link permits take tenant and serial from the certificate (`KC:KnovasLink/src/knovas_link/protocol_v2.py:58-74`) | `KB:knovas-software/app/tests/test_connector_jobs.py`, `test_connector_sites.py` | **E+T** |
| P1-03 | Config and package come only from the newest **published** version, never a draft (v1:47) | `KC:…/builder/protocol.py:42,676-705` | no test asserts that a draft never reaches a component (closest: `KC:…/docbridge_integration/tests/test_builder_mappings.py:326`) | **E** |
| P1-04 | Reports carry codes and counts only; anything else is refused (v1:21) | Link `KC:KnovasLink/src/knovas_link/relay.py:58-149`; Platform `KC:…/builder/protocol.py:439-510`, `builder/components.py` | `KC:KnovasLink/tests/test_relay.py:177` `test_a_report_with_anything_but_codes_and_counts_is_refused`, `:210` `test_links_report_rules_are_the_platforms`; `KC:KnovasLink/tests/test_link_heartbeat.py:135` | **E+T** |
| P1-05 | Nothing is written to a tenant without a shown dry run and a person's approval; a schedule runs only dry runs, except `auto_apply` (v1:22-26) | sync component `KC:…/sync_component/agent.py:229-271`, `engine.py:53-91`; Connector platform_link `KC:KnovasConnector/src/platform_link/agent.py:135-140,164-165`; Jira path `KB:scripts/client_zero/brain_mapping_local.sh:189`. **Bypassed:** the Connector's legacy continuous mode uploads with no dry run (`KC:KnovasConnector/src/sync/sync_scheduler.py:521-532`); the old client-zero loader (`KB:.github/workflows/client-zero.yml:146-147`) | `KC:…/docbridge_integration/tests/test_sync_component_autosync.py`; `KC:KnovasConnector/tests/unit/test_platform_link.py` | **NE** |
| P1-06 | A source's credential stays at the company, in the component's environment (v1:27) | `register` answers without credentials (`KC:KnovasLink/src/knovas_link/link.py:389-396`); v2 `no_credential_leaves_site` (`KC:KnovasConnectorKit/src/knovas_connector_kit/conformance/suite.py:456`) | `KC:KnovasLink/tests/test_local_api.py:49`; kit suite (passed today) | **E+T** |
| P1-07 | Enrolment: a one-time token (24 h); the key is made on site; the control token is shown once and kept as a hash (v1:31) | `KC:…/builder/protocol.py:88-89,113-124,165-226`; Link `KC:KnovasLink/src/knovas_link/identity.py`; Connector `KC:KnovasConnector/src/platform_link/identity.py:68` | `KC:KnovasLink/tests/test_enrolment.py:72,94,102,117`; `KC:…/tests/test_builder_site_certificate.py:105,120` | **E+T** |
| P1-08 | Every call carries the control token, `X-Component` and the client certificate's fingerprint, unless `BUILDER_CONNECTOR_REQUIRE_CLIENT_CERT=false` (v1:37) | `KC:…/builder/protocol.py:355-376` | `KC:…/tests/test_builder_components.py:306`; `test_connector_terminator_config.py` | **E+T** |
| P1-09 | A1: until the site's certificate is recorded, the token opens the heartbeat only; every other call is 403 `certificate_not_recorded` (v1:39) | `KC:…/builder/protocol.py:365-372` | `KC:…/tests/test_builder_site_certificate.py:129,141,174` | **E+T** |
| P1-10 | A component that does not know a command answers `refused` (v1:53) | `KC:…/sync_component/agent.py:386-404` (an unknown command: `:403-404`) | none found that sends an unknown command | **E** |
| P1-11 | Within v1 only additions; open objects (job, stream message) may carry unknown fields, closed ones (actor, args, result) refuse them (v1:55, la:288-300) | `KC:KnovasLink/contracts/*.schema.json` (`additionalProperties`) | `KC:KnovasLink/tests/test_contracts.py:245,252` (vectors `job.unknown-field` valid, `result.unknown-field` invalid) | **E+T** |
| P1-12 | A component that cannot write to a source reports `source.write_unsupported` and does not run it (v1:95) | `KC:…/sync_component/agent.py:126,174` | `KC:…/tests/test_connector_kinds.py` | **E+T** |
| P1-13 | Writing needs `write:<kind>` declared and `access: read_write` switched on by a person (v1:99, v1:237) | Link `KC:KnovasLink/src/knovas_link/jobs.py:260-262` (`write_disabled`) | `KC:KnovasLink/tests/test_work_channel.py:242` | **E+T** |
| P1-14 | An empty or missing folder is refused, not read (v1:110) | `KC:…/sync_component/fetchers.py:186-199` (`fetch.folder_empty`). The Connector skips pruning only on a truncated or stopped scan (`KC:KnovasConnector/src/sync/sync_executor.py:1751-1759`); no empty-mount check was found. | `KC:…/tests/test_connector_kinds.py` (sync component) | **NE** (Connector) |
| P1-15 | 1.2 sync status: fixed fields, commits as hex, at most 10 `held_back` codes; a bad shape is 400 and an unknown field is dropped (v1:158-163, la:86-93) | `KC:KnovasLink/src/knovas_link/sync_status.py:24`; `KC:…/builder/protocol.py:439-510` | `KC:KnovasLink/tests/test_link_heartbeat.py:186,217` | **E+T** |
| P1-16 | 1.2 each component behind Link gets its own pause and schedule (v1:143-147, la:94-97) | `KC:KnovasLink/src/knovas_link/link.py:374-383` | `KC:KnovasLink/tests/test_link_heartbeat.py:155` | **E+T** |
| P1-17 | 1.2 «bei neuen Commits»: poll the head every `poll_seconds` (15–3600), plan only the mappings `affected` names (v1:134-142) | `KC:…/sync_component/agent.py:43-60,316-350` | `KC:…/tests/test_sync_component_autosync.py` | **E+T** |
| P1-18 | 1.2 `auto_apply` only for a clean plan: no guard hold, drift, retire entry, release, held document, unknown plan version; it never approves a guard hold (v1:148-157) | `KC:…/sync_component/engine.py:53-91`; `agent.py:257-271` | `KC:…/tests/test_sync_component_autosync.py` | **E+T** (no deployed tenant uses it) |
| P1-19 | `synced_commit` is written only after an apply without errors (v1:160) | `KC:…/sync_component/engine.py:253` | `KC:…/tests/test_sync_component_autosync.py` | **E+T** |
| DP-01 | A component writes to the Brain itself, through Link under A1; the Platform is the control plane only and no customer data passes through it (v1:59-61) | **Bypassed:** the Platform's "Sync von GitHub" reads the repository in the Platform and writes with the tenant certificate (`KC:…/brain_sync/runner.py:4-6`; `KC:…/builder/sources.py:48-49`); the in-Platform HTTP/JSON reader (`builder/sources.py:53-54`) | none | **NE** |
| DP-02 | Records: a dry run plans on site and only counts are reported; an apply writes exactly that plan, once (v1:65) | `KC:…/sync_component/engine.py:1,150,198`; brain-runner refuses a stale plan (`KB:scripts/brain_runner/__main__.py:670-672`) | `KC:…/tests/test_sync_component.py`; `KB:scripts/brain_runner/tests/` | **E+T** |
| DP-03 | Documents (Connector, platform_link): an apply sends only the dry run's list; each source names its access groups or `alle`; a folder nobody chose for is refused (v1:72-75) | `KC:KnovasConnector/src/platform_link/operations.py:43-50` | `KC:KnovasConnector/tests/unit/test_platform_link.py` (not run today) | **E+T** |
| DP-04 | Documents: no deletions; a removed document stays (v1:76) | `KC:KnovasConnector/src/platform_link/operations.py:97` (platform_link only) | `test_platform_link.py` | **E+T** (platform_link); legacy mode deletes, see ST-05 |
| DP-05 | Permissions (`principal`, `group`, `membership`, `permission`) are proposed, not built (v1:79-81) | refused by the runner (`KB:scripts/brain_runner/streams.py:36-37,289`; `config.py:279-281`) | `KB:scripts/brain_runner/tests/test_streams.py` | **NA** (SS-582 needs a protocol change) |
| DP-06 | `inventory` and `sample` are not built (v1:83-87) | — | — | **NA** |
| DP-07 | Outbound (Jira) is not built (v1:89-91) | superseded by the v2 write path (Jira actions) | — | **NA** |

## 2. The local API, protocol 1.2 (`la:`)

| ID | Rule | Enforcement | Test | Verdict |
|---|---|---|---|---|
| LA-01 | Every call: `Authorization: Bearer` plus `X-Component`, else 401 (la:19-26) | `KC:KnovasLink/src/knovas_link/local_api.py:83-86` | `KC:KnovasLink/tests/test_local_api.py:22` | **E+T** |
| LA-02 | `register`: `component` equals the name; unknown job kinds are ignored; a later registration replaces; 503 with no configuration (la:60-77) | `local_api.py:90-112`; `link.py:365-367` | `test_local_api.py:65,74,82` | **E+T** |
| LA-03 | Commands fan out once to the named component; done is relayed with the name; 404 `unknown_command` (la:98-104) | `local_api.py:132-149`; `link.py:100-121` | `test_local_api.py:104,123,139` | **E+T** |
| LA-04 | Config and package are limited to the component's own sources and workspaces (la:110-112) | `link.py:396-416` | `test_local_api.py:160,172,183` | **E+T** |
| LA-05 | Ping never calls Knovas (la:114-123) | `local_api.py:159-161` | `test_local_api.py:192` | **E+T** |
| LA-06 | `GET /jobs?wait=` takes whole seconds 0–25 (more counts as 25), else 400 (la:127) | `local_api.py:165-177` | `test_local_api.py:202,214` | **E+T** |
| LA-07 | A component gets only jobs of kinds it named, for sources assigned to it (la:130) | `KC:KnovasLink/src/knovas_link/jobs.py:263-267` | `KC:KnovasLink/tests/test_work_channel.py:219`; `test_local_api.py:220` | **E+T** |
| LA-08 | Link validates every job; an invalid one is never handed out, `failed invalid_job` (la:131-133) | `jobs.py:241-243,281-286` | `test_work_channel.py:250` | **E+T** |
| LA-09 | Components never see the lease (la:134-135) | `jobs.py:222` | `test_work_channel.py:54` | **E+T** |
| LA-10 | Past the deadline Link answers `unreachable`; a late part or result is 409 `lease_lost` (la:136-137) | `jobs.py:496-510,540-544` | `test_work_channel.py:193,205,212` | **E+T** |
| LA-11 | While waiting for the certificate Link claims nothing (la:138) | `jobs.py:180-183` | `test_work_channel.py:303` | **E+T** |
| LA-12 | No component for the kind: `unsupported`; switch off: `fetch_disabled` or `write_disabled` (la:139-140) | `jobs.py:253-267` | `test_work_channel.py:219,233,242` | **E+T** |
| LA-13 | Local probes; an unmeasured round trip is shown unknown, never fine (la:141-145) | `jobs.py:496-509,696-706` | `test_work_channel.py:317,347` | **E+T** |
| LA-14 | Parts: at most 4 MiB, `n` from 0 by one, no empty part; a refused part ends the fetch; a part Knovas did not take ends it with `part_not_accepted` (la:149-160) | `jobs.py:51,546-623` | `test_work_channel.py:63,82,102,116,127,141,156` | **E+T** |
| LA-15 | A result is validated (400 `bad_field`) and passed on unchanged with the lease (la:164-167) | `jobs.py:625-687` | `test_work_channel.py:168,271,284` | **E+T** |
| LA-16 | Brain relay: exact allow-list plus `secured/graph/**`; else 403 `not_relayed`; 503 `waiting_for_certificate`; never retried (la:214-227) | `KC:KnovasLink/src/knovas_link/relay.py:20-48`; `local_api.py:195-217` | `KC:KnovasLink/tests/test_relay.py:24,35,80,97,104,111,121` | **E+T** |
| LA-17 | **Known limit F6:** below `secured/graph/` every method passes, deletes included. "Before the first external customer, the allow-list is narrowed to the routes and methods the runner uses" (v1:127) | not narrowed: `relay.py:25`, `local_api.py:40` | `test_relay.py:56` `test_every_method_is_relayed` asserts the wide relay | **NE** |
| LA-18 | Reports through Link: codes and counts only (la:231-233) | `relay.py:125-149` | `test_relay.py:141,177,198,210` | **E+T** |
| LA-19 | Errors and logs never hold a name or a value (la:50-51) | `local_api.py:266-293` (logged by type only) | `test_relay.py:69`; `KC:KnovasLink/tests/test_enrolment.py:230` | **E+T** |
| LA-20 | The lock pins every contract file; a stale contract stops Link (la:307-309) | `KC:KnovasLink/src/knovas_link/contracts.py` | `test_enrolment.py:142`; `KC:KnovasLink/tests/test_contracts.py:340` | **E+T** |
| LA-21 | A component in link mode never calls the Platform or the Brain itself; everything goes through Link (la:14-15) | sync component: `KC:…/sync_component/__main__.py:121`; Jira: `KC:KnovasConnectorJira/src/knovas_connector_jira/site.py:11`. **The Knovas Connector has no link mode** (grep for `knovas_link` and `/local/v1` in `KC:KnovasConnector`: empty). | `KC:…/tests/test_sync_component.py:334` | **NE** (Connector) |

## 3. Streams, jobs, probes, write proposals, originals (protocol 1.1, `v1:`)

| ID | Rule | Enforcement | Test | Verdict |
|---|---|---|---|---|
| ST-01 | A stream message is `{type, source, external_id, version, content_hash, payload, state}` per the schema (v1:169-176) | `KC:KnovasLink/contracts/stream-message.schema.json`; the runner validates against its pinned copy (`KB:scripts/brain_runner/streams.py:60-62,267-275`) | `KC:KnovasLink/tests/test_contracts.py:245-285` (vectors); `KB:scripts/brain_runner/tests/test_streams.py` | **E+T** |
| ST-02 | Reserved types are refused by every 1.1 receiver with `unsupported_type` (v1:177-181) | brain-runner, the only receiver: `streams.py:62,289`. It takes `record` now (v2); the permission types are refused. | `test_streams.py`, `test_record_streams.py` (passed today) | **E+T** |
| ST-03 | Identity before content: `source` is the workspace source key, `external_id` the item's key, `version` the source's own; nothing is matched by name (v1:183) | `KB:scripts/brain_runner/streams.py:6-16,182-189` | `test_streams.py` | **E+T** |
| ST-04 | At least once and idempotent: a replayed stream changes nothing (v1:184) | `streams.py:41-42`; v2 property `replay_changes_nothing` (`KC:KnovasConnectorKit/src/knovas_connector_kit/conformance/suite.py:152`) | `test_streams.py`; kit `tests/test_conformance_suite.py` | **E+T** |
| ST-05 | **Deletions reach the Brain only through a `tombstone` or absence from a completed `generation`, under the deletion guard, never through an incremental run** (v1:185; v2:119-121) | **Enforced in brain-runner:** an incomplete listing emits no tombstone or generation (`streams.py:26-36,306`); guard `KB:scripts/brain_runner/guard.py:7-23`, `diff.py:736,766`, `documents.py:382-392`. **Bypassed:** server `DELETE` routes with no guard (`KB:…/api/secure_api.py:2868,3137`; `KB:knovas-software/app/src/api/graph_api.py:1581,1718,2363`); Link relays them (`KC:KnovasLink/src/knovas_link/relay.py:20-25`); the Connector's legacy `delete_on_remove` (on by default; `KC:KnovasConnector/src/sync/knovas_uploader.py:523-530`, `sync_executor.py:991-993,1026`); the old loader and cleanup (`KB:scripts/client_zero/load_ontology.py:702,1020,1046,1068,1074`; `p8_cleanup.py:61`) | runner: `KB:scripts/brain_runner/tests/test_claims_and_guard.py:118,127,146`, `test_runner.py:187` (passed today). **No test anywhere asserts that a path outside the runner is refused.** | **NE** |
| ST-06 | A `state` is acknowledged only after the receiver holds everything before it (v1:186) | sync component: the next plan's `previous` is the summary of the last plan whose apply succeeded (`KC:…/sync_component/runner_cli.py:5-8`) | `KC:…/tests/test_sync_component.py:359` | **E+T** |
| ST-07 | What reaches the Brain goes through the upload routes, through Link, with a source reference `{source, external_id, version}`; `PUT /secured/documents/source_ref` adds one later (v1:187) | runner and sync component: yes (`KB:…/api/secure_api.py:5266`). **The Knovas Connector sends no source reference and does not go through Link** (`KC:KnovasConnector/src/sync/knovas_uploader.py:357-370`), so its originals cannot be fetched. | `KB:knovas-software/app/tests/test_document_source_ref.py` | **NE** (Connector) |
| JB-01 | A job is `{id, kind, source, target_serial, deadline, actor, args}` (v1:193) | schemas; `KB:…/services/connector_jobs.py:341-347` | `KC:KnovasLink/tests/test_contracts.py:222,260-293` | **E+T** |
| JB-02 | The actor is recorded and never trusted for a decision (v1:202) | recorded (`connector_jobs.py`); not read for authorisation | none (a negative) | **NA** (until SS-295 identity) |
| JB-03 | A claim takes one job of the caller's tenant for its sources, or a probe addressed to its serial; lease 60 s; an opaque 16–128 character lease (v1:204) | `KB:…/services/connector_jobs.py:132,736-755`; Link checks the lease's shape (`KC:KnovasLink/src/knovas_link/jobs.py:218-221`) | `KB:knovas-software/app/tests/test_connector_jobs.py`; `KC:KnovasLink/tests/test_work_channel.py:261` | **E+T** |
| JB-04 | `X-Knovas-Lease` on every part and result; missing, stale or another claim's is 409 `lease_lost` (v1:207) | `connector_jobs.py:33-37,145`; `KC:KnovasLink/src/knovas_link/jobs.py:663-682` | `test_connector_jobs.py`; `test_work_channel.py:271,284` | **E+T** |
| JB-05 | Long-polls are capped per pod and per tenant: 429 with `Retry-After` (v1:208) | `connector_jobs.py:31,235`; Link backs off (`jobs.py:188-193`) | `test_connector_jobs.py`; `test_work_channel.py:378` | **E+T** |
| JB-06 | A job of one tenant is never visible to, claimable by or answerable from another (v1:210) | `connector_jobs.py:37,760` (another tenant's job is `unknown_job` 404) | `test_connector_jobs.py` | **E+T** |
| JB-07 | Results: the six statuses; codes from `codes.json` or a component's own of `^[a-z][a-z0-9_.]{1,60}$`; never text (v1:211, la:204-205) | `KC:KnovasLink/contracts/job-result.schema.json`; Link `jobs.py:633-636`; `KB:…/services/connector_jobs.py:514,583-605` | `KC:KnovasLink/tests/test_contracts.py:204,316,327` (vector `result.code-free-text`) | **E+T** |
| JB-08 | A late answer is discarded and counted (v1:206) | `connector_jobs.py:95,687` | `test_connector_jobs.py` | **E+T** |
| PR-01 | Probe: `POST /secured/connector/probe` queues a probe to the caller's serial; Link answers; every 5 minutes (v1:219-221) | `KB:…/api/secure_api.py:5092`; `KC:KnovasLink/src/knovas_link/jobs.py:708-737`; `settings.py` (`LINK_PROBE_SECONDS` 300) | `test_work_channel.py:317,337,356` | **E+T** |
| PR-02 | A site is online only with a probe in the last 10 minutes; an unmeasured state is unknown, never fine (v1:222) | `KC:…/builder/components.py:47,167,174-190` | `KC:KnovasLink/tests/test_link_heartbeat.py:109` (Link); `KC:…/tests/test_builder_sites.py`, `test_builder_components.py` (Builder) | **E+T** |
| WP-01 | One mode, `proposal`; `standing` is refused `mode_unsupported` (or `invalid_job` through Link) (v1:228) | job schema (`mode` enum); `KC:…/sync_component/github_jobs.py:371-372`; `KB:…/services/connector_jobs.py:453` | vector `KC:KnovasLink/contracts/vectors/invalid/job.write-mode-standing.json` via `test_contracts.py:252` | **E+T** |
| WP-02 | A proposal needs a visible node owned by a mapping, still in the source, with `source_version`; a change rejected before is `previously_rejected` (v1:230) | `KB:…/services/source_write_proposals.py:27-34,546-548` | `KB:knovas-software/app/tests/test_source_write_proposals.py` | **E+T** |
| WP-03 | Accepting queues a `write` with `expected_version` = the node's `source_version`; the accepter is the actor, the proposer `on_behalf_of` (v1:231-232) | `source_write_proposals.py:31,35-37` | `test_source_write_proposals.py` | **E+T** |
| WP-04 | The component compiles only facts the mapping's `writes` names, as plain fields; refusals `not_writable`, `computed_attribute`, `value_not_allowed`, `not_invertible`, `unknown_mapping` (v1:233) | `KB:scripts/brain_runner/inverse.py:21-25,43` (brain-runner compile); `KC:…/sync_component/github_jobs.py:350-362` (`runner.bad_change`) | `KC:…/tests/test_github_write.py` `TestNothingIsWritten::test_what_the_inverse_mapping_refuses_is_the_jobs_refusal` (passed today) | **E+T** |
| WP-05 | Item not at `expected_version`: `conflict`, nothing written (v1:234) | `github_jobs.py:389` | `test_github_write.py` `TestNothingIsWritten::test_a_file_changed_since_the_version_is_a_conflict` | **E+T** |
| WP-06 | The Brain's value changes only with the next sync (v1:235, GI-CONN-04) | structural: the write path has no Brain write | none | **E** |
| WP-07 | Accepting is not bound to a verified person; the route authenticates the tenant certificate (v1:237) | — | — | **NA** (known limit; SS-295) |
| FO-01 | `GET /secured/documents/original`: the pointer is checked as search filters it; not visible is 404 (v1:245) | `KB:…/api/secure_api.py:5318-5327`; `KB:…/services/fetch_relay.py:99` | `KB:knovas-software/app/tests/test_fetch_relay.py` | **E+T** |
| FO-02 | No source reference: 409 `no_source_ref` (v1:246) | `fetch_relay.py:93` | `test_fetch_relay.py` | **E+T** |
| FO-03 | Deadline 20 s, `max_bytes` 25 MiB, parts of at most 4 MiB from 0 in order (v1:247) | `fetch_relay.py:82-83`; `KC:KnovasLink/src/knovas_link/jobs.py:51` | `test_fetch_relay.py`; `KC:KnovasLink/tests/test_work_channel.py:63,116` | **E+T** |
| FO-04 | Frames streamed; SHA-256 recomputed; a gap or mismatch ends with an error; nothing written to disk, database or cache; no retry (v1:248) | `fetch_relay.py:24-35,96,251` | `test_fetch_relay.py` | **E+T** |
| FO-05 | 503 `source_unreachable` or 403 `source_refused`, never the indexed text instead (v1:249) | `fetch_relay.py:12,17-18,94-95` | `test_fetch_relay.py` | **E+T** |
| FO-06 | A different fetched version marks the reference stale (v1:250) | `fetch_relay.py:25,33` | `test_fetch_relay.py` | **E+T** |
| FO-07 | One audit event per fetch (v1:251) | `fetch_relay.py:46-48` | `test_fetch_relay.py` (12 audit assertions) | **E+T** |
| FO-08 | Fetching is off until a person switches it on: `fetch_disabled` (v1:252) | `KC:KnovasLink/src/knovas_link/jobs.py:257-259` | `test_work_channel.py:233` | **E+T** |
| FO-09 | GitHub: the bytes' git blob hash equals `version`, else `version_mismatch`; larger than `max_bytes` is `too_large` (v1:254, la:194,198) | `KC:…/sync_component/github_jobs.py:160-173` | `KC:…/tests/test_github_jobs.py` | **E+T** |

## 4. Protocol 2.0 (`v2:`)

| ID | Rule | Enforcement | Test | Verdict |
|---|---|---|---|---|
| V2-01 | The protocol names no source kind: no message or rule depends on `source_kind`'s value (v2:44) | contract test over schemas, codes, examples and text | `KC:KnovasLink/tests/test_contracts_v2.py:382,388` | **E+T** |
| V2-02 | A declaration holds `protocol`, `connector`, `source_kind`, `streams`, `fetch`, `actions` (params, preview, staging, grouping, inverse) and `conformance` (v2:84-95) | `KC:KnovasLink/contracts/v2/declaration.schema.json`; Link validates at `register` (`KC:KnovasLink/src/knovas_link/local_api.py:103-106`) | `test_contracts_v2.py:177,184` (examples); `KC:KnovasLink/tests/test_protocol_v2.py:159` | **E+T** |
| V2-03 | The Builder offers reading only with the three read properties, writing only declared actions with the write properties, `native_review` only with `review_outcome`; `preview: none` is never approvable (v2:97-101) | `KC:…/builder/protocol_v2.py:44-48,104` (behind `BUILDER_PROTOCOL_V2`) | `KC:…/tests/test_builder_protocol_v2.py` (passed today on Linux line endings) | **E+T** |
| V2-04 | The conformance report is the connector's own statement; a Knovas-signed report comes later (v2:440-441, 460-461; D-9) | not signed: the Builder trusts `conformance.passed` as declared | — | **NA** (kept open on purpose, D-9) |
| V2-05 | Link sends one site declaration at start and whenever a connector registers **or leaves**; the Brain binds it to the certificate (v2:103-110) | at start and on register: `KC:KnovasLink/src/knovas_link/protocol_v2.py:81-104,131-134`, `local_api.py:109-110`; Brain `KB:…/api/secure_api.py:5130-5154`, `KB:…/services/connector_sites.py:7-18`. **"Or leaves": no path re-declares when a connector goes away** (no unregister in `KC:KnovasLink/src/knovas_link/registry.py`) | `test_protocol_v2.py:159,168`; `KB:knovas-software/app/tests/test_connector_sites.py` | **NE** (leave) |
| V2-06 | The Brain queues v2 jobs only for a certificate whose site declared protocol 2 (v2:108-109, 413-415) | `KB:…/services/connector_sites.py:17-18,70,78`; `KB:…/services/connector_jobs.py:54-64,1695` | `KB:…/tests/test_connector_sites.py`, `test_connector_jobs_act.py` | **E+T** |
| V2-07 | A Link with v2 off refuses a v2 job with `protocol_unsupported` and ignores declarations | `KC:KnovasLink/src/knovas_link/jobs.py:277-280`; `local_api.py:103` | `test_protocol_v2.py:128` | **E+T** |
| V2-08 | `record` = `{record_kind, fields, computed?, links?}`; a connector sends records only to a v2 receiver (v2:124-129) | `KC:KnovasLink/contracts/v2/stream-message.schema.json`; Jira validates its dump (`KC:KnovasConnectorJira/src/knovas_connector_jira/dump.py:99`); the runner refuses a bad record (`KB:scripts/brain_runner/streams.py:20-22,293-295`) | `KB:scripts/brain_runner/tests/test_record_streams.py`; `KC:KnovasConnectorJira/tests/test_dump.py` | **E+T** |
| V2-09 | **Reading as in 1.1:** a stream; runs and dry runs as heartbeat commands; uploads and graph writes through Link's relay; deletions only under the guard (v2:119-121) | **Jira reads around Link:** the site component refuses `run` (`KC:KnovasConnectorJira/src/knovas_connector_jira/site.py:21-23,239`); the stream goes to a file that a person plans and applies on server01 with the tenant certificate (`KB:scripts/client_zero/brain_mapping_local.sh:17-18,151-218`). Link has no stream or read job (`KC:KnovasLink/src/knovas_link/registry.py:27-29`); the Brain has no record route (plan I4 `POST /secured/sync/records`: not found). | none | **NE** |
| V2-10 | A v2 job carries `protocol: "2.x"`; write-path jobs name `target_serial`; an `act` carries one permit per step beside `args` (v2:136-142) | `KC:KnovasLink/contracts/v2/job.schema.json`; `KB:…/services/connector_jobs.py:54-64,123-125` | `test_contracts_v2.py:177,184` (`job.preview-missing-target-serial`, `job.missing-protocol`) | **E+T** |
| V2-11 | A missing, extra or unreadable permit is answered by Link's permit checks, never as `invalid_job` (v2:140-142) | `KC:KnovasLink/src/knovas_link/write_path.py:189-208` | `test_protocol_v2.py:263,614,625` | **E+T** |
| V2-12 | Only the named site claims a write-path job (the Brain), **and** Link refuses a permit naming another serial (v2:156-159) | `KB:…/services/connector_jobs.py:745-747`; `write_path.py:200-201` | `KB:…/tests/test_connector_jobs_act.py`; `test_protocol_v2.py:263` | **E+T** |
| V2-13 | Deadlines: `preview` 30 s; `act` the permit's expiry; `reread`, `review_outcome`, `unstage` 60 s (v2:160-161) | preview: `KB:…/services/write_back_dispatcher.py:75,210` | `KB:knovas-software/app/tests/test_write_back_dispatcher.py` | **E+T** (preview); others not checked |
| V2-14 | While a change is staged, the Brain asks for its review outcome every 5 minutes (v2:162-163, 209-215) | **not wired:** `KB:…/services/write_back_dispatcher.py:56` ("`review_outcome`, WB-33, not wired"). The GitHub v2 connector declares `native_review` (`KC:…/sync_component/github_v2.py`). | none | **NE** |
| V2-15 | Every change, value, difference and conflict names its `step` (D-14) (v2:164-169) | kit `KC:KnovasConnectorKit/src/knovas_connector_kit/connector.py:28-41,107`; result schema `$defs/step`; Link refuses a result that breaks it (`KC:KnovasLink/src/knovas_link/jobs.py:633`) | `test_contracts_v2.py:262,285,292,300`; `test_protocol_v2.py:308`; property `preview_equals_receipt` | **E+T** |
| V2-16 | `direct`: write only while each target is at `expected_version`, else `conflict` and nothing written (v2:206-208) | GitHub `github_v2.py:276,291,312`; Jira `KC:KnovasConnectorJira/src/knovas_connector_jira/connector.py:310,364` | property `preview_equals_receipt` (GitHub, Jira, example passed today) | **E+T** |
| V2-17 | `native_review`: `staged` with a `stage_ref`; accept applies, decline refuses (v2:209-215) | connector side `github_v2.py:338-343`; kit example | property `review_outcome` (example passed today) | **E+T** connector; Brain side, see V2-14 |
| V2-18 | A permit is a JWS compact token in one canonical encoding; an unreadable token is `permit_bad_signature`, never an error (v2:236-242) | `KC:KnovasLink/src/knovas_link/permits.py:97-108,177-224`; `KC:KnovasConnectorKit/src/knovas_connector_kit/permits.py` | `test_protocol_v2.py:614,625,635`; `KC:KnovasConnectorKit/tests/test_contracts_and_permits.py` | **E+T** |
| V2-19 | Ed25519; `kid` = `wbp-` + 16 hex digits of the key's SHA-256 (v2:243-244) | `permits.py:147,221` | `test_contracts_v2.py:220,236` | **E+T** |
| V2-20 | Keys are pinned only from `GET /secured/connector/v2/permit-keys` over mTLS, at most 4, never from the Platform or a job (v2:245-249) | `KC:KnovasLink/src/knovas_link/protocol_v2.py:106-129`; `permits.py:127-158`; `KB:…/api/secure_api.py:5169-5192` | `test_protocol_v2.py:191` (passes on Linux) | **E+T** |
| V2-21 | The Brain never signs a second permit for an action; a lost result is recovered with `reread` (v2:337-339) | `KB:…/services/write_back_permits.py:23-24,156`; `write_back_dispatcher.py:281` | `KB:knovas-software/app/tests/test_write_back_dispatcher.py` | **E+T** |
| V2-22 | `site` is a decimal serial, 1–49 digits; `params_hash` is over canonical `{target, params}`; the permit carries no value (v2:251-260) | `KC:KnovasLink/contracts/v2/permit.schema.json`; `permits.py:93-94` | `test_contracts_v2.py:144,159` (`permit.site-not-a-serial`, `permit.carries-a-value`) | **E+T** |
| V2-23 | Lifetime: `exp` after `iat`, at most 10800 s later; a first delivery after `exp` is `permit_expired`; a repeat of a written permit still gets its first result (v2:263-265) | `permits.py:32,215-216`; `write_path.py:217-218` | `test_protocol_v2.py:233,292,482` | **E+T** |
| V2-24 | Link step 1, every step has its permit: else `permit_missing` (v2:276) | `write_path.py:191-192` | `test_protocol_v2.py:263`; `KC:KnovasConnectorKit/tests/test_gate.py` | **E+T** |
| V2-25 | Step 2: readable, canonical, EdDSA, a pinned key whose window holds `iat`, a valid signature: else `permit_bad_signature` (v2:277-281) | `write_path.py:194-198`; `permits.py:164-168` | `test_protocol_v2.py:233,614` | **E+T** |
| V2-26 | Step 3: `site` is Link's own serial: else `permit_wrong_site` (v2:282) | `write_path.py:200-201` | `test_protocol_v2.py:263`; `test_gate.py` | **E+T** |
| V2-27 | Step 4: tenant, source, action, `params_hash`, `expected_version` and `staging` equal the job's: else `permit_mismatch` (v2:283-284) | `write_path.py:202-208` | `test_protocol_v2.py:263`; `test_gate.py` | **E+T** |
| V2-28 | Step 5: a used permit is answered with its stored first result, `repeat: true`, `permit_used`; a delivery still with the connector waits for it (v2:285-290) | `write_path.py:210-214`; `jobs.py:355-389` | `test_protocol_v2.py:215,465,504,527,540` (`:215` passes on Linux) | **E+T** |
| V2-29 | Step 6: now is before `exp`: else `permit_expired` (v2:291) | `write_path.py:217-218` | `test_protocol_v2.py:482` | **E+T** |
| V2-30 | Step 7: deadline, `read_write`, the action and its staging declared, grouping: else `unreachable`, `write_disabled`, `action_not_declared`, `staging_unsupported`, `grouping_unsupported` (v2:292-296) | `jobs.py:305-330,393-400` | `test_protocol_v2.py:337,361,373,419`. **No test anywhere asserts `staging_unsupported`** (grep over the Link, kit, Jira and Platform tests and the suite: empty). | **E+T** (except `staging_unsupported`: E) |
| V2-31 | Step 8: reserve each permit and write the write-log line before the job goes on: else `failed write_log_unavailable` (v2:297-298) | `jobs.py:401-414`; `write_path.py:93-103,133-149` | `test_protocol_v2.py:382` | **E+T** |
| V2-32 | The result is stored under every permit id before it is passed on, never replaced; every delivery is a write-log line, refusals included (v2:300-304) | `jobs.py:648-660`; `write_path.py:151-167` | `test_protocol_v2.py:263,569` (`:569` passes on Linux) | **E+T** |
| V2-33 | The site's write log and the Brain's record reconcile by permit id (SPEC-write-back-26, WB-25) (v2:303-304) | no reconciler found (`KB:knovas-software/app/src` grep for reconcile: doc-anchor and brain-store jobs only) | none | **NE** |
| V2-34 | Link keeps the used-permit store at least 30 days after expiry (v2:329-332) | `write_path.py:47,141` (`KEEP_SECONDS`) | none asserts the retention (grep for `KEEP_SECONDS`/`keep_until` in `KC:KnovasLink/tests`: empty) | **E** |
| V2-35 | The connector marks each write with its permit id, so a lost result is found again, not written twice (v2:333-336) | GitHub: commit trailers `Knovas-Permit:` (`github_v2.py:20,60-61,171`); Jira (suite) | property `exactly_once` (GitHub, Jira, example passed today) | **E+T** |
| V2-36 | Receipts: one per step, `values` before and after, `differences`, `side_effects`; a non-empty difference holds the action until a person clears it (v2:309-323) | `KC:KnovasLink/contracts/v2/result.schema.json` `$defs/receipt`; Brain `KB:knovas-software/app/tests/test_connector_v2_results.py` | `test_contracts_v2.py:248`; property `preview_equals_receipt` | **E+T** |
| V2-37 | Conflict: `status: conflict`, `code: version_changed`, `conflict {step, target, expected_version, found_version, changes?}` at the lowest step (v2:343-349) | result schema; connectors (V2-16) | `test_contracts_v2.py:177` (`result.conflict`); property `preview_equals_receipt` | **E+T** |
| V2-38 | Grouping: `atomic`/`item`/`none`; Link and the connector refuse otherwise; the preview names the shared grouping (v2:359-373) | `KC:KnovasLink/src/knovas_link/jobs.py:73-80,328-329`; `KC:KnovasConnectorKit/src/knovas_connector_kit/connector.py:44-57,161-162`; Jira `connector.py:377,392` | `test_protocol_v2.py:419`; kit mutants; `KC:KnovasConnectorJira/tests/test_actions.py` | **E+T** |
| V2-39 | A v2 result's values travel only on the work channel; never in Link's logs, reports, heartbeat or a metric label (v2:402-406) | Link logs ids, kinds, codes and counts only (`jobs.py:26`) | `test_protocol_v2.py:405` | **E+T** |
| V2-40 | Caps: 500 changes, 64 KiB per value, 256 KiB per diff; larger is refused by the connector (v2:406-407) | schema: changes `maxItems: 500`, value `maxLength: 65536` (`result.schema.json:33-34,65`); no 256 KiB diff cap found | none for the diff cap | **NE** (diff cap) |
| V2-41 | 1.x unchanged beside v2; a v2 Link validates 1.x jobs against 1.x schemas (v2:410-416) | `jobs.py:238-243` | `test_protocol_v2.py:396` | **E+T** |
| V2-42 | A source moves to v2 only after its open 1.x proposals are settled (v2:418-420) | `KB:…/services/source_write_proposals.py:50` (`source_written_by_change_sets`) | `KB:knovas-software/app/tests/test_source_write_proposals.py` | **E+T** |
| V2-43 | Within v2 only additions; closed objects (declaration, permit, args, result) refuse unknown fields (v2:421-422) | schemas `additionalProperties: false` | `test_contracts_v2.py:184` (`result.unknown-field`) | **E+T** |
| V2-44 | The conformance suite: seven properties against a fake source; each property fails its own mutant (v2:430-438) | `KC:KnovasConnectorKit/src/knovas_connector_kit/conformance/suite.py:35-37,152-567` | `KC:KnovasConnectorKit/tests/test_conformance_suite.py`; `KC:KnovasConnectorJira/tests/test_conformance.py`; `KC:…/tests/test_sync_component_github_v2.py` (all passed today) | **E+T** |
| V2-45 | The lock reads `2.0.0-draft` until the first release; "nothing of v2 is released yet" (v2:14-16) | lock unchanged. **The Brain side runs v2 in prod for af639810 since 2026-11-13** (`KB:infra/kubernetes/components/app-config-prod/app-config-prod.yaml:139-140`). | — | **NE** (the status and the deployment disagree) |
| V2-46 | The permission stream types stay reserved; SharePoint permissions are not in v2.0 (v2:87-88, 524) | refused by the runner (`KB:scripts/brain_runner/streams.py:36-37`) | `test_streams.py` | **NA** (SS-582 needs v2.1) |

## 5. Runs today

| Suite | Command | Result |
|---|---|---|
| Connector kit | `cd KnovasConnectorKit && py -3 -m pytest -q tests` | 65 passed (Windows and Linux) |
| Conformance CLI, example | `PYTHONPATH=src py -3 -m knovas_connector_kit.conformance knovas_connector_kit.example:make_harness` | 7/7, exit 0, report `9d519e02…` |
| Conformance CLI, Jira | `cd KnovasConnectorJira && PYTHONPATH="src;tests;../KnovasConnectorKit/src" py -3 -m knovas_connector_kit.conformance harness:make_harness` | 6/6 applicable (`review_outcome` n/a), exit 0, report `f1fbcecc…` |
| Knovas Link | `cd KnovasLink && PYTHONPATH=src python -m pytest -q tests` | Linux: 937 passed. Windows: 8 failed and 2 errors, all POSIX mode or path artifacts. |
| Jira connector | `cd KnovasConnectorJira && python -m pytest -q tests` | Linux: 152 passed. Windows: 1 failed (0600 mode). |
| sync component + Builder v2 (14 files) | `cd KnovasPlatform/components/docbridge_integration && PYTHONPATH=<brain-runner 0.3.3 @ KB c618c7bed> py -3 -m pytest -o addopts="" -q tests/test_sync_component*.py tests/test_github_*.py tests/test_builder_protocol_v2.py tests/test_ci_runner_and_link_jobs.py tests/test_connector_kinds.py tests/test_builder_autosync.py tests/test_brain_sync.py` | 379 passed, 1 skipped, 1 failed (CRLF checkout of the Platform's schema copy; Windows only) |
| brain-runner guard and streams (develop) | `py -3 -m pytest -q brain_runner/tests/test_claims_and_guard.py …/test_streams.py …/test_record_streams.py …/test_retire_entries.py …/test_documents_apply.py` | 118 passed, 1 skipped |

Not run: the Knovas Connector suite and the secure-api connector tests, which need PostgreSQL and Redis. Their verdicts above rest on reading the code and finding the tests, not on a run today.

## 6. Summary

126 rules:

| Verdict | Count | Rules |
|---|---|---|
| E+T | 101 | — |
| E | 4 | P1-03, P1-10, WP-06, V2-34 (and `staging_unsupported` within V2-30) |
| NE | 14 | P1-01, P1-05, P1-14, DP-01, LA-17, LA-21, ST-05, ST-07, V2-05, V2-09, V2-14, V2-33, V2-40, V2-45 |
| NA | 7 | DP-05, DP-06, DP-07, JB-02, WP-07, V2-04, V2-46 |

The protocol is enforced and tested wherever its path runs through Link, the kit, the runner and secure-api's connector routes. Every **NE** lies at an edge: a path that goes around Link or the runner (the Knovas Connector, Jira's read path, the in-Platform readers, server-side deletes), or a later step that was planned but not built (relay narrowing, review polling, write-log reconciliation).

## 7. Proposed child issues

These are proposals; no Jira issue was created. An existing issue is named where one already covers the work.

| # | Proposed title | From | Why (one line) |
|---|---|---|---|
| N1 | Deletions reach the Brain only through the guard: refuse unguarded deletes on the server and at Link | ST-05, LA-17 | Today the guard lives only in brain-runner. Five server DELETE routes, Link's relay, the Connector's legacy mode and two old scripts delete without it. |
| N2 | Narrow Link's relay to the runner's routes and methods (F6) | LA-17 | Every method below `secured/graph/` passes, so a compromised component on a site has full graph write and delete authority. |
| N3 | One way into the Brain for record streams: a stream job through Link, or plan I4's `POST /secured/sync/records` | V2-09 | Jira's records reach the Brain only when a person runs a script on server01 with the tenant certificate. A new v2 backend would take the same detour. |
| N4 | The Knovas Connector behind Link, with source references (existing: SS-580, I3) | P1-01, LA-21, ST-07, ST-05 | File share and Microsoft 365 are outside the protocol. They take inbound `/sync`, use their own data plane, send no source reference and delete unguarded in legacy mode. |
| N5 | Wire `review_outcome` polling in the Brain (WB-33), or stop declaring `native_review` | V2-14 | GitHub v2 declares `native_review`, but the Brain never asks how a staged change ended, so such a change set cannot complete. |
| N6 | Reconcile Link's write log with the Brain's record by permit id (WB-25) | V2-33 | The protocol promises the reconciliation; no code does it. |
| N7 | Decide v2's release status: lock `2.0.0` or switch the Brain side off | V2-45 | The lock says draft and "nothing released", while prod has `WRITE_BACK_V2_ENABLED` on for af639810. |
| N8 | In-Platform readers (GitHub "Sync von GitHub", HTTP/JSON adapter): keep on purpose or move behind Link | DP-01 | They carry customer data through the Platform, against DEC-0056's data path. |
| N9 | Re-declare the site when a connector leaves | V2-05 | The Brain keeps offering a connector that went away until Link restarts. |
| N10 | Tests for four enforced but untested rules: a draft never reaches a component; an unknown command is refused; 30-day permit retention; `staging_unsupported` | P1-03, P1-10, V2-34, V2-30 | Each is enforced, but a regression would pass CI. |
| N11 | The 256 KiB diff cap in the result schema, or remove it from the text | V2-40 | The text names a cap that the contract does not hold. |
| N12 | The Connector refuses an empty or unmounted share before it scans | P1-14 | A share that did not mount looks like one whose documents were all deleted. In legacy mode that deletes. |
| N13 | Run the brain_mapping and Jira path tests on PRs and develop | inventory §3.2 | They run only on master pushes (`KB:.github/workflows/client-zero.yml:15-17,60-61`). |
| — | Permission stream types, protocol v2.1 (existing: SS-582, I5) | DP-05, V2-46 | SharePoint permissions need them. |
| — | HTTP 500 graph writes (existing: SS-960) | inventory §2.4 | 500 is never retried; failed run-record facts are never planned again. |
| — | Documentation drift (fold into SS-959's CI check) | inventory §8 | Eleven places where a document and the code disagree, including `local-api.md:3` "not built". |
