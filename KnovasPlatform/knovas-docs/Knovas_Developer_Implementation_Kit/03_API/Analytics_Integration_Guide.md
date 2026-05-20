---
doc_type: guide
product: Knovas Semantic Search
classification: developer_kit
canonical: false
owner: backend
updated: 2026-04-26
tags: [knovas-semantic-search, api, analytics, engagement, feedback, ratings, integration, client]
audience: [client, developer]
---

# Analytics Integration Guide

Who this is for: software engineers integrating Knovas Semantic Search **through the secured HTTP APIs** (`/secured/query`, `/secured/analytics/engagement`, `/secured/analytics/relevance-feedback`, `/secured/document/rating`).

When to use: after your search integration is working, add engagement reporting and/or explicit feedback to help Knovas Semantic Search measure and improve search quality.

Knovas Semantic Search supports two kinds of client-reported feedback:

- **Implicit engagement** — behavioral signals (view, click, download, dismiss) tied to a search session. See [POST /secured/analytics/engagement](#post-securedanalyticsengagement).
- **Explicit feedback** — direct ratings: per-query relevance (1–5) and permanent per-document importance/quality scores. See [Explicit Feedback](#explicit-feedback).

## 5-Minute Quick Start

### Step 1: Capture the session ID

Every `/secured/query` success payload includes **`query_session_id`** at the **top level** (flat envelope; no nested `data` wrapper on query). See *Step 1* below for the field list.

Store this `query_session_id` alongside the search results you display to your users. **`results[]`** entries also expose **`document_uuid`** and **`ingested_summary`** — see [Secure_API.md](Secure_API.md).

### Step 2: Report engagement events

When a user interacts with a search result (views, clicks, downloads, or dismisses a document), send a `POST /secured/analytics/engagement` request:

```json
{
  "query_session_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "events": [
    {
      "action": "view",
      "pointer": "doc-123",
      "position": 1
    }
  ]
}
```

Response: `202 Accepted` with `{"data": {"accepted": 1}}`.

That's it. No other changes to your integration are needed.

## Endpoint Reference

### POST /secured/analytics/engagement

**Authentication**: mTLS (same as all `/secured/*` endpoints).

**Request body**:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `query_session_id` | UUID string | Yes | From the `/secured/query` response |
| `events` | Array | Yes | Up to 50 engagement events |
| `events[].action` | String | Yes | One of: `view`, `click`, `download`, `dismiss` |
| `events[].pointer` | String | Yes | The document pointer from query results |
| `events[].position` | Integer | No | 1-based rank position as displayed to user |

**Response**: `202 Accepted`

```json
{
  "status": "success",
  "message": "Engagement events accepted",
  "data": {
    "accepted": 2
  }
}
```

**Error responses**:

| Status | Condition |
|--------|-----------|
| 400/422 | Missing `query_session_id`, empty events array, or events exceeds 50 |
| 401 | Invalid or missing mTLS certificate |

### Action Definitions

| Action | When to send |
|--------|-------------|
| `view` | User opens or previews a document from search results |
| `click` | User clicks on a search result (if distinct from view in your UI) |
| `download` | User downloads the full document |
| `dismiss` | User explicitly marks a result as irrelevant |

If your UI does not distinguish between `view` and `click`, use `view` for all document-open actions.

## What Knovas Semantic Search Tracks Automatically

These require **no action** from you:

- **Query metrics**: number of queries, result counts, similarity scores
- **Document recommendations**: which documents appear in search results, how often, at what rank
- **Chunk-level recommendations**: which parts of documents drive search matches
- **Document lifecycle**: transmissions, deletions, certificate operations

## Best Practices

### When to send events

- Send engagement events as close to the user action as possible.
- You can batch multiple events into a single request (up to 50).
- Events should reference the `query_session_id` from the search that produced the results.

### Error handling

- Treat the engagement endpoint as **fire-and-forget**. If it returns an error or is unreachable, your search functionality is unaffected.
- Do not retry failed engagement reports — they are best-effort.
- Do not block your UI on engagement reporting.

### Position tracking

- If your search UI re-ranks or filters results before displaying them to the user, send the **displayed position**, not the position returned by Knovas Semantic Search.
- Position is optional but highly valuable for search quality measurement.

## Privacy Guarantees

- Knovas Semantic Search never stores your query text in analytics.
- The `query_session_id` is an opaque UUID — it cannot be reverse-engineered to recover query content.
- No user identity information is required or accepted. The only identity dimension is the mTLS tenant.
- All analytics data is tenant-isolated: your data is never mixed with other tenants' data.

## FAQ

**Does engagement reporting affect search latency?**
No. The `/secured/query` endpoint generates the session ID with zero overhead (UUID generation). Engagement reporting is a separate, async endpoint.

**Is engagement reporting required?**
Strongly recommended but not required. Knovas Semantic Search works fully without it. However, engagement data enables search quality measurement and future relevance tuning.

**What if I send events for an expired session?**
Events are accepted on a best-effort basis. Very old session IDs (>24 hours) may not correlate correctly during aggregation, but they will not cause errors.

**What happens if I send invalid actions?**
Invalid actions are silently dropped. The `accepted` count in the response tells you how many events were valid.

**Can I send engagement events from multiple users for the same session?**
Yes. If your platform serves the same search results to multiple users, each user's engagement can reference the same `query_session_id`.

---

## Explicit Feedback

Explicit feedback lets your application (or end users) communicate direct opinions about document relevance and quality — separate from the implicit signals captured by engagement events.

### Two distinct concepts

| Concept | Endpoint | Storage | When to use |
|---------|----------|---------|-------------|
| Per-query relevance | `POST /secured/analytics/relevance-feedback` | Append-only, one row per rating | User rates a result immediately after a search |
| Permanent document rating | `POST /secured/document/rating` | Upsert, one row per (tenant, document) | Document-level importance or quality curation |

### POST `/secured/analytics/relevance-feedback`

Rate how relevant a document was for a specific query. Each call appends a new record — ratings accumulate as an audit trail.

```json
{
  "pointer": "doc-123",
  "relevance_score": 4,
  "query_session_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `pointer` | String | Yes | Document identifier (same value as returned by `/secured/query`) |
| `relevance_score` | Integer | Yes | `1` (not relevant) to `5` (very relevant) |
| `query_session_id` | UUID string | No | `query_session_id` from the search that surfaced this document |

Response: `202 Accepted`

```json
{
  "status": "success",
  "message": "Relevance feedback recorded",
  "recorded": true
}
```

**Guidance:** Treat this endpoint as fire-and-forget, identical to the engagement endpoint. If a user rates multiple results from the same search, send one request per document.

### POST `/secured/document/rating`

Set a permanent importance and/or quality score for a document. Repeated calls for the same `pointer` update the existing row (upsert). Not tied to any query session.

```json
{
  "pointer": "doc-123",
  "importance_score": 5,
  "quality_score": 3
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `pointer` | String | Yes | Document identifier |
| `importance_score` | Integer | No | `1`–`5` — how critical this document is to the tenant's workflow |
| `quality_score` | Integer | No | `1`–`5` — how reliable or well-written the source is |

At least one of `importance_score` or `quality_score` must be present.

Response: `200 OK` with the current state of the rating row:

```json
{
  "status": "success",
  "message": "Document rating updated",
  "pointer": "doc-123",
  "importance_score": 5,
  "quality_score": 3,
  "last_updated": "2026-04-17T10:23:45.123456+00:00"
}
```

### GET `/secured/document/rating?pointer=<id>`

Retrieve the current rating and a breakdown of all historical relevance-feedback scores for a document.

Response:

```json
{
  "status": "success",
  "message": "Document rating retrieved",
  "rating": {
    "pointer": "doc-123",
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

`rating` is `null` when no permanent rating has been set. `relevance_feedback.total_ratings` is `0` when no relevance feedback has been submitted.

### Privacy

- Explicit feedback stores only `pointer` (opaque client-supplied identifier), scores, and timestamps.
- No query text, no user identity, no document content is persisted.
- All feedback is tenant-isolated — your data is never accessible to other tenants.

