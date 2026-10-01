# Configuration

Full environment and scheduler reference. Required variables for boot are listed in [SETUP.md](SETUP.md) step 2.

## Environment variables

See [.env.example](../.env.example) for the complete list with defaults.

### RC_PLATFORM_BROKER_PUBKEY_PATH

Path to the firm's Platform's `broker_ed25519.pub`, mounted read-only. With it
set, a signed-in user carrying the `admin` or `ingestion_manager` role in the
Platform's `X-Platform-Principal` assertion may use `/discover`, `/sync`,
`/sync/body`, `/sync/config`, and `/sync/start|stop|status` through the console,
beside the existing Knovas-employee JWT path. Without it, only Knovas employees
can (the header is refused with 403 when this path is unset).

`POST /sync/body` stores the folder list and starts nothing; a running
continuous worker picks it up at its next cycle. Use it, not `POST /sync`,
when the intent is to save a configuration rather than to run one now.

The Platform console's *Speichern und uebertragen* writes `POST /sync/config`
(after reading the current one with `GET /sync/config`), so the push only works
with `RC_SYNC_CONFIG_API_ENABLED=true`; with the default `false` the API answers
404 and the console cannot transfer the profile at all.

### Search context sidecars

Set `SEARCH_CONTEXT_STORE_PATH` to a directory shared with docbridge-web (same pattern as `ONEDRIVE_SEARCH_ENRICHMENT_PATH` / `SEARCH_ENRICHMENT_PATH`). RemoteController writes one JSON file per uploaded document during sync; docbridge reads them at query time to show first-page previews and match context in search results.

Backfill existing corpora without re-uploading. In the unified stack, run it in
a one-off RemoteController container from the repository root; `--identifier-prefix`
must be the prefix the documents were ingested with (`KNOVAS_IDENTIFIER_PREFIX`),
or the Platform never finds the text:

```bash
docker compose --env-file knovas.env run -d --rm --name knovas-snippet-backfill \
  -v "$PWD/RemoteController/scripts:/app/scripts:ro" remote-controller \
  python /app/scripts/build_context_sidecars.py --jobs 2 \
  --identifier-prefix <prefix> --store-dir /var/rc-state/search_context
docker logs -f knovas-snippet-backfill
```

It logs its progress once a minute and leaves a sidecar that is already newer than
its file alone, so a run that was stopped continues where it was (`--force`
rewrites them all). `--jobs` is how many documents are extracted at once, one CPU
core each. `./scripts/doctor.sh` reports how much of the share has text, and
whether a backfill is running.

**Rerun it after an upgrade of the RC image** (`--force`): the sidecar holds the
extractor's text, and a release that changes extraction — per-page OCR, the
layout mode — changes the text the Platform shows under a hit. The sidecar is
always built from the unmarked text; page-break markers exist only on the wire.

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
| `.pdf` | `pymupdf` (per-page text; sentence page back-pointers; per-page OCR of image pages via Tesseract) |
| `.eml` | Standard library `email` (subject → transmission title) |
| `.msg` | `extract-msg` (subject → transmission title) |

Each chunk carries a `page_number` (PDFs only) and a `sentence_number` derived from `content.sentences` — every sentence has an exact `char_start` offset into `content.text`, guaranteed by a dispatcher post-condition.

`ingestion.part_max_chars` defaults to `500000` (the Secure API `snippet` limit). Lower it in the sync request body if you need smaller transmission parts.

**Open/download:** Ingest uses the **original** relative path in `identifier` (e.g. `corpus/akten/Brief.pdf`). KnovasPlatform resolves search pointers to that path on the AutoDoc mount, so clients open the original file—not the converted text.

Align deployment with KnovasPlatform:

- Mount the same tree of originals on RC watch roots and `AUTODOC_MOUNT_PATH`.
- Set `ingestion.identifier_prefix` equal to `AUTODOC_IDENTIFIER_PREFIX` (e.g. both `corpus`).

Scanned PDF pages without a text layer are OCR'd when `RC_PDF_OCR_ENABLED` is true (default) and Tesseract is installed in the container. Set `RC_TESSERACT_LANG` (default `deu+eng`) for language packs. Markdown is never requested from the extractor (it cost ~8 s per document and parked mixed PDFs and large-table DOCX as "markdown expansion ratio" — see [operations.md](operations.md#documents-parked-by-the-markdown-expansion-guard)).

**Docker build:** the Dockerfile installs `knovas-extract` from git. `KNOVAS_EXTRACT_REF` (default `main`) selects the revision — pin it to a tag or sha per RC release: `docker compose build --build-arg KNOVAS_EXTRACT_REF=v0.4.0a1 remote-controller`. `--build-arg KNOVAS_EXTRACT_FROM_GIT=` installs from PyPI instead. The image sets `TESSDATA_PREFIX` and `OMP_THREAD_LIMIT=1` and ships the `deu`, `eng`, `fra` and `ita` models; extraction performs no network I/O.

### Extraction, OCR and page markers

All read from the environment by `src/sync/document_text.py`, `knovas_uploader.py`, `sync_executor.py` and `ocr_cache.py` (not by `config.py`). Keywords the installed `knovas-extract` does not take are withheld, so the same image runs against 0.3 (today) and 0.4 (`text_mode=`, `ocr=`).

