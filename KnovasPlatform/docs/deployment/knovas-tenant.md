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

# Plain HTTP on 127.0.0.1 only (no TLS in front): otherwise the session cookie is dropped.
WEB_SESSION_COOKIE_SECURE=false
```

`IDENTITY_ENABLED` stays unset (on): saved views, the remembered selection, change sets and their audit need accounts. Every key `setup.sh` does not consume (`ONTOLOGY_SOURCE`, `VIEWS_*`, `WEB_SESSION_COOKIE_SECURE`) is passed through to the Platform unchanged. The tunable view settings and their defaults are in `KnovasPlatform/components/docbridge_integration/config/config.yaml` under `views:`.

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
   - KnowledgeBase is private, so the build needs a read-only token as a BuildKit secret, kept outside every checkout:

     ```bash
     docker compose build --secret id=kb_read_token,src=/path/outside/the/checkout/kb_read_token docbridge-web
     ```

   - Without the secret the image builds without brain-runner, and the sync page says the package is not installed.
2. **Settings** (Einstellungen → Ansichten und Wissensbasis, or pinned by the environment):

   | Setting | Key | Pinned by | Knovas |
   |---|---|---|---|
   | Repository der Wissensbasis | `brain_sync.repository` | `BRAIN_SYNC_REPOSITORY` | `Seifeddini/KnowledgeBase` (default) |
   | Branch | `brain_sync.branch` | `BRAIN_SYNC_BRANCH` | `master` (default) |
   | Pfad zur brain-config.yaml | `brain_sync.config_path` | `BRAIN_SYNC_CONFIG_PATH` | `docs/ModernDocs/client-zero/brain-config.yaml` (default) |
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
(`brain_sync.probe`, `brain_sync.apply`). The token appears in no response, log or history.

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

With accounts, change-set previews can read a local KnowledgeBase checkout: `VIEWS_KB_LOCAL_REPO=<path to the checkout>`. Creating pull requests needs `KB_GITHUB_TOKEN` (contents and pull requests on KnowledgeBase only).
