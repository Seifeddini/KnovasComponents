

## doc_type: api_reference
product: knovas
classification: developer_kit
canonical: false
owner: backend
updated: 2026-07-23
tags:
  - knovas
  - api
  - secure
audience:
  - developer
  - client

# Secure API

Who this is for: engineers integrating **Knovas** (privacy-preserving knowledge search) into their own application. You call HTTPS endpoints only; you do not deploy or operate Knovas infrastructure.

When to use: detailed request/response rules for document upload, search, delete, and optional feedback APIs.

**Prerequisites:** Complete onboarding in the [Client Integration Guide](../Audience/Client%20Integration%20Guide.md) so you have a client certificate, private key, and CA root. All paths below use **HTTPS + mTLS** on your Knovas API host (for example `https://api.example.com/secured/...`).

## How authentication works

- Every `/secured/`* route requires a **valid client certificate** presented during the TLS handshake.
- Your **tenant** (isolated document store) is determined from that certificate. Do **not** send a tenant id in the JSON body.
- JSON responses use a flat envelope: `status`, `message`, plus endpoint-specific fields at the **top level** (no nested `data` object).

## 5-minute path

1. `POST /secured/init_document_transmission` — start an upload session.
2. `POST /secured/transmit_document_part` — send each text part (`0` … `part_count - 1`).
3. `POST /secured/query` — search your tenant's knowledge base.
4. Optionally analytics/feedback endpoints (see [Analytics Integration Guide](Analytics_Integration_Guide.md)).

## Endpoint summary


| Endpoint                                | Method | Purpose                                              |
| --------------------------------------- | ------ | ---------------------------------------------------- |
| `/secured/init_document_transmission`   | POST   | Create upload session; returns `transmission_key_id` |
| `/secured/transmit_document_part`       | POST   | Send one document text part                          |
| `/secured/query`                        | POST   | Semantic search in your tenant                       |
| `/secured/delete_information_object`    | DELETE | Delete a document by `pointer` (identifier)          |
| `/secured/analytics/engagement`         | POST   | Report implicit engagement after search              |
| `/secured/analytics/relevance-feedback` | POST   | Per-query relevance rating (1–5), append-only        |
| `/secured/document/rating`              | POST   | Permanent importance/quality rating (upsert)         |
| `/secured/sign_certificate`               | POST   | Sign a tenant CSR; returns cert + chain (no private key) |
| `/secured/generate_certificate`           | POST   | **Legacy** — server-generated encrypted key (not recommended) |
| `/secured/health`                       | GET    | Authenticated health check (optional)                |


## POST `/secured/sign_certificate`

Sign a certificate signing request (CSR) for the authenticated tenant. Use this after bootstrap onboarding to hold private keys locally while Knovas remains the issuing CA.

**Authentication:** mTLS with any active, registered tenant certificate (bootstrap or prior CSR cert). Tenant id is derived from the client certificate only — do **not** send `customer_id` in the body.

Request:

```json
{
  "csr": "-----BEGIN CERTIFICATE REQUEST-----\n...\n-----END CERTIFICATE REQUEST-----",
  "validity_days": 365,
  "organisation": "optional override"
}
```

Rules:

- `csr` — required PEM CSR; RSA ≥ 2048 bits or approved EC curve; must not request CA capability
- `validity_days` — optional, integer `1`–`1095` (default `365`)
- `organisation` — optional; defaults to organisation on the authenticating certificate

Success (`200`):

```json
{
  "status": "success",
  "message": "Certificate created successfully",
  "certificate": "-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----",
  "certificate_chain": "-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----",
  "serial_number": "123456789",
  "expires_at": "2027-06-08T12:00:00+00:00",
  "validity_days": 365
}
```

**No `private_key` or `private_key_password` is returned.** Pair the certificate with the key you used to create the CSR.

Common errors: `400` (invalid CSR), `401` (certificate problem), `429` (rate limit), `500` (signing or DB failure)

> **Note:** `POST /secured/generate_certificate` still returns a server-generated encrypted private key. New integrations should prefer CSR signing via this endpoint.

