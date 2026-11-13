# The Platform for the Knovas tenant (Ansichten)

How to run the Platform against Knovas' own tenant, `af639810-afc7-403f-8139-f6026d085c99`, so that the
process, structure and documentation views ("Ansichten") show the company's own brain. The views are
read-only on the brain. The views themselves are described in
[`docs/superpowers/plans/2026-10-27-process-and-spine-views.md`](../../../docs/superpowers/plans/2026-10-27-process-and-spine-views.md)
and measured in [`docs/superpowers/reports/2026-10-27-views-final-report.md`](../../../docs/superpowers/reports/2026-10-27-views-final-report.md).

**Step by step, for the Knovas machine outside Azure (KG-1, CL-8, S-6):**
[section 4](#4-step-by-step-the-platform-with-the-brain-builder-from-a-branch) runs the Platform with the Brain
Builder from a branch's released images; [section 5](#5-step-by-step-the-knovas-site-knovas-link-and-the-sync-component)
installs the Knovas site (Knovas Link and the sync component) beside it. Sections 1 to 3 are the reference.
How the parts fit together: [../connection-layer.md](../connection-layer.md). Knovas's internal owner page for the
whole connection layer (the Brain's side, trust boundaries, flows, what is live in production):
[`docs/Docs/04_COMPONENTS/connection_layer.md`](https://github.com/Seifeddini/KnowledgeBase/blob/cl/jolly-fermat-ro3vkk/docs/Docs/04_COMPONENTS/connection_layer.md)
in KnowledgeBase.

## Where it runs today (30 Oct 2026)

**No standing deployment serves the Knovas tenant.** Searched for one:
- this monorepo: compose files, `deploy/`, these docs;
- KnowledgeBase's client-zero documents;
- the development workstation.

Two things exist instead:

- **The demo stack on the workstation.** The compose project `knovascomponents` in the KnovasComponents checkout is the demo setup: `KNOVAS_API_URL=http://knovas-mock:5000`, `COMPOSE_PROFILES=mock,experiments`, `IDENTITY_ENABLED=false`. It talks to the mock, not to the tenant, and has been stopped since 20 Oct 2026.
- **A development server.** The views were built and measured against the tenant with `src/web_interface/app.py` run directly (section 3 below), on `http://127.0.0.1:5077` without accounts and `http://127.0.0.1:5078` with accounts.

## 1. Run it with Docker Compose (recommended)

This uses the same stack as a firm deployment ([../setup.md](../setup.md)), with two differences:
- **Start only the Platform services.** Never start Knovas Connector against the Knovas tenant: it syncs `KNOVAS_DOCUMENTS_PATH` into the tenant, and an empty folder read as a share prunes the tenant's documents.
- **Turn on the graph source.** The views read the tenant's Knowledge Graph.

### Certificates (never in the repository)

The Knovas organisation's client certificate is kept outside every checkout. Copy the three files into the repository root's `certs/` (git-ignores `*.pem`):

| File in `certs/` | What it is |
|---|---|
| `client-cert.pem` | the organisation's client certificate (its subject names the tenant) |
| `client-key.pem` | its private key |
| `ca-root.pem` | the Knovas CA |

`./scripts/setup.sh` links them as `client.crt`, `client.key` and `ca.crt`. Compose mounts `./certs` read-only at `/app/certs`.

These settings carry the certificate into the app. `setup.sh` writes them into `KnovasPlatform/.env.generated` (git-ignored); nobody types them:

| Setting | Value |
|---|---|
| `SEMANTIX_CLIENT_CERT` | `/app/certs/client.crt` |
| `SEMANTIX_CLIENT_KEY` | `/app/certs/client.key` |
| `SEMANTIX_CA_CERT` | `/app/certs/ca.crt` |
| `SEMANTIX_CUSTOMER_ID` | `KNOVAS_TENANT_ID` from `knovas.env`, else the certificate's subject |
| `SEMANTIX_API_URL` | `KNOVAS_API_URL` from `knovas.env` |

### `knovas.env` (repository root, git-ignored)

```bash
KNOVAS_API_URL=https://api.knovas.ch:8443
KNOVAS_TENANT_ID=af639810-afc7-403f-8139-f6026d085c99
KNOVAS_PLATFORM_URL=http://127.0.0.1:8081          # or https://<fqdn> behind host nginx
KNOVAS_DOCUMENTS_PATH=./data/no-local-documents     # compose needs a value; the Connector stays off
PLATFORM_ADMIN_EMAIL=<the CTO's address>
COMPOSE_PROFILES=                                   # no mock, no experiments

# The views read the tenant's Knowledge Graph. Without this they answer
# "Wissensnetz-Modus erforderlich".
ONTOLOGY_SOURCE=graph
# CORTEX_ENABLED stays unset (true): /views belongs to Cortex and is refused when it is false.

# Optional since KC-0.1: an admin sets these two on the settings page ("Settings on the settings
# page" below). Set here, they pin the value and the page shows it read-only.
#   The tenant's definitions bundle (see "The definitions bundle" below):
# VIEWS_CONFIG_SOURCE=github:Seifeddini/KnowledgeBase@master:docs/ModernDocs/client-zero/generated/bundle.json
#   Who sees which profile views (component README, "Profile views"):
# VIEWS_PROFILE_ROLES=admin=CTO; member=Engineer

# Knovas's knowledge base (V1-G4): the product ships no default for these, so the Knovas
# tenant sets them here. Without them, change requests, source links and the sync say
# "nicht konfiguriert" ("The tenant's knowledge base" below).
VIEWS_KB_GITHUB_REPO=Seifeddini/KnowledgeBase
VIEWS_KB_DECISIONS_DIR=docs/ModernDocs/client-zero/ontology/decisions
VIEWS_KB_PLAN_LINKS_PATH=docs/ModernDocs/client-zero/ontology/plan-links.yaml
VIEWS_KB_SAMPLE_EDGES_PATH=docs/ModernDocs/client-zero/ontology/sample.yaml
BRAIN_SYNC_REPOSITORY=Seifeddini/KnowledgeBase
BRAIN_SYNC_CONFIG_PATH=docs/ModernDocs/client-zero/brain-config.yaml

# Plain HTTP on 127.0.0.1 only (no TLS in front): otherwise the session cookie is dropped.
WEB_SESSION_COOKIE_SECURE=false
```

`IDENTITY_ENABLED` stays unset (on): saved views, the remembered selection, change sets and their audit need accounts. Every key `setup.sh` does not consume (`ONTOLOGY_SOURCE`, `VIEWS_*`, `WEB_SESSION_COOKIE_SECURE`) is passed through to the Platform unchanged. The tunable view settings and their defaults are in `KnovasPlatform/components/docbridge_integration/config/config.yaml` under `views:`.

### The tenant's knowledge base (Knovas values, not product defaults)

The Platform ships no default that names a tenant (V1-G4; `tests/test_product_has_no_knovas_defaults.py` fails on
one). An empty setting means "not configured": the feature that needs it refuses and names the setting, it never
falls back to Knovas's repository. The Knovas tenant therefore sets these in `knovas.env` (above):

| Setting | Key | Variable | Knovas | Without it |
|---|---|---|---|---|
| Repository for change requests and source links | `views.change_requests.github_repo` | `VIEWS_KB_GITHUB_REPO` | `Seifeddini/KnowledgeBase` | No change requests ("Kein Zugang … konfiguriert"); documentation pages link to no source |
| Folder of decision records | `views.change_requests.decisions_dir` | `VIEWS_KB_DECISIONS_DIR` | `docs/ModernDocs/client-zero/ontology/decisions` | A substantive change is refused ("Nicht konfiguriert: views.change_requests.decisions_dir"); minor changes still go through |
| Plan links (serves, motivated_by, justified_by) | `views.change_requests.plan_links_path` | `VIEWS_KB_PLAN_LINKS_PATH` | `docs/ModernDocs/client-zero/ontology/plan-links.yaml` | Capability, goal and hypothesis are locked in the plan form, with the reason |
| Pre-mortems and risk mitigations (examines, mitigated_by) | `views.change_requests.sample_edges_path` | `VIEWS_KB_SAMPLE_EDGES_PATH` | `docs/ModernDocs/client-zero/ontology/sample.yaml` | Those links are locked, with the reason |
| Sync: repository | `brain_sync.repository` | `BRAIN_SYNC_REPOSITORY` | `Seifeddini/KnowledgeBase` | "Sync nicht eingerichtet: repository fehlt" |
| Sync: configuration | `brain_sync.config_path` | `BRAIN_SYNC_CONFIG_PATH` | `docs/ModernDocs/client-zero/brain-config.yaml` | "Sync nicht eingerichtet: config_path fehlt" |

The two sync settings can also be set on the settings page ("Sync von GitHub" below). The edit map of the Knovas
bundle names the edge lists relative to `docs/ModernDocs` (`client-zero/ontology/plan-links.yaml`); a writer
accepts that because it is the tail of the configured path, and refuses when the edit map names another file.

### The definitions bundle (never a copy in the Platform)

The views draw processes, spines, pages, the edit map, descriptions and key patterns from the tenant's
**definitions bundle** (KnowledgeBase KB-7, `docs/ModernDocs/client-zero/generated/bundle.json`), with the
three computed reports it names. Definition nodes on the tenant still come first; the bundle fills what the
tenant does not hold (listed as gaps on "Definitionen"). `views.config_source` (`VIEWS_CONFIG_SOURCE`) names it:

| Form | Use |
|---|---|
| `github:<owner>/<repo>@<ref>:<path to bundle.json>` | A deployment. Read with the change requests' GitHub client and token (`KB_GITHUB_TOKEN`, else the file `views.change_requests.github_token_file`, default `/app/secrets/kb_github_token`). The token needs Contents (read) on KnowledgeBase. |
| `file:<path>` | Development and tests: a KnowledgeBase checkout's `bundle.json` (the reports are read beside it). |

The bundle is read again after `views.definitions_ttl_seconds` (default 600 s) and kept by its content hash.
The refresh button reads it at once. A bundle with an unknown `format_version`, or whose `content_hash`
does not match its sections, is refused, and every view shows why.

The Brain Builder reads the same bundle. A workspace started with "Mit der Ontologie des Mandanten" takes the
tenant's node types from the graph and its relations, processes, hierarchies (spines), pages and profiles from
the bundle, because the graph holds no definitions; a type the bundle defines keeps the bundle's definition, the
tenant's hand-made types join it. Mappings, edit sources and node descriptions are not in a bundle and stay
empty. Without a readable bundle (no source set, unreachable, refused) the workspace starts with the types alone,
and its change log says so ("ohne Definitionen").
A workspace that exists takes them later: "Definitionen des Mandanten übernehmen …" on its page shows what the
bundle would add (types, relations, processes with their rules, hierarchies, pages, profiles the workspace lacks;
a type or relation it has gets only the fields it has no value for) and adopts it as one edit, which "Rückgängig"
takes back as a whole. Nothing the workspace has is replaced or removed.

Compose mounts no token file. Put the token into a file outside the checkout and mount it with a local
`docker-compose.override.yml`, never in `knovas.env`. `setup.sh` copies `knovas.env` into the
world-readable `.env.generated`:

```yaml
services:
  docbridge-web:
    volumes:
      - /path/outside/the/checkout/kb_github_token:/app/secrets/kb_github_token:ro
```

### Settings on the settings page (KC-0.1)

An administrator changes the views' settings on **Einstellungen → Ansichten und Wissensbasis** (`/settings`),
without editing `config.yaml` or the environment and without a restart. Only `admin` accounts see the
section; the API behind it (`/api/settings/platform`) answers 403 to anyone else.

| Setting | Key | Pinned by | For |
|---|---|---|---|
| Definitionsbündel | `views.config_source` | `VIEWS_CONFIG_SOURCE` | this tenant |
| Rollen und Profile | `views.profile_roles` | `VIEWS_PROFILE_ROLES` | this tenant |
| Token für die Wissensbasis (GitHub) | `views.change_requests.github_token` | `KB_GITHUB_TOKEN`, or the file `views.change_requests.github_token_file` when it exists | this tenant |
| Lesebudget der Systemkarte | `views.map_fact_budget` | `VIEWS_MAP_FACT_BUDGET` | the installation |
| Gültigkeit des Graph-Abbilds | `views.ttl_seconds` | `VIEWS_TTL_SECONDS` | the installation |
| Gültigkeit der Definitionen | `views.definitions_ttl_seconds` | `VIEWS_DEFINITIONS_TTL_SECONDS` | the installation |
| Kantengrenze | `views.edge_limit` | `VIEWS_EDGE_LIMIT` | the installation |
| Zeitbudget der Kantenliste | `views.edge_budget_seconds` | `VIEWS_EDGE_BUDGET_SECONDS` | the installation |

- **Where a value comes from, shown beside each setting:**
  1. **Installation:** a non-empty environment variable pins the value. The page shows it read-only, with the variable's name. An empty variable does not pin. A token file that exists pins the token.
  2. **Seite:** the value an admin saved, with who saved it and when.
  3. **Standard:** `config.yaml`'s default.

  To hand a setting back to the page, remove its variable from `knovas.env` and restart once.
- **Tests:**
  - "Test" beside the bundle source loads it as the views would. It shows the format version, content hash and counts (types, processes, profiles), or the exact error.
  - "Test" beside the token checks read access to `views.change_requests.github_repo`.
- **Rollen und Profile** is a picker: the Platform's roles against the profiles in the loaded bundle. Without a loaded bundle, the profile names are typed in.
- **Every change is audited** (`settings.changed`: who, which key, old and new value). A token appears there only as "geändert".
- **The token is write-only.** The page says "gesetzt" or "nicht gesetzt" and never shows it.

  It is stored encrypted with **`PLATFORM_SETTINGS_KEY`**, a Fernet key the deployment provides. Without the key, a token cannot be saved and the page says so. Generate the key once and keep it outside every checkout:

  ```bash
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```

  Give it to the Platform as a file, not in `knovas.env` (which `setup.sh` copies into the world-readable `.env.generated`): mount it read-only and set `PLATFORM_SETTINGS_KEY_FILE` in the same local `docker-compose.override.yml`. A token saved on the page then replaces the token-file mount above.

  ```yaml
  services:
    docbridge-web:
      environment:
        PLATFORM_SETTINGS_KEY_FILE: /app/secrets/platform_settings_key
      volumes:
        - /path/outside/the/checkout/platform_settings_key:/app/secrets/platform_settings_key:ro
  ```

  Losing or changing the key makes a saved token unreadable. The page then says "gesetzt, aber mit diesem Schlüssel nicht lesbar"; save the token again.

### Sync von GitHub (KC-0.2)

The Platform syncs the tenant's Brain from its knowledge base on demand: **Einstellungen → Sync von GitHub** (`/sync`,
`admin` accounts only). **Not on the Brain Builder machine:** there the Builder and its sites write the Brain, and this
sync stays off for the tenant (S-6; section 4.6 leaves `BRAIN_SYNC_*` empty). It runs the knowledge base's own mapping runner, **brain-runner**, as an installed package.
The image installs a pinned release; the Platform never imports or runs code from the repository.

**Set-up, once:**

1. **The package in the image.**
   - The Dockerfile installs brain-runner at `BRAIN_RUNNER_GIT_REF` (today `0.3.3`, commit `c618c7bed`, tag
     `brain-runner-v0.3.3` on KnowledgeBase: fetch, plan and apply for the sync and the builder, Brain calls through
     Link, write jobs, `affected` for «bei neuen Commits», and the `records` extractor for record streams). The tag is set by a person on that commit; until it
     exists the pin is the commit itself.
   - KnowledgeBase is private, so the build needs a read-only token: a fine-grained GitHub token for
     `Seifeddini/KnowledgeBase` only, permission **Contents: read**. Put it in `knovas.env` and rebuild:

     ```bash
     KB_READ_TOKEN=github_pat_...          # in knovas.env
     ./scripts/start.sh                     # or: docker compose --env-file knovas.env build docbridge-web
     ```

   - `docker-compose.yml` hands it to the build as the BuildKit secret `kb_read_token`; it lands in no image layer and
     no log. (`docker compose build` has no `--secret` flag; the secret comes from the compose file.)
   - `start.sh` also takes the token from `.env` (it runs compose with `--env-file knovas.env`, which skips `.env`)
     and says after the build whether brain-runner is installed. A layer built without the token is rebuilt once a
     token is given (build argument `KB_READ_TOKEN_GIVEN`; a secret alone does not change the layer cache).
   - Check the build output for `brain-runner 0.3.3`. Without the token the image builds without brain-runner, and the
     sync page and the builder's source test say the package is not installed.
2. **Settings** (Einstellungen → Ansichten und Wissensbasis, or pinned by the environment):

   | Setting | Key | Pinned by | Knovas |
   |---|---|---|---|
   | Repository der Wissensbasis | `brain_sync.repository` | `BRAIN_SYNC_REPOSITORY` | `Seifeddini/KnowledgeBase` (no product default since V1-G4: set it) |
   | Branch | `brain_sync.branch` | `BRAIN_SYNC_BRANCH` | `master` (default) |
   | Pfad zur brain-config.yaml | `brain_sync.config_path` | `BRAIN_SYNC_CONFIG_PATH` | `docs/ModernDocs/client-zero/brain-config.yaml` (no product default since V1-G4: set it) |
   | Lese-Token für den Sync | `brain_sync.github_token` | `BRAIN_SYNC_GITHUB_TOKEN`, or the file `brain_sync.github_token_file` | a fine-grained token: **Contents (read) on that repository only** |

   - The token is write-only on the page and stored encrypted (needs `PLATFORM_SETTINGS_KEY`, above).
   - The **write credential is the tenant's client certificate** (`certs/`, above): the sync writes to the Brain with
     it, as the Platform does everything else.
3. **Storage:** `brain_sync.storage_dir` (`BRAIN_SYNC_STORAGE_DIR`, default `/app/data/brain-sync`). Per tenant it
   holds:
   - the GitHub source's file cache;
   - the runner's state;
   - the fetched configuration;
   - the plans;
   - the outputs of each successful sync (`outputs/<commit>/bundle.json` and `evaluation.json`), and `current.json`
     naming the last one.

   Keep it on the data volume: losing it costs one full fetch, not data.

**Use:**

1. **Neuen Stand holen.** Resolves the branch to a commit and shows its SHA, date and message, and how many files the
   mappings read changed since the last successful sync. Changed configuration files are fetched now; unchanged ones
   are copied from the last fetch.
2. **Probelauf.** In the background, with progress on the page.
   - Fetches the configuration at that commit (only changed files) and plans every mapping through the package with
     its GitHub source.
   - Only the changed files the mappings read are downloaded; the rest comes from the tenant's cache.
   - Shows per mapping:
     - source items and candidates;
     - what would be added, changed, retired, unchanged;
     - drift;
     - whether it is blocked (deletion guard, unreadable sources).
   - Nothing is written to the Brain.
3. **Anwenden.** In the background.
   - Applies exactly the Probelauf's plans with the tenant's certificate, then the package's `evaluate` and `bundle`.
   - **Refused**, and recorded as refused, when:
     - there is no successful Probelauf;
     - the Probelauf was already applied;
     - the branch has moved to another commit since;
     - the tenant (its id or certificate) changed since.

     In each case, run the Probelauf again.
   - One sync per tenant at a time.

After a successful Anwenden, the views read its bundle. `views.config_source` defaults to `sync` (the last successful
sync) once one exists; a value on the page or in `VIEWS_CONFIG_SOURCE` still wins.

**History** (the page, and `brain_sync_runs`): commit, who, when, files fetched, counts per mapping, result,
duration, the plan's sha256. Counts and hashes only, no file names or values. Every run is audited
(`brain_sync.probe`, `brain_sync.apply`) with the outcome `ok`, `denied` (refused) or `error` (failed); the run's
own status is in the entry's detail. The token appears in no response, log or history.

### Brain Builder: review and publish (BB-07, B1)

**Prüfen & Veröffentlichen** compares the draft with the newest published version (an empty model before the first
publish), element by element: types, relations, processes and their rules, sources (with their addresses), mappings,
hierarchies, pages, profiles, the role mapping, access groups, folder rules, and every other part of the package
("Weitere Teile des Pakets"). An imported workspace therefore shows everything the package brought, each element
marked "Paket importiert von …"; the change log's origin row is no longer the whole story.

The **dry run** adds what publish writes beside the ontology: the access groups it creates and the folder rules it
adds on the live tenant (read from the Brain now, never written), and the role mapping it writes into
`views.profile_roles` (against what the setting holds now). It records a digest of all of it, with the published
state it starts from and the ontology plan. An **approval holds that digest** (migration 0023). Publish plans again
and refuses (409) when the fresh digest is not one an approval holds: the draft, the published version, the
tenant's types, groups or rules, or the role mapping changed since. Then run a new dry run and approve again.
Publish writes access groups and folder rules exactly as the approved plan names them.

Self-approval stays allowed (decision of 2026-11-06). The person who imported (or created) the draft may approve it;
the approval is recorded with `own_import` and shown as "Vom Importeur selbst freigegeben", in the activity feed too.
Each publish run keeps an `audit` block: the draft's origin, who imported it, who approved (and whether that was the
importer), who published, and the digest.

For KB-10: before approving an import, read the review page's sections "Zugriffsgruppen", "Ordnerregeln" and
"Rollen und Profile" and the dry run's table "Was das Veröffentlichen außerdem schreibt"; the hand comparison of
`views.profile_roles` the pre-review asked for is what this table shows.

### Brain Builder: sync components and access (steps B13, B14)

**Connector protocol v1** (`/api/connectors/v1`). Sync components enrolled in the builder call out to the Platform
with a control token. Every call must also carry `X-Client-Cert-Fingerprint`: the sha256 of the client certificate,
in hex. The TLS terminator in front of the Platform sets it from the certificate it verified. It must overwrite or
strip any value the client sends, never pass one on.

The Platform refuses calls without the header once a certificate was issued. A deployment with no TLS terminator that
checks client certificates must say so explicitly; the control token alone then authenticates a component.

A site's Knovas Link under DEC-0056 A1 gets no certificate at enrolment. The Platform records its request's fingerprint;
a Knovas admin compares it, signs the request in-cluster and records the signed certificate on the site page
(`connect-a-site.md`). Until then, with the header required, Link's heartbeat is taken but nothing else (403
`certificate_not_recorded`); afterwards every call must carry the recorded certificate's fingerprint (BB-07 B3).

