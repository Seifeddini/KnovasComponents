# Draft pull request: `cl/jolly-fermat-ro3vkk` into `main` (2026-11-10)

Prepared for FEAT-2026-11-10-write-back (J-4, C-5) and BET-2026-11-write-back (WB-07, WB-12). Not opened: the
founder opens it, with the body below. The release steps after it are in "Release" at the end of this file.

---

## Title

Brain v1, the Brain Builder, Knovas Link and connector protocol 2.0 (write back to the source, Jira connector)

## Body

**What this brings to `main`** (229 commits on the branch: 226, then the J-4/C-5 commit `0b8d092`, the ADF commit
`74d0ddf`, and the fix of the independent verification KC-3; full list at the end).
Items are named per commit.

1. **Ansichten and the Cortex star view** (Oct 26–28; views steps 1–8, KC-1..KC-3): the ego graph, process and spine
   views from the tenant's definitions, saved views, change requests as one pull request, documentation pages.
2. **Platform settings and Sync von GitHub** (KC-0.1, KC-0.2).
3. **The Brain Builder** (B0–B14, BB-12): workspaces, ontology canvas, industry packs, review and publish, processes,
   sources and mappings, views, operation, team, sync components, the access window.
4. **Connector protocol 1.x and the sync component** (KB-3.1, KN-1, KC-7a): a deployable sync component, file and
   GitHub connectors.
5. **Knovas Link and the Builder's sites** (MVP-1..MVP-32, KC-7a, KC-7b, DEC-0056): Link's local API, enrolment,
   certificate request and install, relays, the work channel; the sync component in link mode with GitHub fetch and
   write; sites, install package, credentials as env lines, switches, «Freigaben», «Verbindung».
6. **Brain v1** (FEAT-2026-11-05-knovas-brain-v1: V1-G4, V1-G6, V1-G10, V1-G11, BB-07 B1–B7, S-4, KG-1, KG-2): no
   tenant defaults in product code, kustomize in the image, the release workflow, the deployment guide, brain-runner
   0.3.2 pinned, «bei neuen Commits» and automatic apply. **Released once, from `7c557e6`.**
7. **Software map cards** (FEAT-2026-11-06-knovas-software-map): `docs/components/`.
8. **Connector protocol 2.0, write back to the source** (FEAT-2026-11-10-write-back, BET-2026-11-write-back; since
   `7c557e6`, not released): the protocol text and contracts (WB-10, D-7..D-9, D-14, D-15, D-24); the connector kit and
   its conformance suite (WB-11, A-3, A-4); Knovas Link v2 behind `LINK_PROTOCOL_V2` (A-5; WB-21, WB-06, WB-25 site
   halves); GitHub on the kit behind `SYNC_PROTOCOL_V2` (C-3); declarations, «Freigaben» with change sets and the
   generic settings form behind `BUILDER_PROTOCOL_V2` (C-1, C-2, C-8); the Platform keeps components of their own kind
   (migration 0025); the Jira connector (J-0..J-2, WB-07, WB-20); and with this change **the Jira connector at a site**
   (its image, `deploy/docker-compose.jira.yml`, `LINK_COMPONENTS` in Link, `dump-stream`) and **the site's half of
   C-5** (`SYNC_PROTOCOL_V2=1` pinned to keep reading, fetch and 1.x jobs; the switch-over in
   `KnovasPlatform/docs/connect-a-site.md` § 6.2); the description as plain text (`74d0ddf`, ADF); and the
   **verification KC-3 fixes**: the Jira credential in the connector's own file `knovas-jira.env`, which the sync
   component never receives (the Builder names that file for a source of a connector's own kind), and the sync
   component never using a credential with a `_HOST` line, a host line or another kind's line (N5, SPEC-write-back-20);
   migration 0025 refusing a child of the kind `knovas_link`; the ADF bound in bytes of UTF-8.

**Switches, all off by default:** `BUILDER_PROTOCOL_V2` (Platform), `LINK_PROTOCOL_V2` and `LINK_COMPONENTS` (Link),
`SYNC_PROTOCOL_V2` (sync component). The Jira connector runs only where a site adds its file. Without them a site and
the Platform behave as released from `7c557e6`.

**Database:** Platform migration **0025** (`0025_builder_declared_component_kind.sql`) is the only one since the last
release: a component behind Link may have a kind of the heartbeat's shape, never `knovas_link` (no Platform ever wrote
such a child). A Platform whose database is still at `main` applies 0005–0025 at its next start; back up first
(knovas-tenant.md 4.10). 0025 changed after `74d0ddf`: a database that applied the earlier 0025 (only a test or local
one; none is released) stops at start with a checksum mismatch and is recreated from the backup.

**At the site:** the Jira lines (`SYNC_CREDENTIAL_<KEY>` and `_HOST`) go into `knovas-jira.env` beside the package,
never into `knovas-sources.env`; compose refuses to start the Jira file without it (knovas-tenant.md 5.7).

