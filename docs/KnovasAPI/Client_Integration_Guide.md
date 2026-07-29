---
doc_type: guide
product: knovas
classification: developer_kit
canonical: false
owner: platform
updated: 2026-07-23
tags:
  - knovas
  - developer
  - guide
  - api
audience:
  - developer
---

# Knovas — Client Integration Guide

Who this is for: engineers building an integration **against Knovas-hosted APIs** only. You do not need access to Knovas servers, Kubernetes, or internal repositories.

**What Knovas provides:** A multi-tenant knowledge API. You upload document text, Knovas indexes it for semantic search, and your application queries results over **mutual TLS (mTLS)**. Each customer organisation receives an isolated tenant; your client certificate selects that tenant automatically.

When to use: first-time onboarding, day-to-day upload/query flows, operational limits, and error handling.

## 5-minute path

1. Complete **onboarding** over **HTTPS** (`POST /create_entity`) using the registration key from your Knovas onboarding email.
2. Store the returned certificate material immediately; use **HTTPS + mTLS** for all `/secured/*` calls.
3. **Recommended:** rotate to a client-held key via `POST /secured/sign_certificate` (CSR) before production traffic.
4. Upload: `init_document_transmission` → one or more `transmit_document_part` requests.
5. Search: `POST /secured/query`.
6. Retry with backoff on `429`, `503`, and `504`.
7. Optionally send feedback (see [Analytics Integration Guide](Analytics_Integration_Guide.md)).

## Architecture at a glance

```text
Your app
  │
  ├─ HTTPS :443 (server TLS only, publicly-trusted cert) ──► POST /create_entity  (registration key → bootstrap cert + plaintext key + CA)
  │
  ├─ HTTPS + mTLS :8443 (bootstrap cert) ──► POST /secured/sign_certificate  (optional CSR rotation)
  │
  └─ HTTPS + mTLS :8443 (bootstrap or rotated cert) ──► /secured/*  (upload, query, optional analytics)
```


| Surface | Port | TLS | Who calls | Purpose |
| ------- | ---- | --- | --------- | ------- |
| `POST /create_entity` | `443` (standard HTTPS) | Server TLS only (no client certificate) | Your backend once per onboarding | Exchange registration key for client certificate |
| `/secured/*` | `8443` | Server TLS + **client certificate required** | Your backend for all product APIs | Upload, query, ratings, optional analytics |

Your Knovas contact gives you the API base URL (for example `https://api.knovas.ch`). **The port differs by surface** — bootstrap onboarding (`/create_entity`) uses the standard HTTPS port `443` (omit the port), while every `/secured/*` call (including CSR rotation, upload, and query) requires the explicit `:8443` port. Calling `/secured/*` on `443` returns a `404` — it isn't routed there at all.

## Security and data handling

- **Tenant isolation:** Each client certificate maps to one tenant. You cannot read or write another tenant's documents.
- **Authentication:** Secured routes require a valid, non-revoked client certificate issued by Knovas.
- **Encryption:** Traffic uses TLS; data is encrypted at rest on Knovas infrastructure.
- **What search returns:** Query responses include document identifiers, scores, optional **ingested summary** text, and chunk locations — not necessarily the full original file you uploaded.
- **What Knovas stores:** Uploaded snippet text is stored to power search (vectors + keyword indexes). Treat the service as processing **confidential client content** under your contract with Knovas.

## Step 1: Onboarding (HTTPS)

Onboarding is a **two-step process**:

1. **Knovas** (or your partner admin) starts registration and you receive a **one-time registration key** by email. You do not call this step yourself unless you operate the admin API.
2. **Your application** completes registration with that key over **HTTPS**.

For this first call you do **not** present a client certificate. Verify the server TLS certificate using your OS trust store or the server trust anchor Knovas provides in your onboarding package. After a successful response, use the returned `ca_root_cert` as `--cacert` for all subsequent mTLS calls.

### Step 1a — Registration key (handled by Knovas)