## POST `/secured/init_document_transmission`

Create a transmission key for the authenticated tenant.

**HTTP status on success:** `201 Created`

Request:

```json
{
  "part_count": 3,
  "identifier": "doc-2026-03-24-acme-contract-v1",
  "title": "Rekursantwort 2024-03",
  "description": "Optional short abstract shown in embeddings.",
  "path": "/Rekursantworten/2024/brief.md"
}
```

Rules:

- `part_count` — required, integer, `1`–`10000`
- `identifier` — **required**, string, `1`–`1000` characters. This is the document id you will see as `pointer` in query results. Re-uploading with the same `identifier` replaces the previous version.
- `title` — optional, max 500 characters. Strongest relevance signal; applied to every chunk.
- `description` — optional, max 2000 characters. Prepended to chunk embeddings with the title.
- `path` — optional, max 2000 characters. File path (e.g. `/folder/file.md`). Path segments are BM25-indexed per chunk (3× boost over body text). Not returned in query results.

Success (`201`):

```json
{
  "status": "success",
  "message": "Transmission initialized",
  "transmission_key_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

Common errors: `400`, `401`, `500`

## POST `/secured/transmit_document_part`

Send one part of the document. When all parts are received, ingestion and indexing run in the background.

Request:

```json
{
  "key": "550e8400-e29b-41d4-a716-446655440000",
  "part_number": 0,
  "snippet": "First text chunk of the document...",
  "page_number": 1,
  "sentence_number": 1
}
```

Rules:

- `key` — required; must be the `transmission_key_id` from init and must belong to your tenant
- `part_number` — required integer in `0` … `part_count - 1` (stable order)
- `snippet` — required, non-empty, max `500000` characters
- `page_number` — optional integer `>= 1` — the source page this part came from; returned on query hits
- `sentence_number` — optional integer `>= 1` — the sentence position within the part; returned on query hits
- `tables` — optional array of structured tables (max 50). See **Structured tables** below.

**Preparing content:** Convert PDFs, Word, HTML, etc. to **Markdown-style plain text** before chunking. See the [Client Integration Guide](../Audience/Client%20Integration%20Guide.md) → *Document format*. Use `#`/`##`/`###` headings in the `snippet` to mark chapters and sections — Knovas reads that structure from the text; there is no separate "chapter" or "section" field.

### Structured tables (optional)

If your source document has real tables (spreadsheet-style grids), send them as structured data in `tables` **in addition to** the prose in `snippet`. Knovas serializes each table into its own searchable chunk, so column/row values stay aligned instead of collapsing into a wall of text. This mirrors the `content.tables[]` output of the `knovas-extract` libraries (spec ≥ 1.1.0).

```json
{
  "key": "550e8400-e29b-41d4-a716-446655440000",
  "part_number": 4,
  "snippet": "## Revenue by region\n\nSee the table below.",
  "page_number": 7,
  "tables": [
    {
      "client_table_hint": "revenue-by-region",
      "title": "Revenue by region (Q3 2025)",
      "headers": ["Region", "Revenue", "YoY %"],
      "rows": [
        ["EMEA", "12.4M", "+8%"],
        ["APAC", "9.1M", "+14%"]
      ],
      "page": 7,
      "bbox": [72.0, 140.5, 523.0, 388.2]
    }
  ]
}
```

Per-table fields and limits:

| Field | Type | Required | Limit |
| ----- | ---- | -------- | ----- |
| `client_table_hint` | string | **required** | 1–128 chars. A label you choose (e.g. a slug); not used as a stable id. |
| `headers` | array of strings | **required** | ≤ 64 columns; each header ≤ 512 chars. |
| `rows` | array of arrays of strings | **required** | ≤ 5000 rows; **each row must have exactly as many cells as `headers`**; each cell ≤ 1024 chars. |
| `title` | string | optional | ≤ 512 chars. |
| `page` | integer | optional | `1`–`100000`. |
| `bbox` | array of 4 numbers | optional | `[x0, y0, x1, y1]` page coordinates. |

Whole request bound: at most **50** tables per part. Only the keys above are allowed — any unknown key rejects the request.

