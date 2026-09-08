# Demo (mock API)

This is the **offline mock API**, not the lawyer file corpus. Client pack for the generated Kanzlei: [../../docs/client/](../../docs/client/).

Use this only when you do not have a Knovas tenant yet. Do not expose mock services on production networks.

Run everything from the repo root. Point `knovas.env` at the mock and expand it:

```bash
cd KnovasComponents
cp knovas.env.example knovas.env
```

Set `KNOVAS_API_URL=http://knovas-mock:5000` (the compose service name), fill
`KNOVAS_PLATFORM_URL`, `KNOVAS_DOCUMENTS_PATH` and `PLATFORM_ADMIN_EMAIL`, then:

```bash
./scripts/setup.sh
docker compose --env-file knovas.env --profile mock up -d --build
```

The mock is **profile-gated**: a normal `./scripts/start.sh` never starts it.

The mock speaks the legacy unsecured API, so also set
`SEMANTIX_USE_SECURED_API=false` and `SEMANTIX_ALLOW_LEGACY_API_FALLBACK=true`
in `KnovasPlatform/.env.generated` after running setup — that file is
regenerated on every `setup.sh`, so re-apply it if you re-run setup.

Verify with `./KnovasPlatform/scripts/verify_deploy.sh` (or
`.\KnovasPlatform\scripts\verify_deploy.ps1` on Windows).

Stop with `docker compose --env-file knovas.env --profile mock down`, or see
[stopping web servers](../../docs/stopping-web-servers.md).

For production deployment, follow [setup.md](setup.md) instead.
