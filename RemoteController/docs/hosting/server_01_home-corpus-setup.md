# RemoteController setup on server_01_home (corpus)

Complete step-by-step record for configuring **RemoteController** on `server_01_home` to ingest the **KnovasInternal corpus** into Knovas.

**Server:** `server_01_home` → `192.168.1.16`, user `master`  
**Monorepo path:** `/home/master/KnovasInternal`  
**Corpus path:** `/home/master/KnovasInternal/corpus` (~91 MB, 3,515 files, mostly `.txt`)  
**RC URL (internal):** `http://127.0.0.1:5001` (localhost only)

---

## What was already on the server

Before this setup, the following existed:

| Item | Location | Notes |
|------|----------|-------|
| Knovas monorepo | `/home/master/KnovasInternal` | Cloned from GitHub |
| Corpus | `/home/master/KnovasInternal/corpus` | 8 subfolders: `court_decisions_ch`, `court_decisions_de`, `emails_synthetisch`, `eu_recht`, `gesetze_ch`, `gesetze_de`, `synthetisch`, `wikipedia_de` |
| Tenant mTLS certs | `/home/master/KnovasInternal/certs/` | `client-cert.pem`, `client-key.pem`, `ca-root.pem` (optional `client-key.password.txt`) |
| KnovasPlatform | `/home/master/KnovasInternal/KnovasPlatform` | Search UI running on `:8081` |
| RemoteController | `/home/master/KnovasInternal/RemoteController` | Present but **not running**; `.env` had placeholder values |

**Organisation / tenant UUID** (set `RC_CLIENT_ID` in `.env`; optional `certs/organisation_id.txt`):

```
8f8e65bf-6fcd-4a75-9814-d859b6a64591
```

**Knovas API endpoints** (reachable from server):

| API | URL | Verified |
|-----|-----|----------|
| Internal API | `http://api.knovas.ch:8080` | `GET /health` → `healthy` |
| Secured API (mTLS) | `https://api.knovas.ch:8443` | `GET /secured/health` with tenant cert → success |

---

## Step 1 — Connect to the server

From Windows, use WSL SSH (host alias already in `~/.ssh/config`):

```bash
# ~/.ssh/config
Host server_01_home
    HostName 192.168.1.16
    User master
```

Connect:

```bash
wsl ssh server_01_home
# password: (provided separately — do not commit to git)
```

Verify basics:

```bash
hostname          # server01
whoami            # master
uname -a          # Ubuntu 6.8.x
docker --version  # Docker 29.x
groups            # includes docker
```

---

## Step 2 — Inspect the corpus

```bash
cd /home/master/KnovasInternal
ls -la corpus/
du -sh corpus/
find corpus -type f | wc -l
```

Expected:

- **~91 MB** total size
- **~3,515** files ( **~2,515** are `.txt` )
- Subdirectories listed above

Sample file:

```bash
head -3 corpus/wikipedia_de/Pleite.txt
```

---

## Step 3 — Inspect existing RemoteController state

```bash
cd /home/master/KnovasInternal/RemoteController
ls -la
cat .env
docker compose ps
```

**Problems found in the original `.env`:**

| Variable | Original value | Problem |
|----------|----------------|---------|
| `RC_WATCH_ROOTS` | `../corpus` | Relative path — invalid inside Docker container |
| `RC_CLIENT_ID` | `00000000-0000-0000-0000-000000000001` | Placeholder, not your tenant UUID |
| `RC_INSTANCE_TOKEN` | `change-me-from-knovas-admin` | Placeholder — **still required from Knovas admin** |
| `SEMANTIX_*_PATH` | `../semantix-certs/...` | Relative paths — invalid inside container |
| Certs mount | `RemoteController/certs/` empty | Tenant certs were only in monorepo root |

---

## Step 4 — Tenant mTLS certs (`~/KnovasInternal/certs`)

Docker Compose mounts **`../certs`** (monorepo root) → **`/certs`** in the container. Paths in `.env` must use **container paths** (`/certs/client-cert.pem`, etc.).

