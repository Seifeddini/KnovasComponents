# Configuration

Full environment and scheduler reference. Required variables for boot are listed in [SETUP.md](SETUP.md) step 2.

## Environment variables

See [.env.example](../.env.example) for the complete list with defaults.

### RC_PLATFORM_BROKER_PUBKEY_PATH

Path to the firm's Platform's `broker_ed25519.pub`, mounted read-only. With it
set, a signed-in user carrying the `admin` or `ingestion_manager` role in the
Platform's `X-Platform-Principal` assertion may use `/discover`, `/sync`,
`/sync/config`, and `/sync/start|stop|status` through the console, beside the
existing Knovas-employee JWT path. Without it, only Knovas employees can (the
header is refused with 403 when this path is unset).

### Search context sidecars

Set `SEARCH_CONTEXT_STORE_PATH` to a directory shared with docbridge-web (same pattern as `ONEDRIVE_SEARCH_ENRICHMENT_PATH` / `SEARCH_ENRICHMENT_PATH`). RemoteController writes one JSON file per uploaded document during sync; docbridge reads them at query time to show first-page previews and match context in search results.

Backfill existing corpora without re-uploading:

```bash
python scripts/build_context_sidecars.py --store-dir /mirror/.search_context --root /data/corpus --identifier-prefix corpus
```

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

### Per-source access groups

Each `sources[]` entry may carry `access_groups`. Every document ingested from
that folder is born with those groups, so a walled folder stays walled across
re-syncs rather than being repaired afterwards.

Omit the key for unrestricted folders. An *absent* key lets the Secure API
apply whatever folder rule covers the pointer; an explicit empty array means
"deliberately unrestricted" and overrides that rule.

**Caveat:** with `sequential_subfolders` enabled, RemoteController processes
one source per cycle (`sync_executor.py` logs `sequential_subfolders requires
exactly one source; using first only`). In that mode the first source's
`access_groups` applies. Use one profile per walled folder if you need
different groups under sequential mode.

## Supported document formats

RemoteController converts the following extensions to text (with per-sentence citations) before chunking and upload. Extraction is delegated to the [`knovas-extract`](https://github.com/knovas/knovas-extract-python) package (hardened backends, deterministic pysbd sentence tokenization, defused XML, ZIP-bomb caps):

| Extension | Backend |
|-----------|---------|
| `.md`, `.txt` | Plain text (chardet encoding detection) |
| `.docx` | `python-docx` + `mammoth` |
| `.pdf` | `pymupdf` (per-page text; sentence page back-pointers) |
| `.eml` | Standard library `email` (subject → transmission title) |
| `.msg` | `extract-msg` (subject → transmission title) |

Each chunk carries a `page_number` (PDFs only) and a `sentence_number` derived from `content.sentences` — every sentence has an exact `char_start` offset into `content.text`, guaranteed by a dispatcher post-condition.

`ingestion.part_max_chars` defaults to `500000` (the Secure API `snippet` limit). Lower it in the sync request body if you need smaller transmission parts.

**Open/download:** Ingest uses the **original** relative path in `identifier` (e.g. `corpus/akten/Brief.pdf`). KnovasPlatform resolves search pointers to that path on the AutoDoc mount, so clients open the original file—not the converted text.

Align deployment with KnovasPlatform:

- Mount the same tree of originals on RC watch roots and `AUTODOC_MOUNT_PATH`.
- Set `ingestion.identifier_prefix` equal to `AUTODOC_IDENTIFIER_PREFIX` (e.g. both `corpus`).

Scanned PDFs without a text layer are OCR'd automatically when `RC_PDF_OCR_ENABLED` is true (default) and Tesseract is installed in the container. Set `RC_TESSERACT_LANG` (default `deu+eng`) for language packs.

**Docker build:** `knovas-extract` 0.3.0 (OCR) may not be on PyPI yet. The Dockerfile installs it from `git+https://github.com/Seifeddini/knovas-extract-python.git@main` by default. After PyPI publish, use `docker compose build --build-arg KNOVAS_EXTRACT_FROM_GIT= remote-controller` to install from PyPI instead.

Legacy `.doc` is not supported in v1. Raise `max_file_bytes` in the sync body for large PDFs (default 10 MiB).
