# Ubuntu

Follow [setup.md](../setup.md) first. Ubuntu-specific notes:

```bash
git clone <repo-url> ~/KnovasPlatform && cd ~/KnovasPlatform
cp .env.example .env   # edit
chmod +x start_stack.sh scripts/verify_deploy.sh
./start_stack.sh
```

**Knovas API URL:** Prefer a LAN IP or hostname reachable from containers (e.g. `https://192.168.1.50:8443`). Compose maps `host.docker.internal` to the host gateway on Linux.

**Server A:** Mount CIFS; set `OPEN_UNC_ROOT` / `OPEN_CLIENT_LOCAL_ROOT` for how **client PCs** see the share. Users open files from the browser without installing anything — [integration/opening-documents.md](../integration/opening-documents.md).

**Clients:** Same share access as today; no Knovas package. Optional companion only if browser open is blocked.

**LAN access:** Open `DOCBRIDGE_WEB_PORT` (default 8081) in ufw if needed. Use an external TLS proxy for internet exposure.

**Reboot:** Run `docker compose up -d` from this directory via systemd.