```bash
cd /home/master/KnovasInternal/RemoteController
chmod +x scripts/install_tenant_certs.sh
./scripts/install_tenant_certs.sh
```

If the key is encrypted, the script creates `client-key.plain.pem` — set `SEMANTIX_CLIENT_KEY_PATH=/certs/client-key.plain.pem` in `.env`.

Verify mTLS from the container:

```bash
docker compose -f docker-compose.yml -f docker-compose.internal.yml exec remote-controller \
  python3 -c "import requests; from config import get_config; c=get_config(); r=requests.get(c.semantix_secure_base_url+'/secured/health', cert=(c.semantix_client_cert_path,c.semantix_client_key_path), verify=c.semantix_ca_cert_path, timeout=30); print(r.status_code, r.text[:200])"
```

Expected: JSON with `"healthy": true`.

---

## Step 5 — Configure `.env` for Docker

Edit `/home/master/KnovasInternal/RemoteController/.env`:

```env
# Required
KNOVAS_INTERNAL_API_URL=http://api.knovas.ch:8080
RC_INSTANCE_TOKEN=change-me-from-knovas-admin   # ← replace with real token from Knovas admin
RC_CLIENT_ID=8f8e65bf-6fcd-4a75-9814-d859b6a64591
RC_WATCH_ROOTS=/data/corpus

SEMANTIX_SECURE_BASE_URL=https://api.knovas.ch:8443
SEMANTIX_CLIENT_CERT_PATH=/certs/client-cert.pem
SEMANTIX_CLIENT_KEY_PATH=/certs/client-key.pem
SEMANTIX_CA_CERT_PATH=/certs/ca-root.pem

# Internal LAN: skip employee client cert on localhost (never use in production edge)
RC_MTLS_DEV_BYPASS=true
RC_MTLS_DEV_EMPLOYEE_ID=

# Scheduler — 24h window, conservative ingestion rate
RC_SYNC_DEFAULT_WINDOW_START=00:00
RC_SYNC_DEFAULT_WINDOW_END=23:59
RC_SYNC_DEFAULT_MAX_INGESTION_REQUESTS_PER_MINUTE=4
RC_SYNC_STATE_PATH=/var/rc-state/.rc-sync-state.json
```

**Important:** `RC_INSTANCE_TOKEN` must be issued by Knovas admin. Without it, `/discover` and `/sync` will fail at operator verification even though `/health` returns ok.

---

## Step 6 — Mount the corpus into the container

A relative symlink under `./data/corpus` does **not** work reliably in Docker. Use a direct bind mount in `docker-compose.internal.yml`:

```yaml
# docker-compose.internal.yml
services:
  remote-controller:
    ports:
      - "127.0.0.1:5001:5001"
    volumes:
      - ../corpus:/data/corpus:ro
    environment:
      RC_SYNC_STATE_PATH: /var/rc-state/.rc-sync-state.json

  nginx:
    profiles:
      - edge   # NGINX edge disabled until employee mTLS certs exist
```

Start with internal compose (RC only, no public `:443` edge):

```bash
cd /home/master/KnovasInternal/RemoteController
docker compose -f docker-compose.yml -f docker-compose.internal.yml up -d --build
```

---

## Step 7 — Fix Docker Compose volume bug (state path)

The stock `docker-compose.yml` originally mounted `rc-state:/app`, which **overwrote the entire application directory** inside the container with an empty volume (causing import errors on boot).

**Fixed mount:**

```yaml
volumes:
  - rc-config:/app/config
  - rc-state:/var/rc-state    # was: rc-state:/app  ← wrong
```

After changing this, remove old volumes and recreate:

```bash
docker compose -f docker-compose.yml -f docker-compose.internal.yml down -v
docker compose -f docker-compose.yml -f docker-compose.internal.yml up -d --build
```

---

## Step 8 — Fix volume ownership

The `rc-config` and `rc-state` volumes are created as `root`. The container runs as `rcuser` (uid 10001) and must write:

