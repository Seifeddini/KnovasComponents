# Setup guide

Monorepo path: `KnovasComponents/`. The Platform has no stack of its own any
more: RemoteController, the identity database and the search UI come up together
from the repo root, driven by one `knovas.env`. Run every command below from
`KnovasComponents/`, not from `KnovasPlatform/`.

## 1. What you get

This folder is a **search web app** for your Knovas tenant (Docker). It does **not** index documents.

Ingest and sync documents first with [RemoteController](../../RemoteController/), then complete this guide.

## 2. Before you start

- Knovas tenant and mTLS client certificate — see the [API integration kit](../../docs/KnovasAPI/README.md) and [certificates.md](../../docs/certificates.md)
- Documents already indexed in Knovas
- Docker Engine and Compose; outbound HTTPS to your Knovas API (port 8443 is typical)

Platform-specific notes: [platforms/ubuntu.md](platforms/ubuntu.md), [platforms/debian.md](platforms/debian.md), [platforms/windows.md](platforms/windows.md).

**HTTPS with internal DNS (host nginx):** the stack already binds
`127.0.0.1` only, so nothing special is needed to start it — put nginx in front
of it following [deployment/host-nginx-internal.md](deployment/host-nginx-internal.md).

## 3. Configure

```bash
cd KnovasComponents
cp knovas.env.example knovas.env
```

On Windows (host shell):

```powershell
Copy-Item knovas.env.example knovas.env
```

`knovas.env` is the only file you edit. `./scripts/setup.sh` expands it into the
per-component `.env.generated` files, which are overwritten on every run — edit
`knovas.env` and re-run setup instead of editing them.

Required:

| Variable | Meaning |
|----------|---------|
| `KNOVAS_API_URL` | `https://<knovas-api-host>:8443`, reachable **from inside** the container |
| `KNOVAS_PLATFORM_URL` | `https://<your-fqdn>` — how users reach this app; also `OPEN_PUBLIC_BASE_URL` |
| `KNOVAS_DOCUMENTS_PATH` | Host path of the document share, mounted read-only |
| `PLATFORM_ADMIN_EMAIL` | The firm's first administrator; there is no default account |

`WEB_SECRET_KEY` is generated if you leave it empty, and `KNOVAS_TENANT_ID` is
read from `certs/organisation_id.txt` or the client certificate's CN when unset
— it becomes `SEMANTIX_CUSTOMER_ID`, the tenant signed into every
`principal_assertion`.

Per-user identity is **on by default**. Do **not** set `COMPANY_LOGIN_NAME` /
`COMPANY_LOGIN_PASSWORD`: the shared firm credential is superseded, and the
Platform refuses to start with both doors open. `setup.sh` refuses that
combination too, rather than letting it surface four minutes later as an
unhealthy container. To stage a cutover on an existing deployment, set
`IDENTITY_ENABLED=false` **and** keep both old values.

For **search only** (no UNC file open), leave `KNOVAS_SHARE_UNC` empty.

## 4. Certificates

Place the files Knovas ships, under **their original names**, in the repo
root `certs/` — one directory for both components now:

```bash
cd KnovasComponents
mkdir -p certs
cp /path/to/client-cert.pem certs/
cp /path/to/client-key.pem  certs/
cp /path/to/ca-root.pem     certs/
chmod 600 certs/client-key.pem
```

`./scripts/setup.sh` creates the `client.crt` / `client.key` / `ca.crt` names the
Platform expects as symlinks beside them, and hands RemoteController the `.pem`
spelling it wants — so there is no renaming to get wrong. Cross-component
reference: [docs/certificates.md](../../docs/certificates.md).

Confirm mTLS works before starting the stack — this bypasses the app, so a
failure here is a certificate or network problem, not a config one:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  --cert certs/client.crt --key certs/client.key --cacert certs/ca.crt \
  https://api.knovas.ch:8443/secured/health