| Setting | config key | env | default |
|---|---|---|---|
| Require the client certificate | `builder.components.require_client_certificate` | `BUILDER_CONNECTOR_REQUIRE_CLIENT_CERT` | `true` |
| The Brain signs a component's own certificate at enrolment. `false` where secure-api cannot sign (production today): a site's Knovas Link then waits for the certificate a Knovas admin confirms and signs (DEC-0056, A1; DEC-0062; `connect-a-site.md`), and a component enrolled directly uses the tenant's certificate, only to test on your own machine | `builder.components.issue_certificates` | `BUILDER_CONNECTOR_ISSUE_CERTIFICATES` | `true` |
| Knovas's client CA (a PEM file, public). Needed where the Brain does not sign at enrolment: a site's Knovas Link learns it at enrolment and installs only a certificate it signed for this tenant, and a site certificate recorded in the Builder must be signed by it. Without it Link installs no certificate (`certificate.no_client_ca`) | `builder.components.client_ca` | `BUILDER_CONNECTOR_CLIENT_CA` | (empty) |
| A component is offline after (minutes) | `builder.components.offline_after_minutes` | `BUILDER_COMPONENT_OFFLINE_MINUTES` | `30` |
| Warn when a component lags behind its source (hours) | `builder.components.lag_hours` | `BUILDER_COMPONENT_LAG_HOURS` | `24` |

