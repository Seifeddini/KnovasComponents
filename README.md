# Knovas Components

Customer-hosted components for the Knovas platform.

| Folder | Purpose |
|--------|---------|
| [KnovasPlatform/](KnovasPlatform/) | Search web app (Docker) — query indexed documents |
| [RemoteController/](RemoteController/) | Discover and sync local files into Knovas |

**Typical setup:** ingest with RemoteController, then deploy KnovasPlatform for search. Both need credentials from Knovas (mTLS, tokens).

```bash
git clone https://github.com/Seifeddini/KnovasComponents.git
cd KnovasComponents
```

See each folder’s README. To stop Docker or dev web servers: [docs/stopping-web-servers.md](docs/stopping-web-servers.md).

Release: [Releases](https://github.com/Seifeddini/KnovasComponents/releases).
