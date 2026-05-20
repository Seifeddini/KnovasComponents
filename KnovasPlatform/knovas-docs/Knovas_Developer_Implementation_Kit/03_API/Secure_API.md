---
doc_type: api_reference
product: Knovas Semantic Search
classification: developer_kit
canonical: false
owner: backend
updated: 2026-04-26
tags:
  - knovas-semantic-search
  - api
  - secure
audience:
  - developer
  - client
---

# Secure API

Who this is for: client integrators and backend developers implementing secured tenant operations **via HTTP only** (this is the integration surface; you do not run or configure Knovas Semantic Search services yourself).

When to use: endpoint-level behavior for upload, query, and encrypted-vector workflows.

All endpoints here are under `/secured/*` and require valid mTLS authentication.

## 5-Minute Path

1. Call `POST /secured/init_document_transmission`.
2. Send each snippet with `POST /secured/transmit_document_part`.
3. Query with `POST /secured/query` (each hit includes `ingested_summary` when available).
4. Optionally fetch document metadata with `GET /secured/document/<uuid>` using `document_uuid` from query results.
5. Optionally rotate vectors with `GET /secured/generate-encryption-matrix`.
6. Optionally send explicit feedback with `POST /secured/analytics/relevance-feedback` and `POST /secured/document/rating`.

## Endpoint Summary

| Endpoint | Method | Purpose |
|---|---|---|
| `/secured/init_document_transmission` | POST | Create transmission session key |
| `/secured/transmit_document_part` | POST | Send one document snippet |
| `/secured/query` | POST | Semantic search in tenant |
| `/secured/document/<uuid>` | GET | Document metadata + ingested auto-summary (Weaviate `Document` id) |
| `/secured/llm/generate` | POST | General-purpose LLM text generation |
| `/secured/llm/summarize-document` | POST | Document summarization (inline or `document_uuid`) |
| `/secured/generate-encryption-matrix` | GET | Rotate tenant vectors into encrypted space |
| `/secured/analytics/engagement` | POST | Report implicit user engagement after search |
| `/secured/analytics/relevance-feedback` | POST | Rate a document's relevance for a specific query (1–5) |
| `/secured/document/rating` | POST | Set permanent importance and/or quality rating for a document |
| `/secured/document/rating` | GET | Retrieve current rating and aggregated relevance feedback |

## POST `/secured/init_document_transmission`

Create a transmission key for the authenticated tenant.

Request:

```json
{
  "part_count": 3,
  "identifier": "doc-2026-03-24-acme-contract-v1",
  "title": "Rekursantwort 2024-03",
  "path": "/Rekursantworten/2024/brief.md"
}
```

Rules:

- `part_count` required, integer, `1..10000`
- `identifier` optional, recommended for tracking/search output
- `title` optional string, max 500 chars. Applied to every chunk as the strongest relevance signal.
- `description` optional string, max 2000 chars. Prepended to chunk embeddings alongside title.
- `path` optional string, max 2000 chars. File path such as `/Rekursantworten/2024/brief.md`. Path segments are tokenized and BM25-indexed per chunk, boosting relevance for queries that match the path (3× boost over body text). Not stored in query results.

Success:

```json
{
  "status": "success",
  "message": "Transmission initialized",
  "transmission_key_id": "uuid-or-key-id"
}
```

Common errors: `400`, `401`, `500`

## POST `/secured/transmit_document_part`

Send a single snippet; processing starts when all parts are received.

Request:

```json
{
  "key": "uuid-or-key-id",
  "part_number": 0,
  "snippet": "First text chunk of the document...",
  "page_number": 1,
  "sentence_number": 1
}
```

Rules:

- `key` required; must belong to authenticated tenant
- `part_number` required; use stable sequence (`0..part_count-1`)
- `snippet` required, non-empty, max `500000` chars
- `page_number` optional integer `>= 1`
- `sentence_number` optional integer `>= 1`

**Client-side preparation:** Parse source documents into **Markdown-style plain text** (headings, lists, readable paragraphs) before chunking into `snippet` values. The API accepts unstructured text, but Markdown-oriented parsing yields better structure for retrieval. See [`../Audience/Client Integration Guide.md`](../Audience/Client%20Integration%20Guide.md) → *Document format (recommended)*.

**After ingestion:** The platform stores an **ingested auto-summary** as an extra searchable chunk (same summarization prompts as `POST /secured/llm/summarize-document`). Configuration: Knovas platform `INGESTION_AUTO_SUMMARY_*` settings.

Success:

```json
{
  "status": "success",
  "message": "Success",
  "transmission_complete": false
}
```

Common errors: `400`, `401`, `404`, `503`

## POST `/secured/query`

Run semantic search in the authenticated tenant.

Request:

```json
{
  "Input": "query text",
  "encryption_matrix": [[1.0, 0.0], [0.0, 1.0]]
}
```

Rules:

- `Input` required
- `encryption_matrix` required only when tenant has encrypted embeddings
- matrix must be orthogonal and dimension-compatible

**Response:** Includes `query_session_id`, `pointers`, `result_count`, and `results`. Each `results[]` entry includes **`document_uuid`** (Weaviate `Document` id), **`ingested_summary`** (`{ "present": bool, "text": str }`), ranking fields (`final_score`, `cosine_similarity`, …), optional `page_number` / `sentence_number`, and `top_chunks`. See `POST /secured/query` below for a full example.

`query_session_id` links to engagement events; see [Analytics Integration Guide](Analytics_Integration_Guide.md).

Common errors: `400`, `401`, `503`

## GET `/secured/document/<uuid>`

