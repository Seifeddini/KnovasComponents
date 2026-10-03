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

### RC_GUNICORN_TIMEOUT

Seconds gunicorn waits for a silent worker process before it kills and
restarts it (default `120`; read when the container starts). The image runs
one worker process with four request threads (`-k gthread --threads 4`): the
worker's main loop keeps reporting while a request thread works, so a long
`POST /sync` (a one-time sync runs inside the request) is no longer cut off at
this timeout, and `GET /sync/status` answers meanwhile. It bounds how long a
hung worker process goes unnoticed, not how long a request may take.

### Search context sidecars

Set `SEARCH_CONTEXT_STORE_PATH` to a directory shared with docbridge-web (same pattern as `ONEDRIVE_SEARCH_ENRICHMENT_PATH` / `SEARCH_ENRICHMENT_PATH`). Knovas Connector writes one JSON file per uploaded document during sync; docbridge reads them at query time to show first-page previews and match context in search results.

Backfill existing corpora without re-uploading. In the unified stack, run it in
a one-off Knovas Connector container from the repository root; `--identifier-prefix`
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

**Caveat:** with `sequential_subfolders` enabled, Knovas Connector processes
one source per cycle (`sync_executor.py` logs `sequential_subfolders requires
exactly one source; using first only`). In that mode the first source's
`access_groups` applies. Use one profile per walled folder if you need
different groups under sequential mode.

### Per-source document fields (Dokumentfelder)

