# Moved — see the Knovas Developer Kit

The `/secured/*` reference is maintained in one place: the **Knovas Developer
Kit**, `api/Secure_API.md` (Knovas source: `docs/Knovas_Developer_Kit/api/`).
Knovas hands the current kit to integrating customers; ask your Knovas contact
for it. The public guides at <https://knovas.ch/en/docs/> cover the upload,
search and delete flow in short form.

This copy was a second, diverging version of that page and has been retired
rather than kept in sync by hand — the same way `KnovasPlatform/knovas-docs/`
was retired before it. By October 2026 it was a third of the canonical page's
length: it lacked the document-fields keys (init `fields`, query `where` and
`return_fields`), the honesty keys of a query answer, the `/secured/llm/*` and
document-listing routes, and the rate limits now served by
`GET /secured/limits` — its own rate-limit summary was out of date.

| You want | In the Developer Kit |
|----------|----------------------|
| Upload: `init_document_transmission`, `transmit_document_part`, init `fields` | `api/Secure_API.md` |
| Search: `POST /secured/query`, `where`, `return_fields`, `no_strong_matches`, `no_results_reason` | `api/Secure_API.md` |
| Document fields: `/secured/graph/doc-values`, `doc-values/find`, `doc-fields`, packs, settings, folder defaults | `api/Knowledge_Graph_API.md`, *Document values and fields* |
| Rate limits, error reference | `api/Secure_API.md`, `api/Client_Integration_Guide.md` |
| Engagement and relevance feedback | `api/Analytics_Integration_Guide.md` ([older copy here](Analytics_Integration_Guide.md)) |
| Onboarding, chunking, errors | `api/Client_Integration_Guide.md` ([older copy here](Client_Integration_Guide.md)) |
| mTLS certificate filenames and permissions | [../certificates.md](../certificates.md) |

**Document fields** take effect only for a tenant where Knovas has enabled
them. On any other tenant the new request keys are ignored and the answers
carry none of the new keys.