**Images:** the release workflow builds four (`all`): `docbridge-web-clientbundle`, `knovas-link`, `sync-component`,
`jira-connector` (new; `site` = the last three). `jira-connector` needs its GHCR package created private once.

**Tests run by the author on the change** (the founder's rule: the files of what it touches): KnovasConnectorJira
`tests/` 120 passed (81 before, 39 new: `test_site.py`, `test_dump.py`, `test_image.py`); KnovasLink
`test_enrolment.py`, `test_protocol_v2.py`, `test_local_api.py`, `test_generic.py` 158 passed (2 new);
docbridge_integration `test_release_images_workflow.py`, `test_sync_component_github_v2.py`,
`test_ci_runner_and_link_jobs.py`, `test_product_has_no_knovas_defaults.py`, `test_connector_kinds.py`,
`test_sync_component_generic.py`, `test_sync_component_image.py`, `test_web_timeouts.py`,
`test_builder_connection_generic.py`, `test_experiments_leftovers_deploydocs.py`, `test_builder_sites.py` 292 passed,
1 skipped (2 new); `docker compose config` of the repository's file with the profile and of a site package with
`docker-compose.jira.yml`; cards `card check` and `card render --check` clean, `map-check` (report) lists the changed
lines, each with a review entry. **Left to CI:** the full docbridge_integration suite, the Knovas Connector suite, the compose checks, the kit's suite,
and the Jira image build (`jira-connector` job: build and start once).

**Tests run on the verification KC-3 fixes** (the files they touch): docbridge_integration with PostgreSQL 16
(`PLATFORM_DB_REQUIRED=true`): `test_sync_component_credentials.py` (new: the verifier's canary scenario sends
nothing; GitHub sources keep working), `test_builder_sites.py` (`docker compose config` of a site package with
`docker-compose.jira.yml`: the Jira lines only in `jira-connector`), `test_builder_generic_kind.py` (0025 refusals
asserted as `CheckViolation`), `test_builder_site_credentials.py`, `test_builder_connection_generic.py` 162 passed;
`test_sync_component.py`, `test_connector_kinds.py`, `test_product_has_no_knovas_defaults.py` 143 passed;
`test_github_jobs.py`, `test_sync_component_github_v2.py` 39 passed; KnovasConnectorJira `tests/` 145 passed. Cards
`card check` and `card render --check` clean.

Verification: not verified by its author; a fresh session runs skill `verify` on J-4 and C-5.

---

## Release (the founder, after CI is green)

**Once, before the first run with the Jira image** (workstation; release-images.md "Once per repository" step 4, for
`jira-connector` only):

```bash
read -rs GHCR_PAT && printf '%s' "$GHCR_PAT" | docker login ghcr.io -u <owner> --password-stdin; unset GHCR_PAT
docker pull busybox:1.36.1
docker tag busybox:1.36.1 ghcr.io/<owner>/jira-connector:placeholder && docker push ghcr.io/<owner>/jira-connector:placeholder
docker logout ghcr.io
```

Then on GitHub: your profile → Packages → `jira-connector` → Package settings → **Manage Actions access** → Add
repository → KnovasComponents → role **Write**; "Danger zone" says **Private**. Delete the PAT. (`KB_READ_TOKEN`,
`CI_EPHEMERAL_RUNNER` and `GHCR_PACKAGES_PRIVATE=confirmed` are set from the first release.)

**Clicks:**

1. GitHub → Pull requests → New pull request → base `main`, compare `cl/jolly-fermat-ro3vkk` → title and body above
   → Create. Wait for CI. **Merge pull request** (a merge commit; the release then names a commit on `main`).
2. Actions → **release-images** → Run workflow → Use workflow from: **main** → Images: **all** → Run workflow.
   (Releasing from the branch before merging works the same: "Use workflow from: cl/jolly-fermat-ro3vkk".)
3. Four green jobs. In the job **sync-component**, step "Build and check the image" prints `brain-runner 0.3.2`; in
   **jira-connector** it prints `knovas_connector_jira.connector 0.1.0`. **Stop if** sync-component prints another
   brain-runner version.
4. The run's **Summary**: note the four lines and the commit:

   ```
   DOCBRIDGE_WEB_IMAGE=ghcr.io/<owner>/docbridge-web-clientbundle@sha256:…   (knovas.env on the Platform's machine)
   KNOVAS_LINK_DIGEST=sha256:…                                                (the site's .env)
   SYNC_COMPONENT_DIGEST=sha256:…                                             (the site's .env)
   JIRA_CONNECTOR_DIGEST=sha256:…                                             (the site's .env)
   ```

5. Deploy: the Platform with knovas-tenant.md 4.11 (the checkout at the released commit, the new
   `DOCBRIDGE_WEB_IMAGE`, `BUILDER_PROTOCOL_V2=true`; migration 0025 applies at start), then the site with
   knovas-tenant.md 5.7.

---

## Every commit on the branch since `main` (oldest first)

- `661d934` feat(ontology): choose the star view's front layer
- `af54fe9` feat(ontology): add update functionality for types and entities
- `b08ceb8` refactor(ontology): update CSS for point cloud mockup and graph container
- `7c0629e` feat(ontology): enhance graph layout and sibling point distribution
- `61fa051` fix(ontology): refine sibling point distribution and layout parameters
- `adb7b2c` feat(demo): add local Cortex server for star view development
- `7f5ec83` feat(ontology): implement snapshot loading and saving for local Cortex server
- `584e0dc` feat(ontology): enhance point cloud interaction and sibling dynamics
- `3769de1` feat(ontology): implement spring dynamics for point cloud interactions
- `cdb068c` feat(ontology): enhance CSS and JavaScript for hidden items management
- `be76074` feat(cortex): add HelioGrid as the marketing demo client
- `6e7441d` feat(ontology): enhance CSS and JavaScript for entity management
- `fbb804d` Merge branch 'feat/cortex-star-view' of https://github.com/Seifeddini/KnovasComponents into feat/cortex-star-view
- `e377dd3` feat(ontology): enhance entity creation and styling in the interface
- `7b203ee` feat(views): Ansichten page with a decluttered ego graph (Step 1)
- `6ea4de3` feat(views): process and spine definitions from the tenant, validated (Step 2)
- `0e10ecc` feat(views): TRACE and BOARD of a process, from its definition (Step 3)
- `0386ca1` feat(views): TREE and MATRIX of a spine, from its definition (Step 4)
- `7966ff2` feat(views): saved views as Platform configuration (Step 5)
- `8ec12fd` feat(views): default shared views for the Knovas tenant (Step 6)
- `3c1eee0` docs(views): final report — built, measured, and what KnowledgeBase and the Brain API should fix
- `574659a` Merge pull request #33 from Seifeddini/feat/views-step6-default-views
- `705912f` Merge pull request #32 from Seifeddini/feat/views-step5-saved-views
- `d2e5bee` Merge pull request #31 from Seifeddini/feat/views-step4-tree-matrix
- `42c2b94` Merge pull request #30 from Seifeddini/feat/views-step3-trace-board
- `336a954` Merge pull request #29 from Seifeddini/feat/views-step2-definitions
- `05ca110` Merge pull request #28 from Seifeddini/feat/views-step1-ego-graph
- `1fbd089` feat(views): discovery and navigation; fix the sparse ego graph (Step 6b)
- `8d8d5f9` docs(views): state the curation_status observation as checked, not as a tenant-wide fact
- `fc661cb` feat(views): change requests from the TRACE view (Step 7)
- `883ffc7` fix(views): never pass a client-supplied revision to git as an option
- `e06cd2b` feat(views): definitions, edit map and suggestions from KnowledgeBase client-zero/edit-map
- `b1df14f` feat(views): edit-map routing and the writers for feature-plan links and fields
- `ee19c35` feat(views): change sets — fixes and plan edits become ONE pull request on KnowledgeBase
- `fae0ef8` fix(views): take an operation's process from the instance, not from the request
- `603de5f` feat(views): fix in place, gaps worklist and change-set review in the page (Step 7)
- `7c89ddf` docs(views): Step 7 report and plan; definitions pinned to KnowledgeBase master
- `430aca7` Merge pull request #35 from Seifeddini/feat/views-step7-change-requests
- `566bdf9` Merge pull request #34 from Seifeddini/feat/views-step6b-discovery
- `8551a70` feat(views): software documentation pages from KnowledgeBase's page definitions (Step 8)
- `2478c07` docs(views): Step 8 report section and plan (D7)
- `e654751` Merge pull request #38 from Seifeddini/feat/views-step8-documentation
- `69962dd` docs(platform): run the Platform for the Knovas tenant, and the views' addresses
- `0a55105` views: read each tenant's definitions bundle instead of a vendored copy (WP KC-1)
- `6b60d29` views: shared views from the tenant's profiles, and the CTO's system map (WP KC-2)
- `1d4adb0` views: draw a profile's map only for the users of that profile (KC-2)
- `2169810` views: documentation pages for any type with a page definition (WP KC-3)
- `d90c594` Merge pull request #41 from Seifeddini/views/kc1-kc3-on-star-view
- `74a952c` platform: the views settings editable on the settings page (WP KC-0.1)
- `e7fcf5e` Merge pull request #42 from Seifeddini/cs1/KC-0.1-platform-settings
- `5b92218` platform: Sync von GitHub — the tenant's Brain from its knowledge base, on demand (WP KC-0.2)
- `688b7c2` Merge pull request #43 from Seifeddini/cs1/KC-0.2-github-sync
- `e9e582a` feat(builder): design system and shell (B0)
- `55b0048` feat(builder): workspace model and store (B1)
- `6fe78bd` feat(builder): ontology canvas and type tree, read-only (B2)
- `31ebe20` feat(builder): ontology canvas opens as a tree
- `c938e7c` feat(builder): create a workspace on the platform
- `561bd9b` feat(builder): edit the ontology on the canvas (B3)
- `551ac12` feat(builder): start a workspace from industry packs (B4)
- `9d7e57f` feat(builder): review and publish a workspace version (B5, partly)
- `2ea593b` Merge feat/cortex-star-view into the builder line
- `86e8ef4` feat(builder): the core on every canvas, a workspace from the tenant's ontology; brain-runner 0.1.1
- `22133bf` feat(builder): a publish leaves a RunEvent and becomes the views' source (B5)
- `bf6d54d` feat(builder): model a company's processes as flows (B6)
- `4b5b05d` feat(builder): register and test a company's sources (B7)
- `d340bfa` fix(builder): a source reads only the public network and keeps its credential to its host (B7)
- `7bb00b9` fix(builder): an HTTP source connects to the address it was checked at (B7)
- `e00d5a9` feat(builder): map a source to the ontology visually (B8)
- `2a40e20` fix(builder): a source test or sample that fails answers in JSON with the reason (B7)
- `ec4119d` feat(builder): define how the company sees its Brain (B9)
- `6656f51` feat(builder): operate a published Brain (B10)
- `7f72a7d` test(builder): health reviews on both stores (B10)
- `409e177` feat(builder): make the builder a team tool (B11)
- `a63c729` feat(builder): register and monitor sync components (B13)
- `1a3d9eb` fix(builder): a held plan's approval needs publish; the certificate binding fails closed (B13)
- `6cf8b2b` feat(builder): the access window -- group tree, effective access, explanations (B14)
- `75ccbe8` fix(builder): only a builder role sees the tenant's component register and alerts (B13)
- `a2a75e6` fix(builder): kb_read_token from knovas.env, processes in old workspaces, pack take-over, delete a workspace
- `e965b64` fix(builder): EUO-filed terms are the company's to edit; start.sh hands KB_READ_TOKEN to the build
- `90bc610` fix(builder): rebuild brain-runner once a token is given; aliases keep their place; reset the ontology
- `890463c` feat(builder): an administrator may approve their own draft, deliberately; aliases marked as such
- `3807e52` fix(builder): a processes file the builder makes is of format version 2
- `f8143de` fix(builder): the dry run leaves out the type the engine installs in every tenant
- `30ceee6` fix(builder): a new hierarchy starts with its top level; sections say what they need
- `bf7b929` feat(builder): views by description (Claude API)
- `b92dd7a` fix(start): ask GitHub about KB_READ_TOKEN before building
- `c3246be` fix(start): a refused KB_READ_TOKEN really stays out of the build
- `2a586e5` fix(builder): an alias type goes under its class's ancestor for the tenant
- `c4322e0` feat(builder): relations and hierarchies from what the Brain's links show
- `5a87d98` feat(views): outline, one page with everything a profile's list view names (BET-2026-11-steering-kernel K0-15)
- `8f1287a` feat(views): a clearer outline, and "Ablauf" opens it (BET-2026-11-steering-kernel K0-15)
- `050d095` feat(views): the outline as a tree with types; a selection without a flow opens the overview (BET-2026-11-steering-kernel K0-15)
- `4f4e11c` feat(connectors): a deployable sync component under connector protocol v1 (BET-2026-11-sources-and-access KB-3.1, KC-7a)
- `17e6f48` fix(connectors): the sync component's service under services, and its missing module
- `248080e` feat(connectors): file and GitHub connectors under connector protocol v1 (BET-2026-11-sources-and-access KN-1, KC-7a, KB-3.1)
- `71cb237` docs(connectors): the sync image is built with the env file
- `2f88a4e` fix(connectors): the wizard's placeholders no longer break the shell
- `6c1b188` fix(connectors): what the first real enrolment found
- `020243e` fix(connectors): a settings key that does not work is not a bad token
- `0163027` feat(connectors): a test setting for a deployment where the Brain does not sign
- `200531c` feat(connectors): delete components, remove sources and mappings, read or read and write per source
- `f347264` fix(sources): say why a repository cannot be read
- `cb978ba` feat(sources): map a folder of a repository that the list does not show
- `f20ee55` fix(views): say when the views do not read a published version
- `8c71ee6` fix(mappings): a mapping's dry run does not need a draft
- `897a6c5` fix(cortex): the star view shows a tenant's own types and takes new nodes
- `4b84a64` feat(builder): a workspace takes the tenant's definitions from the views' bundle (BB-12)
- `4f02eae` fix(cortex): the star view shows the tenant's own graph when the whole-graph export fails
- `aee1ac1` feat(link): stream, job and local API contracts (MVP-1, KC-7a)
- `337e2dd` docs(protocol): connector protocol v1.1, additions only (MVP-2, KC-7a)
- `d064770` feat(builder): sites and install package (MVP-23, KC-7b)
- `b28e74f` feat(link): commands in link mode, source assignment, local probes (MVP-1, MVP-2, KC-7a)
- `7a58089` feat(builder): Link's configuration names the component serving each source (MVP-23, KC-7b)
- `d904bed` Merge wip/kc-contracts: connector protocol v1.1 contracts and text (MVP-1, MVP-2, KC-7a)
- `c3e31a2` feat(builder): credentials of a site's sources as an env line (MVP-24, KC-7b)
- `99753bf` fix(builder): the install package matches Knovas Link's local API (MVP-23, KC-7b)
- `d7a2055` feat(builder): a source's fetch and write switches (MVP-25, KC-7b)
- `c94e0d3` feat(builder): mapping panels «Zugriff» and «Zurückschreiben» (MVP-26, KC-7b)
- `234c889` feat(builder): «Freigaben» for write proposals (MVP-27, KC-7b)
- `e988cad` feat(builder): «Betrieb» › «Verbindung», monitoring at MVP level (MVP-28, KC-7b)
- `71aa5f7` feat(link): skeleton, identity and enrolment (MVP-15, KC-7a)
- `07d9b9a` test(link): local API, its tokens and the control plane through Link (MVP-16, KC-7a)
- `e8b6070` docs(builder): connecting a site, and a guard that the slice stays generic (MVP-23..28, KC-7b)
- `c848073` feat(link): relays to secure-api and to the Platform (MVP-17, KC-7a)
- `7a8b2e7` fix(builder): Knovas Link is not deleted while a source reads through its components (MVP-23, KC-7b)
- `7863a77` feat(link): a job's lease, sent back as X-Knovas-Lease (MVP-1, MVP-2, KC-7a)
- `23351fb` Merge wip/kc-contracts-2: per-claim lease token in the job contract (MVP-1, KC-7a)
- `2d8b88f` feat(link): work channel, leases and probes (MVP-18, KC-7a)
- `23a5a19` test(link): heartbeat with the MVP telemetry (MVP-19, KC-7a)
- `3c7d453` fix(builder): split the site's env files; every service reaches its own sources (MVP-23, MVP-24, KC-7b)
- `9f14703` fix(builder): computed only by an explicit mark; group keys are the tree's keys (MVP-26, KC-7b)
- `b3dc8a4` fix(link): stricter relay path, a channel that says connected only when answered (MVP-17, MVP-18, KC-7a)
- `1631f8d` docs(link): README and the generic guard (MVP-31, MVP-32, KC-7a)
- `a528ac1` fix(builder): read job stats and proposals in the shapes the Brain answers (MVP-27, MVP-28, KC-7b)
- `3cd7003` fix(link): no request URL in the logs (MVP-17, KC-7a)
- `f3a98c1` fix(link): forget a job once it has its outcome (MVP-18, KC-7a)
- `b90a0b3` Merge wip/kc-link: Knovas Link (MVP-15..19, MVP-31, MVP-32, KC-7a)
- `5b250a0` Merge wip/kc-builder: sites, credentials, switches, mapping panels, Freigaben, monitoring (MVP-23..28, KC-7b)
- `7081402` fix(builder): «Nicht umkehrbar» with brain-runner's sub-reasons (MVP-26, KC-7b)
- `f5a5e39` feat(link): codes invalid_job and part_not_accepted (MVP-1, MVP-2, KC-7a)
- `faa49fb` fix(link): invalid_job and part_not_accepted for Link's own failures (MVP-18, KC-7a)
- `e45f602` fix(builder): stale documents per source, error_code first, Link's own codes (MVP-27, MVP-28, KC-7b)
- `df8fcb0` Merge wip/kc-builder: integration fixes (KC-7a, KC-7b)
- `431581f` Merge wip/kc-contracts-3: integration fixes (KC-7a, KC-7b)
- `8a2812a` Merge remote-tracking branch 'origin/cl/jolly-fermat-ro3vkk' into wip/kc-link
- `4ae56bc` fix(link): a part Knovas does not take is 400 bad_field, as local-api.md now says (MVP-18, KC-7a)
- `3396f8a` Merge wip/kc-link: Link's own failure codes (MVP-18, KC-7a)
- `35cb2ad` feat(sync-component): front matter edit that changes only the values (MVP-22, KC-7a)
- `577b10a` feat(sync-component): link mode and the work channel (MVP-20, KC-7a)
- `f67965f` test(sync-component): GitHub originals for fetch jobs, probes (MVP-21, KC-7a)
- `03f5406` test(sync-component): GitHub write as a pull request (MVP-22, KC-7a)
- `61989b0` test(sync-component): guard that the product names no client (MVP-31, KC-7a)
- `14a31a3` docs(sync-component): link mode, fetch and write jobs, write as pull request (MVP-20..22, KC-7a)
- `6c2e226` fix(sync-component): one Link session per thread, never through a proxy (MVP-20, KC-7a)
- `0a99415` Merge wip/kc-sync: sync component link mode, GitHub read and write (MVP-20, MVP-21, MVP-22, MVP-31, KN-1)
- `d857b77` feat(link): the sync component's result codes (MVP-1, KC-7a)
- `c8dd79b` Merge wip/kc-contracts-4: the sync component's result codes in the contract (MVP-1, KC-7a)
- `dbac962` feat(builder): German texts for the component's fetch and write codes (MVP-21, MVP-22, MVP-28, KC-7b)
- `1621099` fix(sync-component): 404 on a write step is token_cannot_write; branch cleanup (MVP-22, KC-7a)
- `76e09b1` chore(sync-component): version 0.2.0 (MVP-20, KC-7a)
- `a8dbbae` Merge wip/kc-builder: German texts for the sync component's fetch and write codes (MVP-28, KC-7b)
- `c42b1db` Merge wip/kc-sync: sync component 0.2.0, token_cannot_write on a 404 write step, branch cleanup (MVP-21, MVP-22, KC-7a)
- `a82c084` feat(link): every code the sync component answers is in the contract and the Builder (MVP-1, MVP-21, MVP-22, MVP-28, KC-7a)
- `eadb710` feat(link): certificate-request and install-certificate; a waiting Link picks up its certificate (DEC-0056, MVP-15, MVP-23, KG-1)
- `5bd7b65` feat(builder): the certificate step as DEC-0056 decided, German texts for install-certificate (DEC-0056, MVP-15, MVP-23, KG-1)
- `99232f2` Merge wip/kc-link-cert: Link's certificate-request and install-certificate, the certificate step as DEC-0056 decided (DEC-0056, DEC-0062, MVP-15, KG-1)
- `5665944` test(sync-component): the fake Link answers the document list and folder rules brain-runner 0.3 reads (MVP-14, MVP-20)
- `b279e24` docs(protocol): record the wide relay as an accepted limit until the first external customer (review F6, MVP-17, gate 0.3)
- `6a7145e` feat(builder): German text for source_ref_missing, the Brain's answer to accepting a proposal whose document is gone (review F1, MVP-8, MVP-27)
- `0ee317a` fix(builder): publishing an imported package changes nothing it states (KB-10, KC-10, MVP-26)
- `9502ab7` feat(builder): the export is the whole workspace, brain-builder.yaml beside brain-config.yaml (KB-10, KC-10, MVP-26)
- `5d5178b` Merge wip/kc-builder-cutover: publishing an imported package changes nothing it states; the export is the whole workspace (KB-10, KC-10, MVP-26)
- `d7bf5fa` test(builder): the opt-in package round trip expects the 79 types of the package as of D-6 (KB-10, D-6)
- `bc2dca1` ci: trusted runs on the self-hosted AWS box when CI_RUNNER is set; GitHub-hosted minutes are used up (maintenance; the founder, 2026-11-06)
- `7a43721` fix(cortex): documents are placed beside their entities once the systems are mounted (KB-10, maintenance)
- `35e0d23` test(platform): the settings page's stand-in context carries settings_admin like its route (KB-10, maintenance)
- `2ab6fbd` fix(brain-sync): a failed or refused run is audited as error or denied (KB-10, maintenance)
- `450f536` ci: the old Connector name check leaves KnowledgeBase's bundle snapshot alone (KB-10, maintenance)
- `1b212d5` fix(audit): a failed audit write logs its error code, never the row (KB-10, maintenance)
- `f306c17` feat(sync-component): pinned kustomize in the image, checksum checked before unpacking (V1-G6, FEAT-2026-11-05-knovas-brain-v1)
- `21158ca` feat(platform): product defaults name no tenant; Knovas values go to its deployment (V1-G4, FEAT-2026-11-05-knovas-brain-v1)
- `66aeee5` ci: Knovas Link's tests in CI, and the pinned brain-runner for the Platform's (V1-G11, FEAT-2026-11-05-knovas-brain-v1)
- `3e2f084` ci: pushes to main/master go to knovas-ci-ephemeral when CI_EPHEMERAL_RUNNER is set (K-D3, maintenance)
- `bd35f5c` Merge wip/kc-g-items: no Knovas defaults in product code, kustomize in the sync-component image, Link's tests and the brain-runner install in CI (V1-G4, V1-G6, V1-G11, FEAT-2026-11-05-knovas-brain-v1)
- `eedc8b9` Merge pull request #47 from Seifeddini/cl/jolly-fermat-ro3vkk
- `01716b7` ci: install brain-runner 0.3.0 from the tagged KnowledgeBase merge (V1-G4, FEAT-2026-11-05-knovas-brain-v1)
- `67cc61c` fix(builder): sites run the published version only; binding a source to a site needs «Zugänge verwalten» (BB-07 B2, FEAT-2026-11-05-knovas-brain-v1)
- `198b976` fix(builder): the review shows all a publish changes and the approval binds to it (BB-07 B1, FEAT-2026-11-05-knovas-brain-v1)
- `9a6117c` fix(builder, link): a site's certificate is bound to its request and tenant and enforced (BB-07 B3/B4, S-4 M2, FEAT-2026-11-05-knovas-brain-v1)
- `a63596d` fix(builder): cap YAML alias expansion on import; log every export (BB-07 B6, B7, FEAT-2026-11-05-knovas-brain-v1)
- `5749dfb` fix(builder): the role mapping the dry run compares with, read as the setting holds it (BB-07 B1, FEAT-2026-11-05-knovas-brain-v1)
- `9ff520a` test(builder): the digest an approval holds and the plan publish applies are the same in another process (BB-07 B1, FEAT-2026-11-05-knovas-brain-v1)
- `ebb7c81` ci(release): build, sign and push the Platform, Knovas Link and the sync component from any branch (V1-G10, FEAT-2026-11-05-knovas-brain-v1)
- `0d8e2c2` docs(deploy): the Platform with the Brain Builder from a branch, and the Knovas site, step by step; the connectors' client-certificate terminator (KG-1, S-6, V1-G10, FEAT-2026-11-05-knovas-brain-v1)
- `a5e02a2` docs(connection-layer): one overview of the parts in this repository; entry points point to it; stale statements fixed against the code (MVP-32, KG-1, FEAT-2026-11-05-knovas-brain-v1)
- `bc565b2` brain-runner pinned at 0.3.1 in CI and both images: the sync component can run behind Knovas Link (FEAT-2026-11-05-knovas-brain-v1 step 8, KG-1, MVP-13, MVP-20)
- `81e9c92` docs(components): component cards for the parts the Brain v1 work touched (FEAT-2026-11-06-knovas-software-map A8 stage 1, FEAT-2026-11-05-knovas-brain-v1)
- `964c638` docs(components): the 8 cards follow brain-runner's Software Map fixes: pages with code spans, routes with their blueprint prefix and app, review entries by line id (FEAT-2026-11-06-knovas-software-map B1 B2 B5; DEC-0068 rule 3)
- `fa2d1f8` ci(release-images): nothing is pushed until a person confirmed the three packages private (FEAT-2026-11-05-knovas-brain-v1 KG-1, V1-G10)
- `7c557e6` Automatic GitHub → Brain syncing: «bei neuen Commits», automatic apply, last synced commit per source (FEAT-2026-11-05-knovas-brain-v1 KG-2)
- `713eba6` Connector protocol v2 draft: one generic contract for reading and writing any source, with schemas, examples and contract tests (BET-2026-11-write-back WB-10, FEAT-2026-11-10-write-back Phase A)
- `37d6bac` Connector protocol v2 draft: permits in the Brain's permit format 1 (JWS compact, Ed25519, kid from the key), jobs addressed by site certificate serial (BET-2026-11-write-back WB-10, WB-21; FEAT-2026-11-10-write-back Phase A)
- `66d94c5` Connector protocol v2 draft: the permit keys route is settled as GET /secured/connector/v2/permit-keys (BET-2026-11-write-back WB-10, FEAT-2026-11-10-write-back Phase A)
- `6124796` Connector protocol v2 draft: serials of up to 49 digits, and a permit key's window checked at the permit's iat (BET-2026-11-write-back WB-10, WB-21; FEAT-2026-11-10-write-back B2-Q8, B2-Q9)
- `f5d98fa` Connector kit and conformance suite, draft (BET-2026-11-write-back WB-11; FEAT-2026-11-10-write-back A-3, A-4)
- `80dbeef` Connector protocol v2 draft: a missing or unreadable permit is answered by Link's permit checks, not as invalid_job (BET-2026-11-write-back WB-10, WB-21; FEAT-2026-11-10-write-back A-2)
- `be4d59e` Knovas Link speaks connector protocol 2.0 behind LINK_PROTOCOL_V2, off by default (BET-2026-11-write-back WB-21, WB-06, WB-25 site halves, WB-10; FEAT-2026-11-10-write-back A-5)
- `2115777` Connector protocol v2.0 approved by the founder: unstage optional, undo not in V1; the conformance suite follows (BET-2026-11-write-back WB-10, WB-11; FEAT-2026-11-10-write-back D-7, D-8, D-9)
- `fd94d5f` GitHub on connector protocol 2.0, behind SYNC_PROTOCOL_V2 (off): direct writes with the expected blob sha by default, native_review as today's pull request (BET-2026-11-write-back WB-12; FEAT-2026-11-10-write-back C-3)
- `e1f4846` Connector declarations reach the Builder: Link reports them in its heartbeat, the Platform checks and keeps them, and with BUILDER_PROTOCOL_V2 on the Builder offers writing per declared action and staging (BET-2026-11-write-back WB-12, WB-34 part; FEAT-2026-11-10-write-back C-1)
- `ac6f5e0` «Freigaben» shows change sets with the connector's preview and approves them with the source's staging, behind BUILDER_PROTOCOL_V2 (off); an inexact preview is never approved (FEAT-2026-11-10-write-back C-2; BET-2026-11-write-back WB-34 part, WB-03 part)
- `c6ba3c4` Card the external system EXT-JIRA-API: what the Jira connector relies on in Jira Cloud's REST API v3 (FEAT-2026-11-10-write-back J-0; BET-2026-11-write-back WB-07)
- `72febf4` The Jira connector on connector protocol 2.0: issues as record streams, set_assignee, transition_issue and set_due_date under permits, tested against a fake Jira (FEAT-2026-11-10-write-back J-1, J-2; BET-2026-11-write-back WB-07)
- `aedb5bc` Connector protocol v2.0, amended 2026-11-10: every change, difference and conflict names its step, and a permit lives up to 3 hours (FEAT-2026-11-10-write-back D-14, D-15; BET-2026-11-write-back WB-10, WB-11, WB-21)
- `c781eea` Jira connector on the amended protocol: every change names its step, field edits of one issue are written together (FEAT-2026-11-10-write-back J-2, D-14; BET-2026-11-write-back WB-07)
- `60b5b9c` Builder, protocol 2.0: native_review only with review_outcome, the declaration decides writing (not the kind catalogue), and one bad declaration never refuses Link's heartbeat (FEAT-2026-11-10-write-back C-1, C-2; Phase A verification M4, L3; BET-2026-11-write-back WB-12, WB-34 part)
- `a35a9a4` Knovas Link answers a repeat before anything else and runs a permit once: permit checks first, a reservation before the connector, every refusal logged, one encoding of a token (FEAT-2026-11-10-write-back A-5; BET-2026-11-write-back WB-21, WB-06, WB-25; verifier Phase A M1, M2, L1, L2, L5)
- `041e2a4` The conformance suite checks each declared staging: a connector that only stages can pass, and staging twice, staging a stale item or ignoring the staging and site binding fail; permits read in one encoding and never raise (FEAT-2026-11-10-write-back A-3, A-4; BET-2026-11-write-back WB-11; verifier Phase A M3, L1, L2, L5)
- `17d5fe5` GitHub connector: a repeat's receipt is read at the commit that carries its permit, found in the file's whole history, and the search fails explicitly rather than write again (FEAT-2026-11-10-write-back C-3; BET-2026-11-write-back WB-12; verifier Phase A L4)
- `1396982` Connector protocol v2 text after the verification of Phase A: unstage is optional in the conformance table, the lock's draft is explained, and a change note lists the corrections (FEAT-2026-11-10-write-back D-7; BET-2026-11-write-back WB-10; verifier Phase A L6)
- `12456d3` Site guide § 5.4 states what each GitHub token needs, not which repository writes: Pull requests write only where changes arrive as pull requests, a direct source's branch must let the token push (FEAT-2026-11-10-write-back P-2, D-20; BET-2026-11-write-back WB-12)
- `d3fa93a` Connector protocol v2.0, amended 2026-11-10 (D-24): a third grouping, item - several steps of one item in one act, all or none, and one act per item from the Brain (FEAT-2026-11-10-write-back D-24; BET-2026-11-write-back WB-10, WB-11)
- `b8ea23b` Jira connector declares grouping item for set_assignee and set_due_date: one act per issue, an act spanning issues refused grouping_unsupported before anything is read (FEAT-2026-11-10-write-back J-2, D-24; BET-2026-11-write-back WB-07)
- `2901cce` Builder, protocol 2.0: a source of a kind a connector at a site declared and the Builder has no form for is created with a generic settings form, never with a credential, and reaches the site as entered (FEAT-2026-11-10-write-back C-8; BET-2026-11-write-back WB-12, WB-07; SPEC-write-back-13)
- `1bc9aed` Builder, protocol 2.0: a component behind Knovas Link is kept under its own kind when its declaration is one the protocol's schema allows, so a new connector registers without a Platform change (FEAT-2026-11-10-write-back C-8; BET-2026-11-write-back WB-12, WB-07; SPEC-write-back-13)
- `df1d8c3` Jira connector sends its credential only to the host the site's environment binds to it: SYNC_CREDENTIAL_<KEY>_HOST beside SYNC_CREDENTIAL_<KEY>, a site setting naming another host refused jira.bad_settings before any request, no redirect followed (FEAT-2026-11-10-write-back J-2; BET-2026-11-write-back WB-07, WB-20; GI-CONN-07, SPEC-write-back-20; verifier KC N1)
- `0b8d092` The Jira connector runs at a site behind Knovas Link with its own image, and dump-stream writes a source's record stream; the sync component serves a GitHub source on protocol 2 while its reads stay; the release workflow builds four images (FEAT-2026-11-10-write-back J-4, C-5; BET-2026-11-write-back WB-07, WB-12)
- `74d0ddf` Jira connector: a rich-text field (the description, Atlassian Document Format) reaches the record as plain text, bounded at 32,768 characters, instead of null (FEAT-2026-11-10-write-back J-4; BET-2026-11-write-back WB-07, WB-30)
- this fix: the Jira credential only in the Jira connector's own knovas-jira.env and never handed out by the sync component; migration 0025 refuses a child of Link's own kind; the ADF bound in bytes (FEAT-2026-11-10-write-back J-4; BET-2026-11-write-back WB-07, WB-20; SPEC-write-back-20; verifier KC-3 N5, notes 2, 3, 5)
- (this change) The Jira connector runs at a site behind Knovas Link with its own image, and dump-stream writes a source's record stream; the sync component serves a GitHub source on protocol 2 while its reads stay; the release workflow builds four images (FEAT-2026-11-10-write-back J-4, C-5; BET-2026-11-write-back WB-07, WB-12)
