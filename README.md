# Knovas Components

> **Design canvas branch.** The Platform's visual design is taken out here so a
> new one can start from nothing; everything still works. Start with
> [DESIGN-CANVAS.md](KnovasPlatform/components/docbridge_integration/src/web_interface/DESIGN-CANVAS.md).

Customer-hosted components for the Knovas platform.

| Folder | Purpose |
|--------|---------|
| [KnovasPlatform/](KnovasPlatform/) | Search web app (Docker) — query indexed documents; with the Brain Builder |
| [Knovas Connector](KnovasConnector/) — folder `KnovasConnector/` | Discover and sync local files into Knovas |
| [Knovas Link](KnovasLink/) — folder `KnovasLink/` | The one door of a company's site to Knovas: the components beside it (the sync component) reach the Brain only through it |
| [Connector kit](KnovasConnectorKit/) — folder `KnovasConnectorKit/` | Draft: the base every connector of protocol v2 is built on, and its conformance suite ([connector-protocol-v2.md](KnovasPlatform/docs/connector-protocol-v2.md)) |

**Connecting a company's sources to its Brain** (Knovas Link, the sync component, the Brain Builder's sites and
approvals; fetching originals and writing changes back as pull requests): start with
[KnovasPlatform/docs/connection-layer.md](KnovasPlatform/docs/connection-layer.md). Built on this branch, not yet
released.

**Send to clients:** [docs/client/](docs/client/) — one page, Demo-Kanzlei from files to search.

**Typical setup:** ingest with Knovas Connector, then deploy KnovasPlatform for search. Both need credentials from Knovas (mTLS, tokens).

Both components use the **same** mTLS bundle but expect different filenames in
different directories. Read [docs/certificates.md](docs/certificates.md) before
copying certs into either one — mismatched names and directory permissions are
the most common setup failure.

```bash
git clone https://github.com/Seifeddini/KnovasComponents.git
cd KnovasComponents
cp knovas.env.example knovas.env   # fill 5 values, add certs to certs/
./scripts/setup.sh && ./scripts/start.sh
```

Unified stack (Knovas Connector + Platform): one `knovas.env`, shared document mount, the Connector on `127.0.0.1:5001` only.

**Documents in OneDrive or SharePoint?** Set `KNOVAS_DOCUMENTS_URL` (the folder's
address from the browser) plus `M365_CLIENT_ID` / `M365_CLIENT_SECRET` instead of
`KNOVAS_DOCUMENTS_PATH`. Nothing is copied onto the server; results open and
preview in OneDrive/SharePoint. See [docs/microsoft-365.md](docs/microsoft-365.md).

**A dedicated Azure server for one client:** `./scripts/azure/create-server.sh`, then
`setup.sh`, `start.sh` and `sudo ./scripts/host-https.sh <name> <email>` on the server.
About an hour, step by step: [docs/azure-server.md](docs/azure-server.md).

The fifth value is `PLATFORM_ADMIN_EMAIL`. There is no default account: first start creates
that administrator and writes a one-time password to `/app/data/platform-admin-bootstrap` inside
the `docbridge-web` container — read it with
`docker compose --env-file knovas.env exec docbridge-web cat /app/data/platform-admin-bootstrap`,
sign in, change it, and delete the file. Set `PLATFORM_ADMIN_PASSWORD` in `knovas.env` to choose
it yourself instead, and `./scripts/admin-password.sh` resets it at any time.

See each folder’s README for component-only dev. **Hosting partners:** [docs/hosting-requirements.md](docs/hosting-requirements.md). To stop Docker or dev web servers: [docs/stopping-web-servers.md](docs/stopping-web-servers.md).

Release: [Releases](https://github.com/Seifeddini/KnovasComponents/releases).
