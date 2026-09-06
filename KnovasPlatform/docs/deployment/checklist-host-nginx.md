# Checklist: host nginx + internal DNS

Full guide: [host-nginx-internal.md](host-nginx-internal.md).

## Before deploy

- [ ] Documents indexed in Knovas ([RemoteController](../../../RemoteController/))
- [ ] mTLS files in the repo root `certs/` (`client-cert.pem`, `client-key.pem`, `ca-root.pem`)
- [ ] Internal DNS: `<fqdn>` → server IP on vnet/LAN
- [ ] Internal TLS cert issued and trusted on client PCs

## Server configuration

- [ ] `cp knovas.env.example knovas.env` — `KNOVAS_API_URL`, `KNOVAS_PLATFORM_URL=https://<fqdn>`, `KNOVAS_DOCUMENTS_PATH`, `PLATFORM_ADMIN_EMAIL`
- [ ] No `COMPANY_LOGIN_*` in `knovas.env` (per-user identity is on by default)
- [ ] `./scripts/setup.sh && ./scripts/start.sh` (binds `127.0.0.1` only)
- [ ] First admin password: `PLATFORM_ADMIN_PASSWORD` in `knovas.env`, or read `/app/data/platform-admin-bootstrap` in the container; `./scripts/admin-password.sh` resets it
- [ ] `curl -fsS http://127.0.0.1:8081/health` → `ok`

## Host nginx

- [ ] Copy [deploy/host-nginx/knovas-platform.conf.example](../../deploy/host-nginx/knovas-platform.conf.example) → `/etc/nginx/sites-available/knovas`
- [ ] Set `server_name`, `ssl_certificate*`, `proxy_pass` port if not 8081
- [ ] `sudo nginx -t && sudo systemctl reload nginx`
- [ ] `curl -fsS https://<fqdn>/health` → `ok`

## Network

- [ ] Firewall: allow **443** from client subnets; **8081** not exposed externally
- [ ] Outbound from Docker to Knovas API works

## Verify from a second machine

- [ ] `nslookup <fqdn>` → correct IP
- [ ] Browser `https://<fqdn>` — no cert warning (if CA deployed)
- [ ] Login and search work
- [ ] `VERIFY_BASE_URL=https://<fqdn> ./KnovasPlatform/scripts/verify_deploy.sh` (on server)

## Optional

- [ ] AutoDoc mount + file open: [opening-documents.md](../integration/opening-documents.md)
- [ ] systemd on reboot: [knovas-platform.service.example](../../deploy/systemd/knovas-platform.service.example)
