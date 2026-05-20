# Getting started — clone, configure, run

This guide is the single ordered path from a fresh clone to a working Remote Controller (RC). Use it with the reference docs linked at each step.

## Before you begin

You need from **Knovas** (before or during onboarding):

| Item | Used as |
|------|---------|
| `RC_INSTANCE_TOKEN` | `.env` — authenticates RC to Knovas verify API |
| `RC_CLIENT_ID` | `.env` — your tenant UUID |
| `KNOVAS_INTERNAL_API_URL` | `.env` — base URL for `POST /remote_controller/verify_operator` |
| Knovas Secure API URL + tenant mTLS | `.env` — secured API (typically `:8443`) and cert paths |
| Employee RC certificates | Issued per operator; used by employees at the edge |
| Public RC base URL | Registered in Knovas admin (e.g. `https://rc.yourcompany.com`) |

You provide:

| Item | Notes |
|------|--------|
| Linux host or Docker | Outbound HTTPS to Knovas APIs; inbound HTTPS at edge |
| Watch-root directories | Local paths containing documents to sync |
| Edge reverse proxy | NGINX or Envoy — see step 6 |

External prerequisites are described in [network-and-firewall.md](network-and-firewall.md).

---

## Step 1 — Clone and install dependencies (optional local dev)

```bash
git clone <your-remote-controller-repo-url>
cd remote-controller
cp .env.example .env
```

For local development and tests only:

```bash
pip install -e ".[dev]"
export RC_SKIP_CONFIG_VALIDATION=true   # never set in production
pytest
```

Production containers **must not** set `RC_SKIP_CONFIG_VALIDATION`. Missing required variables must fail at boot.

---

## Step 2 — Configure environment

Edit `.env` using [.env.example](../../.env.example) as reference. Required variables:

- `KNOVAS_INTERNAL_API_URL`, `RC_INSTANCE_TOKEN`, `RC_CLIENT_ID`
- `RC_WATCH_ROOTS` — comma-separated **absolute** paths inside the container (e.g. `/data/docs`)
- Knovas Secure API URL and tenant mTLS paths (see `.env.example`)

Set file permissions to `0600` on cert and key files. Details: [configuration.md](configuration.md).

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

**Docker only:**

```bash
docker build -t remote-controller:0.1.1 .
docker run -d --name remote-controller \
  --env-file .env \
  -v "$(pwd)/certs:/certs:ro" \
  -v "$(pwd)/data:/data:ro" \
  -v rc-config:/app/config \
  remote-controller:0.1.1
```

See [installation.md](installation.md) for bare-metal Python and worker notes (**use a single Gunicorn worker** for continuous sync).

---

## Step 5 — Verify health

From the RC host (or through the edge if configured):

```bash
curl -sS http://127.0.0.1:5001/health
```

Expect HTTP **200** and `"status":"ok"` when config and watch roots are valid. HTTP **503** with `"status":"degraded"` means fix `.env` or volume mounts before going live. See [operations.md](operations.md).

If the container exits immediately, check logs — required env vars are validated at startup in production.

---

## Step 6 — Configure the edge proxy

Do **not** expose port 5001 directly to the internet. Terminate employee RC mTLS at NGINX/Envoy and forward headers to RC:

- `X-SSL-Client-Cert`
- `X-SSL-Client-DN` (CN = operator UUID)

Copy and customize [nginx-edge.example.conf](nginx-edge.example.conf). With Compose, it is mounted automatically; adjust `server_name` and certificate paths under `certs/edge/`.

---

## Step 7 — Network and Knovas registration

1. Complete [network-and-firewall.md](network-and-firewall.md) (firewall, public URL, outbound rules).
2. Verify health from **outside** your LAN: `curl -sS https://rc.yourcompany.com/health`
3. Give Knovas admin your base URL; they register the endpoint and confirm the probe.

---

## Step 8 — First discover and sync

Employee workflow (during the configured sync window):

```bash
# Obtain employee JWT from Knovas Internal API (generate_emp_jwt)

curl -sS "https://rc.yourcompany.com/discover" \
  --cert employee-rc.pem --key employee-rc.key \
  -H "Authorization: Bearer <employee_jwt>"

curl -sS -X POST "https://rc.yourcompany.com/sync" \
  --cert employee-rc.pem --key employee-rc.key \
  -H "Authorization: Bearer <employee_jwt>" \
  -H "Content-Type: application/json" \
  -d @sync-request.json
```

Check `GET /sync/status` and confirm documents in Knovas. Checklist: [onboarding-checklist.md](onboarding-checklist.md).

---

## Optional — live API smoke tests

After `.env` points at real staging APIs:

```bash
pytest --knovas-api
```

---

## Related docs

| Doc | Purpose |
|-----|---------|
| [installation.md](installation.md) | Docker details, single-worker requirement |
| [configuration.md](configuration.md) | Env vars, sync JSON, permissions |
| [network-and-firewall.md](network-and-firewall.md) | Ingress/egress checklist |
| [operations.md](operations.md) | Health, metrics, upgrades |
| [onboarding-checklist.md](onboarding-checklist.md) | Partner + Knovas handoff |
