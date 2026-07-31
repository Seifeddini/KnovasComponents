# Knovas Components

Customer-hosted components for the Knovas platform.

| Folder | Purpose |
|--------|---------|
| [KnovasPlatform/](KnovasPlatform/) | Search web app (Docker) — query indexed documents |
| [RemoteController/](RemoteController/) | Discover and sync local files into Knovas |

**Typical setup:** ingest with RemoteController, then deploy KnovasPlatform for search. Both need credentials from Knovas (mTLS, tokens).

Both components use the **same** mTLS bundle but expect different filenames in
different directories. Read [docs/certificates.md](docs/certificates.md) before
copying certs into either one — mismatched names and directory permissions are
the most common setup failure.

```bash
git clone https://github.com/Seifeddini/KnovasComponents.git
cd KnovasComponents
cp knovas.env.example knovas.env   # fill 4 values, add certs to certs/
./scripts/setup.sh && ./scripts/start.sh
```

Unified stack (RC + Platform): one `knovas.env`, shared document mount, RC on `127.0.0.1:5001` only.

See each folder’s README for component-only dev. **Hosting partners:** [docs/hosting-requirements.md](docs/hosting-requirements.md). To stop Docker or dev web servers: [docs/stopping-web-servers.md](docs/stopping-web-servers.md).

Release: [Releases](https://github.com/Seifeddini/KnovasComponents/releases).
