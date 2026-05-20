# Installation

Start with the ordered guide: [GETTING_STARTED.md](GETTING_STARTED.md).

## Requirements

- Linux host or container runtime (Docker recommended)
- Python 3.11+ (if running from source)
- Outbound HTTPS to Knovas Internal API and Knovas Secure API (`:8443`)
- Inbound HTTPS from Knovas employee clients (via your edge proxy)

## Docker Compose (recommended)

```bash
cp .env.example .env
# edit .env, prepare ./certs and ./data
docker compose up -d --build
```

Uses [docker-compose.yml](../../docker-compose.yml) with RC + NGINX edge. Customize [nginx-edge.example.conf](nginx-edge.example.conf) and `certs/edge/` TLS material.

## Docker (RC only)

```bash
docker build -t remote-controller:0.1.1 .
docker run -d \
  --name remote-controller \
  --env-file .env \
  -v /path/to/certs:/certs:ro \
  -v /path/to/data:/data:ro \
  -v rc-config:/app/config \
  remote-controller:0.1.1
```

Mount:

- Tenant mTLS cert, key, and CA at paths referenced in `.env`
- Watch roots (e.g. `/data/docs`) matching `RC_WATCH_ROOTS`

Place NGINX or Envoy in front to terminate employee RC mTLS — see [nginx-edge.example.conf](nginx-edge.example.conf). Do not publish port 5001 to the public internet.

## Gunicorn workers

The image runs **one** Gunicorn worker (`-w 1`). Continuous sync uses in-process locks; multiple workers cause duplicate schedulers and conflicting state files. If you run Gunicorn manually, keep `-w 1`.

## First boot

1. Copy `.env.example` to `.env` and fill all required values (production must not set `RC_SKIP_CONFIG_VALIDATION`).
2. Create `config/remote_controller_sync.json` or let the service seed defaults on first start.
3. `curl http://127.0.0.1:5001/health` should return HTTP 200 with `"status":"ok"` when config and watch roots are valid (503 if degraded).
