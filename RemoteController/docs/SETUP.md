# Setup — clone to fully functional Remote Controller

Single ordered path from a fresh folder to a working Remote Controller (RC) in **production**. Use reference docs linked at each step.

**For local-only development on your machine** (localhost `127.0.0.1:5001`, no HTTPS edge, no employee JWT), use [local-setup.md](local-setup.md) instead.

## Milestones

| Milestone | How you know |
|-----------|----------------|
| Service healthy | `GET /health` returns HTTP 200, `"status":"ok"` |
| Production-ready edge | HTTPS public URL at NGINX/Envoy |
| End-to-end sync | `GET /discover` and `POST /sync` succeed; documents appear in Knovas |

For local-only setup, see [local-setup.md](local-setup.md). For curl and pytest after setup, see [local-commands.md](local-commands.md).

---

## Before you begin

**From Knovas** (before or during onboarding):

| Item | Used as |
|------|---------|
| `RC_INSTANCE_TOKEN` | `.env` — authenticates RC to Knovas verify API |
| `RC_CLIENT_ID` | `.env` — your tenant UUID |
| `KNOVAS_INTERNAL_API_URL` | `.env` — base URL for `POST /remote_controller/verify_operator` |
| Knovas Secure API URL + tenant mTLS | `.env` — secured API (typically `:8443`) and cert paths |
| Public RC base URL | Registered in Knovas admin (e.g. `https://rc.yourcompany.com`) |

**You provide:**

| Item | Notes |
|------|--------|
| Linux host or Docker | Outbound HTTPS to Knovas APIs; inbound HTTPS at edge |
| Watch-root directories | Local paths containing documents to sync |
| Edge reverse proxy | NGINX or Envoy — see step 6 |

Network prerequisites: [network-and-firewall.md](network-and-firewall.md).

---

## Step 1 — Clone

**Monorepo:**

```bash
git clone https://github.com/Seifeddini/KnovasComponents.git
cd KnovasComponents/RemoteController
```

**Standalone Remote Controller repo:**

```bash
git clone <your-remote-controller-repo-url>
cd RemoteController
```

```bash
cp .env.example .env
```

Production containers **must not** set `RC_SKIP_CONFIG_VALIDATION`. Missing required variables must fail at boot.

---

## Step 2 — Configure environment

Edit `.env` using [.env.example](../.env.example). Required variables:

- `KNOVAS_INTERNAL_API_URL`, `RC_INSTANCE_TOKEN`, `RC_CLIENT_ID`
- `RC_WATCH_ROOTS` — comma-separated **absolute** paths inside the container (e.g. `/data/docs`)
- Knovas Secure API URL and tenant mTLS paths (see `.env.example`)

Set file permissions to `0600` on the tenant key file. For Docker (user `rcuser`, uid **10001**), also run `chown 10001:10001` on mounted cert files so the container can read the key. Full reference: [configuration.md](configuration.md).

---

## Step 3 — Prepare volumes

```bash
mkdir -p certs data config certs/edge
chmod 600 certs/*.pem certs/*.key 2>/dev/null || true
```

- Mount tenant certs under `/certs` (paths must match `.env`).
- Mount document roots under `/data` (paths must match `RC_WATCH_ROOTS`).
- For Compose + NGINX: place public TLS and employee CA under `certs/edge/` (see [nginx-edge.example.conf](nginx-edge.example.conf)).

Scheduler config `config/remote_controller_sync.json` is created on first start if missing.

---

## Step 4 — Build and run

**Docker Compose (recommended):**

```bash
docker compose up -d --build
```

Uses [docker-compose.yml](../docker-compose.yml) with RC + NGINX edge.

**Docker only (RC container):**

```bash
docker build -t remote-controller:0.1.1 .
docker run -d --name remote-controller \
  --env-file .env \
  -v "$(pwd)/certs:/certs:ro" \
  -v "$(pwd)/data:/data:ro" \
  -v rc-config:/app/config \
  remote-controller:0.1.1
```

Place NGINX or Envoy in front for HTTPS — do not publish port 5001 to the public internet.

**Gunicorn workers:** The image runs **one** worker (`-w 1`). Continuous sync uses in-process locks; multiple workers cause duplicate schedulers and conflicting state files. If you run Gunicorn manually, keep `-w 1`.

**Python from source (dev/staging):** See [local-commands.md](local-commands.md).

---

## Step 5 — Verify health

From the RC host (or through the edge if configured):

```bash
curl -sS http://127.0.0.1:5001/health
```

- HTTP **200** — `"status":"ok"`; config, watch roots, and scheduler checks are healthy.
- HTTP **503** — `"status":"degraded"`; fix `.env` or volume mounts before going live.

If the container exits immediately, check logs — required env vars are validated at startup in production. Details: [operations.md](operations.md).

---

## Step 6 — Configure the edge proxy

**Required for production employee access.** Do not expose port 5001 directly to the internet.

Terminate HTTPS at NGINX/Envoy and proxy to RC. Employees authenticate with `Authorization: Bearer <JWT>` only.

Copy and customize [nginx-edge.example.conf](nginx-edge.example.conf). With Compose, it is mounted automatically; adjust `server_name` and certificate paths under `certs/edge/`.

---

## Step 7 — Network and Knovas registration

1. Complete [network-and-firewall.md](network-and-firewall.md) (firewall, public URL, outbound rules).
2. Verify health from **outside** your LAN: `curl -sS https://rc.yourcompany.com/health`
3. Give Knovas admin your base URL; they register the endpoint and confirm the probe.

---

## Step 8 — First discover and sync

Employee workflow (during the configured sync window). Obtain an employee JWT from the Knovas Internal API (`generate_emp_jwt`).

Adjust `sources[].path` in [examples/sync-request.json](../examples/sync-request.json) to match your `RC_WATCH_ROOTS` mounts.

```bash
export RC_BASE=https://rc.yourcompany.com
export EMPLOYEE_JWT="<employee_jwt>"

curl -sS "$RC_BASE/discover" \
  -H "Authorization: Bearer $EMPLOYEE_JWT"

curl -sS -X POST "$RC_BASE/sync" \
  -H "Authorization: Bearer $EMPLOYEE_JWT" \
  -H "Content-Type: application/json" \
  -d @examples/sync-request.json

curl -sS "$RC_BASE/sync/status" \
  -H "Authorization: Bearer $EMPLOYEE_JWT"
```

Stop continuous background sync (worker only — RC API stays up):

```bash
curl -sS -X POST "$RC_BASE/sync/stop" \
  -H "Authorization: Bearer $EMPLOYEE_JWT"
```

Details: [operations.md](operations.md#stop-sync) and [local-commands.md](local-commands.md#stop-sync).

Confirm documents in Knovas. Go-live checklist: [onboarding-checklist.md](onboarding-checklist.md).

For local testing without employee certs, see [local-commands.md](local-commands.md).

---

## Related docs

| Doc | Purpose |
|-----|---------|
| [local-setup.md](local-setup.md) | Local-only setup on your machine (localhost) |
| [local-commands.md](local-commands.md) | API cheat sheet, curl sync/discover, pytest |
| [configuration.md](configuration.md) | Env vars, sync JSON, permissions |
| [network-and-firewall.md](network-and-firewall.md) | Ingress/egress checklist |
| [operations.md](operations.md) | Health, metrics, upgrades |
| [onboarding-checklist.md](onboarding-checklist.md) | Partner + Knovas handoff |