Returns document metadata and **`ingested_summary`** for the Weaviate `Document` id `<uuid>` (same as `document_uuid` on query results and on `POST /secured/llm/summarize-document` in reference mode). See `GET /secured/document/<uuid>` below.

## POST `/secured/analytics/engagement`

Report user engagement events tied to a search session. See [Analytics Integration Guide](Analytics_Integration_Guide.md) for full details.

Request:

```json
{
  "query_session_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "events": [
    {"action": "view", "pointer": "doc-123", "position": 1}
  ]
}
```

Rules:

- `query_session_id` required (UUID from query response)
- `events` required, max 50 items
- `action` must be one of: `view`, `click`, `download`, `dismiss`

Response: `202 Accepted`

## POST `/secured/analytics/relevance-feedback`

Rate how relevant a specific document was for a query. Append-only — each call adds a record; ratings are never overwritten. Returns `202 Accepted`.

Request:

```json
{
  "pointer": "Q3 Financial Report 2025",
  "relevance_score": 4,
  "query_session_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479"
}
```

Rules:

- `pointer` required, non-empty
- `relevance_score` required, integer `1..5` (1 = not relevant, 5 = very relevant)
- `query_session_id` optional UUID; links the rating to the query session that produced the result

Response (`202`):

```json
{
  "status": "success",
  "message": "Relevance feedback recorded",
  "recorded": true
}
```

Common errors: `400`, `401`

## POST `/secured/document/rating`

Set or update a permanent importance and/or quality score for a document. One row is stored per `(tenant, pointer)` pair and updated on every call (upsert).

Request:

```json
{
  "pointer": "Q3 Financial Report 2025",
  "importance_score": 5,
  "quality_score": 3
}
```

Rules:

- `pointer` required, non-empty
- `importance_score` optional, integer `1..5` — how critical the document is to the tenant's workflow
- `quality_score` optional, integer `1..5` — how reliable or well-written the source is
- At least one of `importance_score` or `quality_score` must be provided

Response (`200`):

```json
{
  "status": "success",
  "message": "Document rating updated",
  "pointer": "Q3 Financial Report 2025",
  "importance_score": 5,
  "quality_score": 3,
  "last_updated": "2026-04-17T10:23:45.123456+00:00"
}
```

Common errors: `400`, `401`

## GET `/secured/document/rating`

Retrieve the current permanent rating and aggregated relevance-feedback statistics for a document.

Query parameter:

- `pointer` required

Response (`200`):

```json
{
  "status": "success",
  "message": "Document rating retrieved",
  "rating": {
    "pointer": "Q3 Financial Report 2025",
    "importance_score": 5,
    "quality_score": 3,
    "last_updated": "2026-04-17T10:23:45.123456+00:00"
  },
  "relevance_feedback": {
    "total_ratings": 12,
    "avg_relevance": 3.83,
    "score_5": 4,
    "score_4": 3,
    "score_3": 3,
    "score_2": 1,
    "score_1": 1
  }
}
```

`rating` is `null` when no permanent rating has been set. `relevance_feedback.total_ratings` is `0` when no per-query feedback has been submitted.

Common errors: `400`, `401`

## GET `/secured/generate-encryption-matrix`

Generate matrix `Q` and rotate stored tenant vectors.

Query parameter:

- `include_matrix` optional, default `false`

Success (example):

```json
{
  "status": "success",
  "message": "Encryption matrix generated and embeddings rotated",
  "client_id": "client-uuid",
  "updated_objects": 245,
  "embedding_dimension": 1024,
  "matrix_version": "2026-03-15T09:31:12.187654+00:00",
  "matrix_id": "matrix-6f5f50b3-f9ab-43f4-8e91-9eb0d9ab0f6d"
}
```

Effects:

- Rotates all vectors for authenticated tenant
- Enables encrypted-space query behavior

Common errors: `500`

## Upload Sequence (Reference)

1. `init_document_transmission`
2. Read `transmission_key_id`
3. Call `transmit_document_part` for each part
4. Final part returns `transmission_complete = true`
5. Background indexing may add an **ingested auto-summary** chunk; see `transmit_document_part` and `POST /secured/query` in this document.

## Error Reference

| Status | Meaning |
|---|---|
| `400` | Request validation failure |
| `401` | mTLS authentication/authorization failure |
| `404` | Transmission key not found |
| `500` | Internal processing failure |
| `503` | Temporary backend/state issue |



## Related (integration docs in this kit)

- Onboarding, chunking, limits, and retry behaviour: [`../Audience/Client Integration Guide.md`](../Audience/Client%20Integration%20Guide.md)
- Engagement reporting after search: [`Analytics_Integration_Guide.md`](Analytics_Integration_Guide.md)

## Semantic query (`POST /secured/query`) — product behaviour

**What you need to know as an API caller:** Search runs in **your authenticated tenant only** (identity comes from your client certificate — not from a tenant id in the JSON body). If your tenant uses **encrypted embeddings**, supply a valid `encryption_matrix` as required by `Secure_API.md`. Each hit in **`results`** includes **`document_uuid`** and **`ingested_summary`** when available (see `POST /secured/query` above).

**Privacy:** Raw query text is **not** stored for retrieval; the platform may retain **non-content metadata** (for example timing and coarse length) as part of normal operations.

**Session id for analytics:** Successful query responses include a **`query_session_id`** you can pass to [`POST /secured/analytics/engagement`](Analytics_Integration_Guide.md) and optionally to [`POST /secured/analytics/relevance-feedback`](Analytics_Integration_Guide.md) if you implement explicit feedback.

