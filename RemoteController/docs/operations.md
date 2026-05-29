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
- Sync state is stored in **SQLite** (`.rc-sync-state.db` beside `RC_SYNC_STATE_PATH`). Legacy `.rc-sync-state.json` is imported once on first start and renamed to `.json.migrated`.

### Stop sync

Stop the **background worker** with `POST /sync/stop`. The RC API stays up; only continuous ingestion stops.

```bash
export RC_BASE=http://127.0.0.1:5001   # or your HTTPS edge URL

# Local bypass (no JWT) when RC_INTERNAL_LOCAL_BYPASS=true:
curl -sS -X POST "$RC_BASE/sync/stop"

# Production:
curl -sS -X POST "$RC_BASE/sync/stop" \
  -H "Authorization: Bearer $EMPLOYEE_JWT"

curl -sS "$RC_BASE/sync/status"
```

Confirm stop: `"scheduler_state": "not_running"` and `"worker_alive": false`.

The worker completes the **current file upload** before exiting (`pause_policy`: `finish_current_unit_then_pause`). Already-synced paths remain in SQLite; stopping does not roll back uploads.

To prevent sync from auto-starting after a container restart, set `"enabled": false` in `remote_controller_sync.json` or leave `RC_SYNC_AUTO_START_CONTINUOUS=false` (default).

**Do not confuse** `POST /sync/stop` with `docker compose down` — the latter kills the container and may interrupt an in-flight upload. Stop the worker first, then restart or upgrade the image.

## Large corpora (100s of GB)

- Set **`sequential_subfolders`: true** in `remote_controller_sync.json` when the sync source root contains many top-level folders (e.g. WinJur bucket dirs). RC processes **one subfolder per cycle**, then advances automatically when that folder has no pending uploads.
- Set `max_files_per_cycle` (e.g. 200–500) to cap uploads per cycle.
- Set `max_scan_entries_per_cycle` (e.g. 10000) to cap filesystem work per cycle on slow SMB mounts.
- Use a **24h sync window** (`00:00`–`23:59`) for initial backfill; the default is no longer limited to business hours.
- Use `scan_interval_idle_max_seconds` so steady-state rescans back off when nothing is pending.
- `POST /sync` responses cap `transmissions` (default 100 entries); counts in `document_sync` remain full.
- **Do not** use `GET /sync/status?deep_scan=1` on huge trees — it is capped by `max_scan_entries_per_cycle` but still runs in the HTTP worker. Prefer logs and `GET /sync/status?live=1`.
- Example scheduler config for WinJur: [config/remote_controller_sync.winjur.example.json](../config/remote_controller_sync.winjur.example.json).
- Uploads stream file parts (bounded RAM per file). Initial ingest wall-clock still depends on Semantix rate limits.

## Upgrades

1. Stop continuous worker (`POST /sync/stop`).
2. Replace image or package; preserve `.env`, certs, state, and config volumes.
3. Verify `/health`, then run a test `GET /discover`.

Use a **single** Gunicorn worker (`-w 1`) when running from source; multiple workers conflict on scheduler state.
