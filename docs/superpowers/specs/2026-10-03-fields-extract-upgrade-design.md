# Design: Document fields 1.5.0 and knovas-extract 0.4.0a1 in the Knovas Platform and the Knovas Connector

Date: 2026-10-03
Status: approved in conversation (approach A); this document is the written spec for review
Plan: `docs/superpowers/plans/2026-10-03-fields-extract-upgrade.md` (written after this spec is approved)

Inputs:
- Knovas documentation 1.5.0 (updated 2026-10-03), all 14 pages, read in full for
  `document-fields`, `add-documents`, `search`, `manage-documents`.
- knovas-extract-python `origin/main` = `b5d45404a6df0aa5fb2b934c8ae4efab9fe764a1` (version 0.4.0a1).
- Three read-only audits run on 2026-10-03: knovas-extract usage (library vs. both components),
  Document fields (1.5.0 docs vs. the `claude/document-fields-integration` branch, with the server
  code at KnowledgeBase `master` 1a5e2650 as reference), and a trial merge with test runs.

Written in English like the other cross-repository specs; UI strings stay German.
"Connector" means the Knovas Connector, the component in `RemoteController/` (renamed in
user-facing text only; folder, service, `RC_*` settings and identifiers keep their names).

---

## 1. Context

Four lines of work exist and none of them is complete on its own:

| Line | Where | State |
|---|---|---|
| Platform + Connector with OCR, layout text mode, experiments | `main` @ `0e63cac` | no Document-fields support |
| Document fields in Connector, Platform and mock API | `origin/claude/document-fields-integration` @ `09063a5` (10 commits on `f9d872c`) | no PR; covers nearly every client rule of the 1.5.0 docs |
| "RemoteController is now called Knovas Connector" | commit `08228ba` inside draft PR #22 (`cl/wonderful-ritchie-s07v1q`) | PR #22 conflicts with `main` and also carries the knowledge-directories feature |
| knovas-extract 0.4.0a1 (per-page OCR, layout text mode) | library `origin/main` @ `b5d4540` | untagged; PyPI's newest is 0.2.0; library CI red |

What the audits found that this design acts on:

1. **Both component images install the library from the moving git `main`**, the CI test jobs on
   `main` install PyPI 0.2.0 (one Connector unit test fails against it), the PyPI fallbacks in the
   Dockerfiles are broken (`>=0.3` cannot resolve; `>=0.2` installs 0.2.0, whose `[pdf]` pulls the
   PolyForm-Noncommercial `pymupdf-layout`). The OCR plan's pin (`2026-10-01-ocr-markdown-lite.md`
   §5 M0) never landed.
2. **The library's red CI is test hygiene, not product code**: unguarded `fitz`/`rapidfuzz`/`yaml`
   imports break collection in extras-free environments, one test assumes POSIX paths, one mypy
   `no-any-return`, one pyright redeclaration, one bandit `assert`. A probe fixing exactly these
   (7 files, +24/−12) turned every CI gate green on Linux (unit 161, golden 224, OCR 80 + 5 live
   Tesseract, lint, bandit) and on Windows (613 with all extras).