Table validation is **fail-closed**: any violation rejects the whole request with `400` and a stable `error_code` (see the error table), and no part of it is stored. Legacy clients that omit `tables` are unaffected.

**After all parts are received:** Knovas embeds chunks, stores them for search, and (when enabled) generates an **ingested auto-summary** as an extra searchable chunk. The summary may appear in later query results as `ingested_summary` once processing finishes.

Success (`200`):

```json
{
  "status": "success",
  "message": "Success",
  "transmission_complete": false
}
```

`transmission_complete` is `true` on the request that delivers the last part. The HTTP response returns before embedding finishes; retry `503` responses for the same part if needed.

Common errors: `400`, `401`, `404`, `503`

## POST `/secured/query`

Semantic search scoped to your tenant.

Request (single query):

```json
{
  "Input": "What were the Q3 revenue figures?"
}
```

Request (multiple query strings — rankings aggregate across them):

```json
{
  "Input": ["Q3 revenue", "third quarter earnings"]
}
```

Rules:

- `Input` — required; non-empty string or non-empty list of strings

**Response fields:**


| Field              | Meaning                                                           |
| ------------------ | ----------------------------------------------------------------- |
| `query_session_id` | UUID for engagement/feedback APIs                                 |
| `pointers`         | Document identifiers (`identifier` from upload), best match first |
| `result_count`     | Number of results                                                 |
| `results`          | One ranked object per document (see below)                        |
| `meta`             | `embed_latency_ms`, `stage1_latency_ms`, `stage2_latency_ms`      |


Each `results[]` item includes at least:


| Field                                   | Meaning                                                                                   |
| --------------------------------------- | ----------------------------------------------------------------------------------------- |
| `pointer`                               | Your document `identifier`                                                                |
| `document_uuid`                         | Internal document id (opaque UUID per hit)                                                |
| `ingested_summary`                      | `{ "present": bool, "text": str }` — auto-summary at ingest time when available           |
| `final_score`                           | Combined ranking score                                                                    |
| `cosine_similarity` / `cosine_distance` | Vector similarity for the best chunk                                                      |
| `page_number` / `sentence_number`       | Location of the best chunk (may be `null`)                                                |
| `top_chunks`                            | Array of per-chunk scores and locations                                                   |


Example (`200`):

```json
{
  "status": "success",
  "message": "Query executed successfully",
  "query_session_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "pointers": ["Q3 Financial Report 2025"],
  "result_count": 1,
  "results": [
    {
      "pointer": "Q3 Financial Report 2025",
      "document_uuid": "550e8400-e29b-41d4-a716-446655440000",
      "ingested_summary": {
        "present": true,
        "text": "Summary generated when the document was ingested."
      },
      "final_score": 0.91,
      "cosine_similarity": 0.8234,
      "cosine_distance": 0.1766,
      "page_number": 3,
      "sentence_number": 12,
      "top_chunks": [
        {"cosine_similarity": 0.85, "page_number": 3, "sentence_number": 12}
      ]
    }
  ],
  "meta": {
    "embed_latency_ms": 120.5,
    "stage1_latency_ms": 12.0,
    "stage2_latency_ms": 48.0
  }
}
```

Deployments may add extra ranking fields (for example `maxsim_*`). Query responses do **not** include full document body text — only metadata, scores, and optional `ingested_summary.text`.

Common errors: `400`, `401`, `503`

### Privacy and stored data

- Search uses only your tenant's data (from your certificate).
- Query **text** is not returned to other tenants and is not exposed in API responses.
- Knovas may store query **embeddings** and coarse metadata (length, timestamp) for operations and quality measurement; it does not store raw query strings in the document index used for client retrieval.

## DELETE `/secured/delete_information_object`

Delete one document and its chunks by `pointer` (your `identifier`).

Request body:

```json
{
  "pointer": "Q3 Financial Report 2025"
}
```

Success (`200`) includes `document_uuid`, `deleted_sentences`, and `deleted_versions`. **404** if the pointer does not exist.

## POST `/secured/analytics/engagement`

