# Remote Controller

Customer-hosted service: discover local files and sync text to **Knovas** (employee JWT; tenant mTLS for ingestion).

**After sync**, deploy [KnovasPlatform](../KnovasPlatform/) for search.

## Quick start (local only)

Run on your machine with the API at `http://127.0.0.1:5001` only (no remote access). Full steps: **[docs/local-setup.md](docs/local-setup.md)**.

```bash
git clone https://github.com/Seifeddini/KnovasComponents.git
cd KnovasComponents/RemoteController
cp .env.example .env   # fill Knovas URLs, RC_CLIENT_ID, cert paths — see local-setup.md
docker compose -f docker-compose.yml -f docker-compose.internal.yml up -d --build
curl -sS http://127.0.0.1:5001/health
```

## Docs

| Doc | Use |
|-----|-----|
| [docs/local-setup.md](docs/local-setup.md) | **Start here** — local-only setup and operation |
| [docs/SETUP.md](docs/SETUP.md) | Production: HTTPS edge, employee JWT, go-live |
| [docs/local-commands.md](docs/local-commands.md) | API cheat sheet and pytest |
| [docs/README.md](docs/README.md) | Full doc index |

## Security

Employee RC cert for RC routes; tenant cert for Knovas ingestion only. Do not expose port 5001 publicly in production — use the edge proxy in [docs/nginx-edge.example.conf](docs/nginx-edge.example.conf).
