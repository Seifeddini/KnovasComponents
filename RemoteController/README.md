# Remote Controller

Customer-hosted service: discover local files and sync them to **Knovas** (employee JWT; tenant mTLS for ingestion). Supports `.md`, `.txt`, `.docx`, `.pdf`, `.eml`, and `.msg` — binary formats are converted to Markdown for search while document identifiers keep the original path for open/download in KnovasPlatform.

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

## Reset sync entirely

Clears **local** sync progress so RC treats every file as new and re-uploads on the next run. This does **not** remove documents already ingested in Knovas.

1. **Stop the worker** and wait until `worker_alive` is `false`:

```bash
export RC_BASE=http://127.0.0.1:5001   # or your HTTPS edge URL

# /sync/stop takes no arguments but still requires a JSON body (CSRF defense)
curl -sS -X POST "$RC_BASE/sync/stop" \
  -H "Content-Type: application/json" -d '{}'
curl -sS "$RC_BASE/sync/status"        # expect scheduler_state: not_running
```

2. **Delete sync state** in the directory of `RC_SYNC_STATE_PATH` (default `.rc-sync-state.json` → SQLite at `.rc-sync-state.db`):

```bash
# Docker (rc-state volume, default /var/rc-state)
docker compose exec remote-controller rm -f \
  /var/rc-state/.rc-sync-state.db \
  /var/rc-state/.rc-sync-state.db-wal \
  /var/rc-state/.rc-sync-state.db-shm

# Local / from source (project root when RC_SYNC_STATE_PATH is unset)
rm -f .rc-sync-state.db .rc-sync-state.db-wal .rc-sync-state.db-shm
```

Optionally remove `.rc-sync-last-request.json` in the same directory if you also want to clear the saved sync request body.

3. **Start sync again** with `POST /sync` or `POST /sync/start` — see [docs/local-commands.md](docs/local-commands.md).

Re-uploading may create duplicate transmissions in Knovas. To wipe Docker state **and** scheduler config volumes entirely, use `docker compose down -v` ([docs/local-setup.md](docs/local-setup.md#step-8--stop-and-reset)).

## Security

RC routes are authenticated by an **employee Bearer JWT**, which RC validates by
calling back to Knovas (`/remote_controller/verify_operator`) with its
`RC_INSTANCE_TOKEN`. RC itself does **not** verify an employee client
certificate — see the known gap in [docs/SETUP.md](docs/SETUP.md#step-6--configure-the-edge-proxy).

The tenant mTLS certs in `.env` are used only for **outbound** ingestion to
Knovas, never to authenticate inbound RC calls. Filenames and permissions:
[docs/certificates.md](../docs/certificates.md).

Do not expose port 5001 publicly in production — use the edge proxy in [docs/nginx-edge.example.conf](docs/nginx-edge.example.conf).
