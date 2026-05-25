# Local setup — run Remote Controller on your machine only

This guide is for developers who want Remote Controller (RC) on **their own PC**, with the API reachable only at `http://127.0.0.1:5001` — not from other machines on the network or the internet.

You will still call **Knovas APIs outbound** when you sync files. Nothing in this guide exposes RC to remote clients.

For production (HTTPS edge, employee JWT, public URL), use [SETUP.md](SETUP.md) instead.

---

## What you are building

| Piece | What it does |
|-------|----------------|
| RC container | Scans local folders and can upload text to Knovas |
| `127.0.0.1:5001` | API only on your machine (localhost) |
| `docker-compose.internal.yml` | Binds that port and enables local auth bypass |
| Tenant mTLS certs | RC uses these to talk to Knovas when syncing |

```text
Your PC (localhost only)
  curl → http://127.0.0.1:5001 → RC container → ../certs + ./data/docs
                                      ↓ (outbound only)
                                 Knovas APIs
```

---

## Prerequisites

- **Git** — to clone the repo
- **Docker** — [Docker Desktop](https://www.docker.com/products/docker-desktop/) on Windows/Mac, or Docker Engine on Linux
- **curl** — Linux/Mac: `curl`; Windows: `curl.exe` in PowerShell (built in on Windows 10+)

---

## What you need from Knovas

Ask your Knovas admin for these before Step 4:

| You receive | Put in `.env` as | Notes |
|-------------|------------------|--------|
| Tenant UUID | `RC_CLIENT_ID` | Your organisation id |
| Internal API base URL | `KNOVAS_INTERNAL_API_URL` | e.g. `http://api.knovas.ch:8080` |
| Secured API base URL | `SEMANTIX_SECURE_BASE_URL` | e.g. `https://api.knovas.ch:8443` |
| `client-cert.pem` | `SEMANTIX_CLIENT_CERT_PATH=/certs/client-cert.pem` | Copy into monorepo `certs/` |
| `client-key.pem` (or `.plain.pem`) | `SEMANTIX_CLIENT_KEY_PATH=/certs/client-key.pem` | Mode `0600`; see [configuration.md](configuration.md) |
| `ca-root.pem` | `SEMANTIX_CA_CERT_PATH=/certs/ca-root.pem` | CA for mTLS |

You do **not** need `RC_INSTANCE_TOKEN` or an employee JWT for this local setup — `docker-compose.internal.yml` sets `RC_INTERNAL_LOCAL_BYPASS=true`.

---

## Step 1 — Clone and open the project

**Monorepo:**

```bash
git clone https://github.com/Seifeddini/KnovasComponents.git
cd KnovasComponents/RemoteController
```

**Windows (PowerShell):** use the same paths; run commands from `RemoteController`.

---

## Step 2 — Sample documents

RC reads files from paths **inside the container**. The repo includes a sample folder mapped to `/data/docs`.

On your machine the folder is:

```text
RemoteController/data/docs/
```

A sample file `hello.txt` is already there. Add more `.txt` or `.md` files if you like.

In `.env` you will set `RC_WATCH_ROOTS=/data/docs` — that is the **container** path, not `data/docs` on your disk.

| Host path (your disk) | Container path (in `.env`) |
|-----------------------|----------------------------|
| `RemoteController/data/docs/` | `/data/docs` |

---

## Step 3 — Tenant certificates

Create the certs folder at the **monorepo root** (one level above `RemoteController`):

```bash
# From RemoteController/
mkdir -p ../certs
```

Copy the three PEM files from Knovas into `KnovasComponents/certs/`:

```text
KnovasComponents/certs/
  client-cert.pem
  client-key.pem      # or client-key.plain.pem — match SEMANTIX_CLIENT_KEY_PATH in .env
  ca-root.pem
```

**Linux:** restrict the key file:

```bash
chmod 600 ../certs/client-key.pem
```

Docker runs RC as user `rcuser` (uid **10001**). If sync fails with permission errors on the key, on Linux you may need:

```bash
sudo chown 10001:10001 ../certs/client-key.pem
```

More detail: [configuration.md](configuration.md).

---

## Step 4 — Configure `.env`

```bash
cp .env.example .env
```

Edit `.env`. Minimum for **local-only** (with internal compose):

```env
# From Knovas admin
KNOVAS_INTERNAL_API_URL=http://api.knovas.ch:8080
RC_CLIENT_ID=your-tenant-uuid-here
SEMANTIX_SECURE_BASE_URL=https://api.knovas.ch:8443
SEMANTIX_CLIENT_CERT_PATH=/certs/client-cert.pem
SEMANTIX_CLIENT_KEY_PATH=/certs/client-key.pem
SEMANTIX_CA_CERT_PATH=/certs/ca-root.pem

# Local sample folder (must match Step 2)
RC_WATCH_ROOTS=/data/docs

# Optional: leave empty or placeholder — bypass does not require a real token
RC_INSTANCE_TOKEN=

# Wider sync window so tests are not blocked by time of day
RC_SYNC_DEFAULT_WINDOW_START=00:00
RC_SYNC_DEFAULT_WINDOW_END=23:59
```

`RC_INTERNAL_LOCAL_BYPASS` is set automatically by `docker-compose.internal.yml` — you do not need to add it to `.env`.

Do **not** set `RC_SKIP_CONFIG_VALIDATION` for normal local runs (that is for pytest only).

---

## Step 5 — Start (localhost only)

From `RemoteController/`:

```bash
docker compose -f docker-compose.yml -f docker-compose.internal.yml up -d --build
```

Why both files?

- `docker-compose.yml` — builds RC, mounts `../certs` and `./data`
- `docker-compose.internal.yml` — publishes **`127.0.0.1:5001` only**, enables local bypass, does **not** start the HTTPS NGINX edge on port 443

Watch logs:

```bash
docker compose -f docker-compose.yml -f docker-compose.internal.yml logs -f remote-controller
```

Press `Ctrl+C` to leave logs (containers keep running).

---

## Step 6 — Verify health

**Linux / Mac / Git Bash:**

```bash
curl -sS http://127.0.0.1:5001/health
```

**Windows PowerShell:**

```powershell
curl.exe -sS http://127.0.0.1:5001/health
```

**Success:** HTTP 200 and JSON contains `"status":"ok"`.

**Degraded (`"status":"degraded"`):** often `watch_roots` — use a single root `RC_WATCH_ROOTS=/data/docs` (not `/data/contracts` unless that folder exists). Ensure `data/docs` exists on the host. See `watch_roots_detail` in the JSON. Discover may still work when only one root is missing.

**Connection refused:** you probably started without `docker-compose.internal.yml`, or Docker is not running.

---

## Step 7 — Operate locally (no JWT)

Set the base URL once.

**Bash:**

```bash
export RC_BASE=http://127.0.0.1:5001
```

**PowerShell:**

```powershell
$env:RC_BASE = "http://127.0.0.1:5001"
```

All commands below use `$RC_BASE` (Bash) or `$env:RC_BASE` (PowerShell). Adjust if your shell differs.

### 7.1 — Discover files

Lists files RC can see under your watch roots (no login header).

```bash
curl -sS "$RC_BASE/discover"
```

PowerShell:

```powershell
curl.exe -sS "$env:RC_BASE/discover"
```

Optional: limit to the sample folder and `.txt` only:

```bash
curl -sS "$RC_BASE/discover?root=/data/docs&max_depth=5&include_globs=**/*.txt"
```

### 7.2 — One-shot sync to Knovas

Uses [examples/sync-request.json](../examples/sync-request.json). The path `/data/docs` must match `RC_WATCH_ROOTS`.

```bash
curl -sS -X POST "$RC_BASE/sync" \
  -H "Content-Type: application/json" \
  -d @examples/sync-request.json
```

PowerShell (run from `RemoteController/`):

```powershell
curl.exe -sS -X POST "$env:RC_BASE/sync" `
  -H "Content-Type: application/json" `
  -d "@examples/sync-request.json"
```

Check status:

```bash
curl -sS "$RC_BASE/sync/status"
```

Confirm uploaded documents in Knovas (your admin UI or support channel).

### 7.3 — Continuous background sync

Start (same JSON body as one-shot):

```bash
curl -sS -X POST "$RC_BASE/sync/start" \
  -H "Content-Type: application/json" \
  -d @examples/sync-request.json
```

Status (add `?live=1` for a live file inventory):

```bash
curl -sS "$RC_BASE/sync/status"
curl -sS "$RC_BASE/sync/status?live=1"
```

Stop:

```bash
curl -sS -X POST "$RC_BASE/sync/stop"
```

### 7.4 — Metrics

```bash
curl -sS "$RC_BASE/metrics"
```

### Command cheat sheet

More endpoints and production auth: [local-commands.md](local-commands.md).

| Action | Method | Path |
|--------|--------|------|
| Health | GET | `/health` |
| Discover | GET | `/discover` |
| Sync once | POST | `/sync` |
| Start continuous | POST | `/sync/start` |
| Stop | POST | `/sync/stop` |
| Status | GET | `/sync/status` |
| Metrics | GET | `/metrics` |

---

## Step 8 — Stop and reset

Stop containers:

```bash
docker compose -f docker-compose.yml -f docker-compose.internal.yml down
```

To remove named volumes (sync state and config inside Docker):

```bash
docker compose -f docker-compose.yml -f docker-compose.internal.yml down -v
```

See also [stopping web servers](../../docs/stopping-web-servers.md).

---

## When you outgrow local setup

Local mode is **not** for production:

- Do not expose port 5001 to the LAN or internet.
- Do not leave `RC_INTERNAL_LOCAL_BYPASS` enabled on a shared server.

For HTTPS edge, employee JWT, firewall, and Knovas registration, follow [SETUP.md](SETUP.md).

---

## Troubleshooting

| Symptom | What to check |
|---------|----------------|
| `Connection refused` on `:5001` | Use both compose files (`docker-compose.internal.yml`). Is Docker running? |
| `401` on discover/sync | Local bypass off — restart with internal compose overlay |
| Health `degraded`, watch roots | `RC_WATCH_ROOTS` must match a mounted path; ensure `data/docs` exists |
| Container exits on start | `docker compose ... logs remote-controller` — missing required `.env` values |
| Sync errors / 5xx | Tenant certs in `../certs`, URLs in `.env`, outbound HTTPS to Knovas |
| Sync does nothing | Outside sync window — set `RC_SYNC_DEFAULT_WINDOW_START/END` to `00:00` / `23:59` |
| No files in sync | `sources[].path` in JSON must be under `RC_WATCH_ROOTS`; check `include_globs` |

**Large corpus at monorepo root** (`KnovasComponents/corpus/`): add `-f docker-compose.corpus.yml` to the compose command and set `RC_WATCH_ROOTS=/data/corpus`. See [docker-compose.corpus.yml](../docker-compose.corpus.yml).

**Linux server automation** (not the main junior path): [scripts/setup_server_corpus.sh](../scripts/setup_server_corpus.sh).

---

## Related docs

| Doc | Use |
|-----|-----|
| [local-commands.md](local-commands.md) | Endpoint reference, pytest, production curl |
| [configuration.md](configuration.md) | All env vars and scheduler JSON |
| [SETUP.md](SETUP.md) | Production deployment |
