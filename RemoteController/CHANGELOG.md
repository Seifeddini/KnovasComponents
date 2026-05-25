# Changelog

## Unreleased

- Optional `max_document_age_seconds` in scheduler config and sync-request filters; files older than the limit are not uploaded and appear as `excluded_max_age` in `document_sync` tracking.

## 0.1.1 — 2026-05-19

- Production boot validates required environment variables (no `RC_SKIP_CONFIG_VALIDATION` in prod).
- Docker/Gunicorn default to a single worker for continuous sync safety.
- Added `GETTING_STARTED.md`, `docker-compose.yml`, and `nginx-edge.example.conf`.
- Health returns HTTP 503 when config or watch roots are degraded.
- Added MIT `LICENSE`; CI docker build job; expanded contract tests.

## 0.1.0 — 2026-05-18

- Initial standalone Remote Controller release.
- Flask API: `/health`, `/metrics`, `/discover`, `/sync`, `/sync/start`, `/sync/stop`, `/sync/status`, `/sync/config`.
- Knovas operator verification via `POST /remote_controller/verify_operator`.
- Filesystem discovery and Knovas Secure API upload with incremental sync state.
- Sync scheduler with time windows, ingest rate limits, and continuous mode.
