# Open API

## Browser open (default) — `GET /api/document/<doc_id>/client-path`

Session cookie required. Used when **Öffnen** is clicked; no companion.

Query: `path` — relative to AutoDoc root.

```json
{
  "success": true,
  "unc": "\\\\fileserver\\Share\\AutoDoc\\Akte4711\\Brief.docx",
  "path": "/mnt/autodoc/Akte4711/Brief.docx"
}
```

The SPA opens `unc` and/or `path` on the **user's PC** via the browser (see [opening-documents.md](opening-documents.md)).

## Companion open (optional) — open tokens

Only if `OPEN_COMPANION_ENABLED=true`. Spec: `GET /api/open-tokens/spec`

### Mint — `POST /api/open-tokens/mint`

Session + `X-CSRF-Token`. Returns `companion_href` (`semantix-doc:open?...`).

### Redeem — `POST /api/open-tokens/redeem`

`Authorization: Bearer <token>`. Returns `unc` and/or `path` for the companion process.

## Other

- `GET /api/document/<id>/preview?path=` — inline PDF (login required)
- `GET /api/document/<id>/download?path=` — only if `OPEN_ALLOW_DEGRADED_DOWNLOAD_OPEN=true`
