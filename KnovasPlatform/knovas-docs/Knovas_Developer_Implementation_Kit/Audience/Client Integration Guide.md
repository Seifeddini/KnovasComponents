---
doc_type: guide
product: Knovas Semantic Search
classification: developer_kit
canonical: false
owner: platform
updated: 2026-04-17
Category:
  - Docs
  - Guide
audience:
  - developer
---

# Knovas Semantic Search - Client Integration Guide

Who this is for: client integration engineers and technical leads **calling Knovas Semantic Search over HTTPS / mTLS** (no access to our servers or internals beyond documented endpoints).

When to use: onboarding a client, uploading documents, and querying the tenant knowledge base.

## 5-Minute Path

1. Complete onboarding on port `8080` to get certificate material.
2. Use mTLS on port `8443` for all `/secured/*` endpoints.
3. Upload documents with `init_document_transmission` then `transmit_document_part`.
4. Query with `/secured/query`.
5. Implement retry/backoff for `429`, `503`, and `504`.
6. Optionally report explicit feedback with `POST /secured/analytics/relevance-feedback` and `POST /secured/document/rating`.

## Security and Data Guarantees

- Tenant isolation is enforced by native Weaviate multi-tenancy.
- Secured API access requires valid mTLS certificate authentication.
- Vector database stores embeddings and metadata, not raw document text.
- Data is encrypted in transit and at rest.

## Integration Flow

```text
Onboarding (8080) -> Receive cert/key/CA -> Secured operations (8443)
                   -> Upload document parts -> Query knowledge base
```

## Step 1: Onboarding (HTTP :8080)

The onboarding endpoint returns:

- `certificate_pem`
- `private_key` (encrypted PEM)
- `private_key_password`
- `ca_root_cert`
- `organisation_id` or `client_id`

Use your registration key:

```bash
curl -X POST http://<host>:8080/create_entity \
  -H "Content-Type: application/json" \
  -d '{
    "key": "<registration_key_from_email>",
    "entity_data": {
      "postal_code": "8154",
      "city": "Oberglatt",
      "country": "Switzerland",
      "address": "Buelachstrasse 70"
    },
    "entity_type": "organisation",
    "entity_name": "Your Company GmbH"
  }'
```

Store the returned certificate material in a secrets vault immediately.

## Step 2: Initialize Transmission (HTTPS :8443 + mTLS)

```bash
curl -X POST https://<host>:8443/secured/init_document_transmission \
  --cert client_cert.pem \
  --key client_key.pem \
  --cacert ca_root_cert.pem \
  -H "Content-Type: application/json" \
  -d '{
    "part_count": 3,
    "identifier": "Q3 Financial Report 2025"
  }'
```

Save `transmission_key_id` from the response.

## Step 3: Send Document Parts

```bash
curl -X POST https://<host>:8443/secured/transmit_document_part \
  --cert client_cert.pem \
  --key client_key.pem \
  --cacert ca_root_cert.pem \
  -H "Content-Type: application/json" \
  -d '{
    "key": "<transmission_key_id>",
    "snippet": "Document part text...",
    "part_number": 0,
    "page_number": 1,
    "sentence_number": 1
  }'
```

Send parts in a stable sequence (`0..part_count-1`).

## Document format (recommended)

Knovas Semantic Search ingests **plain text snippets** (`snippet` on each `transmit_document_part` call). The service does not interpret HTML, Word layout, or PDF coordinates for you. **Client software that prepares documents for upload should parse source material into Markdown-style text** before chunking into parts:

- **Use Markdown (or Markdown-like) structure** so headings, lists, emphasis, and code blocks stay readable as linear text: `#` / `##` for sections, `-` or `1.` for lists, fenced code for verbatim content where relevant.
- **Prefer semantic structure over visual markup**: export or convert Office/PDF sources to structured text (headings and paragraphs) rather than pasting dense single-line blobs or raw HTML.
- **Strip or normalize boilerplate** (headers, footers, repeated page numbers) so each snippet carries substantive content; this improves embedding quality and query recall.
- **Keep chunk boundaries sensible**: split on section or paragraph boundaries when possible so a single part does not splice mid-sentence more often than necessary.

This approach keeps tokens meaningful for the embedding model, aligns with common tooling (Markdown parsers, lightweight converters), and makes `identifier` + optional `page_number` / `sentence_number` metadata easier to reason about.

