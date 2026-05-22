# Setup guide

Monorepo path: `KnovasComponents/KnovasPlatform/`.

## 1. What you get

This folder is a **search web app** for your Knovas tenant (Docker). It does **not** index documents.

Ingest and sync documents first with [RemoteController](../../RemoteController/), then complete this guide.

## 2. Before you start

- Knovas tenant and mTLS client certificate — see the [Implementation Kit](../knovas-docs/Knovas_Developer_Implementation_Kit/README.md)
- Documents already indexed in Knovas
- Docker Engine and Compose; outbound HTTPS to your Knovas API (port 8443 is typical)

Platform-specific notes: [platforms/ubuntu.md](platforms/ubuntu.md), [platforms/windows.md](platforms/windows.md).

## 3. Configure

```bash
cp .env.example .env
```

On Windows (host shell):

```powershell
Copy-Item .env.example .env
```

Set strong values for `WEB_SECRET_KEY`, `COMPANY_LOGIN_*`, and all **Knovas API** variables (`SEMANTIX_API_URL`, mTLS paths, secured mode). Do not leave placeholder secrets.

For **search only** (no UNC file open), set `OPEN_COMPANION_ENABLED=false` in `.env`.

## 4. Certificates

Place in `./certs/` (see [certs/README.md](../certs/README.md)): `client.crt`, `client.key`, `ca.crt`. Paths must match `.env`.

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

**Faster restart** (reuse existing image; no rebuild):

```bash
docker compose up -d docbridge-web docbridge-web-nginx
```

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

- Firewall `DOCBRIDGE_WEB_PORT`; terminate TLS at a reverse proxy if not LAN-only
- Use a strong `WEB_SECRET_KEY`; restrict `/api/open-tokens/redeem` to client subnets when possible
- Multiple Gunicorn workers weaken one-time token replay protection — prefer one worker or sticky sessions

## 8. Issues

See [integration/troubleshooting.md](integration/troubleshooting.md).
