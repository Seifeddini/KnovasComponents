# Remote Controller

Customer-hosted service: discover local files and sync text to **Knovas** (employee JWT; tenant mTLS for ingestion).

**After sync**, deploy [KnovasPlatform](../KnovasPlatform/) for search.

## Quick start

```bash
git clone https://github.com/Seifeddini/KnovasComponents.git
cd KnovasComponents/RemoteController
cp .env.example .env   # fill required values
docker compose up -d --build
curl -sS http://127.0.0.1:5001/health
```

**Full setup:** [docs/SETUP.md](docs/SETUP.md)

**Local curl, sync, and tests:** [docs/local-commands.md](docs/local-commands.md)

## Docs

[docs/README.md](docs/README.md)

## Security

Employee RC cert for RC routes; tenant cert for Knovas ingestion only. Do not expose port 5001 publicly — use the edge proxy in [docs/nginx-edge.example.conf](docs/nginx-edge.example.conf).
