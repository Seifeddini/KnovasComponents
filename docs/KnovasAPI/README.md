# Knovas — API integration kit

For developers integrating with Knovas **directly over HTTPS**, without hosting
Knovas Connector or KnovasPlatform. You call the documented endpoints from your
own application; everything behind them — databases, embeddings, orchestration —
is Knovas-operated.

`/secured/*` additionally requires **mutual TLS** with your tenant client
certificate, on port **8443**.

## Canonical source

The API reference is maintained in one place, the **Knovas Developer Kit**
(Knovas source `docs/Knovas_Developer_Kit/api/`): `Secure_API.md`,
`Knowledge_Graph_API.md`, `Client_Integration_Guide.md`,
`Analytics_Integration_Guide.md`, `Request_Signing.md`, `Usage_Receipts.md`.
Knovas hands the current kit to integrating customers. Copies here are not
kept in sync by hand: `Secure_API.md` has been retired to a pointer, and the
two guides below are **older copies** (mid-2026) — where they differ from the
kit (rate limits in particular), the kit is right.

**Document fields** (typed values per document: init `fields`, query `where` /
`return_fields`, `/secured/graph/doc-values`, `doc-fields`, folder defaults)
are documented only in the kit (`Secure_API.md`, and `Knowledge_Graph_API.md`
→ *Document values and fields*). They take effect only for a tenant where
Knovas has enabled them.

## Read order

| Step | Document | Purpose |
|------|----------|---------|
| 1 | [Client_Integration_Guide.md](Client_Integration_Guide.md) | Onboarding, document preparation, chunking, ports, limits, error handling (older copy) |
| 2 | [Secure_API.md](Secure_API.md) | Contract for `/secured/*`: upload, query, delete — moved to the Developer Kit |
| 3 | [Analytics_Integration_Guide.md](Analytics_Integration_Guide.md) | Optional engagement reporting (`query_session_id`, `/secured/analytics/engagement`) (older copy) |

## Certificates

All three documents assume you already hold the tenant mTLS bundle. Raw `curl`
lets you name those files anything — but if you also run Knovas Connector or
KnovasPlatform, each expects its own filenames in its own directory. See
[../certificates.md](../certificates.md).

## What you do not need

Internal APIs (employee JWT), source layout, Docker Compose, Weaviate, or
embedding models are not part of your tenant integration surface.

## Sensitive information

Do not commit private keys, passwords, or full PEM chains to source control.
Rotate client certificates before expiry per your security policy.
