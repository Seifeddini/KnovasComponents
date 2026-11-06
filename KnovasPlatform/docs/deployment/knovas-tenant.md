# The Platform for the Knovas tenant (Ansichten)

How to run the Platform against Knovas' own tenant, `af639810-afc7-403f-8139-f6026d085c99`, so that the
process, structure and documentation views ("Ansichten") show the company's own brain. The views are
read-only on the brain. The views themselves are described in
[`docs/superpowers/plans/2026-10-27-process-and-spine-views.md`](../../../docs/superpowers/plans/2026-10-27-process-and-spine-views.md)
and measured in [`docs/superpowers/reports/2026-10-27-views-final-report.md`](../../../docs/superpowers/reports/2026-10-27-views-final-report.md).

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
`admin` accounts only). It runs the knowledge base's own mapping runner, **brain-runner**, as an installed package.
The image installs a pinned release; the Platform never imports or runs code from the repository.

**Set-up, once:**

1. **The package in the image.**
   - The Dockerfile installs brain-runner at `BRAIN_RUNNER_GIT_REF` (today `0.1.1`, commit `43ea868e`, tag
     `brain-runner-v0.1.1` on KnowledgeBase: fetch for the sync, ontology plan and apply for the builder).
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
   - Check the build output for `brain-runner 0.1.1`. Without the token the image builds without brain-runner, and the
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

| Setting | config key | env | default |
|---|---|---|---|
| Require the client certificate | `builder.components.require_client_certificate` | `BUILDER_CONNECTOR_REQUIRE_CLIENT_CERT` | `true` |
| The Brain signs a component's own certificate at enrolment. `false` where secure-api cannot sign (production today): a site's Knovas Link then waits for the certificate a Knovas admin confirms and signs (DEC-0056, A1; DEC-0062; `connect-a-site.md`), and a component enrolled directly uses the tenant's certificate, only to test on your own machine | `builder.components.issue_certificates` | `BUILDER_CONNECTOR_ISSUE_CERTIFICATES` | `true` |
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
