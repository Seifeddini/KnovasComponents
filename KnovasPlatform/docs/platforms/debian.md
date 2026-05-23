# Debian

Debian 11/12 follows the same steps as [ubuntu.md](ubuntu.md) for Docker and Knovas Platform.

## Quick start (direct HTTP, dev or LAN)

```bash
git clone <repo-url> ~/KnovasPlatform && cd ~/KnovasPlatform
cp .env.example .env   # edit
chmod +x start_stack.sh stop_stack.sh scripts/verify_deploy.sh
./start_stack.sh
```

Browser: `http://<server-ip>:8081` (or `DOCBRIDGE_WEB_PORT` from `.env`).

## Production: internal DNS + host nginx (HTTPS)

For `https://<internal-fqdn>` with corporate TLS on nginx:

```bash
chmod +x scripts/start_stack_host_nginx.sh
./scripts/start_stack_host_nginx.sh
```

Then configure host nginx from [deploy/host-nginx/knovas-platform.conf.example](../../deploy/host-nginx/knovas-platform.conf.example).

**Guide:** [deployment/host-nginx-internal.md](../deployment/host-nginx-internal.md)  
**Checklist:** [deployment/checklist-host-nginx.md](../deployment/checklist-host-nginx.md)

## Packages

```bash
sudo apt update
sudo apt install -y git ca-certificates curl
# Docker: official Docker repo or docker.io + docker-compose-plugin
```

## Knovas API from containers

Set `SEMANTIX_API_URL` to a hostname or IP reachable from Docker (not `host.docker.internal` unless the API runs on the same host). Compose maps `host.docker.internal` to the host gateway on Linux when needed.

## Reboot

Optional: [deploy/systemd/knovas-platform.service.example](../../deploy/systemd/knovas-platform.service.example) for host-nginx mode.

**Stop:** `./stop_stack.sh` — [stopping web servers](../../../docs/stopping-web-servers.md).
