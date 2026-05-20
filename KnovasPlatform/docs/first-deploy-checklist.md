# First deploy checklist

Monorepo path: `KnovasComponents/KnovasPlatform/`. Ingest documents with [RemoteController](../../RemoteController/) if needed.

## Prerequisites

- [ ] Knovas tenant + mTLS client cert ([Implementation Kit](../knovas-docs/Knovas_Developer_Implementation_Kit/README.md))
- [ ] Documents already indexed in Knovas (this repo is search UI only)
- [ ] Docker Engine + Compose; outbound HTTPS to your Knovas API (port 8443 typical)

## Configure

```bash
cp .env.example .env
```

Set strong `WEB_SECRET_KEY`, `COMPANY_LOGIN_*`, and all **Knovas API** values in `.env` (base URL, mTLS paths, secured mode). Do not leave placeholder secrets.

## Certificates

Place in `./certs/` (see [certs/README.md](../certs/README.md)): `client.crt`, `client.key`, `ca.crt`.

## AutoDoc + UNC (optional)

1. Mount document share on host; set `AUTODOC_MOUNT_PATH` in `.env`.
2. For Windows **Öffnen** on UNC: set `OPEN_UNC_ROOT` (client-visible UNC) and `OPEN_LOCAL_ROOT=/mnt/autodoc` if needed.
3. Service account (container) and end-user (RDP) must see the same logical files; map DFS aliases via `open.unc_roots` in [config.yaml](../components/docbridge_integration/config/config.yaml) if paths differ.

## Windows companion (optional)

Only if users open Office files on UNC (not search-only).

1. Build the Windows open companion under `components/` (`dotnet build -c Release`).
2. Install the exe on the gold image; import its `register-protocol.reg` (edit path first).
3. Details: [companion.md](client-integration/companion.md)

Set `OPEN_COMPANION_ENABLED=false` for search-only.

## Run

```bash
./start_stack.sh
./scripts/verify_deploy.sh
```

- Browser: `http://<host>:8081` — company login from `.env`
- `/api/health` should report Knovas API reachable when configured

## Demo (mock API)

In `.env` use mock URL and legacy mode per comments in `.env.example`, then:

```bash
docker compose --profile mock up -d --build
```

Do not expose mock services on production networks.

## Hardening

- Firewall `DOCBRIDGE_WEB_PORT`; TLS at reverse proxy if not LAN-only
- Strong `WEB_SECRET_KEY`; restrict `/api/open-tokens/redeem` to client subnets if possible
- Multiple Gunicorn workers weaken one-time token replay — prefer one worker or sticky sessions

## Issues

[troubleshooting.md](client-integration/troubleshooting.md)
