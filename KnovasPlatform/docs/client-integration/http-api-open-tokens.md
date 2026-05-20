# Open tokens API

Spec: `GET /api/open-tokens/spec`

## Mint — `POST /api/open-tokens/mint`

Session cookie + header `X-CSRF-Token` (from `window.__DOCBRIDGE__.csrfToken`).

```json
{"doc_id":"12345","path":"Akte4711/Brief.docx"}
```

`path` is relative to AutoDoc root. Response includes `token`, `companion_href`, `redeem_url`.

## Redeem — `POST /api/open-tokens/redeem`

`Authorization: Bearer <token>` only (no session).

```json
{"success": true, "unc": "\\\\fileserver\\Share\\AutoDoc\\..."}
```

## Other

- `GET /api/document/<id>/preview?path=` — inline PDF (login required)
- `GET /api/document/<id>/download?path=` — only if degraded download enabled in config
