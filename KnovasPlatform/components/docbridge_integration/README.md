# DocBridge web app

Flask search UI and Knovas API client. Built as Docker service `docbridge-web`.

Deploy and configure via [docs/setup.md](../../docs/setup.md).

```bash
pytest   # from this directory
```

## Admin upload: extraction, OCR and page markers

Documents uploaded through the admin console are extracted with
[`knovas-extract`](https://github.com/Seifeddini/knovas-extract-python) by
`src/knovas_extract_upload.py`, which mirrors the Knovas Connector's sync
pipeline (`KnovasConnector/src/sync/document_text.py`, `knovas_uploader.py`):
a document uploaded here and the same document synced by the RC reach the
server in the same wire format. The environment variable names are the RC's
(see `KnovasConnector/docs/configuration.md`); the defaults are the
conservative ones of an interactive request on a host shared with the search
UI. Markdown is never requested (`emit_markdown=False`) — it cost ~8 s per
document and parked mixed PDFs and large-table DOCX as "markdown expansion
ratio". Keywords the installed `knovas-extract` does not take (`text_mode=`,
`ocr=`, the `Limits` OCR fields) are withheld, so the same source still runs
against an older release; the image and CI install 0.4.0a1.

In the unified stack, `knovas.env` sets `RC_PDF_TEXT_MODE`, `RC_DOCX_TEXT_MODE`,
`RC_OCR_ENGINE`, `RC_OCR_DPI` and `RC_TESSERACT_LANG` for both sides
(`docker-compose.yml` passes them to `docbridge-web`, empty meaning the
default), so a file reaches the index alike through the admin upload and the
Knovas Connector. The time and size limits below are the Platform's own.