- `config/remote_controller_sync.json` (auto-created on first boot)
- `/var/rc-state/.rc-sync-state.json` (sync progress)

```bash
docker exec -u root remotecontroller-remote-controller-1 \
  chown -R rcuser:rcuser /app/config /var/rc-state
docker restart remotecontroller-remote-controller-1
```

---

## Step 9 — Verify health

```bash
curl -sS http://127.0.0.1:5001/health | python3 -m json.tool
```

**Expected (success):**

```json
{
  "status": "ok",
  "service": "remote-controller",
  "checks": {
    "config": "ok",
    "watch_roots": "ok",
    "watch_roots_detail": [
      { "path": "/data/corpus", "exists": true, "readable": true }
    ],
    "scheduler": "ok",
    "scheduler_state": "awaiting_initial_sync_body"
  }
}
```

Confirm corpus visible inside container:

```bash
docker exec remotecontroller-remote-controller-1 \
  ls /data/corpus/wikipedia_de | head
```

Confirm scheduler config was created:

```bash
docker exec remotecontroller-remote-controller-1 \
  cat config/remote_controller_sync.json
```

---

## Step 10 — Sync request for the corpus

Use `examples/sync-request-corpus.json`:

```json
{
  "mode": "incremental",
  "sources": [{ "path": "/data/corpus", "recursive": true }],
  "filters": {
    "include_globs": ["**/*.txt"],
    "exclude_globs": ["**/.git/**"]
  },
  "ingestion": {
    "identifier_prefix": "corpus",
    "part_max_chars": 50000
  }
}
```

This syncs all `.txt` files under all corpus subfolders recursively. No `max_document_age_seconds` filter — historical legal content is included.

---

## Step 11 — Run first sync (requires Knovas credentials)

You still need:

1. **`RC_INSTANCE_TOKEN`** — from Knovas admin (replace placeholder in `.env`, restart container)
2. **Employee JWT** — from Knovas Internal API (`generate_emp_jwt`)
3. **`RC_MTLS_DEV_EMPLOYEE_ID`** — set to the operator UUID matching the JWT (because dev bypass is enabled)

On the server:

```bash
cd /home/master/KnovasInternal/RemoteController

# After setting RC_INSTANCE_TOKEN and RC_MTLS_DEV_EMPLOYEE_ID in .env:
docker compose -f docker-compose.yml -f docker-compose.internal.yml up -d

export RC_BASE=http://127.0.0.1:5001
export EMPLOYEE_JWT="<from Knovas generate_emp_jwt>"

# Discover files under watch root
curl -sS "$RC_BASE/discover" \
  -H "Authorization: Bearer $EMPLOYEE_JWT"

# One-shot incremental sync
curl -sS -X POST "$RC_BASE/sync" \
  -H "Authorization: Bearer $EMPLOYEE_JWT" \
  -H "Content-Type: application/json" \
  -d @examples/sync-request-corpus.json

# Check progress
curl -sS "$RC_BASE/sync/status" \
  -H "Authorization: Bearer $EMPLOYEE_JWT"
```

For continuous background sync:

```bash
curl -sS -X POST "$RC_BASE/sync/start" \
  -H "Authorization: Bearer $EMPLOYEE_JWT" \
  -H "Content-Type: application/json" \
  -d @examples/sync-request-corpus.json
```

Stop continuous sync:

```bash
curl -sS -X POST "$RC_BASE/sync/stop" \
  -H "Authorization: Bearer $EMPLOYEE_JWT"
```

---

## Step 12 — Confirm ingestion in KnovasPlatform

After sync completes, documents should appear in the Knovas search UI:

```bash
# KnovasPlatform already running on this server:
curl -fsS http://127.0.0.1:8081/health
```

Open the platform in a browser and search for a known corpus term (e.g. a Wikipedia article title from `corpus/wikipedia_de/`).

---

## Automated setup script

A repeatable script is at `RemoteController/scripts/setup_server_corpus.sh`. Run on the server:

