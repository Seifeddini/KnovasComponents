# Knovas — API integration kit

For developers integrating with Knovas **directly over HTTPS**, without hosting
RemoteController or KnovasPlatform. You call the documented endpoints from your
own application; everything behind them — databases, embeddings, orchestration —
is Knovas-operated.

`/secured/*` additionally requires **mutual TLS** with your tenant client
certificate, on port **8443**.

## Read order

| Step | Document | Purpose |
|------|----------|---------|
| 1 | [Client_Integration_Guide.md](Client_Integration_Guide.md) | Onboarding, document preparation, chunking, ports, limits, error handling |
| 2 | [Secure_API.md](Secure_API.md) | Contract for `/secured/*`: upload, query, delete |
| 3 | [Analytics_Integration_Guide.md](Analytics_Integration_Guide.md) | Optional engagement reporting (`query_session_id`, `/secured/analytics/engagement`) |

## Certificates

All three documents assume you already hold the tenant mTLS bundle. Raw `curl`
lets you name those files anything — but if you also run RemoteController or
KnovasPlatform, each expects its own filenames in its own directory. See
[../certificates.md](../certificates.md).

## What you do not need

Internal APIs (employee JWT), source layout, Docker Compose, Weaviate, or
embedding models are not part of your tenant integration surface.

## Sensitive information

Do not commit private keys, passwords, or full PEM chains to source control.
Rotate client certificates before expiry per your security policy.
