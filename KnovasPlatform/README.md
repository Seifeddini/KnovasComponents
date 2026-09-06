# Knovas Platform

Ready-to-run **search web app** for your Knovas tenant (Docker), usually on a Linux server. **Öffnen** uses the browser to open files on each user's PC via the shared drive — no client install ([docs/integration/opening-documents.md](docs/integration/opening-documents.md)).

**Ingest documents first** with [RemoteController](../RemoteController/) (or your own pipeline). This app only searches already-indexed content.

## Get started

1. Open [docs/README.md](docs/README.md)
2. Follow [docs/setup.md](docs/setup.md)
3. **HTTPS + internal DNS (host nginx):** [docs/deployment/host-nginx-internal.md](docs/deployment/host-nginx-internal.md)

The stack runs from the **repo root**, not from this folder:

```bash
cd KnovasComponents
cp knovas.env.example knovas.env   # edit
./scripts/setup.sh && ./scripts/start.sh
```

Requirements: mTLS client certs in the root `certs/`, Docker Compose, a filled `knovas.env`.

**Demo without a tenant:** [docs/demo.md](docs/demo.md)

**Knovas HTTP API:** [docs/KnovasAPI/README.md](../docs/KnovasAPI/README.md)

**mTLS certificates:** [docs/certificates.md](../docs/certificates.md) — both components read the **repo root** `certs/`. Drop in the `.pem` files Knovas ships; `./scripts/setup.sh` adds the `client.crt` / `client.key` / `ca.crt` names the Platform expects.
