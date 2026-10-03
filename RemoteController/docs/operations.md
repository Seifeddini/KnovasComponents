# Operations

Day-to-day curl examples: [local-commands.md](local-commands.md). First-time setup: [SETUP.md](SETUP.md).

## Health

`GET /health` is unauthenticated. Use for load balancers and Knovas admin probes.

- HTTP **200** — `"status":"ok"`; config, watch roots, and scheduler checks are healthy.
- HTTP **503** — `"status":"degraded"`; inspect `checks` (missing env, unreadable watch roots, or scheduler error) before routing traffic.

## Metrics

`GET /metrics` exposes Prometheus format. Unauthenticated — restrict at the network edge if needed.

Document fields add four counters (see [Document fields](#document-fields)). Every label value comes from a closed set, anything else counts as `other`, so no field key, value, path or pointer can become a label.

## Logs

- Structured logs use file **basenames** only (not full paths).
- JWT, instance tokens, PEMs, and file contents are never logged.
- Document-field code logs codes and counts only (`doc_fields outcome=staged staged=3 warnings=2`): never a value, a template capture, a relative path or a pointer.

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
curl -sS -X POST "$RC_BASE/sync/stop" \
  -H "Content-Type: application/json" -d '{}'

# Production:
curl -sS -X POST "$RC_BASE/sync/stop" \
  -H "Authorization: Bearer $EMPLOYEE_JWT" \
  -H "Content-Type: application/json" -d '{}'

curl -sS "$RC_BASE/sync/status"
```

`/sync/stop` takes no arguments, but every state-changing RC route requires
`Content-Type: application/json` and a body — a bare POST returns
`400 {"error":"Request body must be JSON"}`. `GET /sync/status` is unaffected.

Confirm stop: `"scheduler_state": "not_running"` and `"worker_alive": false`.

The worker completes the **current file upload** before exiting (`pause_policy`: `finish_current_unit_then_pause`). Already-synced paths remain in SQLite; stopping does not roll back uploads.

To prevent sync from auto-starting after a container restart, set `"enabled": false` in `remote_controller_sync.json` or leave `RC_SYNC_AUTO_START_CONTINUOUS=false` (default).

**Do not confuse** `POST /sync/stop` with `docker compose down` — the latter kills the container and may interrupt an in-flight upload. Stop the worker first, then restart or upgrade the image.

## Document fields

Configuration and costs: [configuration.md](configuration.md#per-source-document-fields-dokumentfelder). Document fields work only once Knovas has enabled them for the tenant; until then the Knovas Connector sends them where configured, the server ignores them, and nothing else changes.

### Status

`GET /sync/status` (authenticated) carries two additions — keys, codes and counts, never values:

```json
"capabilities": ["source_fields_v1", "field_templates_v1", "metadata_fields_v1", "fields_requeue_v1"],
"doc_fields": {
  "enabled": true,
  "server": "accepted",
  "per_cycle": 100,
  "documents": {"with_fields": 1234, "pending_reupload": 56, "refused": 3, "not_accepted": 0, "reupload_failed": 1},
  "last_cycle": {"staged": 40, "not_accepted": 0, "cleared": 0, "none": 12,
                 "refused": {"unknown_field": 2}, "reupload_failed": {}, "rel_collisions": 0, "requeued": 0},
  "warnings": {"unresolved_entity": 12, "ambiguous_date": 1},
  "dropped": {},
  "unknown_keys": ["mandat"],
  "suggest": {"mandat": ["mandant"]},
  "template_errors": {"field_template_invalid": 0}
}
```

- `capabilities` tells the Platform which sync-body keys this Knovas Connector understands; an older one reports none, and the Platform then refuses to push a profile that uses fields ("Der Knovas Connector ist zu alt").
- `enabled` is `RC_DOC_FIELDS`. `server` is `accepted` once an answer carried the fields echo (or a fields refusal, which also shows the feature is on), `not_accepted` when fields were sent and no echo came back (feature off at Knovas, or an older server), `unknown` before either. "Not accepted" never means "stored".
- `documents.pending_reupload` estimates the documents still to be re-sent: `fields_changed` from the last scan not yet done, plus those requeued since (before the first cycle after a start, the stored requeued rows). `refused`, `not_accepted` and `reupload_failed` are the stored outcomes. `server` follows the latest cycle that got an answer, so idle cycles do not turn it back to `unknown`.
- `unknown_keys` / `suggest` hold registry keys only (at most 20, from the last cycle): a configured key Knovas does not know, and the keys it suggests instead.
- `document_sync.fields_changed` (also in `?live=1`) counts files whose content is synced but whose field configuration changed.

`/health` is unchanged (it is unauthenticated). A `POST /sync` answer adds `document_sync.fields_changed`, a per-transmission `fields` entry `{outcome, staged, warning_codes}` when the init carried fields, and a top-level `doc_fields` summary of the run.

Outcomes per document: `staged` (the echo came back), `cleared` (`{}` was sent and echoed: the document's upload-layer values were removed), `not_accepted` (fields sent, no echo), `refused:<code>` (indexed without fields after a refusal), `none` (nothing to send), `reupload_failed:<class>` (left the re-upload queue after `RC_FIELDS_REUPLOAD_MAX_ATTEMPTS`).

### Re-send on request

```bash
curl -sS -X POST "$RC_BASE/sync/doc-fields/requeue" \
  -H "Authorization: Bearer $EMPLOYEE_JWT" \
  -H "Content-Type: application/json" -d '{"outcome": "not_accepted"}'
# {"requeued": 140}
```

`outcome` is `not_accepted`, `refused`, `reupload_failed` or `all`; anything else is a 400. The matching documents the last cycle's scan reached become `fields_changed` and are re-sent within `RC_FIELDS_REUPLOAD_PER_CYCLE` per cycle (a document no scan visits again — a completed subfolder of a sequential import, a removed file kept by `delete_on_remove: false` — is not queued and keeps its outcome; before the first cycle after a start the request is not limited); a running worker starts its next cycle at once. Same authorization as `/sync/start` (the Platform's Ingestion tab offers it as *Erneut senden*). **Each re-sent document is a full, billed upload with a fresh extraction and OCR** — see [configuration.md](configuration.md#re-uploads-and-what-they-cost). Documents recorded `not_accepted` are queued automatically the first time Knovas answers with an echo (those that cycle's scan reached). A fields re-send that keeps failing never counts toward the extraction retries of `RC_EXTRACT_MAX_RETRIES`: Knovas already holds its text, so it is not recorded partial and leaves the queue as `reupload_failed:extract`.

### Metrics

| Counter | Labels |
|---------|--------|
| `rc_doc_fields_uploads_total{outcome}` | `staged`, `cleared`, `not_accepted`, `none`, `refused`, `reupload_failed`, `other` |
| `rc_doc_fields_refusals_total{code}` | `invalid_fields`, `unknown_field`, `ambiguous_field`, `fields_too_large`, `doc_fields_unavailable`, `doc_fields_ingest_unavailable`, `assertion_rejected`, `other` |
| `rc_doc_fields_warnings_total{code}` | the echo warning codes (`invalid_value`, `checksum_failed`, `type_mismatch`, `restricted_identifier`, `cap_exceeded`, `ambiguous_date`, `unresolved_entity`, `ambiguous_entity`, `key_looks_personal`), `other` |
| `rc_doc_fields_client_dropped_total{reason}` | values left out before sending: `system_key`, `value_too_long`, `cap_exceeded`, `too_large`, `invalid_value`, `other` |

### State

The SQLite `documents` table gains five columns (`fields_digest`, `fields_sent`, `fields_outcome`, `fields_warning_codes`, `fields_attempts`), added on first start; an older Knovas Connector ignores them. `fields_digest` is a hash of the configuration, never the values. Resetting the sync state (above) also forgets which documents had fields: the next full upload sends them again.

## Large corpora (100s of GB)

- Set **`sequential_subfolders`: true** in `remote_controller_sync.json` when the sync source root contains many top-level folders (e.g. WinJur bucket dirs). RC processes **one subfolder per cycle**, then advances automatically when that folder has no pending uploads.
- Set `max_files_per_cycle` (e.g. 200–500) to cap uploads per cycle.
- Set `max_scan_entries_per_cycle` (e.g. 10000) to cap **directory visits** per cycle on slow SMB mounts (important when most files are unsupported types such as legacy `.doc`).
  **Only together with `sequential_subfolders`**, which keeps its place between cycles. Without it every cycle starts again at the top of the share, so on a share with more folders than the cap the same folders are read every time and the rest never — new documents there are not ingested, and nothing counts as an error. The scheduler state reads `scan_limit_reached`. For continuous sync of such a share set the cap to `0` (the whole share each cycle, backing off to `scan_interval_idle_max_seconds` while nothing changes); `./scripts/doctor.sh` prints the command.
- A `sequential_subfolders` pass ends (`subfolders_complete`) once every folder is done, and from then on nothing is read. It suits a one-time import of a large archive, not a share that keeps changing.
- Use a **24h sync window** (`00:00`–`23:59`) for initial backfill; the default is no longer limited to business hours.
- Use `scan_interval_idle_max_seconds` so steady-state rescans back off when nothing is pending.
- `POST /sync` responses cap `transmissions` (default 100 entries); counts in `document_sync` remain full.
- **Do not** use `GET /sync/status?deep_scan=1` on huge trees — it is capped by `max_scan_entries_per_cycle` but still runs in the HTTP worker. Prefer logs and `GET /sync/status?live=1`.
- Example scheduler config for WinJur: [config/remote_controller_sync.winjur.example.json](../config/remote_controller_sync.winjur.example.json).
- Uploads stream file parts (bounded RAM per file). Initial ingest wall-clock still depends on Knovas ingestion rate limits.

## Scanned PDFs (OCR)

Image pages of PDFs are ingested via Tesseract when `knovas-extract>=0.3` and `tesseract-ocr` are present in the RC image (`RC_PDF_OCR_ENABLED=true` by default; `RC_TESSERACT_LANG=deu+eng`). The tuning variables (`RC_OCR_*`, `RC_EXTRACT_*`, `RC_PDF_TEXT_MODE`) are listed in [configuration.md](configuration.md#extraction-ocr-and-page-markers).

PDFs that failed with `no extractable text` before OCR was enabled were recorded as `skip:unconvertible` in SQLite and will not retry until those rows are removed:

```bash
sqlite3 /var/rc-state/.rc-sync-state.db \
  "DELETE FROM documents WHERE transmission_key_id LIKE 'skip:%';"
```

Then restart continuous sync or wait for the next cycle.

### Documents parked by the markdown expansion guard

Releases before 0.2.0 asked the extractor for markdown on every document. Its expansion guard raised `resource limit exceeded: markdown expansion ratio` on mixed PDFs (digital cover + scanned body) and on DOCX with large tables, and on `.eml`/`.msg` with HTML bodies, and the RC parked each such file as `skip:unconvertible` — forever. 0.2.0 no longer requests markdown. The SQLite state keeps only the `skip:` key, not the message, so the recipe re-queues every parked file of these types (one that is genuinely unconvertible is parked again after one extraction):

```bash
sqlite3 /var/rc-state/.rc-sync-state.db \
  "DELETE FROM documents WHERE transmission_key_id LIKE 'skip:%' AND (
     LOWER(relative_path) LIKE '%.pdf' OR LOWER(relative_path) LIKE '%.docx'
     OR LOWER(relative_path) LIKE '%.eml' OR LOWER(relative_path) LIKE '%.msg');"
```

The messages that identify the parked files are in the RC log (`Upload failed path=… error=resource limit exceeded: markdown expansion ratio …`). Then wait for the next cycle.

### Partial documents and the backfill

A document whose text landed only in part is recorded **partial**, not parked (GI-EXTRACT-02):

- the OCR page or time budget tripped on a long scan — the library returned the text pages with the skipped pages COUNTED (`ocr_pages_skipped`), the document was uploaded as is;
- the extraction child was killed on the wall-clock ceiling or died (`extractor died (exit -9)`) `RC_EXTRACT_MAX_RETRIES` times in a row — note `extract_retries_exhausted`;
- OCR is configured but the library reported no OCR backend (`ocr_backend_none`).

A partial file is not re-uploaded by the incremental cycle (its fingerprint is stored; `document_sync` counts it as `synced`) but stays listed for the nightly pass. `POST /sync` responses carry `partial` on the transmission entry; `/metrics` has `rc_ocr_partial_total`, `rc_extract_retry_total`, `rc_ocr_backend_degraded_total`, `rc_skip_unconvertible_total`.

```bash
# what is recorded, nothing uploaded
docker compose --env-file knovas.env run --rm remote-controller \
  python /app/scripts/backfill_partial_ocr.py --dry-run
# the pass: a budget trip is re-extracted with a 5000-page / 1800 s budget,
# exhausted retries with OCR disabled; a clean upload clears the note
docker compose --env-file knovas.env run --rm remote-controller \
  python /app/scripts/backfill_partial_ocr.py
```

Run it outside the sync window (one document at a time, full OCR). Inspect the notes directly: `sqlite3 /var/rc-state/.rc-sync-state.db "SELECT relative_path, note_json FROM partial_documents;"`. Removing a row from `partial_documents` forgets the note without touching the fingerprint.

### OCR disk cache

OCR output is cached per page image in `/var/rc-state/.rc-ocr-cache.db` (beside `RC_SYNC_STATE_PATH`, mode 0600, SQLite), so a re-sync of a synced folder, the backfill and the shadow run's second rendering perform no OCR for pages already seen. The key includes the engine, language, dpi and preprocessing fingerprint, so a changed `RC_OCR_ENGINE` or `RC_TESSERACT_LANG` never serves stale text.

- **Retention:** least-recently-used entries are evicted above `RC_OCR_CACHE_MAX_MB` (default 512 MiB). A document's entries are purged when it is removed from Knovas (pruned from the share, `remove_tracked`); an entry two documents share stays until the last of them goes.
- **Off-switch:** `RC_OCR_CACHE_MAX_MB=0` — no file is created and every lookup misses. Delete an existing file by hand after switching it off: `rm /var/rc-state/.rc-ocr-cache.db`.
- **Reset:** when you reset the sync state by hand (`DELETE FROM documents …`), remove the cache file as well; it is a copy of document text at rest and belongs to the same lifecycle. Code paths that reset the state (`SyncStateStore.reset_all`) drop it.
- The cache is opened only in the forked extraction child; the API worker never holds it open.

## Upgrades

1. Stop continuous worker (`POST /sync/stop` — remember the `-d '{}'` body).
2. Replace image or package; preserve `.env`, certs, state, and config volumes.
3. Verify `/health`, then run a test `GET /discover`.
4. Rerun `scripts/build_context_sidecars.py --force` when the release changes extraction (see [configuration.md](configuration.md#search-context-sidecars)), and re-queue files the previous release parked (above).
5. 0.3.0 (document fields) needs nothing: the new state columns are added on start, and a document's empty digest equals "no fields configured", so nothing is re-sent until a source is configured with fields.

Use a **single** Gunicorn worker (`-w 1`) when running from source; multiple workers conflict on scheduler state.
