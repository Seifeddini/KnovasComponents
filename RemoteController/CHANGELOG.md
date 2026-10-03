# Changelog

## Unreleased

- Renamed to **Knovas Connector** in documentation, the Platform's screens and script output. The folder `RemoteController/`, the Docker service `remote-controller`, the `RC_*` settings and the config keys keep their names, so existing installations upgrade unchanged.

### Extraction (knovas-extract 0.4.0a1)

- `./scripts/doctor.sh` warns about OCR settings in `knovas.env` that the Knovas Connector would replace by its default (same rules, `scripts/lib/rc_extraction_settings.sh`).
- **Web server**: the image runs one gunicorn `gthread` worker with four threads and `--timeout ${RC_GUNICORN_TIMEOUT:-120}` (shell-form CMD, gunicorn stays PID 1). A long `POST /sync` no longer gets the worker — and the scheduler thread — killed, and `GET /sync/status` answers meanwhile: a one-time run counts as a live worker (`running`, `worker_alive: true`), and `POST /sync/stop` ends it after the current file and answers once it has ended. Config and body writes and start/stop are serialised.
- **Description** from file properties when the profile sets none: `docx:subject`, then `pdf:subject`, then `pdf:xmp_description` (the keys read before were never produced, so PDFs had none).
- PyMuPDF pinned to `1.28.0` in `pyproject.toml`, the Platform's version: both images and both CI jobs run one PDF parser under knovas-extract.

### 0.3.0 — Knovas document fields (Dokumentfelder)

Takes effect only for a tenant where Knovas has enabled Document Fields. Against any other server the bodies, the outcomes and the indexing are as in 0.2.0.

- **Per-source fields in the sync body** (`sources[].fields`, `field_templates`, `metadata_fields`; `contracts/sync_request.schema.json`, golden template vectors in `contracts/vectors/field_templates.json`). Fixed values, path-template captures (`{mandant}/{period}/**`, first match wins, matched on the folders of the source-relative path) and opted-in file properties (`language`, `email_date`, `email_doc_type`, `email_author`, `document_author`) are sent as init `fields` — Knovas's upload layer. Precedence per key: capture, fixed value, file property. Entity values are names, never node ids. `.md`/`.txt` carry no author or language.
- **Fields never block indexing.** A refusal caused by the fields re-posts the init once without them (`refused:<code>`); the previous upload-layer values survive. No echo means `not_accepted`, never "stored". Titles are capped at 500 characters (a longer one used to loop forever). `/sync`, `/sync/body` and `/sync/start` refuse a body whose field template does not compile (`400 $.sources[i].field_templates[j]: field_template_invalid (<code>)`); a body stored before keeps working, its bad source skipped per cycle. A fields re-send that keeps failing never uses up the extraction retries and is never recorded partial (`reupload_failed:extract`).
- **Re-uploads on a configuration change**, bounded: a digest per document detects `fields_changed` (`document_sync.fields_changed`); at most `RC_FIELDS_REUPLOAD_PER_CYCLE` (100) per cycle, after new and modified files, and at most `RC_FIELDS_REUPLOAD_MAX_ATTEMPTS` (3) failed attempts before `reupload_failed:<class>`. Each re-upload is a full, billed upload with OCR. A NULL digest equals "nothing configured", so the upgrade re-sends nothing by itself. When the server starts echoing, `not_accepted` documents are queued automatically.
- **`RC_DOC_FIELDS`** (default `on`) can only switch the feature off: no `fields` key, no digest, unchanged bodies.
- **`GET /sync/status`**: `capabilities` (`source_fields_v1`, `field_templates_v1`, `metadata_fields_v1`, `fields_requeue_v1`) and a `doc_fields` block (server state, per-cycle bound, document counts, last cycle, warning codes, unknown keys) — keys, codes and counts only. **`POST /sync/doc-fields/requeue`** `{"outcome": "not_accepted" | "refused" | "reupload_failed" | "all"}` → `{"requeued": n}`.
- **Metrics** `rc_doc_fields_uploads_total{outcome}`, `rc_doc_fields_refusals_total{code}`, `rc_doc_fields_warnings_total{code}`, `rc_doc_fields_client_dropped_total{reason}`, labels from closed sets. Schema errors under the new keys name the JSON path and the validator keyword, never the instance.
- State DB: five additive `fields_*` columns on `documents`; `record_upload` is an UPSERT that names every column, so recording an upload no longer resets columns it does not name.
- Identical relative paths in several sources: the first source governs the fields (`last_cycle.rel_collisions`).
- Dockerfile `KNOVAS_EXTRACT_REF` defaults to the pinned sha CI tests (`11ec1c38…`, knovas-extract 0.4.0a1). Its `[pdf]` extra is clean, so the CI check that `pymupdf-layout` is absent from the image now blocks the build.

