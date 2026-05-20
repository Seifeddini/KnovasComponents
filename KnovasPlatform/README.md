# Knovas Platform

Ready-to-run **search web app** for your Knovas tenant (Docker). Optional Windows helper to open documents on UNC paths.

**Ingest documents first** with [RemoteController](../RemoteController/) (or your own pipeline). This app only searches already-indexed content.

**You need:** mTLS client certs, Docker, configured `.env`.

## Install

```bash
git clone https://github.com/Seifeddini/KnovasComponents.git
cd KnovasComponents/KnovasPlatform
cp .env.example .env
```

Edit `.env` (secrets, company login, Knovas API URL). Put `client.crt`, `client.key`, `ca.crt` in `./certs/`.

```bash
./start_stack.sh
./scripts/verify_deploy.sh
```

Windows: `Copy-Item .env.example .env`, edit, then `.\start_stack.ps1`

Open **http://localhost:8081** (port from `.env`).

## Docs

| | |
|---|---|
| [first-deploy-checklist.md](docs/first-deploy-checklist.md) | Deploy steps |
| [knovas-docs/…/README.md](knovas-docs/Knovas_Developer_Implementation_Kit/README.md) | Knovas API |
| [docs/README.md](docs/README.md) | All guides |

**Demo:** mock mode in `.env.example` comments, then `docker compose --profile mock up -d --build`.

## Requirements

Docker Compose · HTTPS to Knovas API · 2 GB+ RAM