3. **Connector defects on `main` with 0.4.0a1** (all reproduced by the usage audit):
   - every born-digital PDF is recorded partial (`reason: ocr_backend_none`), counted as a degraded
     OCR backend, and re-extracted and re-uploaded by `scripts/backfill_partial_ocr.py` on every run
     (`RemoteController/src/sync/document_text.py:565-590`; the Platform already fixed the rule at
     `knovas_extract_upload.py:448-483`);
   - the OCR time budget collapses to its 10 s floor on hosts with ≥ 7 cores for PDFs up to
     ~150–250 pages, because the cap subtracts `workers × page_timeout` although in-flight pages run
     in parallel (`document_text.py:369-387`);
   - `dpi=300` is forced in both components, which upsamples low-resolution scans the library would
     render natively (library numbers for a 150 dpi fax: CER 0.145 upsampled vs 0.028 native);
   - sentences (and with them every part's `page_number`) are switched off above 2 MiB of raw file
     size, i.e. for most multi-page scans (`document_text.py:742`);
   - an invalid OCR setting (`RC_TESSERACT_LANG="deu eng"`) makes `OcrOptions` raise `ValueError`,
     which the child reports as "corrupt .pdf", so every PDF is skipped for good;
   - the Connector's gunicorn (`-w 1`, sync worker, `--timeout 120`) kills its only worker — and the
     sync scheduler thread with it — when the documented operator call `POST /sync` runs longer.
4. **DOCX table content never reaches the search index**: the library keeps DOCX table text out of
   `content.text` (it only fills `content.tables`), and the server's part buffer drops the `tables`
   payload (`KnowledgeBase .../redis_snippet_buffer_service.py:205-214` stores snippet, page,
   sentence and billed bytes only).
5. **Document fields**: the branch implements nearly all of 1.5.0. Gaps: field-creation options
   (`code_scheme`, `link_policy`, `fy_start_month`/`fy_label`, per-field `date_order`, multilingual
   choices with aliases), most `where` operators in the filter rail, several server signals, upload
   warnings lose their field key, documents the server ignored fields for are re-sent only when some
   other upload happens, 10+ texts still say fields are "off by default" (1.5.0: on for every account),
   dead search keys (`top_k`, `filters`, `encryption_matrix`).
6. **Unused library output** that serves the product: keywords and status from file properties,
   the PDF subject as description, RTF-only Outlook bodies (need `[rtf]`), the library version and
   OCR counts for operations; and existing documents never benefit from extraction improvements,
   because nothing re-extracts a file that did not change.

## 2. Decisions

| # | Decision | Source |
|---|---|---|
| D1 | Build on a new branch from `main`: merge `claude/document-fields-integration`, cherry-pick **only** `08228ba` from PR #22, apply its rename policy to every user-facing string the fields branch adds. PR #22's knowledge-directories work stays in its own PR; merging it later re-applies the identical rename hunks cleanly. | user |
| D2 | Fix the library's CI in a library PR, release `v0.4.0a1` to PyPI (signed by `release.yml`), and pin both components to `knovas-extract==0.4.0a1` from PyPI. Until the release exists, pin the full commit hash of the library PR's merge commit. Merging and tagging in the library repo happen only after an explicit OK from the user. | user |
| D3 | Scope = baseline (integration, 1.5.0 texts, release + pin, extraction defects) + Document fields complete per 1.5.0 + library used fully + three library fixes before the release (DOCX tables searchable, HTML-only e-mails, fail-soft sentence limit). Other 1.5.0 API areas are follow-ups (§12). | user |
| D4 | Approach A: one library PR; one KnovasComponents branch built in layers, delivered as **one** PR with one commit (or a small series) per workstream of §4–§8. Nothing is pushed and no PR is opened without asking. | user |
| D5 | Existing documents are re-extracted only after an administrator confirms it in the Platform (every upload is billed); the Connector re-sends within a per-cycle bound. | user (approved with the design) |
| D6 | Text-changing library fixes ship before the release so that tenants re-ingest once (0.4's per-page OCR already requires re-ingesting scanned PDFs). | this design |
| D7 | Where the Connector and the Platform mirror each other, both change in the same commit and keep their mirror tests; no new shared package (they ship as separate images). | this design |
| D8 | Feature switches follow the existing patterns: `RC_*` environment settings for the Connector (Platform mirrors the names where it runs the same code), invalid values log a warning and fall back to the default. | existing code |

## 3. Goals, success criteria, non-goals

Goals:
- Both components run on the released knovas-extract 0.4.0a1, the same version in images and CI.
- Every client-side rule and capability of the 1.5.0 Document-fields documentation is implemented
  in the Platform and the Connector.
- Library output that improves search, fields or operations is used; known defects are fixed.
- Existing documents can be brought up to the new extraction on an administrator's decision.

Success criteria (all verified before the PR is offered):
1. Library: all CI gates green on the PR; `v0.4.0a1` on PyPI (after the user's OK).
2. KnovasComponents: Connector, Platform (including the PostgreSQL-backed tests) and mock suites
   green; both images build; `pip show pymupdf-layout` fails in both; the installed library version
   equals the pin in both images and both CI jobs.
3. End-to-end against the mock Knovas API with a small corpus (born-digital PDF, scanned PDF, DOCX
   with a table, HTML-only EML, MSG): no document recorded partial except a deliberately starved
   scan; parts carry page numbers; the DOCX table rows are in the uploaded text; e-mail text has no
   HTML entities; fields are staged; a filtered search reports `where.applied`.
4. No field value, document text or path appears in any new log line, metric label or error message.

Non-goals: see §12.

---

## 4. Library — knovas-extract-python (one PR, then `v0.4.0a1`)

### 4.1 CI green
The validated probe, applied on a branch of the library repo:

| File | Change |
|---|---|
| `tests/unit/test_ocr_decision.py` | `fitz = pytest.importorskip("fitz")` instead of a bare import |
| `tests/golden/test_layout_golden.py` | `pytest.importorskip("yaml")`, `pytest.importorskip("rapidfuzz")` before importing `tests.eval.metrics` |
| `tests/golden/test_prose_regression.py` | `pytest.importorskip("rapidfuzz")` before importing `tests.eval.metrics` |
| `tests/unit/test_ocr_backend.py` | the stubbed Tesseract path is `os.path.abspath("/usr/bin/tesseract")`, absolute on every OS |
| `src/knovas_extract/_ocr/preprocess.py` | `pgm_bytes` binds the buffer to a `bytes` variable (mypy `no-any-return` without numpy) |
| `src/knovas_extract/_ocr/pipeline.py` | the process-pool submit function is named `_submit_process` and assigned (pyright `reportRedeclaration`) |
| `src/knovas_extract/_layout/page.py` | `_ensure_doc` raises `RuntimeError` instead of `assert` (bandit B101; survives `-O`) |

### 4.2 DOCX tables in the text (`text_mode="layout"` for DOCX)
Today `text_mode="layout"` on a DOCX emits plain text plus the warning
`text_mode='layout' is implemented for PDF only`. New behaviour for DOCX:

- The body is walked in document order (`python-docx` `iter_inner_content()` over the body:
  paragraphs and tables that are direct children of `w:body`). Paragraphs are emitted exactly as in
  plain mode (`para.text.rstrip()`, empty ones dropped, blocks joined by one blank line).
- Each table is emitted **in place** as one block in the markdown-lite row grammar of PDF layout
  mode (`docs/layout-text-mode.md`): one line per row, cells joined by ` | `, consecutive duplicates
  produced by merged cells emitted once, empty rows dropped, cell text whitespace-normalised;
  numeric cells under a known column header are folded as `<header>: <value>` (the PDF grammar's
  fold keys); the header line is repeated on top of each pack of at most
  `LayoutOptions.pack_budget_tokens` (150) estimated tokens; a row longer than `row_max_chars` is cut
  at a cell boundary like in PDF mode. The header row is the one the structured extraction already
  chooses for `content.tables[i].headers`, so text and structure agree. Nested tables are flattened
  into their cell's text.
- Invariants (tests pin each): a DOCX without tables is byte-identical to plain mode;
  `content.tables` is unchanged; `content.sections` line numbers are computed against the final text;
  all sentence contracts in `dispatch.extract` hold; the output is deterministic; the existing table
  caps (`_TABLE_CELL_MAX_CHARS`, row/column limits) apply.
- Metadata scalars `docx:text_mode` and `docx:layout_tables` (count of tables rendered), mirroring
  `pdf:text_mode` / `pdf:layout_tables`; `spec_version` stays 1.3.0 (additive keys).
- The warning for other formats becomes `text_mode='layout' is implemented for PDF and DOCX only`.
- Documentation: `docs/layout-text-mode.md` (DOCX section), CHANGELOG.

### 4.3 HTML-only e-mails (EML and MSG)
`extractors/eml.py:38-45` and `extractors/msg.py:35-41` strip tags with a regex, keep entities
(`Gr&uuml;ezi`) and collapse all whitespace into one line. Both use one new private helper
(`_html_text.py`):
1. remove `<script>`, `<style>`, `<head>` and comments with their content;
2. turn `<br>`, and the ends of `p`, `div`, `tr`, `li`, `h1`–`h6`, `table`, `blockquote` into line
   breaks; table cells into ` | `;
3. remove the remaining tags, then `html.unescape`;
4. drop C0 control characters except tab and newline, and Unicode bidi overrides (as `sanitize_scalar`
   does for metadata), per line collapse runs of spaces, strip, and collapse three or more newlines
   into two.

The result goes through the same canonicalisation as every other text. Plain-text bodies are
untouched. Unit tests: entities, line structure, script/style removal, control and bidi character
references, a 1 MiB body within the existing limits.

### 4.4 Sentence limit fail-soft
`_sentences.py:169-172` raises `ResourceExhaustedError("sentence count")` above
`Limits.max_sentences`; the Connector maps that to "resource limit exceeded" and skips the file for
good. New: keep the first `max_sentences` sentences, add one counted warning
(`sentences: N beyond max_sentences omitted`), never raise. Dispatch's sentence contracts hold for the
kept prefix (test).

### 4.5 Release hygiene
- `pyproject.toml` `[project.urls]` and the Sigstore identity in `RELEASING.md` point at
  `github.com/Seifeddini/knovas-extract-python` (today they name `github.com/knovas/…`, which 404s,
  so a published release would carry dead links and a verification recipe that cannot succeed).
- CHANGELOG: the 0.4.0a1 entry gains §4.2–§4.4 and the CI fixes; the stale `## [Unreleased]` block
  below it (the never-released 0.3.0 work) becomes `## [0.3.0] — not released separately; part of 0.4.0a1`.
- README: status line and `spec_version` (it still says 0.1.0.dev / not on PyPI / 1.2.0).

### 4.6 Release
1. PR on knovas-extract-python with §4.1–§4.5; all CI gates green.
2. Ask the user; merge.
3. Ask the user; tag `v0.4.0a1` on the merge commit; `release.yml` runs the matrix, signs, writes
   provenance and the SBOM, and publishes to PyPI.
4. Verify: `pip download knovas-extract==0.4.0a1`, install with the component extras, smoke-extract.

Recommended, not done without asking: close dependabot PR #18 (it widens `pymupdf4llm` on the old
`[pdf]` line; moved to `[pdf-markdown]` it would let `pymupdf-layout` back in).

---

## 5. Integration (KnovasComponents)

### 5.1 Branch
Worktree `.claude/worktrees/fields-extract-upgrade`, branch `worktree-fields-extract-upgrade` from
`origin/main` (`0e63cac`). Merge `origin/claude/document-fields-integration` (a merge commit keeps the
branch's 10 reviewed commits intact), then cherry-pick `08228ba`.

### 5.2 Conflict rules
Known overlaps: `.github/workflows/ci.yml`, `docker-compose.yml`,
`KnovasPlatform/components/docbridge_integration/src/knovas_client.py`,
`.../src/web_interface/app.py`, `RELEASE_NOTES.md`. Rules:
- `docker-compose.yml`: keep `main`'s gunicorn timeout of 180 s for `docbridge-web` (it must stay
  above the Platform's 120 s extraction ceiling); take the branch's privacy-safe access-log settings.
- `ci.yml`: keep both sides' jobs; the library pin steps are rewritten in §9 anyway.
- `knovas_client.py` / `app.py`: keep `main`'s experiments additions (`upload_text_document`, routes)
  and the branch's Document-fields client and search changes side by side.
- `RELEASE_NOTES.md`: one "Unreleased" section holding both sides' entries.
- `main`'s `upload_text_document` (experiments indexer) is a second Platform upload path; it sends no
  `fields`, which is correct for experiment documents. The branch's plan states "no Platform upload
  exists"; the Platform feature doc gets one sentence saying the experiments indexer sends no fields.

The trial merge's exact conflict list and resolutions are recorded in the plan.

### 5.3 Rename in user-facing text
Policy of `08228ba`: everything people read says "Knovas Connector"; what machines read keeps its
name. Applied to what the fields branch adds:
- 14 Platform UI strings (`identity/ingestion_compiler.py:477,480`;
  `web_interface/admin_doc_fields.py:186-187,190,1638`; `web_interface/admin_ingestion.py:351,407,1215`;
  `web_interface/admin_system.py:145`; `static/js/admin_ingestion.js:234`;
  `templates/admin_ingestion.html:119,138,213,215`) and the tests that pin them;
- operator-facing log and metric help texts (`remote_controller_client.py:192`,
  `admin_doc_fields.py:1170,1179`, `RemoteController/src/sync/doc_fields_metrics.py:96`);
- docs, release notes and `knovas.env.example` comments the branch adds.
Not renamed: `RC_*` names, `remote-controller` service, `RemoteController/` paths, code identifiers,
comments, released release-note sections, `docs/superpowers/` records.

### 5.4 Texts aligned with 1.5.0
1.5.0: "Document fields, including filtering by fields in search and in lists, is on for every
account" (Knovas can still switch it off, and older servers lack it). Every text that says "off by
default" / "only once Knovas has enabled it" is rewritten to that statement plus "the Platform and the
Connector check every answer and behave as before when it is off": `RELEASE_NOTES.md`,
`KnovasPlatform/docs/features/document-fields.md`, `docs/client/document-fields.md`,
`docs/client/README.md`, `RemoteController/docs/configuration.md`, `RemoteController/docs/operations.md`,
`RemoteController/CHANGELOG.md`, `RemoteController/README.md`, `docs/specifications.md`,
`knovas.env.example`, and the System-tab hint in `web_interface/admin_system.py`. Caveats that a
Knovas update ("S2", pointer in the request body) is still needed are removed; 1.5.0 documents it.

---

## 6. Extraction fixes (Connector; Platform mirror where the code is shared)

**E1 Partial rule.** One rule in both components (`partial_note_for`): a document is partial when
`pdf:ocr_pages_skipped > 0`, or `pdf:ocr_pages_failed > 0` (new: failed pages are empty pages), or —
only for a library that does not count skipped pages — `expect_ocr` and `pdf:ocr_backend == "none"`
and `pdf:ocr_pages_skipped` absent. The note carries counts only (`ocr_pages_skipped`,
`ocr_pages_failed`, `ocr_pages`, `text_pages`, `ocr_backend`). The degraded-backend metric counts only
the third case and skipped pages that the library attributes to a missing backend. Tests use the
realistic 0.4 key combinations (born-digital with `ocr=`, mixed, starved, no engine).

**E2 OCR budget and workers.** `ocr_time_budget_seconds`: default `min(240, T − 30)`, capped at
`T − page_timeout − 10` (in-flight pages finish in parallel within one page timeout), floor 10 s;
`RC_OCR_TIME_BUDGET_SECONDS` still overrides. `RC_OCR_WORKERS` unset → `OcrOptions(workers=None)`:
the library chooses `min(max_ocr_workers, CPUs − 1)` honouring CPU affinity and the cgroup v2 quota;
`Limits.max_ocr_workers` stays at its default (8). Set → used as today. The Platform keeps its
conservative admin-upload settings (1 worker, 50 pages, 60 s).

**E3 Resolution.** `RC_OCR_DPI` unset → `dpi` is not passed; the library renders at the page's native
resolution, at most 300 dpi, never upsampled. Set → passed (range 30–1200, else warning + unset).
Same in the Platform. Docs that call the setting a ceiling are corrected.

**E4 Sentences and page numbers.** `RC_SENTENCE_EMIT_MAX_BYTES` defaults to `0` (no gate; the
quadratic line counting it guarded against was fixed in the library's 0.3 work, and §4.4 makes the
sentence cap fail-soft); a positive value restores the gate. When a document has no sentences, each
part's `page_number` is derived from `content.pages` line ranges (`Page.line_start`/`line_end`), so
page numbers no longer depend on sentence splitting. Mirrored in the Platform's `knovas_transmit`.

**E5 OCR settings validated.** `RC_TESSERACT_LANG` must match `^[A-Za-z0-9_]+(\+[A-Za-z0-9_]+)*$`,
`RC_OCR_DPI` 30–1200, page timeout ≥ 1, max pages ≥ 1; an invalid value logs one warning naming the
setting (never a document) and uses the default — the existing pattern of `RC_PDF_TEXT_MODE` /
`RC_OCR_ENGINE`. If the library still rejects the options (`ValueError` from `OcrOptions`/`Limits`),
the child reports `extraction configuration invalid: <setting>`, which `is_unconvertible_error` does
**not** match (retryable), and `doctor.sh` checks the same rules.

**E6 Connector web server.** The image runs `gunicorn -w 1 -k gthread --threads 4` with
`--timeout ${RC_GUNICORN_TIMEOUT:-120}`: a gthread worker's main loop keeps heartbeating while request
threads run, so a long `POST /sync` no longer gets the worker (and the scheduler thread) killed, and
`GET /sync/status` answers while a one-time sync runs. The routes that write configuration or state
(`/sync/config`, `/sync/body`, `/sync/start`, `/sync/stop`, `/sync/doc-fields/requeue`, the
re-extraction route of §8) are reviewed for concurrent requests and serialised with one lock where
they write shared files.

**E7 Small corrections.** Docstrings and docs that say the text mode defaults to plain
(`document_text.py:336`, `knovas_extract_upload.py:220`), that `DependencyMissingError` passes through
(`document_text.py:858-859`), that 0.3 is "today" (`configuration.md:147`), that `[pdf]` pulls
`pymupdf-layout` (`ci.yml:296-299`), and that EML/MSG HTML is converted through selectolax
(`RemoteController/pyproject.toml:21-22`). The `_OCR_OPTION_ALIASES` entry `engine → backend` is
removed (`backend` takes an object; passing an engine name there would be a type error).

---

## 7. Document fields complete per 1.5.0

**F1 Field creation and editing (Platform, Dokumentfelder tab).** The create/edit form offers, per
datatype: `code_scheme` (code: `che_uid`, `iban`, `qr_ref`, `bger`, `bvger`, `ecli`, `icd10gm`,
`bcp47`, `generic`), `link_policy` (entity_ref: `resolve` / `never`), `fy_start_month` (1–12) and
`fy_label` (`start`/`end`) (period), `date_order` (`dmy`/`mdy`/`ymd`, default "account setting")
(date), and for choice lists one row per choice with code, labels de/fr/it/en and aliases. Edits send
only changed attributes (existing rule); settings that the server locks answer `409
field_type_locked`, shown as today. A counter "n von 256 Feldern" is shown.

**F2 Filter rail (Platform search and Liste anzeigen).** Per datatype:

| Datatype | Offered | `where` value sent |
|---|---|---|
| enum | one or several choices; "beginnt mit" | `"code"` / `["a","b"]` / `{"prefix": "…"}` |
| code | ist; beginnt mit; eine von (comma list) | `"…"` / `{"prefix": "E11"}` / `["…","…"]` |
| text | ist; beginnt mit | `"…"` / `{"prefix": "…"}` |
| money, number | ist; ab / bis (either or both) | `"CHF 1'000"` / `{"gte": …}` / `{"lte": …}` / `{"between": [a, b]}` when both |
| date, period | Zeitraum (overlaps, today's plain value); von / bis; liegt ganz in; option "auch teilweise" | `"2024"` / `{"gte": …, "lte": …}` / `{"within": "2024"}` / `+ "match": "possible"` |
| bool | ja / nein | `true` / `false` |
| entity_ref | one or several names (suggestions as today) | `{"name": …}` / `[{"name": …}, …]` |
| any datatype | "hat einen Wert" | `{"exists": true}` |

Client-side limits follow the server's: at most 8 keys, at most 50 values in a list (new check), the
existing depth and size limits. The "Verstanden als" chips render every operator. Special fields stay
out of suggestions (unchanged).

**F3 Server signals shown.**
- `"return_fields": {"applied": false}` → notice above the results: field values could not be read;
  results are shown without values.
- `meta.degraded_to_bm25: true` → notice above a non-empty result list too (today only on an empty one).
- `auto_scope` (server switch `QUERY_AUTO_SCOPE_ENABLED`, only when the request sends no `scope`;
  shape on KB `develop`, `query_pipeline.py:1496-1512`: `detections[]` of `{node_id, identifier_id,
  channel, score}`, `node_ids[]`, `applied`, `fallback`, `canonicalized`, `residualized`) →
  `applied: true`: notice "Suche automatisch auf <Namen> eingegrenzt" (names of the `node_ids` read
  through the Platform's knowledge-graph client, so only nodes the person may see are named; others
  are counted); `fallback: true`: notice "In <Namen> nichts gefunden – alle Dokumente durchsucht".
  The API has no per-request switch to turn it off, so the notice explains and offers no action.
  The mock gets both shapes.
- German text for the warning `ambiguous_number`.
- Listing sort "Dokumentpfad absteigend" (`pointer` desc) next to ascending.

**F4 Field keys on upload warnings (Connector).** Per cycle the Connector counts `(code, key)` pairs
instead of codes only; `/sync/status` and the Ingestion tab show "invalid_value 3× (amount)". Keys
only, never values or paths (keys are visible to everyone in the account and cannot look like
personal data, per 1.5.0).

**F5 Re-send without a trigger upload (Connector).** At the start of a cycle in which the state holds
`not_accepted` rows, at most once per hour, the Connector calls `GET /secured/graph/doc-fields`
(mTLS, its existing request helper). `404` means Document fields are off: nothing changes. Any other
answer except `5xx` and network errors means uploads with `fields` are accepted: the `not_accepted`
rows the scan reaches are requeued exactly like after a `staged` echo today (side queue,
`RC_FIELDS_REUPLOAD_PER_CYCLE`, default 100). The existing echo-based trigger stays. The probe sends
no body and logs only the status class.

**F6 `503 where_requires_calibration`.** 1.5.0 lists it as "a problem on the Knovas side. Try again
later." The branch's `listing_only` capability state stays (search filters hidden, lists and card
values kept), but it is held for 5 minutes instead of 1 hour, and its texts say the filter is
temporarily unavailable at Knovas, not that it is "noch nicht eingerichtet".

**F7 Dead search keys.** The Platform stops sending `top_k`, `filters` and `encryption_matrix` (the
server reads none of them). `limit` stays (the server still reads it).

---

## 8. The library used fully

**L1 File-property opt-ins `keywords` and `status` (Connector; Ingestion tab).** Next to the four
existing opt-ins, per folder, default off:
- `keywords` → field `keywords` from `pdf:keywords`, `docx:keywords` and `msg:categories`: split on
  `,` and `;`, trimmed, NFC, case-insensitively de-duplicated, at most 32 values of at most 256
  characters (the 1.5.0 limits);
- `status` → field `status` from `docx:content_status`, trimmed and sent as written; Knovas matches it
  against the choice labels and aliases, a value it does not know comes back as an `invalid_value`
  warning (counted, F4) and is not stored.
They join the upload layer below path templates and fixed values (unchanged precedence). The
Connector's config schema, `contracts/vectors`, the Platform's ingestion compiler and the mock are
extended; the per-document fields digest includes the new items so enabling them re-sends within the
bound like any other field change (with the existing cost confirmation).

**L2 Description.** When the ingestion profile sets no description, the Connector uses, in order,
`docx:subject`, `pdf:subject`, `pdf:xmp_description` (`extract_content.py:26-36`; the keys `subject`
and `description` it looks for today are never produced and are removed).

**L3 DOCX layout mode in both components.** `RC_DOCX_TEXT_MODE` (`layout` default, `plain`) is passed
as `text_mode` for `.docx`; the Platform mirrors it. In layout mode no `tables` payload is sent for
DOCX (as for PDFs since `RC_SEND_PDF_TABLES=false`): the rows are in the text, and a payload would be
indexed twice if the server ever stopped dropping it.

**L4 RTF-only Outlook mails.** Both images install the `[rtf]` extra (striprtf, BSD-3); without it an
MSG whose only body is RTF extracts to empty text because the import error is swallowed.

**L5 Operations.**
- Connector `/metrics`: `rc_build_info{rc_version,knovas_extract_version,pdf_text_mode,docx_text_mode,ocr_engine}`;
  counters `rc_ocr_pages_total{result="ocr"|"failed"|"skipped"}`, `rc_ocr_seconds_total`, and
  `rc_extract_warnings_total{class}` with a fixed set of classes mapped from the library's warning
  prefixes (`ocr`, `layout`, `metadata`, `markdown`, `tables`, `sentences`, `other`) — never warning
  text.
- `/sync/status` reports `knovas_extract_version` and the text modes; the Platform's System tab shows
  the extractor version of the Platform and of the Connector side by side, and warns when they differ.

**L6 Re-extraction after an extractor upgrade (Connector + Platform).**
- The Connector stores an extraction stamp per document: a short hash of the knovas-extract version,
  the PDF and DOCX text modes, the OCR engine, the DPI setting, the sentence policy and an internal
  `EXTRACTION_SCHEMA` constant (bumped when the Connector's own text processing changes). Rows synced
  before this change have no stamp and count as outdated.
- `/sync/status` reports the number of outdated documents per source.
- `POST /sync/reextract/requeue` (same auth as the existing requeue route) puts outdated documents into
  the existing side queue with reason `reextract`, ordered: partial first, then PDFs, then DOCX,
  e-mails, the rest; bounded by `RC_REEXTRACT_PER_CYCLE` (default 100) on top of the fields bound.
  A re-extracted document is uploaded in place (same identifier) and its stamp updated.
- The Connector also stores the SHA-256 of the text it uploaded (`text_sha256`). A re-extraction
  whose text, parts layout and fields are unchanged updates the stamp **without uploading** (no
  billing). Rows uploaded before this change have no hash, so this first round uploads them all;
  later upgrades only upload what really changed.
- Ingestion tab: "N Dokumente mit älterer Extraktion" and a button "Neu extrahieren" with the same
  cost and duration confirmation as a field change (uploads are billed; the dialog computes the
  duration from the document count, the per-cycle bound and the sync schedule, like the existing
  field-change dialog). Nothing is requeued without that click.

**L7 Accidental differences between the Connector and the Platform.**
- Same PyMuPDF pin in both (the version CI tests; the Connector is unpinned today, the Platform pins
  1.28.0).
- Same partial rule (E1), sentence policy (E4), resolution default (E3), DOCX text mode (L3).
- The Platform preview survives table-heavy DOCX: on `ResourceExhaustedError` from the Markdown
  expansion guard it shows the plain text instead of failing.
- Kept deliberately different: OCR budgets and workers (the Platform runs inside a web request),
  OCR cache (disk vs memory), upload part size, title source.

---

## 9. Version pin and CI

- **Dockerfiles** (Connector and Platform): `ARG KNOVAS_EXTRACT_VERSION=0.4.0a1` and
  `ARG KNOVAS_EXTRACT_GIT_REF=` (empty). Empty ref → `pip install "knovas-extract[<extras>]==${KNOVAS_EXTRACT_VERSION}"`
  from PyPI; a ref → `git+https://github.com/Seifeddini/knovas-extract-python.git@<ref>` (development
  only). Extras: Connector `pdf,ocr,docx,msg,html,rtf,sentences`; Platform the same plus `markdown`
  (preview). The broken fallbacks (`>=0.3`, `>=0.2`) are removed. A build prints the installed version
  and fails if it differs from `KNOVAS_EXTRACT_VERSION` (unless a git ref was given).
- **Until 0.4.0a1 is on PyPI** the default is `KNOVAS_EXTRACT_GIT_REF=<full 40-character merge commit>`;
  the switch to PyPI is one commit.
- **pyproject / requirements**: `knovas-extract[…]>=0.4.0a1` (floor, open upper bound).
- **CI**: one job step reads `KNOVAS_EXTRACT_VERSION` from both Dockerfiles and fails if they differ;
  the Connector and Platform test jobs install exactly that version (or ref) before their requirements;
  the `pymupdf-layout` must-fail check runs for **both** images and blocks.
- **Compose / start.sh**: no build-arg changes needed; the pinned default applies. Because the pip
  line now changes with every bump, Docker's layer cache no longer keeps an old `main` install.

## 10. Privacy and security invariants (unchanged, restated because new code touches them)
- No field value, document text, snippet, path or e-mail address in logs, metric labels, errors or
  audit entries; counts, codes, keys and versions only. New code gets the same caplog tests the fields
  branch has.
- The OCR disk cache keeps mode 0600 and per-document purge; re-extraction reuses it.
- The capability probe and the re-extraction route carry no document data.
- The `[rtf]` extra adds striprtf (pure Python, BSD-3); NOTICE/licensing docs list it.

## 11. Testing and verification
- **Library**: unit tests for §4.2–§4.4 (DOCX without tables byte-identical, table placement, header
  repetition per pack, merged and nested cells, sections and sentence contracts, determinism; e-mail
  HTML cases; sentence cap); all CI gates of the library on the PR.
- **Connector**: unit tests for E1–E6, F4, F5, L1–L3, L5, L6 (state-DB migration for the stamp, side
  queue ordering and bounds, requeue route), the extractor pin check; full suite.
- **Platform**: tests for F1–F3, F6, F7, L6 (tab, confirmation), L7 (preview fallback), the System
  tab; full suite including the PostgreSQL-backed tests, run serially against a dedicated test
  database (the audit's parallel runs collided on one database).
- **Mock Knovas API**: new responses for `auto_scope`, `return_fields.applied:false`, the probe, and
  `keywords`/`status` values.
- **Images**: build both; `pip show pymupdf-layout` fails; installed version equals the pin; Tesseract
  smoke (`select_backend("auto")` picks tesserocr) in the Connector image.
- **End-to-end**: `docker compose --profile mock` with a five-document corpus (success criterion 3).

## 12. Out of scope (follow-ups)
- Other 1.5.0 API areas: relevance feedback (`/secured/analytics/engagement`, `relevance-feedback`,
  `document/rating`), template filling, knowledge-graph `scope` in search, `graph_assign` at upload
  (the Connector's path templates could file documents under nodes), `GET /secured/document/<uuid>`
  (the Platform's `/api/document/<id>` is still a stub), `Input` as a list of phrasings,
  `documents_by_access`, `cert-info`.
- Library: `.md` front matter as fields (needs `.md` extracted as Markdown), sentence language for
  EML/MSG/TXT/RTF (always English rules), `find_tables()` opt-out for PDFs (performance), section
  hierarchy on sentences (`feat/sentence-section-hierarchy` design).
- Server (KnowledgeBase): the part buffer dropping `tables`; `extractor_version`/`text_mode` in
  upload metadata; a principal assertion for Connector uploads so entity values link in brokered
  tenants (today the server keeps them as unlinked names).
- Search quality: the source folder's own name is in neither `path` nor the identifier of Connector
  uploads; adding it would help known-item search but means re-uploading every document.

## 13. Public documentation (for the maintainer of knovas.ch)
The 1.5.0 page "Document fields → With the RemoteController" should, once this ships:
- say "Knovas Connector" (with "formerly RemoteController" once);
- list the file properties as: e-mail date, e-mail type, author, language, **keywords** (PDF/Word
  keywords, Outlook categories), **status** (Word content status);
- replace "Once Knovas enables the feature, it sends those documents again" by "When Knovas starts
  accepting fields, the Connector notices within an hour and sends those documents again, a limited
  number per cycle";
- add under "In the Knovas Platform": the Ingestion tab can re-extract documents produced by an older
  extractor, after a confirmation (each upload is billed).

## 14. Risks
| Risk | Mitigation |
|---|---|
| A PyPI release cannot be replaced | full library CI on the PR, the user's OK before merge and before tag |
| DOCX layout mode changes the text of DOCX files with tables | byte-identical for DOCX without tables; re-ingest is an administrator decision (L6) |
| `gthread` lets Connector requests run concurrently | review and lock the routes that write shared state (E6); tests with concurrent requests |
| Re-extraction is billed | admin-only, explicit confirmation with real numbers, per-cycle bound |
| The fields branch was built against the mock and the KB goldens, not a live tenant | goldens are byte-identical to the server's examples; a live check on a test tenant is recommended before customer rollout |
| Large merge in `knovas_client.py` / `app.py` | merge commit kept separate; full Platform suite including PostgreSQL before any later commit |
