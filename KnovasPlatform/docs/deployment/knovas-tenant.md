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

# The tenant's definitions bundle (see "The definitions bundle" below). Without it every view
# answers "Definitionsbündel: Kein Definitionsbündel konfiguriert".
VIEWS_CONFIG_SOURCE=github:Seifeddini/KnowledgeBase@master:docs/ModernDocs/client-zero/generated/bundle.json
# Who sees which profile views: the CTO profile for admins, the engineer's for members
# (component README, "Profile views").
VIEWS_PROFILE_ROLES=admin=CTO; member=Engineer

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