Knovas keeps typed **document fields** per document (Mandant, Zeitraum,
Dokumentart, …); since Knovas 1.5.0 they are on for every account. Knovas can
still switch them off for an account, and an older server does not have them,
so nothing here switches them on: the Knovas Connector sends fields when a
source is configured with them and reads from each init answer whether the
server took them. Against a server or account without the feature the fields
are ignored, the document is indexed exactly as before, and `/sync/status`
says `"server": "not_accepted"` ([operations.md](operations.md#document-fields)).

Normally the KnovasPlatform Ingestion tab writes these keys (a folder's
*Felder*: fixed values, path templates, file properties). Each `sources[]`
entry may carry three optional keys; a source without them sends a
byte-identical body to earlier releases.

| Key | Shape | Meaning |
|-----|-------|---------|
| `fields` | object, at most 64 keys | Fixed values for every document of the source, keyed by Knovas registry key (`^[a-z][a-z0-9_]{0,63}$`; never `title`, `description`, `path`, `ingested_at`, `pointer`). A value is a string of 1–256 characters, a number, a boolean, or a list of 1–32 of those. Money is text (`"CHF 1234.50"`); entities are **names** (`"Muster AG"`), never node ids. |
| `field_templates` | list, at most 8 strings of 1–512 characters | Path templates over the folders of the source-relative path, e.g. `{mandant}/{period}/**`. The first template that matches wins; each `{key}` captures the whole folder name as that key's value. |
| `metadata_fields` | list of item names | File properties the administrator opted into (table below). Mapped keys are never registered at Knovas automatically. |

**Path templates.** `template := segment ("/" segment)* ["/**"]`, where a
segment is `*` (exactly one folder), `{key}` (a capture, each key at most once)
or a literal (compared after NFC and case folding). The template is matched
against the **folders** of the path relative to the source folder (`\` counts
as `/`; the file name is not part of it). Without `/**` the depth must match
exactly; with it, deeper folders are allowed. Captures are sent as text — the
server's normaliser reads "GJ 2024" or "Q1 2024" and returns warnings in the
echo. `/sync`, `/sync/body` and `/sync/start` refuse a body whose template does
not compile (`400`, `$.sources[i].field_templates[j]: field_template_invalid
(<code>)` — the position and the code, never the template). A body stored
before that check still runs: its bad template skips **that source** for the
cycle (status `template_errors.field_template_invalid`); it never stops the
cycle.
Golden vectors: [contracts/vectors/field_templates.json](../contracts/vectors/field_templates.json)
(the Platform's preview runs the same vectors).

**File properties** (`metadata_fields`):

| Item | Writes | From |
|------|--------|------|
| `language` | `language` | `.pdf` / `.docx` document properties (`dc:language` — often the authoring program's locale, not the document's language); `.eml` `Content-Language`. `x-default` and `und` are skipped. |
| `email_date` | `document_date` | `.eml` / `.msg` only: the `Date` header. |
| `email_doc_type` | `doc_type` = `correspondence.email` | `.eml` / `.msg` only. |
| `email_author` | `author` | `.eml` / `.msg` `From:` display name, else the address. |
| `document_author` | `author` | `.pdf` / `.docx` author; placeholder authors (`Administrator`, `User`, `Microsoft Office User`, …) are skipped. |
| `keywords` | `keywords` | `.pdf` / `.docx` keywords, `.msg` categories: split on `,` and `;`, trimmed, Unicode NFC, duplicates ignoring case dropped (the first spelling stays), at most 32 values of at most 256 characters. knovas-extract 0.4.0a1 does not read Outlook categories yet (extract-msg 0.56 has none on a message), so `.msg` files give none until it does. |
| `document_status` | `status` | `.docx` content status (*Dokumentstatus*), trimmed and sent as written. Knovas matches it against the labels and other names of the `status` choices; one it does not know is dropped with an `invalid_value` warning (counted per key in `/sync/status`). |

`.md` and `.txt` files never yield `author`, `language`, `keywords` or `status`:
they are extracted as plain text, which has no document properties. A file's
modification time, the Microsoft 365 `lastModifiedDateTime` and PDF/DOCX
created/modified dates never become `document_date`, and no Message-ID or
sender/recipient key is mapped. `keywords` and `document_status` need a
Platform and a Connector that know them (capability `metadata_fields_v2`);
golden vectors: [contracts/vectors/metadata_fields.json](../contracts/vectors/metadata_fields.json).

**Precedence.** Per key: template capture, then the fixed value, then the file
property. All three land in Knovas's **upload layer**, which outranks a
**folder rule** (rule layer, set in the Platform's *Dokumentfelder* tab), and a
manual edit in the Platform outranks both. A rule applies to existing documents
in Knovas without any upload — use rules for every value that does not depend
on the source; reserve per-source values for what does.

**Limits before sending.** Values longer than 256 characters, lists over 32
items and system keys are left out; above 64 keys the lowest precedence goes
first; a payload over 16 KiB drops file properties, then fixed values, then
captures. Each drop is counted (`rc_doc_fields_client_dropped_total{reason}`),
never logged with its value. Titles are capped at 500 characters (the server
refuses longer ones).

**Refusals never block indexing.** When the server refuses an init because of
its fields (`invalid_fields`, `unknown_field`, `ambiguous_field`,
`fields_too_large`, a `fields…` path, 503 `doc_fields_*`, or a BROKERED 401
`assertion_rejected`), the Knovas Connector re-posts the init **once without
fields**. If that succeeds the document is indexed, its previous upload-layer
values stay, and the outcome is `refused:<code>`; a transient 503 keeps the old
digest so the fields are tried again later. If the retry fails too, the refusal
was not about fields and the ordinary upload error applies.

#### Environment

| Variable | Default | Meaning |
|----------|---------|---------|
| `RC_DOC_FIELDS` | `on` | `off` is a kill switch: no `fields` key is ever sent, no digest is computed, the state columns are left alone, and bodies are byte-identical to earlier releases. It can only switch the feature **off** — whether Knovas takes fields is read from its answers. Spellings: `on`/`true`/`1`/`yes`, `off`/`false`/`0`/`no`; anything else stops the boot. Switching back to `on` re-sends each configured source once, within the bound below. |
| `RC_FIELDS_REUPLOAD_PER_CYCLE` | `100` | Documents re-sent per cycle because only their field configuration changed (range 1–10000; outside it the boot stops). There is no `0`: the requeue route relies on the same bound. |
| `RC_FIELDS_REUPLOAD_MAX_ATTEMPTS` | `3` | Failed re-uploads of one document (range 1–100) before it leaves the queue as `reupload_failed:<class>` (`init_401`, `init_403`, `init_4xx`, `init_5xx`, `fields_unavailable`, `extract`, `other`). Rate-limit pauses do not count. |

In the unified stack these go into `knovas.env` (`RC_*` is passed through to
the Knovas Connector).

#### Re-uploads and what they cost

The Knovas Connector stores a digest of each document's governing field
configuration (fixed values, its captures, the metadata items, the mapping
version). When a source's fixed values, templates or metadata items change,
every document of that source is `fields_changed` and is **re-uploaded in
full**:

- a full extraction, OCR included, and a full transmission;
- one billed init per document (`request` plus `ingestion_init`); Knovas skips
  the GPU embedding only when the text is unchanged and its embeddings are
  current.

Re-uploads never displace new work: each cycle first takes new and modified
files up to its cap, then at most `RC_FIELDS_REUPLOAD_PER_CYCLE` re-uploads
while the cap leaves room, last in the upload order. A document's stored NULL
digest equals an empty configuration, so upgrading the Knovas Connector does
not re-send anything by itself.

**How long it takes.** The Platform's Ingestion tab shows an estimate before
a change is saved, computed as `ceil(documents / per-cycle bound)` cycles ×
(scan interval + the time the throughput preset needs to upload one bound),
counted in nights of the sync window for the nightly schedule. Example: 20,000
documents, 100 per cycle, schedule *nightly* (19:00–06:00, 5-minute scan
interval), throughput *Normal* (30 requests a minute): 200 cycles of about
8⅓ minutes (5 minutes plus 100 uploads at 30 a minute), roughly 28 hours of window — **about 3 nights at the earliest**.
The estimate counts one request per document and no text-recognition time, so
multi-part documents and scans make it longer. With the schedule *manual*
(`one_time` runs), each press of Start re-sends at most one bound: 20,000
documents need 200 runs. There is no separate bound for `one_time` runs.

**When the server starts accepting.** While the feature is off at Knovas,
uploads with fields are recorded `not_accepted`. The first upload whose answer
carries the fields echo (`staged`) queues every `not_accepted` document that
cycle's scan reached for a re-upload, within the bound. A cycle that uploads
nothing new gets no echo, so at the start of a cycle whose scan reaches
`not_accepted` documents the Knovas Connector also asks Knovas, at most once
an hour, whether it takes fields (`GET /secured/graph/doc-fields`, no body,
one try): `404` means still off. Only an answer from behind Knovas's
per-account fields gate means on: a 2xx, or a 401/403 whose `error_code` is
`assertion_rejected` (a BROKERED account's missing principal assertion); those
documents are then queued exactly as after an echo. Every other answer means
unknown and is asked again an hour later: a 5xx, a 429, no answer, and a
refusal in front of the gate — the certificate check's `401 AUTH_FAILED`
(also its answer when its own lookup fails), `401 SIGNATURE_REQUIRED`, the
mTLS gateway's 400 — which an account whose fields are off gets too; read as
on, it would re-send billed documents that Knovas still takes without their
fields. `POST /sync/doc-fields/requeue` does the same on
request for `not_accepted`, `refused`, `reupload_failed` or `all`
([operations.md](operations.md#document-fields)).

**Identical relative paths in two sources.** Pointers leave out the source
folder, so `Akten/2024/a.pdf` and `Mail/2024/a.pdf` become the same document
when both sources are rooted one level up — and the state is keyed by that
path. Within a cycle the **first** source that yields a path governs its fields
and digest (content handling is unchanged); the status counts such collisions
as `last_cycle.rel_collisions`.

**Downgrading the Platform.** The new keys live only inside `sources[]`; an
older Platform drops them when it re-reads a profile. If it then pushes that
profile, the Knovas Connector sees an empty configuration for those sources
and, on the next re-upload of each affected document (bounded per cycle),
sends `"fields": {}` — which clears that document's upload-layer values at
Knovas. Folder rules and manual edits are not affected.

## Supported document formats

Knovas Connector converts the following extensions to text (with per-sentence citations) before chunking and upload. Extraction is delegated to the [`knovas-extract`](https://github.com/Seifeddini/knovas-extract-python) package (hardened backends, deterministic pysbd sentence tokenization, defused XML, ZIP-bomb caps):

| Extension | Backend |
|-----------|---------|
| `.md`, `.txt` | Plain text (chardet encoding detection) |
| `.docx` | `python-docx` + `mammoth` |
| `.pdf` | `pymupdf` (per-page text; sentence page back-pointers; per-page OCR of image pages via Tesseract) |
| `.eml` | Standard library `email` (subject → transmission title) |
| `.msg` | `extract-msg` (subject → transmission title); a body that exists only as RTF through `striprtf` (`[rtf]` extra) |

Each chunk carries a `page_number` (PDFs only), the page of its first character from `content.pages`, and a `sentence_number` derived from `content.sentences` — every sentence has an exact `char_start` offset into `content.text`, guaranteed by a dispatcher post-condition. A chunk without sentences, or after the last sentence of a list cut off at the library's `max_sentences` cap, has no `sentence_number`; its `page_number` is unaffected.

`ingestion.part_max_chars` defaults to `500000` (the Secure API `snippet` limit). Lower it in the sync request body if you need smaller transmission parts.

**Open/download:** Ingest uses the **original** relative path in `identifier` (e.g. `corpus/akten/Brief.pdf`). KnovasPlatform resolves search pointers to that path on the AutoDoc mount, so clients open the original file—not the converted text.

Align deployment with KnovasPlatform:

- Mount the same tree of originals on RC watch roots and `AUTODOC_MOUNT_PATH`.
- Set `ingestion.identifier_prefix` equal to `AUTODOC_IDENTIFIER_PREFIX` (e.g. both `corpus`).

Scanned PDF pages without a text layer are OCR'd when `RC_PDF_OCR_ENABLED` is true (default) and Tesseract is installed in the container. Set `RC_TESSERACT_LANG` (default `deu+eng`) for language packs. Markdown is never requested from the extractor (it cost ~8 s per document and parked mixed PDFs and large-table DOCX as "markdown expansion ratio" — see [operations.md](operations.md#documents-parked-by-the-markdown-expansion-guard)).

**Docker build:** the Dockerfile installs one pinned `knovas-extract`, the same as the Platform image: `ARG KNOVAS_EXTRACT_VERSION` (`0.4.0a1`) and `ARG KNOVAS_EXTRACT_GIT_REF`. With an empty ref the build installs `knovas-extract==<version>` from PyPI; with a ref, that revision from git — until 0.4.0a1 is on PyPI the default is a full commit sha of the library. The build log names the installed version and commit, and a PyPI install that is not exactly the version fails the build. CI fails when the two Dockerfiles' defaults differ (`scripts/ci/check_knovas_extract_pin.sh`), installs exactly this pin in both test jobs and checks it there and in both built images (`scripts/ci/assert_knovas_extract_pin.py`). A source install needs the pin first ([local-commands.md](local-commands.md#python-from-source-devstaging)). Extras: `pdf,ocr,docx,msg,html,rtf,sentences` (`rtf` reads Outlook mails whose only body is RTF). Override per build: `docker compose build --build-arg KNOVAS_EXTRACT_GIT_REF=<sha> remote-controller`, or `--build-arg KNOVAS_EXTRACT_GIT_REF=` for PyPI. A new pin changes the Dockerfile, so the next `docker compose up -d --build` installs it (Docker's layer cache cannot keep an older build). The image sets `TESSDATA_PREFIX` and `OMP_THREAD_LIMIT=1` and ships the `deu`, `eng`, `fra` and `ita` models; extraction performs no network I/O.

### Extraction, OCR and page markers

All read from the environment by `src/sync/document_text.py`, `knovas_uploader.py`, `sync_executor.py` and `ocr_cache.py` (not by `config.py`). Keywords the installed `knovas-extract` does not take (`text_mode=`, `ocr=`, the `Limits` OCR fields) are withheld (`extract_accepts`), so the same source still runs against an older release; the image and CI install 0.4.0a1.

| Variable | Default | Meaning |
|----------|---------|---------|
| `RC_PDF_OCR_ENABLED` | `true` | OCR image pages of PDFs (`use_ocr="auto"`). `false` keeps text layers only. |
| `RC_TESSERACT_LANG` | `deu+eng` | Tesseract language packs joined by `+` (at most two; `deu+fra`, `deu+ita` per tenant). Anything else (`deu eng`, `deu,eng`) logs a warning and uses `deu+eng`. |
| `RC_PDF_TEXT_MODE` | `layout` | `plain` — the pre-0.2.0 text. `layout` — markdown-lite rows for fiduciary tables (knovas-extract ≥ 0.4). `shadow` — upload plain, also render layout from the SAME OCR cache (each page OCR'd once) and log one numbers-only `ShadowDiff` line (numeric-token Jaccard, row-line ratios, length ratio, OCR pages, seconds — never text). Falls back to `plain` with a warning when the library has no `text_mode`. |
| `RC_DOCX_TEXT_MODE` | `layout` | `layout` — Word tables are written into the text in place, as markdown-lite rows (one line per row, as for PDFs in layout mode), so their content is searchable; such a DOCX is sent without a `tables` payload (the rows would be indexed twice). Needs a knovas-extract with DOCX layout mode (it reports `docx:text_mode`); an older one returns the plain text and the payload stays. `plain` — paragraphs only, tables as payload. Invalid values log a warning and use `layout`. |
| `RC_OCR_ENGINE` | `auto` | `auto` / `tesserocr` / `cli` / `mupdf` (knovas-extract ≥ 0.4). |
| `RC_OCR_DPI` | (unset) | Unset: no `dpi` is passed and the library renders each page at its native resolution, at most 300 dpi, never upsampled (a 150 dpi fax stays 150 dpi). Set (30–1200): every page is rendered at exactly this resolution, so a lower-resolution scan is upsampled. Any other value logs a warning and counts as unset. |
| `RC_OCR_WORKERS` | (unset) | OCR pages in parallel. Unset: the library decides — available CPUs − 1, counting the CPU affinity and the container's CPU quota, at most 8 (`Limits.max_ocr_workers`). Set: that many, 1–8. |
| `RC_OCR_MAX_PAGES` | `500` | OCR page budget per document. Beyond it the remaining image pages are skipped and COUNTED; the document is uploaded and recorded `partial` for the backfill. At least 1; anything else logs a warning and uses 500. |
| `RC_OCR_TIME_BUDGET_SECONDS` | `min(240, timeout − 30)` | OCR time budget per document; never more than `timeout − page_timeout − 10` (the pages still running when it trips finish in parallel within one page timeout) and never below 10 s, so the partial result reaches the parent before the wall-clock kill. |
| `RC_OCR_PAGE_TIMEOUT_SECONDS` | `60` | Ceiling for one page's OCR. At least 1; anything else logs a warning and uses 60. |
| `RC_OCR_CACHE_MAX_MB` | `512` | OCR disk cache cap (`.rc-ocr-cache.db` beside `RC_SYNC_STATE_PATH`, LRU, mode 0600). `0` disables it: no file, every lookup misses. See [operations.md](operations.md#ocr-disk-cache). |
| `RC_EXTRACT_TIMEOUT_SECONDS` | `300` | Wall-clock ceiling for one document's extraction (child process). `0` extracts in-process without a ceiling. |
| `RC_EXTRACT_TIMEOUT_PER_PAGE_SECONDS` | `2` | For PDFs the ceiling is at least this × page count (the child reports the count first). |
| `RC_EXTRACT_TIMEOUT_MAX_SECONDS` | `1800` | Cap of the page-scaled ceiling. |
| `RC_EXTRACT_MAX_RETRIES` | `3` | Retryable extraction failures (wall-clock kill, killed child, transient error) a file may collect; after the cap it is recorded `partial` (`extract_retries_exhausted`) and the backfill runs it with OCR disabled. Secure API failures never count. |
| `RC_EXTRACT_RLIMIT_AS_MB` | `2048` | Address-space limit of the extraction child (`RLIMIT_AS`); `0` disables. The child also runs at `nice 10`. |
| `RC_PAGE_BREAK_MARKERS` | `true` | Form feed(s) before every text-page start inside a part so the server serves a hit on its own page (GI-INGEST-17). The part's `page_number` stays the page of its first character; the context sidecar is written from the unmarked text. |
| `RC_SEND_PDF_TABLES` | `false` | Send `tables` payloads for PDF parts. The server drops them at the Redis buffer; table rows have to live in the text (`RC_PDF_TEXT_MODE=layout`). DOCX tables are sent unless the library wrote their rows into the text (`RC_DOCX_TEXT_MODE=layout`). |
| `RC_UPLOAD_ORDER` | `small_first` | Order of a cycle's upload queue: smallest file first (`scan` keeps the directory order). |
| `RC_SENTENCE_EMIT_MAX_BYTES` | `0` | `0`: no gate on the file size. Every PDF gets sentence citations (the library splits it page by page). Any other file is split as one text, in time that grows with the square of its size (2 MiB of export rows take ~40 s), so it gets citations while its extracted text is at most 2 MiB; a larger text — a text export, a DOCX with big tables in its text — is uploaded without them. A positive value skips citations (and the context previews) above that many raw bytes for every file; the text is still uploaded. |

Legacy `.doc` is not supported in v1. Raise `max_file_bytes` in the sync body for large PDFs (default 10 MiB).

### Re-extraction after an extractor upgrade

Read by `config.py`; see [operations.md](operations.md#re-extraction-after-an-extractor-upgrade).

| Variable | Default | Meaning |
|----------|---------|---------|
| `RC_REEXTRACT_PER_CYCLE` | `100` | Documents re-extracted per cycle after `POST /sync/reextract/requeue` (the Platform's *Neu extrahieren*), on top of `RC_FIELDS_REUPLOAD_PER_CYCLE` and within `max_files_per_cycle` (range 1–10000; outside it the boot stops). Only a document whose upload would change is sent again — each such upload is billed. |

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
