# Fiduciary search diagnosis — RemoteController and Platform findings (2026-10-01)

A Swiss fiduciary (Treuhand) client gets bad search: most hits show no Trefferkontext, extracted
tables arrive as strings of numbers, and their queries — "Jahresabschluss von Müller AG",
"Steuererklärung von Hans Meier", "Shareholderagreement von XY GmbH" — miss. Law-firm tenants are
fine. This document holds the findings that live in **this** repository (RemoteController and
KnovasPlatform), the sandbox reproduction, and the OCR benchmark summary. The server-side findings
and the overall ranking are in `KnowledgeBase/docs/superpowers/audits/2026-10-01-fiduciary-search-audit.md`;
the extractor defects in `knovas-extract-python/docs/ocr-and-tables-findings.md`.

Written in English because the analysis spans three repositories; UI strings and domain terms stay
German. Constraint from the product owner: no new GPU or CPU capacity.

Line numbers refer to the `cl/eloquent-hawking-gyn8fo` checkout of 2026-10-01 (HEAD `08d0229`).

---

## 1. Why the fiduciary differs from the law firms

| | Law firm | Fiduciary |
|---|---|---|
| Documents | born-digital prose (contracts, letters, briefs) | scans, mixed scan+digital PDFs, tables (Bilanz, Erfolgsrechnung, Lohnausweis, tax forms), Excel, e-mails with attachments |
| Query | content: "Kündigungsfrist im Mietvertrag Huber" | known item: "<Dokumentart> von <Mandant> [Jahr]" |
| Where the answer is | in a sentence of the document | in the folder path, the filename and the cover page |
| What the pipeline indexes well | exactly that sentence | neither path (only as an embedding prefix) nor cover page (OCR garbage or never OCR'd) |

---

## 2. RemoteController findings

### R1 — Markdown is requested, never read, and parks mixed PDFs forever
`RemoteController/src/sync/document_text.py:256` passes `emit_markdown=True`; nothing in `src/`
reads `content.markdown` (`extract_content.py:39-53` maps text/sentences/sections/pages/title/tables;
`bytes_to_markdown`/`file_to_markdown` at `:412-419` return `.text`). Cost measured: a 20-page digital
PDF takes **8.27 s** with Markdown vs **0.45 s** without. Worse: on a PDF that has *some* text layer
(digital cover page + scanned body, or a scan with a one-line scanner/DMS stamp) pymupdf4llm OCRs
the scanned pages itself (English, layout model) and the Markdown is 4-13× longer than
`content.text`, which trips knovas-extract's expansion guard → `ResourceExhaustedError("markdown
expansion ratio")` → `_extract_bytes` maps it to "resource limit exceeded: …" → `is_unconvertible_error`
(`:157-177`) is true → `sync_executor.py:829-831` records `skip:unconvertible` → the file is never
retried until its mtime/size changes. Table-heavy DOCX and HTML-heavy e-mails hit the same guard.
The Platform's legacy upload path has the identical bug (`KnovasPlatform/components/docbridge_integration/src/knovas_extract_upload.py:90`).
**Fix:** `emit_markdown=False` in both places (one line each); then the extractor's guard should
become soft (warning + `markdown=None`) for the Platform preview, which is the only real reader.

### R2 — Sentences, and with them Trefferkontext and first-page preview, are dropped by raw file size
`document_text.py` (`RC_SENTENCE_EMIT_MAX_BYTES`, default 2 MiB) gates `emit_sentences` on
`len(raw)` — the **file** bytes, not the text. Scans are mostly image bytes, MSGs carry attachments,
so exactly the fiduciary's documents get `sentences=None`; `context_sidecar.build_sidecar_payload`
then writes `sentences: []` and `first_page.text: ""` (`context_sidecar.py:74-98`), and the Platform
shows a card with nothing under it. `scripts/build_context_sidecars.py` reuses `extract_document`
and inherits the cap (and the R1 crash). For PDFs pysbd runs per page (cost ≈ linear in pages), so
the cap protects only whole-text formats (DOCX/TXT/MSG bodies).
**Fix:** cap on extracted text size per format, retry once without sentences on
`ResourceExhaustedError("sentence count")`, and build `first_page` from `pages[0].text` when there
are no sentences. Durable: build sidecar units from the transmitted parts using the server's own
split rules (see R11).

### R3 — Upper-case extensions are never synced
`sync_executor.py:39-41` `_matches_globs` uses case-sensitive `Path.match`/`fnmatch` against
`**/*.pdf`. Verified: `Mandanten/Mueller AG/2023/SCAN_0001.PDF` → not matched. Scanners and many DMS
exports write `.PDF`/`.MSG`. The walker drops them before counting, so the gap is invisible.
**Fix:** match lower-cased path against lower-cased pattern; count what the walker drops (per
extension, oversize) and show it on the admin ingestion page.

### R4 — Unsaved profile means a 30-day window on shares
`sync/default_sync_body.py` sets `filters.max_document_age_seconds = 2592000` until an administrator
saves a profile. Prior-year Jahresrechnungen are never indexed and nothing says so.
**Fix:** surface the active window on the admin page; onboarding checklist.

### R5 — Files over 10 MiB are dropped silently — and pruned
`sync_executor.py:220-221` skips `stat.st_size > max_bytes` (default 10 MiB, `:251, 298`) before the
file is added to `scanned_paths` (`:558-560`), so a tracked document that grows past the cap is
**deleted from Knovas** by `_prune_removed_documents`. Multi-page colour scans and MSGs with
attachments routinely exceed 10 MiB.
**Fix:** count and log oversize files; never prune on size; raise the cap per type once the OCR time
budget (R7) exists; stub oversize files (R9).

### R6 — Every failure becomes `skip:unconvertible`, counted as synced, never re-queued
`sync_executor.py:829-831` calls `record_skip(reason="unconvertible")` for every unconvertible error
(expansion, timeout, no text, corrupt, encrypted alike); `sync_state.py:121-131` stores it in the
`transmission_key_id` column of the same `documents` row as a real upload (`sync_state_db.py:14-22`);
`status_from_fingerprint` (`sync_state.py:55-62`) compares only mtime and size, so the file reads
as **synced** in `/sync/status`. After an extractor fix nothing re-extracts them; `scripts/search-probe.sh:134-136`
then recommends re-transferring all documents, which is a whole-corpus re-embed on the server.
The error strings already distinguish the reasons (`document_text.py:280-300, 388-402`); only the
`sync_executor` call discards them.
**Fix:** reason-coded skips (`skip:<code>:<pipeline-tag>`), a `stale_extraction` status that
re-queues skip rows once per pipeline change (capped per cycle, after new/modified files), a
separate `skipped` count, and a targeted `requeue.py` instead of `full_rescan`.

### R7 — OCR is all-or-nothing: 300 s timeout, one bad page kills the document
`document_text.py:345-409` forks a child with `RC_EXTRACT_TIMEOUT_SECONDS=300`; a timeout is
recorded as unconvertible (R6). At ~1 s per OCR page a scan bundle over ~250 pages can never finish,
and the CPU already spent is thrown away. knovas-extract turns any page-level OCR exception into
`CorruptDocumentError` → "corrupt .pdf" → parked. `extractor died (exit N)` (OOM) does not match
`is_unconvertible_error` and loops every cycle.
**Fix:** page-aware timeout (child reports the page count; never parse untrusted PDFs in the
parent), an OCR time budget with partial results, per-page fail-soft in the extractor, `died`
parked with a reason.

### R8 — Fiduciary formats and e-mail attachments are never indexed
`document_text.py:71` `SYNCABLE_EXTENSIONS = {.md .txt .docx .pdf .eml .msg}`; `.xlsx/.xls/.csv/.doc/.pptx/.tif/.jpg`
are dropped at `sync_executor.py:207` before counting. EML/MSG attachments are metadata only
(`knovas_extract` `eml.py`, `msg.py`); sender, recipients and attachment names never reach the
index (`extract_content.description_from_metadata` reads only `docx:subject`/`subject`/`description`);
an e-mail with an empty body is parked as "no extractable text". Table-only DOCX files (forms,
layout-table letters) are parked the same way because knovas-extract puts DOCX tables only into
`content.tables` and `document_text.py:292-294` raises before looking at them.
**Fix:** xlsx via `openpyxl` in a capped "card" mode (sheet names + first rows as ` | `-joined
lines; never as `content.tables`, which the server would embed twice); `.rtf/.html` via the
existing extractors; images opt-in once OCR runs at 300 dpi; e-mail header block appended to the
body; attachments as child documents (`<rel>::att/<n>/<name>`, parent's access groups);
table-only DOCX rendered as rows.

### R9 — A file that fails extraction has no server presence at all
`knovas_uploader.py:147-175` returns before `init_document_transmission` whenever extraction raises,
so encrypted files, OCR failures, timeouts, unsupported and oversize files have no pointer, title
or path on the server — not even a filename search could find them.
**Fix:** upload a "Dateikarte" stub (`Datei: … / Ordner: … / Format: … / Inhalt nicht durchsuchbar:
<Grund>`) as one synthetic part with the real title, path and access groups
(`init_document_transmission` requires `1 ≤ part_count`, so a metadata-only contract is not
needed); record `stub:<code>`; the normal same-pointer update replaces it later. FLAG: one short
embedding per stubbed file.

### R10 — Titles: PDF `/Title` metadata wins over the filename
`knovas_uploader.py:181` `title = extracted_title or file_path.name`. Tax and accounting software
and Word templates emit `/Title` such as "Microsoft Word - Dokument1" or a template client's name.
The server prepends the title to every chunk's embedding, so a template title naming the wrong
client pollutes every chunk of every client's Jahresrechnung.
**Fix:** keep the extracted title only if it shares a non-generic token with the filename/path and
is not repeated across several Mandant folders; otherwise use the filename stem; truncate to 500
characters (the server rejects longer titles with 400 and the RC retries every cycle).

### R11 — No page markers; sentence numbering differs from the server's
`chunking.build_transmission_parts` sends one part per document (`PART_MAX_CHARS` 500 000) with
the part's `page_number` taken from its first pysbd sentence; knovas-extract joins pages with
`\n\n`; the server advances the page only at `\f` → **every chunk carries the first page**, the
Platform's Fundstellen all say "Seite 1" and the preview jumps to page 1. Sentence numbers: the
server adds its own unit offsets (newline levels, then a regex that splits after "Art. 959",
"31. Dezember") on top of the part's start number, so they diverge from the sidecar's pysbd indices;
the Platform's anchor misses and shows sentence 1 (the letterhead). `section_pages.section_prefix_at_offset`
also prepends `# Heading\n\n` to a part that already starts with the heading line (`chunking.py:107-108`),
embedding the heading twice.
**Fix:** replace the blank separator line between pages with `\f` (content hash unaffected: the
server rstrips lines) and take `page_number` from `pages`; build sidecar units with the server's
split rules and stop sending `sentence_number` so the server's derive path numbers identically
(`INGESTION_DERIVE_SENTENCE_NUMBERS` default true); fix the duplicated heading prefix.

### R12 — `tables[]` are sent, validated server-side, then dropped
`knovas_uploader.py:46-54` sends tables; the server's Redis buffer discards them
(`KnowledgeBase` audit §1.2). The RC spends `find_tables` CPU on every PDF page for nothing, and
PyMuPDF's output was garbled on the sample anyway (heading taken as header row, "612'40000\n.").
**Fix:** stop sending PDF tables; put row structure into the text instead (§4).

### R13 — Sidecar lifecycle and repair
`knovas_uploader.py:159-165` writes the sidecar **before** init and part transmission, so a failed
upload leaves new text against old chunks; `_prune_removed_documents` deletes in Knovas and in the
state DB but leaves the sidecar on `rc-state` (deleted text stays readable); `build_context_sidecars.py`
uses a 120 s limit, the same 2 MiB cap and `emit_markdown=True`, and cannot repair empty sidecars
in place. `scripts/doctor.sh:1006` counts a sidecar as covered when the file exists, even with
`sentences: []`.
**Fix:** stage the sidecar and commit after `status ok`; delete on prune; throttled self-heal of
missing/empty/v1 sidecars inside the sync window (no server call, no embedding); honest coverage
in doctor.

### R14 — Pointer collision and no move detection
With one source per client, `rel_root` is each source's own root (`sync_executor.py:443-454`), so
two clients' `2023/Jahresrechnung.pdf` share one pointer and one state-DB primary key — each cycle
overwrites the other's document in place (re-embedding every time). The RC has no rename/move
detection: the common workflow "scan to inbox, then move and rename into `Mandanten/<Client>/<Year>`"
costs a full re-extraction and re-embed, and the inbox pointer is pruned/detached.
**Fix:** prefix relative paths with a stable source label (or refuse overlapping sources);
move detection by (size, raw sha256) → re-point without re-extraction.

### R15 — Docker image and OCR environment
`RemoteController/Dockerfile` installs `knovas-extract` from `git+…@main` (floating, not
reproducible) with the `[pdf]` extra, which pulls `pymupdf4llm` → `pymupdf-layout`
(**PolyForm Noncommercial** or Artifex commercial) into the customer image; ships Tesseract with the
Debian *fast* language packs (deu/eng/fra); does **not** set `OMP_THREAD_LIMIT=1`, which the
benchmark shows is the single largest throughput lever (§5). The documented default
`RC_TESSERACT_LANG=deu+eng` is fine; every extra pack costs throughput.
**Fix:** pin a tagged knovas-extract release; install `pymupdf` explicitly instead of the `[pdf]`
extra (no `pymupdf4llm`); `ENV OMP_THREAD_LIMIT=1`; optionally ship `tessdata_best` for deu/fra/ita.

---

## 3. Platform findings

### P1 — The Platform discards the server's ranking
`KnovasPlatform/components/docbridge_integration/src/knovas_client.py:69-85`
`_extract_semantix_query_similarity` prefers `cosine_similarity` over `final_score`, and
`web_interface/app.py:3027-3030` `_apply_search_refinement` re-sorts every row by that score. On
the server, `cosine_similarity` is the stage-1 score of the reranked best chunk; `final_score` is the
ColBERT result (`query_two_stage.py:1113-1116`). The name-prefilter boost only reorders by
`final_score*1.25` and leaves the field unboosted, so sorting by `final_score` would not restore it
either — only a stored `server_rank` does. Values > 1 are divided by 100 (`:45-56`), so on the
pure-BM25/rescue path a raw BM25 score 7.3 becomes 0.073 and sorts below 0.9.
**Fix:** store `server_rank` on each row; sort by it; keep the score for display and thresholds.

### P2 — Request and post-processing
The UI posts `{query, limit: 20, filters: {}}` (`static/js/app.js:1091-1100`); the search route passes
the query verbatim (`app.py:1746-1750`); the server reads only `Input`, `query_prompt`, `scope` and
ignores `limit`/`top_k`/`filters`. The exact-match filter (`app.py:3014-3025`) is unreachable (checkbox
removed; `config/config.yaml:175` still mentions it) and would have required "von" and "ag" as
substrings. No client-side dropping by default (`min_similarity_score 0.0`, `max_cosine_distance -1`,
`supplement_filename_matches false`, `config.yaml:187-199`).
**Fix:** none needed for dropping; send `server_rank` (P1); later Mandant/Jahr/Dokumentart chips
once the server honours `filters`.

### P3 — Trefferkontext: only from sidecars, wrong anchor, wrong page, table rows filtered out
`context_store.enrich_result_with_context` (`:915-970`) builds everything from the sidecar;
`_anchor_sentence_index` (`:220-241`) falls back to index 0 when the number is not found (the
letterhead becomes "the match"); the page shown comes from the chunk, i.e. "Seite 1" (R11);
`_is_thin_location` (`:560-564`) drops lines with fewer than 4 letter-words — `Flüssige Mittel |
1'234'567.80 | 987'654.30` has two, so Bilanz rows lose to prose; `load_context` returns `None`
silently when the store directory is missing. `_snippetHtml` (`app.js:1341-1358`) shows the
context snippet first, the first page only when there is no snippet, so for a document-finding hit
the card shows an amount cell instead of the cover page.
**Fix:** anchor miss → skip the location instead of sentence 0; page from the sidecar record's
`p`; treat a Swiss amount next to a label, or a ` | ` row, as content; log coverage per search;
for document-intent queries prefer the first page when the snippet covers no query term.

### P4 — Cards cannot be told apart; Akte grouping needs a file that filesystem deployments do not have
Title = filename stem (`knovas_client.py:285-300`; `app.js:1259-1268`); the card shows format, date,
title and snippet, no folder (`app.js:1361-1430`). `akten_id`/`doc_type` come only from
`.search_enrichment.jsonl` (`app.py:3477-3486`), which the OneDrive mirror writes with
`doc_id/web_url/title/modified_at` only (`RemoteController/src/onedrive_mirror/mirror.py:400-407`)
and the AutoDoc filename parser (`file_utils.py:40-79`); a filesystem RC deployment has no
enrichment file, so no grouping and no notice (`docs/search-ui-backlog.md` §3a).
**Fix:** render a breadcrumb from `row.path` (already the pointer today — works before any server
change): "Müller AG › 2023 › Abschluss"; year fallback from the path.

### P5 — Diagnostic scripts misdiagnose
`scripts/doctor.sh:1006` counts an empty sidecar as covered. `scripts/search-probe.sh` treats a
one-word probe as a BM25 keyword probe (a year probe has an empty BM25 string on the server and runs
pure vector), prints NOT INDEXED for stopwords and years, and recommends re-transferring the whole
corpus (`:134-136`) — a whole-tenant re-embed.
**Fix:** import the server stopword list, strip digits before choosing probe terms, count sidecars
with sentences only, replace the full re-transfer advice with the targeted re-queue, and add a
document-finding section that matches query terms against the folded relative paths of tracked
files and reports each file's state, sidecar facts and rank.

### P6 — Preview anchors and the DOCX preview
`web_interface/preview.py:151, 212` locate passages with `page.search_for(span)`; a ` | `-joined
row (§4) will not match on digital pages, and scanned pages have no text layer at all. `preview.py:70`
calls `extract(..., emit_markdown=True)` for DOCX/EML/MSG (PDF is refused at `:61`), so the R1
expansion failure also breaks the DOCX preview of table-heavy files.
**Fix:** search for the row's cells individually; soften the extractor's expansion guard.

### P7 — 429 is not retried; "Mehr laden" re-runs the query
`knovas_client.py:36-42, 1420-1424` retry only connection errors. The server allows ≈12 queries/min
per tenant certificate (`defaults.toml:296-298`). Fiduciary users reformulate a lot.
**Fix:** honour `Retry-After` once; serve "Mehr laden" from a short-TTL cache.

---

## 4. Reproduction in the sandbox

Synthetic Swiss Jahresrechnung (borderless Bilanz with 8-9 pt rows, ruled Erfolgsrechnung, one
prose paragraph) rendered with reportlab; "scans" produced by rendering at 300 dpi, 0.4° skew, slight
blur, JPEG q75, image-only PDF. Run through `document_text._extract_bytes` exactly as the RC does.

| File | What the RC gets today |
|---|---|
| A digital | `content.text` with one cell per line; 1 of 2 tables found by `find_tables`, garbled; perfect pymupdf4llm Markdown — discarded |
| B scan (gray 300 dpi) | OCR ran at 72 dpi: "Forderungenas Liaungen und Leitungen", "45070900" for 456'789.00; ruled table missing entirely; 53 sidecar "sentences" of ≤36 chars |
| C digital cover + scanned body | `ResourceExhaustedError: markdown expansion ratio 4.14` → `skip:unconvertible` — the document is **absent from the index** |
| D scan with scanner stamp | same, ratio 13.2 — absent |
| E colour scan | as B |

Prototype on the same files (word boxes → lines; large horizontal gaps → ` | `; per-page OCR
decision = < 50 chars of text and images > 30 % of the page; Tesseract TSV at 300 dpi for image
pages): every row correct on A-E (`Flüssige Mittel | 1'234'567.80 | 987'654.30`), 0.02 s for 20
digital pages, ~1 s per scanned page. Known gap: a full-width same-baseline joiner interleaves
two-column prose, so table/column region detection is required before it can ship (law-firm
contracts must stay byte-identical).

---

## 5. OCR benchmark summary (harness and full tables in `knovas-extract-python/bench/ocr/`)

Corpus: 13 ground-truth pages (Jahresrechnung, Bankauszug, Lohnausweis, bilan FR, conto economico
IT, Steuererklärung with dot leaders, MWST-Abrechnung, two-column Aktionärbindungsvertrag, GV
protocol, QR invoice, letter) × 7 degradations (clean 300, office 300 gray JPEG + skew + blur,
gray 200, bitonal fax 150, colour 300, 1.5° skew, rubber stamp) + rot90. Metrics after
NFC/whitespace normalisation, case and punctuation kept; `numeric_exact` = share of ground-truth
amount/number tokens reproduced exactly. Synthetic data is cleaner than real scans — treat the
absolute numbers as optimistic, the ranking as robust.

| Config (Tesseract 5.3.4 unless noted) | CER | numeric exact | hallucinated words/page |
|---|---|---|---|
| **status quo** — MuPDF partial OCR, no dpi (= 72 dpi), what knovas-extract does today | 0.575 | 0.15 | 5.6 |
| MuPDF `get_textpage_ocr(dpi=300, full=True)` | 0.212 | 0.78 | 23.2 |
| RapidOCR bundled models (ONNX, CPU) | 0.093 | 0.82 | 0.1 (WER 0.60 — poor on German) |
| Tesseract CLI, fast model, psm 4, 300 dpi | 0.070 | 0.92 | 10.9 |
| Tesseract CLI, fast model, psm 3, 300 dpi | 0.054 | 0.92 | 7.4 |
| fast, psm 3, auto-dpi, auto-rotate, rule-line erase, deu+fra+eng | 0.020 | 0.92 | 1.0 |
| **best** model, psm 3, auto-dpi, auto-rotate, rule-line erase, deu+fra+eng | **0.019** | **0.94** | 1.3 |

Per degradation (best config): clean 0.006, office 300 0.008, gray 200 0.007, colour 0.008,
skew 1.5° 0.032, stamp 0.045, fax 150 0.031 (amounts 80 %), rot90 0.004 with auto-rotate — without
it psm 3 fails completely (CER 0.79). Hardest documents: Steuererklärung (dot leaders) and
Lohnausweis (boxed form).

Throughput, 26 pages on 4 cores: **88 pages/min** with 4 single-threaded processes
(`OMP_THREAD_LIMIT=1`) vs **10 pages/min** sequential with Tesseract's default OpenMP threads
(8.8×); best model 41 pages/min. Single core, office scans: 2.2 s/page (fast), ~3.5 s (best).
Micro-costs: render 300 dpi gray 0.02 s; **PNG encoding 1.3 s** (use PGM via stdin: 0.001 s);
process spawn + model load 0.16-0.31 s; per-page OCR decision < 1 ms.

Recommended default on the customer host: Tesseract CLI, psm 3, 300 dpi gray PGM via stdin, TSV
word boxes, `preserve_interword_spaces=1`, auto-rotate (OSD only when a cheap sideways check
fires), rule-line erase before OCR (lines kept as table metadata), one process per page with
`OMP_THREAD_LIMIT=1`, fast models by default and `tessdata_best` as an opt-in accuracy tier.

---

## 6. Recommendations for this repository, in order

| # | Item | Fixes | Effort | Compute |
|---|---|---|---|---|
| 1 | `emit_markdown=False` in `document_text.py` and `knovas_extract_upload.py` | R1, P6 | S | **−8 s / 20 pages** |
| 2 | Platform keeps `server_rank` | P1 | S | none |
| 3 | Case-insensitive globs; count unsupported/oversize; log prunes | R3, R5 | S | none |
| 4 | Sentence gate on text size + first-page fallback + retry without sentences | R2 | S | negligible |
| 5 | Reason-coded skips, `skipped` status, `stale_extraction` re-queue, `requeue.py`; retire the full-rescan advice | R6, P5 | M | one re-extract per changed file |
| 6 | Pin knovas-extract by tag; drop `pymupdf4llm`/`pymupdf-layout`; `OMP_THREAD_LIMIT=1` | R15 | S | negative |
| 7 | `\f` page markers, `page_number` from pages, sidecar units with server rules, heading-prefix fix | R11, P3 | M | none |
| 8 | Context-store fixes: anchor miss, page from `p`, table rows as content, coverage log; breadcrumb on the card | P3, P4 | S | none |
| 9 | Title sanitiser + 500-char cap | R10 | S | none |
| 10 | Page-aware timeout + OCR budget + fail-soft; stubs for failed/oversize/unsupported files | R7, R9 | M | FLAG one short embed per stub |
| 11 | xlsx card mode, rtf/html, e-mail header block, table-only DOCX as rows; attachments as child documents | R8 | M-L | one-time backlog at the existing rate limit |
| 12 | Sidecar lifecycle: commit after upload, delete on prune, throttled self-heal | R13 | M | RC CPU only |
| 13 | Source-label prefix / overlap refusal; move detection | R14 | M | saves re-embeds |
| 14 | Stop sending PDF `tables[]`; row-preserving text instead (separate plan) | R12 | — | saves CPU |
| 15 | Honour `Retry-After`; "Mehr laden" cache | P7 | S | saves GPU |

Rejected: raising `RC_SENTENCE_EMIT_MAX_BYTES` by config alone (pysbd on whole DOCX/TXT bodies can
exceed the 300 s timeout → missing context becomes a missing document); a full rescan as remedy;
switching an existing deployment's source layout; re-enabling the filename supplement (no
readability check); LibreOffice for `.doc/.xls` (heavy per conversion); spreadsheets as
`content.tables` (double embeddings); image OCR before the 300 dpi fix.

---

## 7. Measuring it at the client

Day 0 with existing tools in the `remote-controller` container: `find` counts by extension
(case-insensitive) and size (> 10 MiB, > 2 MiB); `SELECT transmission_key_id, count(*) FROM documents
GROUP BY 1` in the state DB; grep the logs for `markdown expansion ratio`, `extraction timeout`,
`Skipping sentence emission`; `load_last_sync_body()` for the active window, sources and prefix.
Then a known-item evaluation generated from the folder structure (`<Kategorie> von <Mandant>
<Jahr>` → expected file) with Success@1 / MRR / Recall@5 in server and Platform order, and a miss
waterfall: never ingested → garbage text → ranked low → re-sorted by the Platform. Every change
above is accepted or reverted on those numbers; the law-firm suites in `KnowledgeBase/QualityTests`
are the no-regression gate.

A separate implementation plan — custom "markdown-lite" extraction (headings, row-preserving
tables with folded column headers, reading order for two-column pages, key-value fields, `\f`
pages) on top of the high-throughput OCR configuration above, with its test phase — follows in
`docs/superpowers/plans/`.