```

### 4.1 Per-user identity: the broker signing key

On by default (`IDENTITY_ENABLED=true`). The Platform generates an Ed25519 key
on first start in `PLATFORM_BROKER_KEY_DIR` (a persistent volume, default
`/app/secrets/broker`) and signs each signed-in user into every Knovas call.
Register the public half (`broker_ed25519.pub`) with Knovas, back the
directory up, and set `SEMANTIX_CUSTOMER_ID`. Details and the failure modes:
[../../docs/certificates.md](../../docs/certificates.md#knovasplatform-the-broker-signing-key-per-user-identity).

## 5. Run and verify

```bash
cd KnovasComponents
./scripts/setup.sh
./scripts/start.sh
./KnovasPlatform/scripts/verify_deploy.sh
```

`setup.sh` installs the tenant certificates, generates
`secrets/platform_db_password` (mode 0600), and expands `knovas.env`. It is
idempotent: re-run it after any `knovas.env` change. `start.sh` builds and
starts everything — the first build after a pull takes several minutes.

**Code changes** need an image rebuild, because app code and static CSS are
baked into the image. `start.sh` passes `--build`, so re-running it is enough:

```bash
./scripts/start.sh
```

**Config-only changes** (`knovas.env`) need setup plus a recreate:

```bash
./scripts/setup.sh && ./scripts/start.sh
```

**First sign-in.** There is no default account. The first start creates
`PLATFORM_ADMIN_EMAIL` and writes a one-time password inside the container:

```bash
docker compose --env-file knovas.env exec docbridge-web cat /app/data/platform-admin-bootstrap
```

Sign in with it, change the password, then delete the file. The path is on a
named volume, so it survives a container recreate.

Prefer not to depend on that file at all? Set `PLATFORM_ADMIN_PASSWORD` in
`knovas.env` and the account gets that password with nothing written to disk.
Either way, `./scripts/admin-password.sh` sets or resets an account's password
later — it creates the account as an administrator if it does not exist, and
clears any lockout.

- Browser: `http://127.0.0.1:8081` on the server (`DOCBRIDGE_WEB_PORT`), or
  `https://<fqdn>` once nginx is in front of it
- `/api/health` should report the Knovas API as reachable when configured

No tenant yet? Use [demo.md](demo.md) instead of steps 3–5 against a real API.

To stop the stack: `./scripts/stop.sh` — see [stopping web servers](../../docs/stopping-web-servers.md).

## 6. Optional: open files from AutoDoc (client-side)

Skip for search-only deployments.

**Model:** Server A hosts the app; users on other PCs click **Öffnen** in the browser. The file opens **on their PC** via the share they already use — **no Knovas install** on clients (`OPEN_BROWSER_CLIENT_PATH=true`, default).

**On Server A:**

1. Mount the share; set `AUTODOC_MOUNT_PATH` and `OPEN_LOCAL_ROOT=/mnt/autodoc`.
2. Set `OPEN_UNC_ROOT` (Windows clients) and/or `OPEN_CLIENT_LOCAL_ROOT` (Linux clients).
3. Keep `OPEN_COMPANION_ENABLED=false` unless browser open is blocked by IT policy.
4. Leave `OPEN_ALLOW_SERVER_SIDE_STARTFILE=false`.

Clients only need share access + a normal browser. Details: [integration/opening-documents.md](integration/opening-documents.md). DFS aliases: `open.unc_roots` in [config.yaml](../components/docbridge_integration/config/config.yaml).

## 7. Optional: production hardening

- **Internal DNS + TLS on host nginx:** follow [deployment/host-nginx-internal.md](deployment/host-nginx-internal.md) — nginx template in `deploy/host-nginx/`, checklist in [deployment/checklist-host-nginx.md](deployment/checklist-host-nginx.md)
- The stack binds **`127.0.0.1` only** — both the UI (`8081`) and RemoteController (`5001`). Reaching it from another machine means putting a reverse proxy in front; there is no mode that exposes it directly
- Firewall: allow **443** at nginx; 8081 and 5001 stay unreachable from other hosts
- Use a strong `WEB_SECRET_KEY`; restrict `/api/open-tokens/redeem` to client subnets when possible
- Multiple Gunicorn workers weaken one-time token replay protection — prefer one worker or sticky sessions

## 8. Issues

See [integration/troubleshooting.md](integration/troubleshooting.md).
