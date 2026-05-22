# Knovas Platform

Ready-to-run **search web app** for your Knovas tenant (Docker), usually on a Linux server. **Öffnen** uses the browser to open files on each user's PC via the shared drive — no client install ([docs/integration/opening-documents.md](docs/integration/opening-documents.md)).

**Ingest documents first** with [RemoteController](../RemoteController/) (or your own pipeline). This app only searches already-indexed content.

## Get started

1. Open [docs/README.md](docs/README.md)
2. Follow [docs/setup.md](docs/setup.md)

Requirements: mTLS client certs, Docker Compose, configured `.env`.

**Demo without a tenant:** [docs/demo.md](docs/demo.md)

**Knovas HTTP API:** [knovas-docs/Knovas_Developer_Implementation_Kit/README.md](knovas-docs/Knovas_Developer_Implementation_Kit/README.md)