Report user actions tied to a search session. See [Analytics Integration Guide](Analytics_Integration_Guide.md).

Request:

```json
{
  "query_session_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "events": [
    {"action": "view", "pointer": "Q3 Financial Report 2025", "position": 1}
  ]
}
```

Rules:

- `query_session_id` — required UUID from `POST /secured/query`
- `events` — required, non-empty array, max **50** items per request
- `events[].action` — required: `view`, `click`, `download`, or `dismiss`
- `events[].pointer` — required document identifier from query results
- `events[].position` — optional 1-based rank in your UI

Response (`202`):

```json
{
  "status": "success",
  "message": "Engagement events accepted",
  "accepted": 2
}
```

Invalid events are dropped; `accepted` counts how many were stored. Treat as fire-and-forget.

## POST `/secured/analytics/relevance-feedback`

Rate relevance for a document after a search (append-only). Returns `202`.

```json
{
  "pointer": "Q3 Financial Report 2025",
  "relevance_score": 4,
  "query_session_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479"
}
```

- `pointer` — required
- `relevance_score` — required, integer `1`–`5`
- `query_session_id` — optional but recommended

## POST `/secured/document/rating`

Set or update permanent importance and/or quality for a document (upsert). At least one score required.

```json
{
  "pointer": "Q3 Financial Report 2025",
  "importance_score": 5,
  "quality_score": 3
}
```

Response (`200`) returns `pointer`, scores, and `last_updated`.

## Upload sequence (reference)

1. `POST /secured/init_document_transmission` → save `transmission_key_id`
2. For each part: `POST /secured/transmit_document_part` with `key`, `part_number`, `snippet`
3. Last part returns `transmission_complete: true`

## Rate limits

Gateway limits are per client certificate (tenant). See the [Client Integration Guide — Operational limits](../Audience/Client%20Integration%20Guide.md#operational-limits) for the current table. In short:

- **Query** — slowest path (~12/min at the gateway; application adds a matching token bucket on `/secured/query`)
- **Ingest parts** — highest throughput (3/s sustained)
- **Ingest init** — low frequency (6/min)
- **Other secured endpoints** — 1/s sustained

Treat HTTP `429` as retryable with exponential backoff.

## Error reference


| Status | Meaning                                                                     |
| ------ | --------------------------------------------------------------------------- |
| `400`  | Validation error (`type`: `validation_error`, optional `field`)             |
| `401`  | Certificate missing, invalid, or wrong tenant (`error_code`: `AUTH_FAILED`) |
| `404`  | Transmission key or document not found                                      |
| `413`  | Request body too large (see Client Integration Guide limits)                |
| `429`  | Rate limit exceeded — backoff and retry (see Client Integration Guide limits) |
| `500`  | Server error                                                                |
| `503`  | Temporary failure (Redis, embedder, ingestion) — retry with backoff         |
| `504`  | Gateway timeout — reduce payload or retry                                   |


Error responses carry a machine-readable `error_code` you can branch on. On `POST /secured/transmit_document_part` these include: `EMPTY_SNIPPET`, `SNIPPET_TOO_LARGE`, `MISSING_PART_NUMBER`, `INVALID_PART_NUMBER`, `INVALID_KEY` (all `400`); `RETRY_REQUIRED`, `EMBEDDER_TLS_MISCONFIGURATION`, `INGESTION_FAILED` (all `503`). Invalid `tables` payloads reject with `400` and a `SPEC_TABLE*` code — e.g. `SPEC_TABLES_MAXITEMS` (more than 50 tables), `SPEC_TABLE_HINT_{i}` (bad `client_table_hint`), `SPEC_TABLE_ROW_SHAPE_{i}_{j}` (row `j` of table `i` does not match the header count), `SPEC_TABLE_CELL_{i}_{j}` (oversized cell) — where `{i}`/`{j}` are the 0-based table and row indexes.

## Related docs (this kit)

- [Client Integration Guide](../Audience/Client%20Integration%20Guide.md) — onboarding, curl examples, limits, private key decryption
- [Analytics Integration Guide](Analytics_Integration_Guide.md) — engagement and feedback in depth

