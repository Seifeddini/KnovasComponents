# Ubuntu office PC

Follow [first-deploy-checklist.md](first-deploy-checklist.md) first. Ubuntu-specific notes:

```bash
git clone <repo-url> ~/KnovasPlatform && cd ~/KnovasPlatform
cp .env.example .env   # edit
chmod +x start_stack.sh scripts/verify_deploy.sh
./start_stack.sh
```

**Knovas API URL:** Prefer LAN IP/hostname reachable from containers (e.g. `https://192.168.1.50:8443`). Compose adds `host.docker.internal` → host gateway on Linux.

**AutoDoc:** Mount CIFS on host, set `AUTODOC_MOUNT_PATH`, container sees `/mnt/autodoc`.

**LAN access:** Open `DOCBRIDGE_WEB_PORT` (default 8081) in ufw if needed. Use external TLS proxy for internet exposure.

**Reboot:** Run `docker compose up -d` from this directory via systemd.
