---
doc_type: developer_kit
product: Knovas Semantic Search
classification: developer_kit
canonical: false
owner: platform
updated: 2026-04-15
tags:
  - knovas-semantic-search
  - developer
  - distribution
  - api
audience:
  - developer
---

# Knovas Semantic Search — API integration kit

This folder is a **small, shippable bundle** for **software developers who only integrate with Knovas Semantic Search through our HTTP APIs**.

You are expected to **call the documented endpoints** from your application using **mutual TLS (mTLS)** on the secured surface. Everything else — databases, embeddings service, orchestration — is behind our API.

## Read order

| Step | Document | Purpose |
|------|----------|---------|
| 1 | [**`Audience/Client Integration Guide.md`**](Audience/Client%20Integration%20Guide.md) | Onboarding flow, document preparation, chunking, ports, limits, error handling |
| 2 | [**`03_API/Secure_API.md`**](03_API/Secure_API.md) | **Canonical contract** for `/secured/*`: upload, query, encryption matrix, analytics |
| 3 | [**`03_API/Analytics_Integration_Guide.md`**](03_API/Analytics_Integration_Guide.md) | Optional engagement reporting on top of search (`query_session_id`, `/secured/analytics/engagement`) |

## What you implement

- **TLS client** with **client certificate** issued for your tenant (see onboarding in the Client Integration Guide).
- **HTTP calls** to `/secured/*` only, per **`Secure_API.md`**.
- **Retries with backoff** on `429`, `503`, and `504` as described in the Client Integration Guide.

## What you do not need

- Internal APIs (JWT / employee), source layout, Docker compose, databases, Weaviate, or embedding models — those are **not** part of your integration surface.

## Sensitive information

Do **not** commit **private keys**, **passwords**, or **full PEM chains** to your source control. Rotate client certificates before expiry per your security policy.