The two limits can also be set on the settings page.

**The access window** (`/builder/zugriff`) changes the tenant's access groups through the Brain's
`/secured/access_groups` and folder-rule routes:
- **Moving a group** sends `PATCH /secured/access_groups/<id>` with `parent`. A Brain that cannot move groups yet
  ignores the field. The window reads the tree back and says so.
- **Group grants** with an end date (migration 0018) stop counting the day after they end. The principal assertion
  reads only grants that still run.

### Start

```bash
cd KnovasComponents
mkdir -p data/no-local-documents
./scripts/setup.sh                      # writes the .env.generated files and the DB secret
docker compose --env-file knovas.env up -d --build docbridge-web-nginx
```

`docbridge-web-nginx` depends on `docbridge-web`, which depends on `platform-db`. This starts exactly those three services, and not Knovas Connector. Do not use `./scripts/start.sh` here: it starts the whole stack.

First sign-in:
- **User:** `PLATFORM_ADMIN_EMAIL`.
- **Password:** `PLATFORM_ADMIN_PASSWORD` if set, else the generated one in `PLATFORM_ADMIN_BOOTSTRAP_PATH` (default `/app/data/platform-admin-bootstrap`, in the container).
- The Platform then asks for a new password.

`./scripts/doctor.sh` checks the certificate and whether the tenant's Knowledge Graph answers.