Docs: [configuration.md](docs/configuration.md#per-source-document-fields-dokumentfelder), [operations.md](docs/operations.md#document-fields).

### 0.2.0 — OCR fail-soft, page markers, no more markdown (plan `2026-10-01-ocr-markdown-lite`, M0 + M3)

- **Markdown is never requested from `knovas-extract`** (`emit_markdown=False`). It cost ~8 s per document and its expansion guard parked every mixed PDF and every DOCX with large tables as `skip:unconvertible`. Purge recipe in `docs/operations.md`. The `[markdown]` extra (and with it the PolyForm-NC `pymupdf-layout`) is gone from the dependency and the image.
- **Page-break markers on the wire** (`RC_PAGE_BREAK_MARKERS`, default on; GI-INGEST-17): before every text-page start strictly inside a part the RC writes one form feed per page passed (empty pages counted), never at the part start; a part's `page_number` is the page of its first character. The server serves a hit on page 7 on page 7. `src/sync/page_markers.py`; the context sidecar is built from the unmarked text.
- **Extraction outcomes are recorded, not parked** (GI-EXTRACT-02): a wall-clock kill now raises `extraction timeout …` (not `resource limit exceeded: …`), so one hung OCR page no longer parks a file forever. `sync_executor.record_upload_outcome` returns `synced` / `partial` / `skipped` / `retry`; retryable failures are counted (`RC_EXTRACT_MAX_RETRIES`, default 3) and then recorded partial (`extract_retries_exhausted`) for the OCR-disabled backfill pass. `UploadResult.partial` carries the library's OCR counts; `partial_documents` and `extract_retries` tables in the sync state.
- **`RC_PDF_TEXT_MODE` defaults to `layout`** (markdown-lite rows/headings for structured PDF pages; unstructured pages byte-identical to plain). Set `plain` to restore the previous text or `shadow` to observe.
- **OCR wiring for knovas-extract 0.4** behind introspection (`extract_accepts`): `RC_PDF_TEXT_MODE` (plain | shadow | layout), `RC_OCR_ENGINE/DPI/WORKERS/MAX_PAGES/TIME_BUDGET_SECONDS/PAGE_TIMEOUT_SECONDS`, page-scaled extraction ceiling (`RC_EXTRACT_TIMEOUT_PER_PAGE_SECONDS`, `RC_EXTRACT_TIMEOUT_MAX_SECONDS`). Shadow mode runs the plain and the layout rendering over ONE shared OCR cache and logs a numbers-only `ShadowDiff`.
- **OCR disk cache** (`src/sync/ocr_cache.py`, GI-EXTRACT-04): SQLite beside the sync state, LRU under `RC_OCR_CACHE_MAX_MB` (512; `0` disables), mode 0600, per-document index purged when a document is removed, opened only in the forked child. Prometheus `rc_ocr_cache_{hits,misses}_total`, `rc_ocr_partial_total`, `rc_extract_retry_total`, `rc_ocr_backend_degraded_total`, `rc_skip_unconvertible_total`.
- The extraction child runs at `nice 10` under `RLIMIT_AS` (`RC_EXTRACT_RLIMIT_AS_MB`, 2048; `0` disables).
- No `tables` payload for PDF parts (`RC_SEND_PDF_TABLES`, default off) — the server drops them at the Redis buffer. DOCX headings are no longer duplicated (`# Intro\n\nIntro` → `# Intro`); heading levels are capped at 4 (the server's regex).
- Uploads within a cycle go smallest file first (`RC_UPLOAD_ORDER=small_first`).
- `scripts/backfill_partial_ocr.py` (`--dry-run`) re-extracts and re-uploads partial documents; `scripts/build_context_sidecars.py` is to be rerun after an upgrade.
- Dockerfile: `KNOVAS_EXTRACT_REF` build arg (pin per release), `KNOVAS_EXTRACT_EXTRAS=pdf,ocr,docx,msg,html,sentences`, `tesseract-ocr-ita`, `TESSDATA_PREFIX`, `OMP_THREAD_LIMIT=1`. CI installs Tesseract for the OCR tests and checks the image (models, env; `pip show pymupdf-layout` must fail — advisory until 0.4.0a1 is pinned).

- Scanned/image-only PDFs are OCR'd via `knovas-extract>=0.3` + Tesseract (`RC_PDF_OCR_ENABLED`, `RC_TESSERACT_LANG`). RC Docker image installs `tesseract-ocr` language packs.
- Corrupt or mislabeled documents (e.g. invalid `.docx`, empty PDFs) are skipped in incremental sync instead of crashing the scheduler worker.
- Employee auth for `/discover`, `/sync`, and sync control endpoints is **JWT only** (Bearer token with operator UUID claim). Employee RC client certificates and `RC_MTLS_DEV_*` settings are removed.
- Optional `max_document_age_seconds` in scheduler config and sync-request filters; files older than the limit are not uploaded and appear as `excluded_max_age` in `document_sync` tracking.

## 0.1.1 — 2026-05-19

- Production boot validates required environment variables (no `RC_SKIP_CONFIG_VALIDATION` in prod).
- Docker/Gunicorn default to a single worker for continuous sync safety.
- Added `GETTING_STARTED.md`, `docker-compose.yml`, and `nginx-edge.example.conf`.
- Health returns HTTP 503 when config or watch roots are degraded.
- Added MIT `LICENSE`; CI docker build job; expanded contract tests.

## 0.1.0 — 2026-05-18

- Initial standalone Remote Controller release.
- Flask API: `/health`, `/metrics`, `/discover`, `/sync`, `/sync/start`, `/sync/stop`, `/sync/status`, `/sync/config`.
- Knovas operator verification via `POST /remote_controller/verify_operator`.
- Filesystem discovery and Knovas Secure API upload with incremental sync state.
- Sync scheduler with time windows, ingest rate limits, and continuous mode.