| Variable | Default | Meaning |
|----------|---------|---------|
| `RC_PDF_OCR_ENABLED` | `true` | OCR image pages of PDFs (`use_ocr="auto"`). `false` keeps text layers only. |
| `RC_TESSERACT_LANG` | `deu+eng` | Tesseract language packs (at most two; `deu+fra`, `deu+ita` per tenant). |
| `RC_PDF_TEXT_MODE` | `plain` | `plain` — today's text. `layout` — markdown-lite rows for fiduciary tables (knovas-extract ≥ 0.4). `shadow` — upload plain, also render layout from the SAME OCR cache (each page OCR'd once) and log one numbers-only `ShadowDiff` line (numeric-token Jaccard, row-line ratios, length ratio, OCR pages, seconds — never text). Falls back to `plain` with a warning when the library has no `text_mode`. |
| `RC_OCR_ENGINE` | `auto` | `auto` / `tesserocr` / `cli` / `mupdf` (knovas-extract ≥ 0.4). |
| `RC_OCR_DPI` | `300` | Render dpi ceiling; the library never upsamples a lower-resolution scan. |
| `RC_OCR_WORKERS` | `max(1, cores − 2)` | OCR pages in parallel, at most 8. |
| `RC_OCR_MAX_PAGES` | `500` | OCR page budget per document. Beyond it the remaining image pages are skipped and COUNTED; the document is uploaded and recorded `partial` for the backfill. |
| `RC_OCR_TIME_BUDGET_SECONDS` | `min(240, timeout − 30)` | OCR time budget per document; never more than `timeout − workers × page_timeout − 10` so the partial result reaches the parent before the wall-clock kill. |
| `RC_OCR_PAGE_TIMEOUT_SECONDS` | `60` | Ceiling for one page's OCR. |
| `RC_OCR_CACHE_MAX_MB` | `512` | OCR disk cache cap (`.rc-ocr-cache.db` beside `RC_SYNC_STATE_PATH`, LRU, mode 0600). `0` disables it: no file, every lookup misses. See [operations.md](operations.md#ocr-disk-cache). |
| `RC_EXTRACT_TIMEOUT_SECONDS` | `300` | Wall-clock ceiling for one document's extraction (child process). `0` extracts in-process without a ceiling. |
| `RC_EXTRACT_TIMEOUT_PER_PAGE_SECONDS` | `2` | For PDFs the ceiling is at least this × page count (the child reports the count first). |
| `RC_EXTRACT_TIMEOUT_MAX_SECONDS` | `1800` | Cap of the page-scaled ceiling. |
| `RC_EXTRACT_MAX_RETRIES` | `3` | Retryable extraction failures (wall-clock kill, killed child, transient error) a file may collect; after the cap it is recorded `partial` (`extract_retries_exhausted`) and the backfill runs it with OCR disabled. Secure API failures never count. |
| `RC_EXTRACT_RLIMIT_AS_MB` | `2048` | Address-space limit of the extraction child (`RLIMIT_AS`); `0` disables. The child also runs at `nice 10`. |
| `RC_PAGE_BREAK_MARKERS` | `true` | Form feed(s) before every text-page start inside a part so the server serves a hit on its own page (GI-INGEST-17). The part's `page_number` stays the page of its first character; the context sidecar is written from the unmarked text. |
| `RC_SEND_PDF_TABLES` | `false` | Send `tables` payloads for PDF parts. The server drops them at the Redis buffer; table rows have to live in the text (`RC_PDF_TEXT_MODE=layout`). DOCX tables are still sent. |
| `RC_UPLOAD_ORDER` | `small_first` | Order of a cycle's upload queue: smallest file first (`scan` keeps the directory order). |
| `RC_SENTENCE_EMIT_MAX_BYTES` | `2097152` | Inputs above this skip sentence emission (citations and context previews), the text is still uploaded. |

Legacy `.doc` is not supported in v1. Raise `max_file_bytes` in the sync body for large PDFs (default 10 MiB).

## Microsoft 365 (OneDrive / SharePoint) as the document source

| Variable | Meaning |
|----------|---------|
| `M365_FOLDER_URL` | The folder's address as the browser shows it. When set, the folder **is** the watch root: `/mnt/documents` stands for the folder, `/mnt/documents/Akten` for its subfolder `Akten`, so sync bodies and `/discover` work unchanged. |
| `M365_CLIENT_ID` / `M365_CLIENT_SECRET` | Entra app with the Microsoft Graph application permission `Sites.Read.All` (admin consent). |
| `M365_TENANT_ID` | Optional; derived from the address (`contoso.sharepoint.com` -> `contoso.onmicrosoft.com`). |
| `M365_STATE_DIR` | Default `<dir of RC_SYNC_STATE_PATH>/m365`: resolution cache, item inventory, delta position. |
| `M365_LINKS_PATH` | Default `$M365_STATE_DIR/links.jsonl`: identifier -> web URL for the Platform. |

Files are fetched from Microsoft Graph only when new or changed, into a temporary
directory removed after upload; the server keeps no copy. The inventory follows
Graph's delta feed by item id (delta carries no paths; a folder rename reports
the folder alone). A failed refresh stops the cycle before anything is pruned.
Routes: `/discover` lists the folder; `POST /m365/preview` returns Graph's
embeddable viewer URL for a published identifier. Diagnose with
`python -m m365.check`. In the unified stack these come from
`KNOVAS_DOCUMENTS_URL` / `M365_*` in `knovas.env`; see `docs/microsoft-365.md`.
The legacy `ONEDRIVE_*` mirror is not started while `M365_FOLDER_URL` is set.
