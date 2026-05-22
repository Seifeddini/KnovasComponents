# Configuration

Full environment and scheduler reference. Required variables for boot are listed in [SETUP.md](SETUP.md) step 2.

## Environment variables

See [.env.example](../.env.example) for the complete list with defaults.

## Scheduler config file

Path: `RC_SYNC_CONFIG_PATH` (default `config/remote_controller_sync.json`).

Schema: [contracts/remote_controller_sync_config.schema.json](../contracts/remote_controller_sync_config.schema.json).

Example:

```json
{
  "schema_version": 1,
  "enabled": true,
  "mode": "continuous",
  "window": { "start_local": "08:00", "end_local": "20:00" },
  "rate_limit": { "max_ingestion_requests_per_minute": 30, "burst": 5 },
  "scan_interval_seconds": 60,
  "pause_policy": "finish_current_unit_then_pause"
}
```

## Edge proxy headers

Terminate employee RC mTLS at NGINX/Envoy and forward:

- `X-SSL-Client-Cert` — PEM (URL-encoded tabs as newlines)
- `X-SSL-Client-DN` — subject DN with CN = operator UUID

Only trust these headers from your local reverse proxy (bind RC to `127.0.0.1` behind the proxy). Example: [nginx-edge.example.conf](nginx-edge.example.conf).

## File permissions

Set mode `0600` for:

- Tenant and employee cert/key files
- `.rc-sync-state.json`
- `.rc-sync-last-request.json`
- `config/remote_controller_sync.json`

## Two configuration layers

| Layer | Source | Controls |
|-------|--------|----------|
| What to sync | `POST /sync` JSON body | sources, filters, ingestion |
| When / how fast | `remote_controller_sync.json` | window, rate_limit, continuous mode |

Sync request shape: [examples/sync-request.json](../examples/sync-request.json) and [contracts/sync_request.schema.json](../contracts/sync_request.schema.json).
