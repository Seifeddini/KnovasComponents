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
  "max_document_age_seconds": 2592000,
  "pause_policy": "finish_current_unit_then_pause"
}
```

Optional `max_document_age_seconds` sets the default maximum file age (by `mtime`) for sync. Per-request `filters.max_document_age_seconds` in the sync body overrides this value when set.

## Edge proxy

Terminate HTTPS at NGINX/Envoy and proxy to RC. Employee requests use `Authorization: Bearer <JWT>` only. Example: [nginx-edge.example.conf](nginx-edge.example.conf).

## File permissions

Set mode `0600` for:

- Tenant cert/key files
- `.rc-sync-state.json` (path from `RC_SYNC_STATE_PATH`, e.g. `/var/rc-state/.rc-sync-state.json`)
- `.rc-sync-last-request.json` (same directory as the sync state file)
- `config/remote_controller_sync.json`

## Two configuration layers

| Layer | Source | Controls |
|-------|--------|----------|
| What to sync | `POST /sync` JSON body | sources, filters (`max_file_bytes`, `max_document_age_seconds`), ingestion |
| When / how fast | `remote_controller_sync.json` | window, rate_limit, continuous mode, optional `max_document_age_seconds` default |

**Max document age:** Files whose `mtime` is older than the effective limit are not uploaded. They appear in `document_sync` with status `excluded_max_age` (unless already synced at the same fingerprint). Effective limit = `filters.max_document_age_seconds` in the sync body if set, else `max_document_age_seconds` in the scheduler config, else no limit.

Sync request shape: [examples/sync-request.json](../examples/sync-request.json) and [contracts/sync_request.schema.json](../contracts/sync_request.schema.json).

## Supported document formats

RemoteController converts the following extensions to Markdown (or plain text) before chunking and upload:

| Extension | Handling |
|-----------|----------|
| `.md`, `.txt` | Read as UTF-8 text |
| `.docx` | Structure-aware Markdown (`python-docx`) |
| `.pdf` | Per-page text with `## Page N` headings (`pymupdf`) |
| `.eml` | Headers + body (`email` stdlib) |
| `.msg` | Headers + body (`extract-msg`) |

**Open/download:** Ingest uses the **original** relative path in `identifier` (e.g. `corpus/akten/Brief.pdf`). KnovasPlatform resolves search pointers to that path on the AutoDoc mount, so clients open the original file—not the converted text.

Align deployment with KnovasPlatform:

- Mount the same tree of originals on RC watch roots and `AUTODOC_MOUNT_PATH`.
- Set `ingestion.identifier_prefix` equal to `AUTODOC_IDENTIFIER_PREFIX` (e.g. both `corpus`).

Scanned PDFs without a text layer fail sync with a conversion error. Legacy `.doc` is not supported in v1. Raise `max_file_bytes` in the sync body for large PDFs (default 10 MiB).