**Custom views (Eigene Ansichten)** stay off here: `CUSTOM_VIEWS_ENABLED` is `false` unless `knovas.env` sets it. View files are trusted code, so switch it on only for folders whose files you wrote or reviewed yourself. The switch, the folder (`CUSTOM_VIEWS_PATH`), the manifest, the limits and the checks are in [custom-views.md](../custom-views.md).

## 2. The views and their addresses

Base address: `http://127.0.0.1:8081` with the compose default (`DOCBRIDGE_WEB_PORT`), or `https://<fqdn>` behind host nginx ([host-nginx-internal.md](host-nginx-internal.md)). The Cortex page (`/ontology`) links to `/views` ("Ansichten").

| View | Address |
|---|---|
| Graph (ego graph of a node) | `/views?kind=graph&node=<node id>` |
| Ablauf (TRACE of an instance) | `/views?kind=trace&process=PROC-feature-lifecycle&node=<node id>` |
| Board | `/views?kind=board&process=PROC-feature-lifecycle` |
| Baum (tree of a spine) | `/views?kind=tree&spine=SPINE-product` |
| Matrix | `/views?kind=matrix&spine=SPINE-product&rows=L3&cols=L5` |
| Liste (one type) | `/views?kind=list&type=FeaturePlan` |
| Lücken (gaps worklist) | `/views?kind=gaps&gaps=PROC-feature-lifecycle` |
| Dokumentation | `/views/doc/<key>`, e.g. `/views/doc/SYS-KNOVAS-SEMANTIX`, `/views/doc/L1-RETRIEVE`, `/views/doc/L2-KNOWLEDGE-GRAPH` |
| Dokumentation as Markdown (for agents) | `/api/views/doc/export?key=<key>` (`&scope=subtree` for everything below) |
| Definitionen | `/views?kind=definitions` |
| Änderungen (the user's change set) | `/views?kind=changes` |
| A saved view | `/views?view=<saved view id>`. The shared views come from the bundle's profiles: an admin creates them once with "Ansichten aus den Profilen anlegen" in the saved-views menu. |
| The CTO's system map | `/views?kind=tree&pview=cto/system-map` |

Without `node`, the Graph and Ablauf tabs open "Startpunkt wählen". Every view shows "Stand HH:MM:SS"; the refresh button bypasses the 60-second cache.

## 3. Development server (what the measurements used)

For a quick look without Docker, run the app directly from `KnovasPlatform/components/docbridge_integration` with its virtual environment. Point the certificate settings at the files where they are kept, never at a copy inside the repository:

```bash
export SEMANTIX_API_URL=https://api.knovas.ch:8443
export SEMANTIX_CUSTOMER_ID=af639810-afc7-403f-8139-f6026d085c99
export SEMANTIX_CLIENT_CERT=<path>/client-cert.pem SEMANTIX_CLIENT_KEY=<path>/client-key.pem SEMANTIX_CA_CERT=<path>/ca-root.pem
export ONTOLOGY_SOURCE=graph
export VIEWS_CONFIG_SOURCE=file:<KnowledgeBase checkout>/docs/ModernDocs/client-zero/generated/bundle.json
# Without accounts (read-only views; no saved views or change sets). Binds to 127.0.0.1 only:
export IDENTITY_ENABLED=false COMPANY_LOGIN_ENABLED=false
# With accounts instead: IDENTITY_ENABLED=true, PLATFORM_DB_DSN=postgresql://…, PLATFORM_ADMIN_EMAIL=…
cd src && python -c "from web_interface.app import create_app; create_app().run(host='127.0.0.1', port=5077)"
```

With accounts, change-set previews can read a local KnowledgeBase checkout: `VIEWS_KB_LOCAL_REPO=<path to the checkout>`, with the `VIEWS_KB_*` paths of "The tenant's knowledge base" above exported too. Creating pull requests needs `KB_GITHUB_TOKEN` (contents and pull requests on KnowledgeBase only).

## 4. Step by step: the Platform with the Brain Builder from a branch

For the Knovas machine outside Azure (KG-1, CL-8): the Platform with accounts and the Brain Builder, from images the
release workflow built from a branch ([release-images.md](release-images.md)), with its own revocable certificate
(S-6), the connectors' client-certificate terminator, Sync von GitHub off, and a tested backup. Every command is
copied as it stands; replace only what is in `<angle brackets>`. **You should see** says what a good result looks
like; **Stop if** says when not to go on.

The values used below:

| Placeholder | What |
|---|---|
| `<branch>` | the KnovasComponents branch to deploy, today `cl/jolly-fermat-ro3vkk` |
| `<commit>` | the full commit the release run built (its summary names it) |
| `<fqdn>` | the Platform's public name on this machine, e.g. a name under knovas.ch with its DNS A record pointing here |
| `<machine>` | how you reach the machine with `ssh` |
| `<platform digest line>` | the line `DOCBRIDGE_WEB_IMAGE=ghcr.io/seifeddini/docbridge-web-clientbundle@sha256:…` from the run's summary |

### 4.0 Before you start

- **Once per repository** ([release-images.md, "Once per repository"](release-images.md#once-per-repository)): the
  workflow file on `main`, the secret `KB_READ_TOKEN`, the variable `CI_EPHEMERAL_RUNNER`, and **before the first
  run** the three packages created private with Actions access, confirmed by the variable `GHCR_PACKAGES_PRIVATE`.
- **Your workstation** (WSL): `gh` (signed in), `kubectl` with the context `knovas-prod-aks`, `jq`, `openssl`, `ssh`/`scp`.
- **The machine:** Ubuntu 24.04, Docker Engine with the compose plugin, and:

  ```bash
  sudo apt-get update && sudo apt-get install -y git jq openssl unzip nginx certbot libnginx-mod-http-js
  docker compose version
  ```

  You should see `Docker Compose version v2…` (or later). Ports 80 and 443 reach the machine; `<fqdn>` resolves to it.
- **Your Knovas admin login** (an employee with the admin flag) for internal-api.
- **The Knovas root CA** `ca-root.pem` (the CA of the Brain's server certificate), from the folder where the tenant
  certificates are kept. It is public; it is not the tenant's certificate.

### 4.1 Build the images from the branch (workstation)

```bash
gh workflow run release-images.yml --repo Seifeddini/KnovasComponents --ref cl/jolly-fermat-ro3vkk -f images=all
sleep 10
gh run list --repo Seifeddini/KnovasComponents --workflow release-images.yml --limit 1
gh run watch <run id from the line above> --repo Seifeddini/KnovasComponents --exit-status
gh run view <run id> --repo Seifeddini/KnovasComponents --web
```

You should see three jobs (`platform`, `knovas-link`, `sync-component`) end green, and in the browser, on the run's
**Summary**, three sections. Write down: the **commit**, the `DOCBRIDGE_WEB_IMAGE=…` line, `KNOVAS_LINK_DIGEST=…` and
`SYNC_COMPONENT_DIGEST=…` (the last two are for section 5).

**Stop if** `gh workflow run` says the workflow is not found (it is not on `main` yet: 4.0), or a job is red: the
summary of [release-images.md, "Run it"](release-images.md#run-it) says which setting is missing.

### 4.2 The checkout at that commit (machine)

The checkout gives `docker-compose.yml`, `config/` and the nginx configuration; they must be of the **same commit** as
the image.

```bash
ssh <machine>
git clone https://github.com/Seifeddini/KnovasComponents.git ~/knovas-platform     # first time only
cd ~/knovas-platform
git fetch origin
git checkout --detach <commit>
git rev-parse HEAD
```

You should see exactly `<commit>`. **Stop if** it differs.

### 4.3 Pull the image and check its signature (machine)

Create a pull credential for this machine: github.com → Settings → Developer settings → Personal access tokens →
**Tokens (classic)** → Generate new token (classic): note `ghcr pull <machine>`, an expiry, scope **`read:packages`**
only. Then:

```bash
docker login ghcr.io -u Seifeddini           # paste the token as the password
curl -sSfL -o /tmp/cosign https://github.com/sigstore/cosign/releases/download/v2.6.1/cosign-linux-amd64
echo "064954c5d8c7e3b28188eee5b1727b31c411550bc5fefd41aa672d3c761d103a  /tmp/cosign" | sha256sum -c -
chmod +x /tmp/cosign
IMG=ghcr.io/seifeddini/docbridge-web-clientbundle@sha256:<the platform digest>
/tmp/cosign verify "$IMG" \
  --certificate-identity "https://github.com/Seifeddini/KnovasComponents/.github/workflows/release-images.yml@refs/heads/<branch>" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-github-workflow-sha <commit> > /dev/null && echo "signature OK"
docker pull "$IMG"
docker run --rm "$IMG" python -c "import brain_runner; print('brain-runner', brain_runner.__version__)"
```

You should see `Login Succeeded`, `/tmp/cosign: OK`, `signature OK`, the pull, and `brain-runner 0.…`.
**Stop if** the signature check fails (do not run that image) or brain-runner is missing (the Builder cannot dry-run
or publish without it).

### 4.4 The Platform's own certificate (S-6)

The Platform gets its own certificate from Knovas's client CA, revocable on its own; the tenant's shared certificate
does not go on this machine.

**a) The request (machine):**

```bash
cd ~/knovas-platform
mkdir -p certs
( umask 077; openssl req -new -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes \
    -keyout certs/client-key.pem -subj "/CN=knovas-platform" -out platform.csr )
openssl req -in platform.csr -noout -subject
```

You should see `subject=CN = knovas-platform`. The key `certs/client-key.pem` never leaves the machine.

**b) An admin signs it, in-cluster only (workstation).** Copy the request over, then open the port-forward in a
second terminal and leave it running:

```bash
scp <machine>:knovas-platform/platform.csr .
kubectl --context knovas-prod-aks -n knovas-prod port-forward deployment/internal-api 5000:5000
```

You should see `Forwarding from 127.0.0.1:5000 -> 5000`. In the first terminal:

```bash
read -rp "Knovas admin email: " KN_EMAIL; read -rsp "Password: " KN_PW; echo
ADMIN_JWT="$(jq -n --arg e "$KN_EMAIL" --arg p "$KN_PW" '{email: $e, password: $p}' \
  | curl -sS -X POST http://localhost:5000/generate_emp_jwt -H 'Content-Type: application/json' -d @- \
  | jq -r '.data.jwt // empty')"; unset KN_PW
[ -n "$ADMIN_JWT" ] && echo "signed in"

jq -n --rawfile csr platform.csr --arg cid af639810-afc7-403f-8139-f6026d085c99 \
  '{csr: $csr, customer_id: $cid, organisation: "Knovas Platform", validity_days: 365}' \
  | curl -sS -X POST http://localhost:5000/sign_certificate \
      -H "Authorization: Bearer $ADMIN_JWT" -H 'Content-Type: application/json' -d @- > platform-signed.json
jq -r '.status, .serial_number, .expires_at, (.error // empty)' platform-signed.json
jq -r .certificate platform-signed.json > platform.crt
jq -r .certificate_chain platform-signed.json | awk '/BEGIN CERTIFICATE/{n++} n>1' > knovas-client-ca.pem
openssl x509 -in platform.crt -noout -subject -serial -enddate
openssl verify -CAfile knovas-client-ca.pem platform.crt
```

You should see `signed in`; then `success`, the serial number and the expiry; a subject with
`OU = Semantix-Customer-af639810-afc7-403f-8139-f6026d085c99` and `CN = knovas-platform`; and `platform.crt: OK`.
**Write down the serial number** (it revokes this certificate). **Stop if** the status is not `success` (a `403` means
your employee has no admin flag; a `404` means the request went through the public address, not the port-forward), or
`openssl verify` does not say `OK` (then the chain is incomplete: do not go on, ask for the client CA's full chain).

`knovas-client-ca.pem` is Knovas's client CA chain (public): the Platform checks site certificates against it and
nginx verifies Knovas Link with it. Copy both back and stop the port-forward (Ctrl+C):

```bash
scp platform.crt <machine>:knovas-platform/certs/client-cert.pem
scp knovas-client-ca.pem <machine>:knovas-platform/certs/knovas-client-ca.pem
```

**c) Check on the machine:**

```bash
cd ~/knovas-platform
cp <the Knovas root CA you keep> certs/ca-root.pem
[ "$(openssl x509 -in certs/client-cert.pem -noout -pubkey | sha256sum)" = \
  "$(openssl pkey -in certs/client-key.pem -pubout | sha256sum)" ] && echo "key and certificate match"
ls certs
```

You should see `key and certificate match` and the four files `ca-root.pem client-cert.pem client-key.pem
knovas-client-ca.pem` (no `client-key.password.txt` or `client-key.plain.pem` from an older certificate: delete them
if they are there).

**Revoking it** (if the machine is lost): an admin, through the same port-forward and `ADMIN_JWT`:

```bash
curl -sS -X POST http://localhost:5000/revoke_certificate -H "Authorization: Bearer $ADMIN_JWT" \
  -H 'Content-Type: application/json' \
  -d '{"certificate_serial_number": "<serial number>", "reason_text": "platform machine retired"}'
```

### 4.5 The settings key (machine)

The Builder keeps its components' token hashes and the sources' secrets encrypted with `PLATFORM_SETTINGS_KEY`;
without it no site can enrol (`no_secrets`). Keep it outside the checkout:

```bash
IMG=ghcr.io/seifeddini/docbridge-web-clientbundle@sha256:<the platform digest>     # as in 4.3
sudo install -d -m 0700 /etc/knovas
docker run --rm "$IMG" python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" \
  | sudo tee /etc/knovas/platform_settings_key > /dev/null
sudo chmod 0600 /etc/knovas/platform_settings_key
cat > ~/knovas-platform/docker-compose.override.yml <<'EOF'
services:
  docbridge-web:
    environment:
      PLATFORM_SETTINGS_KEY_FILE: /app/secrets/platform_settings_key
    volumes:
      - /etc/knovas/platform_settings_key:/app/secrets/platform_settings_key:ro
EOF
```

Losing this key makes every saved secret unreadable: it goes into every backup (4.9).

### 4.6 `knovas.env` (machine)

```bash
cd ~/knovas-platform
mkdir -p data/no-local-documents
nano knovas.env
```

Content (replace the three `<…>`):

```bash
KNOVAS_API_URL=https://api.knovas.ch:8443
KNOVAS_TENANT_ID=af639810-afc7-403f-8139-f6026d085c99
KNOVAS_PLATFORM_URL=https://<fqdn>
KNOVAS_DOCUMENTS_PATH=./data/no-local-documents     # the Knovas Connector is never started here
PLATFORM_ADMIN_EMAIL=<your address>
COMPOSE_PROFILES=
ONTOLOGY_SOURCE=graph

# The released image (4.1). Never build while it is set.
<platform digest line>

# The tenant's knowledge base, for the views ("The tenant's knowledge base" above)
VIEWS_KB_GITHUB_REPO=Seifeddini/KnowledgeBase
VIEWS_KB_DECISIONS_DIR=docs/ModernDocs/client-zero/ontology/decisions
VIEWS_KB_PLAN_LINKS_PATH=docs/ModernDocs/client-zero/ontology/plan-links.yaml
VIEWS_KB_SAMPLE_EDGES_PATH=docs/ModernDocs/client-zero/ontology/sample.yaml

# Sync von GitHub (KC-0.2) stays OFF for this tenant (S-6): the Builder and its sites are the Brain's writer.
# Leave both empty here, and never fill them on the settings page.
BRAIN_SYNC_REPOSITORY=
BRAIN_SYNC_CONFIG_PATH=

# Brain Builder sites (DEC-0056 A1, S-6): production's Secure API does not sign at enrolment; a Knovas admin signs
# each site's request. Link learns the client CA at enrolment; nginx (4.8) verifies Link with the same file.
BUILDER_CONNECTOR_ISSUE_CERTIFICATES=false
BUILDER_CONNECTOR_CLIENT_CA=/app/certs/knovas-client-ca.pem
BUILDER_CONNECTOR_REQUIRE_CLIENT_CERT=true
```

`KB_READ_TOKEN` is **not** needed here: the image comes built. `WEB_SESSION_COOKIE_SECURE` stays unset (HTTPS).

### 4.7 Start (machine)

```bash
cd ~/knovas-platform
./scripts/setup.sh
docker compose --env-file knovas.env pull docbridge-web
docker compose --env-file knovas.env up -d --no-build docbridge-web-nginx
docker compose --env-file knovas.env ps
docker inspect --format '{{.Config.Image}}' "$(docker compose --env-file knovas.env ps -q docbridge-web)"
./scripts/doctor.sh
```

`setup.sh` may ask for your sudo password (it hands the certificate files to the Connector's user). You should see
`Setup complete.`, then `platform-db`, `docbridge-web` and `docbridge-web-nginx` running (`healthy` after a minute),
and the image line printing `ghcr.io/seifeddini/docbridge-web-clientbundle@sha256:<the platform digest>`. In
doctor.sh's output look for `OK  /secured/graph — Knowledge Graph is enabled`; its lines about Knovas Connector,
documents, the share and the image's build time do not apply here (the Connector is never started, the image was not
built on this machine). **Never** use `./scripts/start.sh` or `--build` here:
both build an image instead of running the released one, and start.sh starts the Knovas Connector.
**Stop if** `docbridge-web` restarts in a loop: `docker compose --env-file knovas.env logs --tail 50 docbridge-web`.

### 4.8 HTTPS and the connectors' terminator (machine)

```bash
cd ~/knovas-platform
sudo ./scripts/host-https.sh <fqdn> <your address> certs/knovas-client-ca.pem
curl -sS -o /dev/null -w '%{http_code}\n' https://<fqdn>/health
```

The third argument switches on the client-certificate terminator: nginx verifies a site's certificate against
Knovas's client CA and hands its SHA-256 to the Platform (`X-Client-Cert-Fingerprint`), which
`BUILDER_CONNECTOR_REQUIRE_CLIENT_CERT=true` needs. Later runs of host-https.sh keep it. You should see
`https://<fqdn> answers` and `200`. **Stop if** the script says `needs nginx's njs module`
(`sudo apt-get install -y libnginx-mod-http-js`, then again).

### 4.9 First sign-in and the checks (browser)

```bash
docker compose --env-file knovas.env exec docbridge-web cat /app/data/platform-admin-bootstrap
```

Sign in at `https://<fqdn>` as `PLATFORM_ADMIN_EMAIL` with that password, set a new one, then delete the file
(`docker compose --env-file knovas.env exec docbridge-web rm /app/data/platform-admin-bootstrap`). Check:

- **Einstellungen → Sync von GitHub** says "Sync nicht eingerichtet: repository fehlt" (KC-0.2 is off).
- **Einstellungen → Ansichten und Wissensbasis:** the token field does not say the key is missing.
- **`https://<fqdn>/builder`** opens the Brain Builder.

### 4.10 Backup of the Builder's store, and the one restore test (machine)

The Builder's store is the Platform's PostgreSQL (`platform-db`, database `knovas_platform`): workspaces, versions,
approvals, sites, components, the settings (encrypted secrets), accounts and grants. Beside it: the app's data volume
(the published bundle the views read, the broker key) and the settings key.

**Backup** (before every update, and daily):

```bash
cd ~/knovas-platform
B=~/knovas-backups/$(date +%Y%m%d-%H%M); mkdir -p "$B"; chmod 700 ~/knovas-backups "$B"
docker compose --env-file knovas.env exec -T platform-db pg_dump -U platform -d knovas_platform --format=custom > "$B/platform-db.dump"
docker compose --env-file knovas.env exec -T docbridge-web tar -C /app -czf - data secrets/broker > "$B/docbridge-app.tgz"
sudo cp /etc/knovas/platform_settings_key "$B/" && sudo chown "$USER" "$B/platform_settings_key"
cp knovas.env docker-compose.override.yml "$B/"
git rev-parse HEAD > "$B/commit"
ls -l "$B"
```

You should see `platform-db.dump` and `docbridge-app.tgz` larger than zero. Copy the folder off the machine
(`scp -r <machine>:knovas-backups/<folder> .` onto encrypted storage): it holds the settings key and the database whose
secrets it opens.

**The restore test** (once now, and after any change to the backup): restore into a scratch database beside the live
one and compare the Builder's counts. Nothing live is touched.

```bash
Q="select 'workspaces', count(*) from builder_workspace union all select 'versions', count(*) from builder_version union all select 'sites', count(*) from builder_site union all select 'components', count(*) from builder_component union all select 'settings', count(*) from platform_settings union all select 'users', count(*) from users"
docker compose --env-file knovas.env exec -T platform-db createdb -U platform restore_check
docker compose --env-file knovas.env exec -T platform-db pg_restore -U platform -d restore_check --no-owner --exit-on-error < "$B/platform-db.dump" && echo restored
for db in knovas_platform restore_check; do echo "== $db"; docker compose --env-file knovas.env exec -T platform-db psql -U platform -d "$db" -Atc "$Q"; done
docker compose --env-file knovas.env exec -T platform-db dropdb -U platform restore_check
tar -tzf "$B/docbridge-app.tgz" | head
```

You should see `restored`, then the same six counts for both databases (if nothing changed since the backup), and
the archive listing `data/…` and `secrets/broker/…`. Write the date and the counts into the operations log.
**Stop if** pg_restore prints an error or a count differs: the backup is not usable.

**A real restore** (the machine's database is lost or wrong), from a backup folder `$B`:

```bash
docker compose --env-file knovas.env stop docbridge-web-nginx docbridge-web
docker compose --env-file knovas.env exec -T platform-db dropdb -U platform knovas_platform
docker compose --env-file knovas.env exec -T platform-db createdb -U platform knovas_platform
docker compose --env-file knovas.env exec -T platform-db pg_restore -U platform -d knovas_platform --no-owner --exit-on-error < "$B/platform-db.dump"
docker compose --env-file knovas.env run --rm --no-deps -T docbridge-web tar -C /app -xzf - < "$B/docbridge-app.tgz"
sudo install -m 0600 "$B/platform_settings_key" /etc/knovas/platform_settings_key
docker compose --env-file knovas.env up -d --no-build docbridge-web-nginx
```

### 4.11 Update to a newer build of a branch

1. Run 4.1 again (`-f images=platform`, or `all` when the site moves too) on the branch; note the new commit and line.
2. Back up (4.10).
3. On the machine:

   ```bash
   cd ~/knovas-platform
   git fetch origin && git checkout --detach <new commit> && git rev-parse HEAD
   nano knovas.env                      # replace the DOCBRIDGE_WEB_IMAGE line with the new one
   ./scripts/setup.sh
   docker compose --env-file knovas.env pull docbridge-web
   docker compose --env-file knovas.env up -d --no-build docbridge-web-nginx
   docker inspect --format '{{.Config.Image}}' "$(docker compose --env-file knovas.env ps -q docbridge-web)"
   echo "$(date -Is) <new commit> <new digest>" >> ~/knovas-deployments.log
   ```

   You should see the new commit, the new digest, and the services healthy again. The Platform applies new database
   migrations by itself when it starts.

### 4.12 Roll back

Take the previous commit and digest from `~/knovas-deployments.log` (or the earlier run's summary). First check
whether the newer build changed the database:

```bash
git diff --stat <previous commit> <current commit> -- KnovasPlatform/components/docbridge_integration/src/identity/migrations/
```

- **No files listed:** check out the previous commit, put its `DOCBRIDGE_WEB_IMAGE` line back, then `./scripts/setup.sh`,
  `pull`, `up -d --no-build docbridge-web-nginx` as in 4.11.
- **Migrations listed:** migrations only go forward. Do the same, and before the `up`, the real restore of 4.10 from the
  backup taken before the update. What was done in the Builder since that backup is lost.

### 4.13 A Platform without the Brain Builder (other clients)

The same image serves a client that does not use the Builder yet. There is no switch that removes the Builder: it
opens for `admin` and the Builder roles only. For such a client:

- leave all `BUILDER_CONNECTOR_*` lines out, create no site, give nobody a Builder role;
- run `host-https.sh` **without** the third argument (no terminator; the header is always stripped);
- no Knovas Link and no sync component on its machine (section 5 does not apply), and `release-images` with
  `-f images=platform` is enough;
- `PLATFORM_SETTINGS_KEY` only if its admins save tokens on the settings page; Sync von GitHub as that client needs.

## 5. Step by step: the Knovas site (Knovas Link and the sync component)

On the same machine as the Platform (CL-8). Generic reference: [../connect-a-site.md](../connect-a-site.md).

### 5.0 Before you start

- Section 4 is done: the Platform runs with `BUILDER_CONNECTOR_CLIENT_CA` and the terminator (4.8). Link learns the
  client CA and the tenant when it enrols, so this must be in place **before** 5.2.
- From the release run (4.1): `KNOVAS_LINK_DIGEST=sha256:…` and `SYNC_COMPONENT_DIGEST=sha256:…`, best from the same
  run as the Platform.
- In the Builder you hold «Zugänge verwalten» (admin has it).
- **The sync-component image carries brain-runner 0.3.2 or later.** In the release run (4.1), the sync-component job's
  step "Build and check the image" prints `brain-runner <version>`. **Stop if** it prints an older version: 0.3.1 has
  no `affected --source github`, so «bei neuen Commits» plans every mapping on each new commit; 0.1.1 (the pin before
  2026-11-07) can neither reach the Brain through Link (`BRAIN_LOCAL_TOKEN`) nor compile a write (`compile-write`).
  Both Dockerfiles pin 0.3.3 (`BRAIN_RUNNER_GIT_REF=c618c7bed…`, and the build stops on another version); release from
  a checkout that has that pin (Brain v1 plan, step 8; the sync component's README, "Release coupling").

### 5.1 Create the site and download the package (browser)

Builder → **Verbinden** (the «Standorte» list) → **Standort anlegen**: name `knovas-hq`, responsible person, tick
**GitHub** → Standort anlegen. On the site page: **Installationspaket herunterladen**. The zip holds
`docker-compose.yml`, `knovas-link.env` (the Platform's address and a one-time token, 24 hours, used once) and
`knovas-sources.env` (the GitHub tokens: only the sync component reads it; the Jira credential never goes there, it
has its own file, 5.7). Every download makes a new token and voids the one before.

### 5.2 Install it (machine)

```bash
scp ~/Downloads/knovas-site-knovas-hq.zip <machine>:      # from your workstation
ssh <machine>
mkdir -p ~/knovas-site && cd ~/knovas-site
unzip -o ~/knovas-site-knovas-hq.zip && rm ~/knovas-site-knovas-hq.zip
grep LINK_PLATFORM_URL knovas-link.env
cat > .env <<'EOF'
KNOVAS_LINK_DIGEST=sha256:<from the release run>
SYNC_COMPONENT_DIGEST=sha256:<from the release run>
EOF
cat > docker-compose.override.yml <<'EOF'
# The package names its images by digest without a registry; they come from GHCR (release-images.md).
services:
  knovas-link:
    image: ghcr.io/seifeddini/knovas-link@${KNOVAS_LINK_DIGEST:?KNOVAS_LINK_DIGEST in .env}
  sync-component:
    image: ghcr.io/seifeddini/sync-component@${SYNC_COMPONENT_DIGEST:?SYNC_COMPONENT_DIGEST in .env}
EOF
docker compose config --images
docker compose pull
docker compose up -d
docker compose ps
docker compose logs --tail 20 knovas-link
```

You should see `LINK_PLATFORM_URL=https://<fqdn>/api/connectors/v1`, the two images
`ghcr.io/seifeddini/knovas-link@sha256:…` and `ghcr.io/seifeddini/sync-component@sha256:…`, both containers running,
and in Link's log `enrol.done, certificate none` and `certificate.waiting: …`. The machine is already logged in to
GHCR (4.3). To check the signatures, run the `cosign verify` of 4.3 with each image.
**Stop if** the log says `enrol.refused` (the token was used or is older than 24 hours: download the package again) or
`enrol.unreachable` (Link does not reach `<fqdn>`: `curl -sS https://<fqdn>/health` on the machine).

### 5.3 The site's certificate (DEC-0056 A1)

**a) The request (machine):**

```bash
cd ~/knovas-site
docker compose exec -T knovas-link python -m knovas_link certificate-request > site.csr
docker compose exec -T knovas-link python -m knovas_link certificate-fingerprint
openssl req -in site.csr -noout -subject
```

You should see a fingerprint (64 hex characters) **equal to** the one the site page shows under «Zertifikat des
Standorts», and `subject=CN = knovas-link`. **Stop if** they differ.

**b) An admin compares and signs, in-cluster only (workstation),** with the port-forward and `ADMIN_JWT` of 4.4 b:

```bash
scp <machine>:knovas-site/site.csr .
openssl req -in site.csr -noout -pubkey | openssl pkey -pubin -outform DER | sha256sum
jq -n --rawfile csr site.csr --arg cid af639810-afc7-403f-8139-f6026d085c99 \
  '{csr: $csr, customer_id: $cid, organisation: "Knovas Site knovas-hq", validity_days: 365}' \
  | curl -sS -X POST http://localhost:5000/sign_certificate \
      -H "Authorization: Bearer $ADMIN_JWT" -H 'Content-Type: application/json' -d @- > site-signed.json
jq -r '.status, .serial_number, .expires_at, (.error // empty)' site-signed.json
jq -r .certificate site-signed.json > site.crt
openssl verify -CAfile knovas-client-ca.pem site.crt
```

Sign only if the first line's hash equals the site page's fingerprint. You should see `success`, the serial (write it
down: revoking it stops this site only) and `site.crt: OK`.

**c) Record it in the Builder (browser):** the site page → **Zertifikat erfassen** → paste the whole content of
`site.crt` (`cat site.crt`, from `-----BEGIN CERTIFICATE-----` to `-----END CERTIFICATE-----`) → save. You should see
it accepted. It is refused, with the reason, if it is not for this site's request, not for this tenant, not for client
authentication, not valid now, or not signed by `knovas-client-ca.pem`.

**d) Install it (machine):**

```bash
scp site.crt <machine>:knovas-site/site.crt            # from your workstation
cd ~/knovas-site
docker compose exec -T knovas-link python -m knovas_link install-certificate < site.crt
docker compose logs --tail 20 knovas-link
```

You should see `certificate.installed: serial …`, and within 30 seconds `certificate.picked_up: Link uses its own
certificate from now on`. **Stop if** it prints a code such as `certificate.no_client_ca` or `certificate.wrong_tenant`
(the reasons are in [../connect-a-site.md](../connect-a-site.md)); `certificate.no_client_ca` means Link enrolled before
`BUILDER_CONNECTOR_CLIENT_CA` was set: on the site page «Token erneuern», download the package again and repeat from 5.2.

**e) Check (browser):** the site page's checklist: Paket installiert ✓, Zertifikat ✓, Knovas Link online ✓; press
**Jetzt prüfen**, then Arbeitskanal geprüft ✓. Betrieb → Verbindung shows the site green (a probe within the last 10
minutes), the component green, and the versions. If Link stays red with `certificate_required` or `bad_certificate`
in its log, the terminator is not in place: run 4.8 again with the third argument.

### 5.4 GitHub tokens for the sources (when the sources are added, phase 5)

Two fine-grained tokens (github.com → Settings → Developer settings → Personal access tokens → Fine-grained tokens →
Generate new token), resource owner `Seifeddini`, **Only select repositories**, an expiry:

| Token | Repositories | Repository permissions |
|---|---|---|
| read | the repositories whose sources only read | **Contents: Read-only** (Metadata: read-only is added by itself) |
| write | only the repository of a source that writes back | **Contents: Read and write**; **Pull requests: Read and write** only where that source's changes arrive as pull requests (protocol 1.x write jobs, or staging `native_review`). A `direct` source commits on its branch, so that branch's protection must let the token push |

Which source writes back, and into which repository and branch, is the tenant's decision and changes with it; this
table states only what each token needs. For each source, the Builder shows the exact line for `knovas-sources.env`
(`SYNC_CREDENTIAL_<KEY>=`, with «Kopieren»). Put the write token behind the line of the source that writes back and
the read token behind the others' lines (never `SYNC_CREDENTIAL_GITHUB`, which every GitHub source would use), then:

```bash
cd ~/knovas-site
nano knovas-sources.env
docker compose up -d --force-recreate sync-component
```

and «Verbindung prüfen» on each source. No token is ever typed into the Platform.

### 5.5 Update and roll back the site

Update: put the new digests into `~/knovas-site/.env`, then `docker compose pull && docker compose up -d`. Link and
the component keep their data volumes, so no new certificate is needed. Roll back: the previous digests, the same two
commands. Back up nothing here beyond the volumes' presence: Link's key is in `knovas-site_link-data`; losing it means
«Token erneuern» and a new certificate (5.2–5.3).

### 5.6 Sync on new commits, and apply on its own (KG-2)

Needs an image release from a checkout with this change: Knovas Link 1.2.0 and the sync component 0.3.0 (its page
shows the version), brain-runner 0.3.2 in the sync-component image (5.0), and the Platform built from the same
checkout. Then 5.5 with the new digests.

1. **The schedule.** Builder → Verbinden → the site → the sync component's page (`knovas-hq · sync-component`) →
   «Zeitplan» → **Bei neuen Commits**, «Alle … Sekunden auf neue Commits prüfen» `30` → «Zeitplan übernehmen». It
   reaches the component with Link's next heartbeat (30 s). Every 30 s the component asks GitHub for each source's
   head commit; when nothing is new, nothing else happens. When a commit is new, only the mappings it concerns are
   planned, and the dry run shows on the component page as any other. Knovas Link's own page has no schedule any more:
   each component has its own.
2. **Apply on its own, per source** (admin, «Zugänge verwalten»). The site page → «Quellen und Abgleich» → tick
   **Automatisch anwenden** for a source → confirm. From the next plan on, a plan that holds nothing a person must
   decide is applied right after it is made. A plan with a deletion-guard hold, drift, a retire entry, a release, or a
   stopped or reported document is not: it waits under «Zurückgehaltene Pläne» / «Jetzt anwenden» on the component
   page, as before, and the site page says why («Neuer Commit … geplant, noch nicht angewendet: …»). Off by default;
   untick to stop. Every change is an event on the component (`auto_apply.on` / `auto_apply.off`) and a line in the
   access log.
3. **What to look at.** The site page's «Quellen und Abgleich» (and the «Standorte» list on «Verbinden») shows per
   source the last synced commit (short, linked to the commit on GitHub), when, what the last apply wrote
   (+ added / ~ changed / − retired, errors, «automatisch»), the next check and the schedule. The synced commit moves
   only after an apply without errors.

**Check it end to end:** push a commit that changes one file a mapping reads (for example one card in
KnowledgeBase); within a minute the component page shows a dry run of only the mappings that file concerns, then, with
the switch on and nothing to decide, an apply marked «automatisch», and the site page shows the new commit.


### 5.7 Protocol 2 at the site: write back to the brain repository, and the Jira connector (FEAT-2026-11-10-write-back)

Generic reference: [../connect-a-site.md](../connect-a-site.md) § 6. Everything below is off until you set it; each
part can be turned back by removing its line and `up -d` again.

**0. Images.** One release run from the branch with `images=all` (4.1): it now builds four images. Note the four lines
of its summary (`DOCBRIDGE_WEB_IMAGE=…`, `KNOVAS_LINK_DIGEST=…`, `SYNC_COMPONENT_DIGEST=…`, `JIRA_CONNECTOR_DIGEST=…`).
The first run that pushes `jira-connector` needs that package created private first (release-images.md, "Once per
repository", step 4, with `jira-connector` only).

**1. The Platform** (4.11 with the new platform line; the image carries migration 0025, applied at start). Add to
`knovas.env` before `./scripts/setup.sh`:

```bash
BUILDER_PROTOCOL_V2=true
```

**2. The site** (machine), new digests and the protocol 2 switches:

```bash
cd ~/knovas-site
cp ~/knovas-platform/KnovasConnectorJira/deploy/docker-compose.jira.yml .   # from the checkout at the release commit
nano .env      # the three digests from step 0, and:
#   COMPOSE_FILE=docker-compose.yml:docker-compose.override.yml:docker-compose.jira.yml
#   JIRA_CONNECTOR_IMAGE=ghcr.io/seifeddini/jira-connector
#   JIRA_CONNECTOR_DIGEST=sha256:<from step 0>
nano docker-compose.override.yml   # under sync-component add:  environment: {SYNC_PROTOCOL_V2: "1"}
touch knovas-jira.env && chmod 600 knovas-jira.env   # the Jira connector's own file (step 4 fills it); compose needs it
grep -c SYNC_CREDENTIAL_JIRA knovas-sources.env   # must print 0; move a Jira line from there to knovas-jira.env
docker compose config --images
docker compose pull && docker compose up -d
docker compose ps
docker compose logs --tail 20 knovas-link jira-connector sync-component
```

You should see four images by digest, four containers running, and in the logs `site.registered` (jira-connector) and
`registered with Knovas Link` (sync-component); Link logs `v2.site_declared, connectors 2` once secure-api takes site
declarations (the KnowledgeBase release with protocol 2; before that `v2.site_not_declared`, harmless). `docker-compose.jira.yml`
already sets `LINK_PROTOCOL_V2=true` and `LINK_COMPONENTS=jira-connector` on knovas-link. **Stop if** jira-connector
logs `Knovas Link refused this component's token`: Link was not recreated with `LINK_COMPONENTS` (`docker compose up -d
--force-recreate knovas-link jira-connector`).

**3. The brain repository's source on protocol 2 (C-5).** The source's write token needs **Contents: write** and the
target branch's protection must let it push (5.4). Builder → Betrieb → Freigaben: settle every open proposal of that
source first. Then Quellen & Zuordnung → the source → «Änderungen dürfen zurückgeschrieben werden» on, «Aktionen der
Komponente»: **direkt nach der Freigabe in Knovas** → Prüfen & Veröffentlichen. Reading, «bei neuen Commits» and
automatic apply stay as in 5.6.

**4. The Jira source.** Builder → Quellen & Zuordnung → Quelle hinzufügen → «Von einem Connector am Standort
angemeldet»: kind `jira`, the site's component `jira-connector`, key (for example `jira-pilot`), settings `site`
`https://<site>.atlassian.net` and `project` `<project key>` → create. In `~/knovas-site/knovas-jira.env` (the
Builder shows the line and names this file; never `knovas-sources.env`, which the sync component reads and whose
sources' addresses the Platform names: verifier KC-3 N5):

```bash
SYNC_CREDENTIAL_JIRA_PILOT=<account email>:<API token>
SYNC_CREDENTIAL_JIRA_PILOT_HOST=<site>.atlassian.net
```

then `docker compose up -d --force-recreate jira-connector`, Prüfen & Veröffentlichen, «Verbindung prüfen» on the
component: `source.ok`. To write back: «Änderungen dürfen zurückgeschrieben werden» on the source, publish again.

**5. The record stream for the Brain mapping** (machine; the KnowledgeBase side's `brain_mapping_local.sh jira` reads
this file):

```bash
cd ~/knovas-site
docker compose run --rm -T jira-connector dump-stream --source jira-pilot --out - > jira-pilot.jsonl
```

You should see `dump-stream: <n> records, generation g-…, <n+2> messages` and exit code 0. **Stop if** it prints a
code instead (`jira.unauthorized`: the token; `jira.no_credential`: the two lines; `dump.unknown_source`: the source is
not published on this component yet).
