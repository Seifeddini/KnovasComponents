# Checklist: host nginx + internal DNS

Full guide: [host-nginx-internal.md](host-nginx-internal.md).

## Before deploy

- [ ] Documents indexed in Knovas ([RemoteController](../../../RemoteController/))
- [ ] mTLS files in `KnovasPlatform/certs/` (`client.crt`, `client.key`, `ca.crt`)
- [ ] Internal DNS: `<fqdn>` → server IP on vnet/LAN
- [ ] Internal TLS cert issued and trusted on client PCs

## Server configuration

- [ ] `cp .env.example .env` — secrets, `SEMANTIX_API_URL`, `OPEN_PUBLIC_BASE_URL=https://<fqdn>`
- [ ] `./scripts/start_stack_host_nginx.sh`
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
- [ ] `VERIFY_BASE_URL=https://<fqdn> ./scripts/verify_deploy.sh` (on server)

## Optional

- [ ] AutoDoc mount + file open: [opening-documents.md](../integration/opening-documents.md)
- [ ] systemd on reboot: [knovas-platform.service.example](../../deploy/systemd/knovas-platform.service.example)
