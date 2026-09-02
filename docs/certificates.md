# mTLS certificates — one bundle, three components

Every Knovas component authenticates to the Secure API (`:8443`) with the **same
three files**. Knovas issues them once per tenant.

Each component expects those files under a **different name, in a different
directory**. That is the single most common setup failure, so this page is the
source of truth. Copy the bundle to each component that needs it — do not
symlink between them and do not assume one component's paths work in another.

## Where the files come from

All three originate as JSON fields in the `POST /create_entity` onboarding
response (or `POST /secured/sign_certificate` when you rotate via CSR). See the
[Client Integration Guide](KnovasAPI/Client_Integration_Guide.md). What you name
them on disk is up to you with raw `curl` — but each component expects a
specific spelling.

| Response field | Client Integration Guide | RemoteController | KnovasPlatform | Sensitive |
|---|---|---|---|---|
| `certificate_pem` | `client_cert.pem` | `client-cert.pem` | `client.crt` | No |
| `private_key` | `client_key.pem` | `client-key.pem` | `client.key` | **Yes — mode 0600** |
| `ca_root_cert` | `ca_root_cert.pem` | `ca-root.pem` | `ca.crt` | No |

Underscores in the guide, hyphens in RemoteController, `.crt`/`.key` in
KnovasPlatform. There is no deeper meaning to the difference — just match it.

Knovas may additionally ship `client-key.password.txt`, the passphrase for an
encrypted key. It is sensitive, and it is not a passphrase you chose.

## Per-component placement

| | RemoteController | KnovasPlatform |
|---|---|---|
| **Directory on disk** | `KnovasComponents/certs/` (monorepo root — *not* `RemoteController/certs/`) | `KnovasComponents/KnovasPlatform/certs/` |
| **Mounted at** | `/certs` | `/app/certs` |
| **Env vars** | `SEMANTIX_CLIENT_CERT_PATH`, `SEMANTIX_CLIENT_KEY_PATH`, `SEMANTIX_CA_CERT_PATH` | `SEMANTIX_CLIENT_CERT`, `SEMANTIX_CLIENT_KEY`, `SEMANTIX_CA_CERT` |
| **Container user** | `rcuser`, uid **10001** | root |

Note the env var names differ too: RemoteController uses a `_PATH` suffix,
KnovasPlatform does not. Both are absolute **container** paths, never host paths.
The `SEMANTIX_` prefix is a legacy internal name kept for compatibility — it
refers to the Knovas API.

## RemoteController

RemoteController runs as **uid 10001**, so both the files *and the directory*
must be readable by that uid. A `chmod 600` key inside a `chmod 700` root-owned
directory is unreadable no matter what the file mode says — and `ls -la` from
inside the container still lists it, which makes this failure look like
something else.

Use the script; it handles ownership, the directory traversal bit, and the
optional passphrase:

```bash
cd KnovasComponents/RemoteController
./scripts/install_tenant_certs.sh
```

Equivalent by hand, from the monorepo root:

```bash
sudo chown 10001:10001 certs/client-cert.pem certs/client-key.pem certs/ca-root.pem
sudo chmod 600 certs/client-key.pem
sudo chmod 644 certs/client-cert.pem certs/ca-root.pem
sudo chmod 711 certs          # traversal for uid 10001 — 755 also works
```

**Encrypted key.** `requests` cannot supply a passphrase at runtime. If Knovas
shipped `client-key.password.txt`, decrypt once (the script does this
automatically) and point `.env` at the decrypted file:

```bash
openssl pkey -in certs/client-key.pem \
  -passin "pass:$(tr -d '[:space:]' < certs/client-key.password.txt)" \
  -out certs/client-key.plain.pem
chmod 600 certs/client-key.plain.pem
```

```env
SEMANTIX_CLIENT_KEY_PATH=/certs/client-key.plain.pem
```

That passphrase comes from Knovas — it is not one you chose at install time.

### Verify RemoteController can actually read them

`ls -la` is not sufficient; it shows modes, not whether uid 10001 can `open()`
each file. Run as the image's own user:

