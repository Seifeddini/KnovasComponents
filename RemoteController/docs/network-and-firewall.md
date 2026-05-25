# Network and firewall

Remote Controller runs on the **partner network**. Knovas employees reach the URL registered in Knovas admin.

## Partner network checklist

1. **Publish a stable HTTPS base URL** (e.g. `https://rc.customer.example.com`); avoid raw `localhost` unless Knovas shares your VPN.
2. **Open inbound access** so Knovas employee clients can reach:
   - `GET /health` (admin probe + monitoring)
   - `GET /discover`
   - `POST /sync`, `POST /sync/start`, `POST /sync/stop`, `GET /sync/status`
   - `GET`/`POST /sync/config` only if `RC_SYNC_CONFIG_API_ENABLED=true`
3. **Provide Knovas with allowlist details** (source IPs, VPN CIDRs, or mutual TLS at edge).
4. **Terminate HTTPS at the edge** (NGINX/Envoy); employees send `Authorization: Bearer <JWT>`.
5. **Outbound from RC host** must allow:
   - RC → `KNOVAS_INTERNAL_API_URL` (verify)
   - RC → Knovas Secure API base URL in `.env` (`:8443`, tenant mTLS)
6. **Register the base URL** with Knovas admin; confirm health probe from Knovas infrastructure (not only inside your LAN).
7. **TLS certificate** on the public URL must be valid; document renewal responsibility.

## Example curl (via edge)

```bash
curl -sS "https://rc.customer.example.com/health"

curl -sS "https://rc.customer.example.com/discover" \
  -H "Authorization: Bearer <employee_jwt>"
```

Local testing: [local-commands.md](local-commands.md).

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| Probe fails from Knovas | DNS, firewall, or TLS on public URL |
| 401 on discover/sync | Missing or invalid Bearer JWT |
| 403 | Operator not allowlisted or JWT rejected by Knovas verify |
| 503 | RC cannot reach Knovas verify endpoint |
| Sync paused | Outside configured sync window |
