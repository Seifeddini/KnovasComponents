# Debian

Debian 11/12 follows the same steps as [ubuntu.md](ubuntu.md) for Docker and Knovas Platform.

## Quick start (direct HTTP, dev or LAN)

```bash
git clone <repo-url> ~/KnovasComponents && cd ~/KnovasComponents
cp knovas.env.example knovas.env   # edit
./scripts/setup.sh
./scripts/start.sh
```

Browser on the server: `http://127.0.0.1:8081` (or `DOCBRIDGE_WEB_PORT`). The
stack binds localhost only, so it is not reachable from another machine yet.

## Production: internal DNS + host nginx (HTTPS)

For `https://<internal-fqdn>` with corporate TLS on nginx, the stack needs no
change — it already listens on `127.0.0.1` only. Configure host nginx from
[deploy/host-nginx/knovas-platform.conf.example](../../deploy/host-nginx/knovas-platform.conf.example)
to proxy to it.

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

**Stop:** `./scripts/stop.sh` — [stopping web servers](../../../docs/stopping-web-servers.md).
