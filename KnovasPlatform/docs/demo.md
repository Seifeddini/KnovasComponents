# Demo (mock API)

Use this only when you do not have a Knovas tenant yet. Do not expose mock services on production networks.

In `.env`, set mock URL and legacy mode per comments in [.env.example](../.env.example):

- `SEMANTIX_API_URL=http://semantix-mock:5000`
- `SEMANTIX_USE_SECURED_API=false`
- `SEMANTIX_ALLOW_LEGACY_API_FALLBACK=true`

Then start the stack with the mock profile:

```bash
docker compose --profile mock up -d --build
```

Verify with `./scripts/verify_deploy.sh` (or `.\scripts\verify_deploy.ps1` on Windows).

For production deployment, follow [setup.md](setup.md) instead.