| Variable | Default | Meaning |
|----------|---------|---------|
| `RC_PDF_TEXT_MODE` | `layout` | `plain` — the pre-0.4 text. `layout` — markdown-lite rows for fiduciary tables (knovas-extract ≥ 0.4). `shadow` — upload plain, also render layout from the SAME in-memory OCR cache (each page OCR'd once) and log one numbers-only `ShadowDiff` line (numeric-token Jaccard, row-line ratios, length ratio, OCR pages, seconds — never text). Falls back to `plain` with one warning when the library has no `text_mode`. |
| `RC_DOCX_TEXT_MODE` | `layout` | The Knovas Connector's setting: `layout` asks knovas-extract to write Word tables into the text as markdown-lite rows (no `tables` payload for such a DOCX); `plain` is paragraphs only, tables as payload. Needs the knovas-extract 0.4.0a1 release (the pin `2c95cbc`); an older build keeps paragraphs only. Invalid values log a warning and use `layout`. |
| `RC_TESSERACT_LANG` | config `advanced.extraction.ocr_language` (`deu+eng`) | Tesseract language packs joined by `+`; the env value wins over the config value. A value of another shape logs a warning and is skipped (then the config value, then `deu+eng`). OCR itself is switched by `advanced.extraction.use_ocr` (default on). |
| `RC_OCR_ENGINE` | `auto` | `auto` / `tesserocr` / `cli` / `mupdf` (knovas-extract ≥ 0.4). |
| `RC_OCR_DPI` | (unset) | Unset: no `dpi` is passed — native resolution, at most 300 dpi, never upsampled. Set (30–1200): every page at exactly this resolution (a lower-resolution scan is upsampled). Any other value logs a warning and counts as unset. |
| `RC_OCR_WORKERS` | `1` | OCR pages in parallel, at most 8. One by default: a gunicorn worker shares the host with the search UI. |
| `RC_OCR_MAX_PAGES` | `50` | OCR page budget per upload. Beyond it the remaining image pages are skipped and COUNTED; the document is uploaded and reported `partial`. At least 1; anything else logs a warning and uses the default. |
| `RC_OCR_TIME_BUDGET_SECONDS` | `min(60, timeout − 30)` | OCR time budget per upload; never more than `timeout − page_timeout − 10` (pages still running finish in parallel within one page timeout), so the partial result reaches the request before the wall-clock kill. |
| `RC_OCR_PAGE_TIMEOUT_SECONDS` | `30` | Ceiling for one page's OCR. At least 1; anything else logs a warning and uses the default. |
| `RC_EXTRACT_TIMEOUT_SECONDS` | `120` | Wall-clock ceiling for one upload's extraction (child process). `0` extracts in-process without a ceiling. No per-page scaling: a 300-page scan belongs to the RC, not to a browser request. |
| `RC_EXTRACT_RLIMIT_AS_MB` | `2048` | Address-space limit of the extraction child (`RLIMIT_AS`); `0` disables. The child also runs at `nice 10`. |
| `RC_PAGE_BREAK_MARKERS` | `true` | Form feed(s) before every text-page start inside a part so the server serves a hit on its own page (GI-INGEST-17). The part's `page_number` stays the page of its first character; the context sidecar is written from the unmarked text. |
| `RC_SEND_PDF_TABLES` | `false` | Send `tables` payloads for PDF parts. The server drops them at the Redis buffer; table rows have to live in the text (`RC_PDF_TEXT_MODE=layout`). DOCX tables are sent unless the library wrote their rows into the text (`RC_DOCX_TEXT_MODE=layout`). |
| `SEARCH_CONTEXT_STORE_PATH` | (unset) | Directory of the context sidecars (one JSON per document) the search UI reads for first-page previews and hit context; shared with the RC. |

**The guard.** Extraction never runs in the gunicorn request thread: it runs
in a forked child with the wall-clock ceiling above, `nice 10` and `RLIMIT_AS`,
and the OCR budgets above are handed to the library (`OcrOptions` /
`Limits`, whichever takes them). On the ceiling the child is killed and the
upload fails with `extraction timeout after Ns (child killed)` — a message
that never starts with `resource limit exceeded`, the prefix that means "the
library flagged the input" (GI-EXTRACT-02). The image sets
`OMP_THREAD_LIMIT=1` and `TESSDATA_PREFIX`, and the gunicorn `--timeout`
(`DOCBRIDGE_WEB_TIMEOUT`, default `180`, in the image's CMD and in compose)
must stay above `RC_EXTRACT_TIMEOUT_SECONDS`; every nginx in front
(`nginx/docbridge-web*.conf`, the host-nginx template) waits at least that long
(`proxy_read_timeout 180s`, pinned by `tests/test_web_timeouts.py`).

**Sentences.** Every PDF gets sentence citations: the library splits it page
by page. Any other upload is split as one text, in time that grows with the
square of its size (a 60 000-row DOCX table ran past the 120 s ceiling), so
it gets them only while its extracted text is at most 2 MiB
(`UNPAGED_SENTENCE_MAX_CHARS`, the Knovas Connector's rule); a DOCX, an MSG
or a larger file is extracted without sentences first to measure its text.
A longer text is uploaded without citations.

**Partial uploads.** The same rule as the Knovas Connector (spec E1):
`pdf:ocr_pages_skipped > 0` (budget or page cap, or no OCR engine —
knovas-extract 0.4 counts those pages as skipped), `pdf:ocr_pages_failed > 0`,
or — for a library that does not count skipped pages — `pdf:ocr_backend =
"none"` with OCR configured and no skipped-page count makes the upload
`partial`. A born-digital PDF (backend `none`, nothing skipped) is complete.
The note — counts and the backend name only, never text (GI-EXTRACT-04) — is
logged, returned in the sync result (`partial`) and written into the
document's sidecar, from where the search result carries it as
`context_partial`.

## Document fields

Typed values per document (filters, listing, cards, the field panel, the
*Dokumentfelder* admin tab and per-folder fields in the Ingestion tab) —
shown only as far as Knovas serves them for the tenant, in secured mode only.
Knovas 1.5.0 has them on for every account; the Platform checks every answer
and behaves as before where Knovas has them off. In a BROKERED tenant, the
Knovas Connector's entity values also need S1.
Modules: `src/doc_fields_capability.py` (what Knovas serves; per-person
registry cache), `src/doc_fields_view.py` (the honesty rules as pure
functions), `src/web_interface/doc_fields_routes.py` (search side),
`src/web_interface/admin_doc_fields.py` (admin). Guide:
[docs/features/document-fields.md](../../docs/features/document-fields.md).

| Variable | Default | Meaning |
|----------|---------|---------|
| `DOC_FIELDS_UI` | `auto` | `off` hides all document-field UI. There is no `on`: what shows follows Knovas's answer. |
| `DOC_FIELDS_EDIT_ROLES` | `admin` | Roles that may edit values (comma list of `admin`, `approver`, `ingestion_manager`, `member`); fields with sensitivity `special` stay admin-only. |

The tests run the real client against the mock Knovas API in every server
state (`tests/test_doc_fields_mock_contract.py`, `tests/test_doc_fields_render.py`);
the mock's `pointer_in_body = False` plays a Knovas release before S2.

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

## Profile views (`src/graph_views/profiles.py`)

The Ansichten page's shared views come from the **profiles** in the tenant's definitions bundle
(`views.config_source`, KnowledgeBase KB-2/KB-7), not from a list in the code. Only profiles whose
`delivered_through` is the Platform count; an agent's or a CI gate's profile is served elsewhere.

**Seeding.** An admin presses "Ansichten aus den Profilen anlegen" in the saved-views menu
(`POST /api/views/saved/defaults`). Every view of those profiles becomes a shared saved view. Its name
is the view's `label` and its description is the definition's `description`, both in the language the
profile is written in. Its source is `{profile: "<profile id>/<view id>"}` and its render kind is the
profile's. The definition itself stays in the bundle and is read when the view opens. Seeding again
creates nothing new. It updates the name, description or render kind of a view whose profile changed
them. Every change is in the shared-views audit log (`view.shared.created`, `view.shared.updated`). A
profile view's source never changes.

**Who sees which views.** The Platform's roles are `admin`, `approver`, `ingestion_manager` and
`member`. The setting `views.profile_roles` (`VIEWS_PROFILE_ROLES`) maps them to profiles, matched by
profile `id` or by `audience` (case does not matter):

```
VIEWS_PROFILE_ROLES="admin=CTO; member=Engineer"
```

| Platform role | Profiles (Knovas) | Sees |
|---|---|---|
| `admin` | CTO (`cto`) | the CTO's views: system map, feature trace, work board, decision log, risk register, health |
| `member` | Engineer (`engineer`) | the engineer's feature trace |
| `approver`, `ingestion_manager` | — | no profile views (their other shared and personal views as before) |

- A user with several roles sees the views of all their profiles.
- Another profile's view answers 404, like someone else's personal view.
- Admins may list every profile's views with `GET /api/views/saved?all=1`, to look after them; the picker
  shows their own.
- Without a mapping, everyone signed in sees every Platform profile's views (as shared views were
  before).
- A law firm maps its own audiences, e.g. `admin=Partner; member=Partner`.

**Opening a profile view.** The server answers each profile view with `open`: the render kind of the
page that draws it. The profile says what to draw. A profile view the Platform cannot draw yet says so
instead of drawing something else.

| Profile render | Drawn as |
|---|---|
| `map`, or `tree` with paths | TREE over the view's paths (`/api/views/tree?view=<profile>/<view>`), nodes coloured by its `color_by` signals |
| `tree` / `matrix` with a spine | TREE / MATRIX of that spine |
| `board` | BOARD of its process |
| `trace` | TRACE of its process, or of the process whose instances are its first root type. The view's own paths are not drawn yet; the page says so. |
| `list` | LIST of its first root type; paths and grouping are not drawn yet |
| `doc` | the documentation |
| `matrix` without a spine (e.g. health over rules) | not yet; the page names the view |

**The system map.** The CTO's map is the existing TREE renderer over the profile's paths
(`profiles.levels`):
- level 0 is the view's root types;
- each path that extends the previous one by one hop is the next level;
- paths ending at the same depth (interfaces, and the components a component calls) share a level,
  one link per type.

Each node carries the view's colour signals (`src/graph_views/signals.py`):

| Signal | From | Values |
|---|---|---|
| `docs_completeness` | the bundle's `docs_completeness` report, the node's row by key | complete, stale, incomplete, none |
| `check_status` | the node's page definition: its section with a `status` rule (the invariants a component keeps, and the check that verifies each) | passing, partial, failing, unread, none |

`check_status` reads one fact per check, paced like every fact read. One level of the map reads at
most `views.map_fact_budget` (120) facts not yet cached; checks beyond that are `unread`. Nothing in the
Platform names the map's relations, types or facts; the profile, the page definitions and the report
do.
