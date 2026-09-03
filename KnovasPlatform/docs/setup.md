# Setup guide

Monorepo path: `KnovasComponents/KnovasPlatform/`.

## 1. What you get

This folder is a **search web app** for your Knovas tenant (Docker). It does **not** index documents.

Ingest and sync documents first with [RemoteController](../../RemoteController/), then complete this guide.

## 2. Before you start

- Knovas tenant and mTLS client certificate — see the [API integration kit](../../docs/KnovasAPI/README.md) and [certificates.md](../../docs/certificates.md)
- Documents already indexed in Knovas
- Docker Engine and Compose; outbound HTTPS to your Knovas API (port 8443 is typical)

Platform-specific notes: [platforms/ubuntu.md](platforms/ubuntu.md), [platforms/debian.md](platforms/debian.md), [platforms/windows.md](platforms/windows.md).

**HTTPS with internal DNS (host nginx):** use [deployment/host-nginx-internal.md](deployment/host-nginx-internal.md) and `./scripts/start_stack_host_nginx.sh` instead of step 5 below for production.

## 3. Configure

```bash
cp .env.example .env
```

On Windows (host shell):

```powershell
Copy-Item .env.example .env
```

Set strong values for `WEB_SECRET_KEY` and all **Knovas API** variables (`SEMANTIX_API_URL`, mTLS paths, secured mode). Do not leave placeholder secrets.

Per-user identity is **on by default** (`IDENTITY_ENABLED=true`), so set:

- `PLATFORM_ADMIN_EMAIL` — the first administrator; there is no default account
- `SEMANTIX_CUSTOMER_ID` — tenant id signed into every `principal_assertion` (`api.customer_id`); must match the Knovas tenant
- `PLATFORM_BROKER_KEY_DIR` — directory for `broker_ed25519.pem` / `.pub` / `.kid` (default `/app/secrets/broker`, the directory the image creates; the Platform will not regenerate a partial or unreadable key, and will not mkdir from Python)

Do **not** set `COMPANY_LOGIN_NAME` / `COMPANY_LOGIN_PASSWORD`. The shared firm
credential is superseded, and the Platform refuses to start with both it and
per-user accounts configured. To stage a cutover on an existing deployment, set
`IDENTITY_ENABLED=false` and keep the old values until you migrate.

For **search only** (no UNC file open), set `OPEN_COMPANION_ENABLED=false` in `.env`.

## 4. Certificates

Place in `./certs/` (see [certs/README.md](../certs/README.md)): `client.crt`, `client.key`, `ca.crt`. Paths must match `.env`.

Knovas ships these as `client-cert.pem`, `client-key.pem`, and `ca-root.pem` —
**rename them on copy.** RemoteController uses the original `.pem` names from a
different directory (the monorepo root), so you cannot point KnovasPlatform at
RC's `certs/`. Cross-component reference: [docs/certificates.md](../../docs/certificates.md).

```bash
cp /path/to/client-cert.pem certs/client.crt
cp /path/to/client-key.pem  certs/client.key
cp /path/to/ca-root.pem     certs/ca.crt
chmod 600 certs/client.key
```

Confirm mTLS works before starting the stack — this bypasses the app, so a
failure here is a certificate or network problem, not a config one:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  --cert certs/client.crt --key certs/client.key --cacert certs/ca.crt \
  https://api.knovas.ch:8443/secured/health
```

### 4.1 Per-user identity: the broker signing key

On by default (`IDENTITY_ENABLED=true`). The Platform generates an Ed25519 key
on first start in `PLATFORM_BROKER_KEY_DIR` (a persistent volume, default
`/app/secrets/broker`) and signs each signed-in user into every Knovas call.
Register the public half (`broker_ed25519.pub`) with Knovas, back the
directory up, and set `SEMANTIX_CUSTOMER_ID`. Details and the failure modes:
[../../docs/certificates.md](../../docs/certificates.md#knovasplatform-the-broker-signing-key-per-user-identity).

## 5. Run and verify

```bash
./start_stack.sh
./scripts/verify_deploy.sh
```

`start_stack` performs a **full Docker rebuild** (`build --no-cache` + `up --force-recreate`) so the UI matches this repo. First start after a pull can take several minutes.

**Manual full rebuild** (same as the scripts):

```bash
cd KnovasPlatform
docker compose build --no-cache docbridge-web
docker compose up -d --force-recreate docbridge-web docbridge-web-nginx
```

Windows (PowerShell): `.\start_stack.ps1`

**Faster restart** (reuse existing image; no rebuild) — picks up `.env` changes such as `WEB_APP_TITLE` only after **recreate**:

```bash
docker compose up -d --force-recreate docbridge-web docbridge-web-nginx
```

**Code changes** require an image rebuild first (static CSS and app code are baked into the image):

```bash
docker compose build docbridge-web
docker compose up -d --force-recreate docbridge-web docbridge-web-nginx
```

Or use `./start_stack.sh` for a full no-cache rebuild.

Windows (host shell):

```powershell
.\start_stack.ps1
.\scripts\verify_deploy.ps1
```

- Browser: `http://<host>:8081` (port from `DOCBRIDGE_WEB_PORT` in `.env`) — log in with company credentials from `.env`
- `/api/health` should report the Knovas API as reachable when configured

No tenant yet? Use [demo.md](demo.md) instead of steps 3–5 against a real API.

To stop the stack: `./stop_stack.sh` or `.\stop_stack.ps1` — see [stopping web servers](../../docs/stopping-web-servers.md).

## 6. Optional: open files from AutoDoc (client-side)

Skip for search-only deployments.

**Model:** Server A hosts the app; users on other PCs click **Öffnen** in the browser. The file opens **on their PC** via the share they already use — **no Knovas install** on clients (`OPEN_BROWSER_CLIENT_PATH=true`, default).

**On Server A:**

1. Mount the share; set `AUTODOC_MOUNT_PATH` and `OPEN_LOCAL_ROOT=/mnt/autodoc`.
2. Set `OPEN_UNC_ROOT` (Windows clients) and/or `OPEN_CLIENT_LOCAL_ROOT` (Linux clients).
3. Keep `OPEN_COMPANION_ENABLED=false` unless browser open is blocked by IT policy.
4. Leave `OPEN_ALLOW_SERVER_SIDE_STARTFILE=false`.

Clients only need share access + a normal browser. Details: [integration/opening-documents.md](integration/opening-documents.md). DFS aliases: `open.unc_roots` in [config.yaml](../components/docbridge_integration/config/config.yaml).

## 7. Optional: production hardening

- **Internal DNS + TLS on host nginx:** follow [deployment/host-nginx-internal.md](deployment/host-nginx-internal.md) — `./scripts/start_stack_host_nginx.sh`, nginx template in `deploy/host-nginx/`, checklist in [deployment/checklist-host-nginx.md](deployment/checklist-host-nginx.md)
- **Direct HTTP on `:8081`** (dev, demo, or trusted LAN only): `./start_stack.sh` — do not expose to the internet without a reverse proxy
- Firewall: allow **443** at nginx; bind the app to localhost in host-nginx mode (port 8081 not reachable from other hosts)
- Use a strong `WEB_SECRET_KEY`; restrict `/api/open-tokens/redeem` to client subnets when possible
- Multiple Gunicorn workers weaken one-time token replay protection — prefer one worker or sticky sessions

## 8. Issues

See [integration/troubleshooting.md](integration/troubleshooting.md).
