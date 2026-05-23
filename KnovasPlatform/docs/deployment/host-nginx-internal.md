# Deploy behind host nginx (internal DNS + internal TLS)

Use this guide when:

- Users reach the app at **`https://<your-internal-fqdn>`** (e.g. `https://knovas.example.internal`)
- **Internal DNS** resolves that name to your Linux server on a LAN or virtual network
- **TLS terminates on host nginx** with your corporate CA (not Let's Encrypt)
- Docker runs the Knovas search UI on **localhost only**; nginx proxies to it

Short checklist: [checklist-host-nginx.md](checklist-host-nginx.md).

Monorepo path: `KnovasComponents/KnovasPlatform/`.

## Architecture

```text
Client VM  --HTTPS:443-->  host nginx (internal cert)
                              --HTTP--> 127.0.0.1:8081  -->  Docker (docbridge-web-nginx --> app)
                                                              -->  Knovas API (mTLS, outbound)
```

Clients do **not** connect to port 8081 on the network.

## Prerequisites

| Item | Notes |
|------|--------|
| Internal DNS | `<fqdn>` → IP of this server on the vnet/LAN |
| Internal CA | Server cert in nginx; CA trusted on client PCs |
| Docker + Compose | On Debian/Ubuntu — see [platforms/debian.md](../platforms/debian.md) |
| Knovas mTLS | `certs/client.crt`, `client.key`, `ca.crt` — [certs/README.md](../../certs/README.md) |
| Indexed documents | Ingest with [RemoteController](../../../RemoteController/) first |
| Outbound HTTPS | From container to Knovas API (often port 8443) |

## 1. Configure `.env`

```bash
cd KnovasPlatform
cp .env.example .env
```

Set at minimum:

| Variable | Example |
|----------|---------|
| `ENVIRONMENT` | `production` |
| `WEB_SECRET_KEY` | `openssl rand -hex 32` |
| `COMPANY_LOGIN_NAME` / `COMPANY_LOGIN_PASSWORD` | Strong UI login |
| `SEMANTIX_API_URL` | `https://<knovas-api-host>:8443` reachable **from inside** the container |
| `OPEN_PUBLIC_BASE_URL` | `https://<your-fqdn>` — required if users use **Öffnen** / open-tokens |

Do not use placeholder secrets. See [setup.md](../setup.md) for all variables.

## 2. Start Docker (localhost bind)

```bash
chmod +x scripts/start_stack_host_nginx.sh scripts/verify_deploy.sh stop_stack.sh
./scripts/start_stack_host_nginx.sh
```

This uses [docker-compose.host-nginx.yml](../../docker-compose.host-nginx.yml) so the app listens on **`127.0.0.1:${DOCBRIDGE_WEB_PORT:-8081}`** only.

Verify on the server:

```bash
curl -fsS http://127.0.0.1:8081/health
# expect: ok
```

## 3. Configure host nginx

Copy the template and edit hostname + certificate paths:

```bash
sudo cp deploy/host-nginx/knovas-platform.conf.example /etc/nginx/sites-available/knovas
sudo nano /etc/nginx/sites-available/knovas
```

Replace:

- `knovas.example.internal` → your FQDN (e.g. `knovas.rudincantieni.ch`)
- `ssl_certificate` / `ssl_certificate_key` → paths to your internal PKI files
- `proxy_pass` port if `DOCBRIDGE_WEB_PORT` in `.env` is not `8081`

Enable and reload:

```bash
sudo ln -sf /etc/nginx/sites-available/knovas /etc/nginx/sites-enabled/knovas
sudo nginx -t
sudo systemctl reload nginx
```

Verify via nginx:

```bash
curl -fsS https://<your-fqdn>/health
```

Full stack check:

```bash
VERIFY_BASE_URL=https://<your-fqdn> ./scripts/verify_deploy.sh
```

## 4. Firewall

- **Allow** TCP **443** from client subnets to this server.
- **Do not** expose **8081** to other hosts (localhost bind handles this).

Outbound HTTPS from Docker to the Knovas API must be allowed.

## 5. Verify from another machine

On a client VM on the same virtual network:

```bash
nslookup <your-fqdn>
curl -fsS https://<your-fqdn>/health
```

Open `https://<your-fqdn>` in a browser (no certificate warning if your CA is deployed). Log in with `COMPANY_LOGIN_*` from `.env`.

## 6. Optional: open documents from the browser

Mount AutoDoc on the server and set `OPEN_UNC_ROOT` / `OPEN_CLIENT_LOCAL_ROOT`. See [integration/opening-documents.md](../integration/opening-documents.md).

## 7. Reboot persistence

Optional systemd unit: [deploy/systemd/knovas-platform.service.example](../../deploy/systemd/knovas-platform.service.example).

## Troubleshooting: port 8081 already in use

Docker error: `127.0.0.1:8081/tcp is already in use` (or similar).

### A. Stack already running

If health check succeeds, **do not** force-recreate — only fix nginx:

```bash
curl -fsS http://127.0.0.1:8081/health
```

### B. Stop, then start host-nginx mode

```bash
./stop_stack.sh
ss -tlnp | grep 8081 || true
./scripts/start_stack_host_nginx.sh
```

### C. Stale container

```bash
docker ps -a --filter name=docbridge
docker rm -f docbridge-web-nginx docbridge-web 2>/dev/null || true
./scripts/start_stack_host_nginx.sh
```

### D. Another service uses 8081

1. Set `DOCBRIDGE_WEB_PORT=18081` in `.env`
2. Update host nginx `proxy_pass http://127.0.0.1:18081;`
3. `./stop_stack.sh && ./scripts/start_stack_host_nginx.sh`

### E. Leftover `docker-compose.override.yml`

If you previously created `docker-compose.override.yml` by hand, remove or merge it — use **`docker-compose.host-nginx.yml`** via `start_stack_host_nginx.sh` instead to avoid conflicting port bindings.

More symptoms: [integration/troubleshooting.md](../integration/troubleshooting.md).

## Stop the stack

```bash
./stop_stack.sh
```

## Related docs

- Base setup: [setup.md](../setup.md)
- Ubuntu/Debian notes: [platforms/ubuntu.md](../platforms/ubuntu.md), [platforms/debian.md](../platforms/debian.md)
- Direct HTTP on `:8081` (dev/demo): `./start_stack.sh` — not for production TLS