```bash
cd /home/master/KnovasInternal/RemoteController
chmod +x scripts/setup_server_corpus.sh
./scripts/setup_server_corpus.sh
```

If the server's git checkout of RemoteController is older than your dev tree, upload the latest `src/sync/*` and `src/routes/sync*.py` files before building (the server copy was missing `effective_filters` and still imported `semantix_uploader`).

---

## Operations reference

| Action | Command |
|--------|---------|
| Start RC | `docker compose -f docker-compose.yml -f docker-compose.internal.yml up -d` |
| Stop RC | `docker compose -f docker-compose.yml -f docker-compose.internal.yml down` |
| Logs | `docker compose -f docker-compose.yml -f docker-compose.internal.yml logs -f remote-controller` |
| Health | `curl -sS http://127.0.0.1:5001/health` |
| Metrics | `curl -sS http://127.0.0.1:5001/metrics` |
| Rebuild | `docker compose -f docker-compose.yml -f docker-compose.internal.yml up -d --build` |

---

## Troubleshooting encountered during setup

| Symptom | Cause | Fix |
|---------|-------|-----|
| `ModuleNotFoundError: sync.semantix_uploader` | Old source + `rc-state:/app` volume overwrote `/app` | Upload fixed `sync_executor.py`; change volume to `/var/rc-state`; `down -v` |
| `ImportError: effective_filters` | Server git tree behind local RC changes | Upload latest `src/sync/*.py` |
| `watch_roots: degraded`, path not found | Symlink `data/corpus → ../corpus` invalid in container | Bind mount `../corpus:/data/corpus:ro` in internal compose |
| `scheduler: error` | `rcuser` cannot write `config/` volume | `docker exec -u root ... chown rcuser:rcuser /app/config /var/rc-state` |
| `POST /sync` 500, `Permission denied: '/app/tmp…'` | Last sync body written under read-only `/app` | Use current RC (`save` → `/var/rc-state/`); set `RC_SYNC_STATE_PATH=/var/rc-state/.rc-sync-state.json`; rebuild; `chown rcuser:rcuser /var/rc-state` |
| Sync worker crash, `Permission denied: '/certs/...key'` | Key not readable by uid **10001** | `./scripts/install_tenant_certs.sh` on `~/KnovasInternal/certs` |
| `401 Client certificate not authorized` | RC still using old copied certs under `RemoteController/certs/` | Mount `../certs`; update `.env` to `/certs/client-cert.pem` etc. |
| Sync returns 401/403 | Missing JWT, instance token, or dev employee ID | Set `RC_INSTANCE_TOKEN`, `RC_MTLS_DEV_EMPLOYEE_ID`, use valid JWT |

---

## Production edge (not configured yet)

The NGINX edge on `:443` with employee mTLS is **not** set up on this server. Port 443 is free. For production employee access:

1. Obtain employee RC CA + public TLS certs from Knovas
2. Place under `RemoteController/certs/edge/`
3. Customize `docs/nginx-edge.example.conf`
4. Start full stack: `docker compose up -d` (without `docker-compose.internal.yml`)
5. Register public RC URL with Knovas admin

See [SETUP.md](SETUP.md) steps 6–8.

---

## Current status (2026-05-23)

| Check | Status |
|-------|--------|
| Container running | Yes — `remotecontroller-remote-controller-1` |
| Health | **ok** — `http://127.0.0.1:5001/health` |
| Corpus mounted | Yes — `/data/corpus` readable, 3,515 files |
| Tenant mTLS | Yes — certs in `/certs`, API health verified |
| Scheduler config | Auto-created, continuous mode, 4 req/min |
| `RC_INSTANCE_TOKEN` | **Still placeholder** — required before sync |
| End-to-end sync | **Pending** — needs instance token + employee JWT |

---

## Security notes

- RemoteController listens on **127.0.0.1:5001 only** — not exposed to the LAN
- `RC_MTLS_DEV_BYPASS=true` is for internal testing only; disable before exposing an edge
- Do not commit passwords, instance tokens, or private keys to git
- Cert file permissions: `0600` on `tenant-client.key`
