# Overview

Browsers cannot open `\\server\share\...` directly. This platform:

1. **Search** — web UI + Knovas API over HTTPS.
2. **Open** — signed token; Windows **Knovas Open Companion** redeems it and opens a UNC via the user’s SMB session (no full download to `%TEMP%`).
3. **PDF preview** (default) — inline via `/api/document/.../preview`; optional UNC open for PDFs instead.

Deploy: [first-deploy-checklist.md](../first-deploy-checklist.md). API: [http-api-open-tokens.md](http-api-open-tokens.md).