```bash
docker compose -f docker-compose.yml -f docker-compose.internal.yml exec remote-controller \
  sh -c 'id; for f in /certs/*.pem; do
    if head -c 1 "$f" >/dev/null 2>&1; then echo "READ OK  $f"; else echo "DENIED   $f"; fi
  done'
```

Then confirm the handshake end to end:

```bash
docker compose -f docker-compose.yml -f docker-compose.internal.yml exec remote-controller \
  python3 -c "import requests; from config import get_config; c=get_config(); \
r=requests.get(c.semantix_secure_base_url+'/secured/health', \
cert=(c.semantix_client_cert_path, c.semantix_client_key_path), \
verify=c.semantix_ca_cert_path, timeout=30); print(r.status_code, r.text[:200])"
```

After changing cert files or paths, **recreate** the container — `restart` keeps
the old mount and the old in-process TLS context:

```bash
docker compose -f docker-compose.yml -f docker-compose.internal.yml up -d
```

## KnovasPlatform

```bash
cd KnovasComponents/KnovasPlatform
cp /path/to/client-cert.pem certs/client.crt
cp /path/to/client-key.pem  certs/client.key
cp /path/to/ca-root.pem     certs/ca.crt
chmod 600 certs/client.key
```

Verify, bypassing the app entirely:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  --cert certs/client.crt --key certs/client.key --cacert certs/ca.crt \
  https://api.knovas.ch:8443/secured/health
```

`200` means certs, tenant, and network are all good. Then `./scripts/verify_deploy.sh`,
which also checks the three filenames are present.

## KnovasPlatform: the broker signing key (per-user identity)

With `identity.enabled: true` the Platform signs the signed-in person into every
Knovas call with an **Ed25519 key it generates itself** on first start. Knovas
holds the public half against your tenant; whoever holds the private half can
assert any of your people. Three files live in `identity.broker_key_dir`
(`PLATFORM_BROKER_KEY_DIR`, default `/app/secrets/broker` inside the container):

| File | What it is | Handling |
|------|------------|----------|
| `broker_ed25519.pem` | the private key, `0600` | never leaves the host, never in an image, never in a log |
| `broker_ed25519.pub` | the public key | register it with Knovas (Employee Kit); safe to mail |
| `broker_ed25519.kid` | the key id, derived from the public key | Knovas selects the registered key by it |

**Back the directory up, and mount it as a persistent volume.** The Platform
refuses to start if the key file exists but cannot be read -- it will *not*
generate a replacement, because a fresh key signs perfectly well and every
assertion would then be rejected by a Knovas still holding the old public key,
surfacing days later as "search returns nothing". But an *empty* directory is
a first start, and a new key is created: recreate the container without the
volume and you have silently rotated your key. Losing the key means
re-registering the public half with Knovas.

Also required with identity on: `SEMANTIX_CUSTOMER_ID` (`api.customer_id`).
The assertion is bound to the tenant; without the id the Platform refuses to
start rather than sign an unbound token.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `SSLError(PermissionError(13, 'Permission denied'))` | Container found the files but cannot read them — usually the key, or the directory lacks the traversal bit for uid 10001 | Run `install_tenant_certs.sh`, then `up -d` to recreate |
| `invalid path` / `No such file or directory` on a cert | `.env` names do not match the files on disk, or certs are in `RemoteController/certs/` instead of the monorepo root | Compare `grep SEMANTIX .env` against `ls -la ../certs/` |
| `certificate verify failed` | Wrong CA, or cert and key are not a matching pair | Confirm `ca-root.pem` is the Knovas root CA; re-request the bundle |
| `401 Client certificate not authorized` | Certificate is valid but the tenant is not provisioned, or you are using an old bundle | Contact Knovas — this is server-side, not a local file problem |
| Fixed permissions, error unchanged | You read a cached status from before the fix, or used `restart` instead of `up -d` | Check `last_run_at` in `/sync/status`; recreate the container |

The errors above are ordered by how deep they occur in the handshake. Reaching a
*later* one means the earlier layers now work.

## Related

- [RemoteController local setup](../RemoteController/docs/local-setup.md) · [production setup](../RemoteController/docs/SETUP.md)
- [KnovasPlatform setup](../KnovasPlatform/docs/setup.md)
- [Client Integration Guide](KnovasAPI/Client_Integration_Guide.md) — raw API access without either component
