# Operations

Day-to-day curl examples: [local-commands.md](local-commands.md). First-time setup: [SETUP.md](SETUP.md).

## Health

`GET /health` is unauthenticated. Use for load balancers and Knovas admin probes.

- HTTP **200** — `"status":"ok"`; config, watch roots, and scheduler checks are healthy.
- HTTP **503** — `"status":"degraded"`; inspect `checks` (missing env, unreadable watch roots, or scheduler error) before routing traffic.

## Metrics

`GET /metrics` exposes Prometheus format. Unauthenticated — restrict at the network edge if needed.

## Logs

- Structured logs use file **basenames** only (not full paths).
- JWT, instance tokens, PEMs, and file contents are never logged.

Docker logs:

```bash
docker compose logs -f remote-controller
```

## Continuous sync

- Check `GET /sync/status` for `scheduler_state`, `last_run_at`, `files_synced_local`, and `document_sync` (`synced`, `pending`, `modified`, `excluded_max_age`).
- `files_processed` only increases when a full scheduler cycle completes (continuous mode may run one very long cycle over thousands of files). Prefer `files_synced_local` or `GET /sync/status?live=1` for progress while syncing.
- Use `GET /sync/status?live=1` for lightweight progress (`files_synced_local` from SQLite). Use `GET /sync/status?live=1&deep_scan=1` for a full corpus inventory (expensive on huge trees).
- Stop with `POST /sync/stop`; worker finishes the current file unit then exits.
- Sync state is stored in **SQLite** (`.rc-sync-state.db` beside `RC_SYNC_STATE_PATH`). Legacy `.rc-sync-state.json` is imported once on first start and renamed to `.json.migrated`.

## Large corpora (100s of GB)

- Set `max_files_per_cycle` in `remote_controller_sync.json` (e.g. 500–2000) so each scheduler tick bounds uploads.
- Use `scan_interval_idle_max_seconds` so steady-state rescans back off when nothing is pending.
- `POST /sync` responses cap `transmissions` (default 100 entries); counts in `document_sync` remain full.
- Uploads stream file parts (bounded RAM per file). Initial ingest wall-clock still depends on Semantix rate limits.

## Upgrades

1. Stop continuous worker (`POST /sync/stop`).
2. Replace image or package; preserve `.env`, certs, state, and config volumes.
3. Verify `/health`, then run a test `GET /discover`.

Use a **single** Gunicorn worker (`-w 1`) when running from source; multiple workers conflict on scheduler state.
