# DocBridge web app

Flask search UI and Knovas API client. Built as Docker service `docbridge-web`.

Deploy and configure via [docs/setup.md](../../docs/setup.md).

```bash
pytest   # from this directory
```

## Admin upload: extraction, OCR and page markers

Documents uploaded through the admin console are extracted with
[`knovas-extract`](https://github.com/Seifeddini/knovas-extract-python) by
`src/knovas_extract_upload.py`, which mirrors the RemoteController's sync
pipeline (`RemoteController/src/sync/document_text.py`, `knovas_uploader.py`):
a document uploaded here and the same document synced by the RC reach the
server in the same wire format. The environment variable names are the RC's
(see `RemoteController/docs/configuration.md`); the defaults are the
conservative ones of an interactive request on a host shared with the search
UI. Markdown is never requested (`emit_markdown=False`) — it cost ~8 s per
document and parked mixed PDFs and large-table DOCX as "markdown expansion
ratio". Keywords the installed `knovas-extract` does not take (`text_mode=`,
`ocr=`, the `Limits` OCR fields) are withheld, so the same image runs against
0.3 (today) and 0.4.

| Variable | Default | Meaning |
|----------|---------|---------|
| `RC_PDF_TEXT_MODE` | `plain` | `plain` — today's text. `layout` — markdown-lite rows for fiduciary tables (knovas-extract ≥ 0.4). `shadow` — upload plain, also render layout from the SAME in-memory OCR cache (each page OCR'd once) and log one numbers-only `ShadowDiff` line (numeric-token Jaccard, row-line ratios, length ratio, OCR pages, seconds — never text). Falls back to `plain` with one warning when the library has no `text_mode`. |
| `RC_TESSERACT_LANG` | config `advanced.extraction.ocr_language` (`deu+eng`) | Tesseract language packs; the env value wins over the config value. OCR itself is switched by `advanced.extraction.use_ocr` (default on). |
| `RC_OCR_ENGINE` | `auto` | `auto` / `tesserocr` / `cli` / `mupdf` (knovas-extract ≥ 0.4). |
| `RC_OCR_DPI` | `300` | Render dpi ceiling; the library never upsamples a lower-resolution scan. |
| `RC_OCR_WORKERS` | `1` | OCR pages in parallel, at most 8. One by default: a gunicorn worker shares the host with the search UI. |
| `RC_OCR_MAX_PAGES` | `50` | OCR page budget per upload. Beyond it the remaining image pages are skipped and COUNTED; the document is uploaded and reported `partial`. |
| `RC_OCR_TIME_BUDGET_SECONDS` | `min(60, timeout − 30)` | OCR time budget per upload; never more than `timeout − workers × page_timeout − 10` so the partial result reaches the request before the wall-clock kill. |
| `RC_OCR_PAGE_TIMEOUT_SECONDS` | `30` | Ceiling for one page's OCR. |
| `RC_EXTRACT_TIMEOUT_SECONDS` | `120` | Wall-clock ceiling for one upload's extraction (child process). `0` extracts in-process without a ceiling. No per-page scaling: a 300-page scan belongs to the RC, not to a browser request. |
| `RC_EXTRACT_RLIMIT_AS_MB` | `2048` | Address-space limit of the extraction child (`RLIMIT_AS`); `0` disables. The child also runs at `nice 10`. |
| `RC_PAGE_BREAK_MARKERS` | `true` | Form feed(s) before every text-page start inside a part so the server serves a hit on its own page (GI-INGEST-17). The part's `page_number` stays the page of its first character; the context sidecar is written from the unmarked text. |
| `RC_SEND_PDF_TABLES` | `false` | Send `tables` payloads for PDF parts. The server drops them at the Redis buffer; table rows have to live in the text (`RC_PDF_TEXT_MODE=layout`). DOCX tables are still sent. |
| `SEARCH_CONTEXT_STORE_PATH` | (unset) | Directory of the context sidecars (one JSON per document) the search UI reads for first-page previews and hit context; shared with the RC. |

**The guard.** Extraction never runs in the gunicorn request thread: it runs
in a forked child with the wall-clock ceiling above, `nice 10` and `RLIMIT_AS`,
and the OCR budgets above are handed to the library (`OcrOptions` /
`Limits`, whichever takes them). On the ceiling the child is killed and the
upload fails with `extraction timeout after Ns (child killed)` — a message
that never starts with `resource limit exceeded`, the prefix that means "the
library flagged the input" (GI-EXTRACT-02). The image sets
`OMP_THREAD_LIMIT=1` and `TESSDATA_PREFIX`, and the gunicorn `--timeout`
(Dockerfile `180`, compose `DOCBRIDGE_WEB_TIMEOUT`) must stay above
`RC_EXTRACT_TIMEOUT_SECONDS`.

**Partial uploads.** `metadata.extra` is read defensively: `pdf:ocr_pages_skipped > 0`
(budget trip), or `pdf:ocr_backend = "none"` with OCR configured and no
skipped-page count, makes the upload `partial`. The note — counts and the
backend name only, never text (GI-EXTRACT-04) — is logged, returned in the
sync result (`partial`) and written into the document's sidecar, from where
the search result carries it as `context_partial`.

## Hit context (`src/context_store.py`)

The search card's snippet and "Fundstellen" come from the per-document
sidecar. A sentence is skipped as a location when it is thin — a letterhead
line (address, telephone, IBAN; `MWST`/`UID` only when no amount stands next
to them) or fewer than four letter-words — unless it contains a searched
word. **A table row is content**: a record with `k: "row"` or a text with
` | ` cells counts as a location as soon as it carries at least one
letter-word and one number (`Total Aktiven | 2023: 1'234'567.00 | 2022:
987'654.30`, `MWST 7.7 % | 123.45`); a row of only years or only headers
stays thin.

Sidecar versions: 1 (`{"i","t","p"}` records) and 2 (adds `"k": "row"` on
row records and a document-level `partial` note). The reader accepts both;
the writer emits 2 only when it has rows or a partial note, so prose
sidecars are unchanged.

## Search order

`PLATFORM_KEEP_SERVER_ORDER` (default `true`) keeps the ranking the server
returned (every row carries `server_rank`). The server reranks with ColBERT;
`score` on a row is the stage-1 cosine of the best chunk, and re-sorting by
it throws the rerank away. `false` restores the old re-sort by score.

## Preview anchors (`src/web_interface/preview.py`)

A hit on a table row is located in the PDF by searching the row without its
` | ` separators and fold keys (`2023: 1'234'567.80` → `1'234'567.80`), then
by its longest cell of at least 12 characters, so a click on a Bilanz row
scrolls the viewer to the row instead of staying on page 1.