## Providing document structure for better retrieval

Knovas Semantic Search extracts structural signals from each document to improve search precision. When a query like "Rekursantwort" is issued, chunks from a document *titled* "Rekursantwort" or under a `# Rekursantwort` heading should rank above Rechnung chunks that merely mention the term in passing. Three signals drive this:

### Title (strongest signal)

Pass `title` in `init_document_transmission`. The title is prepended to every chunk embedding and stored as heading context. Example:

```json
{
  "part_count": 2,
  "identifier": "rekursantwort-2024-03",
  "title": "Rekursantwort 2024-03",
  "path": "/Rekursantworten/2024/brief.md"
}
```

### Path (BM25 signal)

Pass `path` as the file-system path of the document (e.g. `/Rekursantworten/2024/brief.md`). Path segments are tokenized (`rekursantworten`, `2024`, `brief`) and BM25-indexed per chunk with a 3× boost over body text. Useful when folder structure encodes document type.

- Max 2000 characters.
- Forward slashes recommended (backslashes are normalized).
- File extension is stripped from the last segment.
- Not returned in query results.

### Markdown headings (structural signal)

Format document content with `#`/`##`/`###` headings. Section headings are extracted per chunk and stored as a BM25-indexed field with the same 3× boost. The active heading hierarchy at each chunk's position is used:

```markdown
# Rekursantwort

## Sachverhalt

Die Beschwerdeführerin reichte am 15. März 2024 eine Beschwerde ein...

## Erwägungen

### Formelles

Die Beschwerde wurde fristgerecht eingereicht...
```

A chunk under `## Sachverhalt` will have heading context `"rekursantwort sachverhalt"`, while a chunk under `### Formelles` will have `"rekursantwort erwägungen formelles"`. Queries matching any of these terms score 3× higher on `heading_context` than on body text.

### Full example

```bash
curl -X POST https://<host>:8443/secured/init_document_transmission \
  --cert client_cert.pem --key client_key.pem --cacert ca_root_cert.pem \
  -H "Content-Type: application/json" \
  -d '{
    "part_count": 1,
    "identifier": "rekursantwort-2024-acme",
    "title": "Rekursantwort 2024-03 — Acme AG",
    "path": "/Rekursantworten/2024/acme-ag.md"
  }'
```

Then transmit the document content as Markdown:

```bash
curl -X POST https://<host>:8443/secured/transmit_document_part \
  --cert client_cert.pem --key client_key.pem --cacert ca_root_cert.pem \
  -H "Content-Type: application/json" \
  -d '{
    "key": "<transmission_key_id>",
    "part_number": 0,
    "snippet": "# Rekursantwort\n\n## Sachverhalt\n\nDie Beschwerdeführerin reichte am 15. März 2024..."
  }'
```

### Migration note

Existing ingested documents will not benefit from heading context until re-ingested. Re-sending a document (same `identifier`) replaces the existing version. No API changes are needed beyond adding `path` and structuring content with headings.

## Step 4: Query

```bash
curl -X POST https://<host>:8443/secured/query \
  --cert client_cert.pem \
  --key client_key.pem \
  --cacert ca_root_cert.pem \
  -H "Content-Type: application/json" \
  -d '{
    "Input": "What were the Q3 revenue figures?"
  }'
```

If your tenant uses encrypted embeddings, include `encryption_matrix` in the request.

## Step 5: Explicit Feedback (Optional)

After your search integration is working, you can send explicit ratings back to Knovas Semantic Search. Entirely optional — your upload and query flows are unaffected.

**Rate a document's relevance for a specific query (append-only):**

```bash
curl -X POST https://<host>:8443/secured/analytics/relevance-feedback \
  --cert client_cert.pem --key client_key.pem --cacert ca_root_cert.pem \
  -H "Content-Type: application/json" \
  -d '{
    "pointer": "Q3 Financial Report 2025",
    "relevance_score": 4,
    "query_session_id": "<query_session_id from query response>"
  }'
```

- `relevance_score` 1–5 (1 = not relevant, 5 = very relevant)
- `query_session_id` optional but recommended for session correlation
- Returns `202 Accepted`

**Set a permanent importance/quality rating for a document (upsert):**