Knovas sends you an email containing a registration key. The key is tied to your entity type (organisation or end-user client), name, and email. It is consumed on first successful use.

### Step 1b — Your call: `POST /create_entity`

Send **only** the key and the remaining profile fields. Do **not** send `entity_type` or `entity_name` in this request — they are already bound to the key.

**Organisation** (address fields required):

```bash
curl -X POST https://api.knovas.ch/create_entity \
  -H "Content-Type: application/json" \
  -d '{
    "key": "<registration_key_from_email>",
    "entity_data": {
      "postal_code": "1224",
      "city": "Zuerich",
      "country": "Switzerland",
      "address": "Beispielstrasse 70"
    }
  }'
```

**Client** under an existing organisation (`first_name` and `last_name` required in `entity_data`; add `postal_code`/`city`/`country`/`address` if this client is itself a business entity with its own registered address, distinct from its parent organisation's — all four are optional):

```bash
curl -X POST https://api.knovas.ch/create_entity \
  -H "Content-Type: application/json" \
  -d '{
    "key": "<registration_key_from_email>",
    "entity_data": {
      "first_name": "Jane",
      "last_name": "Doe"
    }
  }'
```

### Success response — save immediately

The response includes PEM-encoded credentials. Store them in a secrets manager **before** closing the session.


| Field                | Use                                                                            |
| -------------------- | ------------------------------------------------------------------------------ |
| `certificate_pem`    | Bootstrap client certificate for mTLS                                          |
| `private_key`        | PKCS#8 PEM, **plaintext** — store in a secrets manager immediately             |
| `ca_root_cert`       | Trust anchor — pass as `--cacert` on all subsequent mTLS calls                 |
| `organisation_id`    | Present for organisation onboarding                                            |
| `client_id`          | Present for client onboarding                                                  |
| `certificate_serial` | Audit / support reference                                                      |


Never commit these values to source control.

> **Security:** The bootstrap private key is transmitted **once** over HTTPS. Persist it in your secrets manager before closing the session. For day-2 operations, prefer generating keys locally and using [CSR rotation](#step-1c--issue-your-own-certificate-csr) so renewed keys never leave your infrastructure.

### Saving credentials from the shell (bash + jq)

Capture the full response once and extract each field to a file:

```bash
RESPONSE=$(curl -s -X POST https://api.knovas.ch/create_entity \
  -H "Content-Type: application/json" \
  -d '{
    "key": "<registration_key_from_email>",
    "entity_data": {"first_name": "Jane", "last_name": "Doe"}
  }')

echo "$RESPONSE" | jq -r '.certificate_pem' > client_cert.pem
echo "$RESPONSE" | jq -r '.ca_root_cert'    > ca_root_cert.pem
echo "$RESPONSE" | jq -r '.private_key'     > client_key.pem

chmod 600 client_cert.pem client_key.pem ca_root_cert.pem
```

The resulting three files (`client_cert.pem`, `client_key.pem`, `ca_root_cert.pem`) are enough for bootstrap mTLS calls:

```bash
curl --cert client_cert.pem --key client_key.pem --cacert ca_root_cert.pem \
  https://api.knovas.ch:8443/secured/health
```

`jq` is available in most Linux/macOS environments (`apt install jq` / `brew install jq`). On Windows use WSL or replace with `python3 -c "import sys,json; print(json.load(sys.stdin)['<field>'])"`.

> **If you also run RemoteController or KnovasPlatform**, those components expect
> these same three files under **different filenames in their own directories**.
> With raw `curl` the names above are arbitrary; with the components they are not.
> Mapping:
>
> | Response field | This guide | RemoteController | KnovasPlatform |
> |---|---|---|---|
> | `certificate_pem` | `client_cert.pem` | `client-cert.pem` | `client.crt` |
> | `private_key` | `client_key.pem` | `client-key.pem` | `client.key` |
> | `ca_root_cert` | `ca_root_cert.pem` | `ca-root.pem` | `ca.crt` |
>
> Directories and permissions differ too — see `docs/certificates.md` in the
> KnovasComponents repo.

### Step 1c — Issue your own certificate (CSR)

After bootstrap, generate an RSA keypair and CSR **locally**. Submit the CSR with your bootstrap (or any active) client certificate. Knovas signs it with the Knovas client CA and registers it for mTLS — **no private key is returned**.

Python example (key generation + CSR):

```python
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

rotation_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
csr = (
    x509.CertificateSigningRequestBuilder()
    .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "My Integration")]))
    .sign(rotation_key, hashes.SHA256())
)
csr_pem = csr.public_bytes(serialization.Encoding.PEM).decode()
# POST csr_pem to /secured/sign_certificate; store rotation_key + returned certificate_pem
```

```bash
curl -X POST https://api.knovas.ch:8443/secured/sign_certificate \
  --cert client_cert.pem --key client_key.pem --cacert ca_root_cert.pem \
  -H "Content-Type: application/json" \
  -d '{"csr": "<PEM CSR>", "validity_days": 365}'
```

Switch to the returned `certificate` plus your **local** private key for all `/secured/*` calls. The bootstrap certificate remains valid until revoked; rotate promptly and revoke the bootstrap cert when your security policy requires it.

## Step 2: Initialize transmission (HTTPS + mTLS)

Every upload needs a unique `identifier` (your document id) and `part_count` (how many `transmit_document_part` calls you will send).

```bash
curl -X POST https://api.knovas.ch:8443/secured/init_document_transmission \
  --cert client_cert.pem \
  --key client_key.pem \
  --cacert ca_root_cert.pem \
  -H "Content-Type: application/json" \
  -d '{
    "part_count": 3,
    "identifier": "Q3 Financial Report 2025",
    "title": "Q3 Financial Report 2025"
  }'
```

**Success is HTTP `201`.** Save `transmission_key_id` from the JSON body.

Field reference: [Secure API — init](Secure_API.md#post-securedinit_document_transmission).

## Step 3: Send document parts

```bash
curl -X POST https://api.knovas.ch:8443/secured/transmit_document_part \
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

Send `part_number` from `0` through `part_count - 1` in a stable order. The last part returns `"transmission_complete": true`. Indexing may continue asynchronously after HTTP `200`.

## Document format (recommended)

Knovas ingests **plain text** in the `snippet` field. It does not parse PDF layout, Word styles, or HTML for you.

- Convert sources to **Markdown-style text** (`#` headings, lists, paragraphs).
- Prefer structure over visual markup; strip repeated headers/footers.
- Split on section or paragraph boundaries when possible.
- Aim for roughly **500 characters per snippet** for good retrieval quality (well below the 500 000 character API maximum).

## Better retrieval: title, path, and headings

Knovas uses three optional signals so titles and section names rank above incidental mentions in body text.

### Title (strongest)

Pass `title` in `init_document_transmission` (max 500 characters).

### Path (keyword boost)

Pass `path` such as `/Reports/2025/q3-summary.md` (max 2000 characters). Folder and file tokens receive a keyword boost. Paths are not echoed in query results.

### Markdown headings

Use `#` / `##` / `###` in snippets. Active headings at each chunk position are indexed for keyword search.

Example init + one part:

```bash
curl -X POST https://api.knovas.ch:8443/secured/init_document_transmission \
  --cert client_cert.pem --key client_key.pem --cacert ca_root_cert.pem \
  -H "Content-Type: application/json" \
  -d '{
    "part_count": 1,
    "identifier": "rekursantwort-2024-acme",
    "title": "Rekursantwort 2024-03 — Acme AG",
    "path": "/Rekursantworten/2024/acme-ag.md"
  }'
```

```bash
curl -X POST https://api.knovas.ch:8443/secured/transmit_document_part \
  --cert client_cert.pem --key client_key.pem --cacert ca_root_cert.pem \
  -H "Content-Type: application/json" \
  -d '{
    "key": "<transmission_key_id>",
    "part_number": 0,
    "snippet": "# Rekursantwort\n\n## Sachverhalt\n\nDie Beschwerdeführerin reichte am 15. März 2024..."
  }'
```

Re-uploading with the same `identifier` replaces the previous document version.

### Page and sentence numbers

Add `page_number` and/or `sentence_number` (both optional integers `>= 1`) to any `transmit_document_part` call to record where the text came from. Knovas returns these on query hits so you can deep-link back to the exact page/sentence in your own viewer.

### Tables (structured data)

If a part contains a real table, send it as structured data in an optional `tables` array **alongside** the `snippet` prose. Knovas indexes each table as its own searchable unit, so rows and columns stay aligned. Send the table as headers + rows (each row must have exactly one cell per header):

```bash
curl -X POST https://api.knovas.ch:8443/secured/transmit_document_part \
  --cert client_cert.pem --key client_key.pem --cacert ca_root_cert.pem \
  -H "Content-Type: application/json" \
  -d '{
    "key": "<transmission_key_id>",
    "part_number": 1,
    "snippet": "## Revenue by region\n\nSee the table below.",
    "page_number": 7,
    "tables": [
      {
        "client_table_hint": "revenue-by-region",
        "title": "Revenue by region (Q3 2025)",
        "headers": ["Region", "Revenue", "YoY %"],
        "rows": [["EMEA", "12.4M", "+8%"], ["APAC", "9.1M", "+14%"]]
      }
    ]
  }'
```

Limits: up to 50 tables per part, 64 columns, 5000 rows, 1024 chars/cell. `title`, `page`, and `bbox` (`[x0, y0, x1, y1]`) are optional. If you use the [`knovas-extract`](Secure_API.md#structured-tables-optional) libraries, they emit `content.tables[]` in exactly this shape. Full field reference and error codes: [Secure API → Structured tables](Secure_API.md#structured-tables-optional).

> **Chapters & sections:** there is no dedicated field — use Markdown `#`/`##`/`###` headings inside the `snippet`. Knovas reads the heading structure directly from the text.

## Step 4: Query

```bash
curl -X POST https://api.knovas.ch:8443/secured/query \
  --cert client_cert.pem \
  --key client_key.pem \
  --cacert ca_root_cert.pem \
  -H "Content-Type: application/json" \
  -d '{
    "Input": "What were the Q3 revenue figures?"
  }'
```

Save `query_session_id` if you will report [engagement](Analytics_Integration_Guide.md) or relevance feedback.

## Step 5: Explicit feedback (optional)

Does not affect upload or query if omitted.

**Per-query relevance** (`202`):

```bash
curl -X POST https://api.knovas.ch:8443/secured/analytics/relevance-feedback \
  --cert client_cert.pem --key client_key.pem --cacert ca_root_cert.pem \
  -H "Content-Type: application/json" \
  -d '{
    "pointer": "Q3 Financial Report 2025",
    "relevance_score": 4,
    "query_session_id": "<query_session_id from query response>"
  }'
```

**Permanent document rating** (`200`):

```bash
curl -X POST https://api.knovas.ch:8443/secured/document/rating \
  --cert client_cert.pem --key client_key.pem --cacert ca_root_cert.pem \
  -H "Content-Type: application/json" \
  -d '{
    "pointer": "Q3 Financial Report 2025",
    "importance_score": 5,
    "quality_score": 3
  }'
```

**Read ratings:**

```bash
curl "https://api.knovas.ch:8443/secured/document/rating?pointer=Q3%20Financial%20Report%202025" \
  --cert client_cert.pem --key client_key.pem --cacert ca_root_cert.pem
```

## Response formats (secured API)

All `/secured/*` JSON responses share:

```json
{
  "status": "success",
  "message": "<human-readable summary>",
  …
}
```

Errors:

```json
{
  "status": "error",
  "error": "<description>",
  "error_code": "<optional>",
  "type": "validation_error",
  "field": "<optional>"
}
```

### `init_document_transmission` — `201 Created`

```json
{
  "status": "success",
  "message": "Transmission initialized",
  "transmission_key_id": "<uuid>"
}
```

### `transmit_document_part` — `200 OK`

```json
{
  "status": "success",
  "message": "Success",
  "transmission_complete": false
}
```

### `query` — `200 OK`

```json
{
  "status": "success",
  "message": "Query executed successfully",
  "query_session_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "pointers": ["Q3 Financial Report 2025", "Annual Report 2024"],
  "result_count": 2,
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
    },
    {
      "pointer": "Annual Report 2024",
      "document_uuid": "660e8400-e29b-41d4-a716-446655440001",
      "ingested_summary": {"present": false, "text": ""},
      "final_score": 0.72,
      "cosine_similarity": 0.712,
      "cosine_distance": 0.288,
      "page_number": null,
      "sentence_number": null,
      "top_chunks": []
    }
  ],
  "meta": {
    "embed_latency_ms": 95.2,
    "stage1_latency_ms": 10.0,
    "stage2_latency_ms": 40.0
  }
}
```

Use `pointer` as the stable id you chose at upload time. `document_uuid` is an internal id returned per hit (for support or analytics correlation).

Empty search still returns `200` with `"results": []` and `"result_count": 0`.

## Operational limits

Typical production gateway limits (your deployment may differ slightly). Rate limits are enforced per **client certificate** (tenant) unless noted.


| Endpoint / category | Sustained rate | Burst |
| ------------------- | -------------- | ----- |
| `POST /secured/query` | 12 requests/minute (~1 per 5 s) | 2 |
| `POST /secured/transmit_document_part` | 3 requests/second | 12 |
| `POST /secured/init_document_transmission` | 6 requests/minute | 2 |
| `POST /secured/sign_certificate` | 6 requests/minute | 2 |
| Other `/secured/*` (analytics, delete, ratings, health) | 1 request/second | 4 |
| `POST /create_entity` (onboarding) | 3 requests/minute per source IP | 3 |


`POST /secured/query` also has an **application-level** token bucket (about 1 query per 5 seconds sustained, burst 2). You may receive HTTP `429` from either the gateway or the application — always back off and retry.


| Limit | Value |
| ----- | ----- |
| Default JSON body size on `/secured/*` | 1 MB |
| `transmit_document_part` body size | 20 MB |
| Gateway read timeout (secured API) | 600 seconds |
| Application worker timeout | 300 seconds — very large uploads or slow queries may need smaller parts |


## Error handling


| Status | Meaning                                        | Client action                                |
| ------ | ---------------------------------------------- | -------------------------------------------- |
| `400`  | Invalid JSON or field validation               | Fix payload; check `field` when present      |
| `401`  | Certificate problem                            | Verify cert, key, CA, expiry, and revocation |
| `404`  | Unknown transmission key or document           | Check ids                                    |
| `413`  | Body too large                                 | Shrink snippet or split into more parts      |
| `429`  | Rate limit                                     | Exponential backoff                          |
| `503`  | Temporary backend (Redis, embedder, ingestion) | Retry same request                           |
| `504`  | Gateway timeout                                | Smaller chunks; retry with backoff           |


## Best practices

- Use a **unique `identifier` per logical document**; it becomes `pointer` in search results.
- Store bootstrap keys in a secrets manager immediately; rotate to a CSR-signed cert for day-2 operations.
- Renew before certificate expiry via `POST /secured/sign_certificate` (new CSR) or contact Knovas for a fresh registration flow.
- Parse documents to Markdown-style text before upload.
- Treat engagement and feedback APIs as **fire-and-forget** (see Analytics guide).
- For full endpoint rules, use the [Secure API reference](Secure_API.md).

## More in this kit

- [Secure API (`/secured/`*)](Secure_API.md) — complete field lists and delete endpoint
- [Analytics Integration Guide](Analytics_Integration_Guide.md) — engagement events and ratings in depth

