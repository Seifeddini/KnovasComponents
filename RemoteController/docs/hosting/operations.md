# Operations

## Health

`GET /health` is unauthenticated. Use for load balancers and Knovas admin probes.

- HTTP **200** — `"status":"ok"`; config, watch roots, and scheduler checks are healthy.
- HTTP **503** — `"status":"degraded"`; inspect `checks` (missing env, unreadable watch roots, or scheduler error) before routing traffic.

## Metrics

`GET /metrics` exposes Prometheus format. Unauthenticated — restrict at the network edge if needed.

## Logs

- Structured logs use file **basenames** only (not full paths).
- JWT, instance tokens, PEMs, and file contents are never logged.

## Continuous sync

- Check `GET /sync/status` for `scheduler_state`, `last_run_at`, `files_processed`.
- Stop with `POST /sync/stop`; worker finishes the current file unit then exits.
- State file `.rc-sync-state.json` tracks incremental uploads — back up before upgrades.

## Upgrades

1. Stop continuous worker (`POST /sync/stop`).
2. Replace image or package; preserve `.env`, certs, state, and config volumes.
3. Verify `/health`, then run a test `GET /discover`.