```bash
curl -X POST https://<host>:8443/secured/document/rating \
  --cert client_cert.pem --key client_key.pem --cacert ca_root_cert.pem \
  -H "Content-Type: application/json" \
  -d '{
    "pointer": "Q3 Financial Report 2025",
    "importance_score": 5,
    "quality_score": 3
  }'
```

- At least one of `importance_score` or `quality_score` is required
- Calling again for the same `pointer` updates the existing row
- Returns `200 OK` with the current rating

**Retrieve ratings and feedback stats for a document:**

```bash
curl "https://<host>:8443/secured/document/rating?pointer=Q3%20Financial%20Report%202025" \
  --cert client_cert.pem --key client_key.pem --cacert ca_root_cert.pem
```

Returns both the permanent rating (`null` if not set) and an aggregate breakdown of all relevance feedback scores submitted for that document.

See [Analytics Integration Guide](../03_API/Analytics_Integration_Guide.md) for full field reference and privacy guarantees.

## Response formats (secured API)

All JSON bodies from `/secured/*` share a small envelope. Extra fields from a successful call are merged at the **top level** next to `status` and `message` (there is no nested `data` object).

### Success envelope

```json
{
  "status": "success",
  "message": "<human-readable summary>",
  …
}
```

HTTP status is usually `200`. `init_document_transmission` returns **`201 Created`** on success.

### Error envelope

```json
{
  "status": "error",
  "error": "<description>",
  "error_code": "<optional machine code>",
  "type": "validation_error",
  "field": "<optional field name>"
}
```

`type` and `field` appear for validation problems. Auth failures typically use `error_code`: `AUTH_FAILED`. Not found: `NOT_FOUND`.

### `init_document_transmission` — success (`201`)

```json
{
  "status": "success",
  "message": "Transmission initialized",
  "transmission_key_id": "<uuid>"
}
```

### `transmit_document_part` — success (`200`)

`transmission_complete` is `true` when the last expected part (`part_count`) has been received; until then `false`. Embedding and vector indexing continue asynchronously after the HTTP response.

```json
{
  "status": "success",
  "message": "Success",
  "transmission_complete": false
}
```

### `query` — success (`200`)

`pointers` is the list of document identifiers (same order as best-first similarity in `results`). Each `results[]` entry is the best-matching chunk per document (cosine distance from Weaviate; similarity = `1 - distance`).

```json
{
  "status": "success",
  "message": "Query executed successfully",
  "pointers": ["Q3 Financial Report 2025", "Annual Report 2024"],
  "result_count": 2,
  "results": [
    {
      "pointer": "Q3 Financial Report 2025",
      "cosine_similarity": 0.8234,
      "cosine_distance": 0.1766,
      "page_number": 3,
      "sentence_number": 12
    },
    {
      "pointer": "Annual Report 2024",
      "cosine_similarity": 0.712,
      "cosine_distance": 0.288,
      "page_number": null,
      "sentence_number": null
    }
  ]
}
```

`page_number` and `sentence_number` are omitted from the client request unless you send them; when absent on stored chunks they are JSON `null`. An empty match set still succeeds:

```json
{
  "status": "success",
  "message": "Query executed successfully",
  "pointers": [],
  "result_count": 0,
  "results": []
}
```

## Operational Limits (Quick View)

| Limit | Value |
|---|---|
| Secured API rate | 5 requests/second (burst 50) |
| Max payload default | 1 MB |
| `transmit_document_part` payload | 20 MB |
| Proxy read timeout | 300 seconds |

## Error Handling

| Status | Meaning | Client Action |
|---|---|---|
| `400` | Invalid request body | Fix payload and resend |
| `401` | Certificate/auth failure | Verify cert/key/CA and expiry |
| `413` | Payload too large | Reduce chunk size |
| `429` | Rate/connection limit | Exponential backoff |
| `503` | Temporary backend issue | Retry with backoff |
| `504` | Timeout | Reduce batch size and retry |

## Best Practices

- Parse upstream documents to **Markdown-style plain text** before splitting into snippets (see [Document format (recommended)](#document-format-recommended)).
- Keep snippets around 500 characters for retrieval quality.
- Use descriptive `identifier` values, because they appear in query results.
- Never commit certificates or keys to version control.
- Monitor certificate validity and rotate before expiry.

## Deep-Dive Links (this kit)

- [Secure API (`/secured/*`)](../03_API/Secure_API.md)
- [Analytics Integration Guide (engagement reporting)](../03_API/Analytics_Integration_Guide.md)


