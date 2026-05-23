#!/usr/bin/env bash
set -euo pipefail

# Starts the web stack bound to 127.0.0.1 only — for host nginx (internal TLS / internal DNS).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.host-nginx.yml)

if [[ ! -f ".env" ]]; then
  cp .env.example .env
  echo "Created .env from .env.example. Update it, then re-run."
  exit 1
fi

if [[ ! -d "components/docbridge_integration" ]]; then
  echo "Missing components/docbridge_integration."
  exit 1
fi

# shellcheck disable=SC1091
set -a
source .env
set +a
PORT="${DOCBRIDGE_WEB_PORT:-8081}"

echo "Building docbridge-web (no cache)..."
"${COMPOSE[@]}" build --no-cache docbridge-web

echo "Starting docbridge-web and docbridge-web-nginx (127.0.0.1:${PORT})..."
"${COMPOSE[@]}" up -d --force-recreate docbridge-web docbridge-web-nginx
"${COMPOSE[@]}" ps

echo ""
echo "Host nginx mode: app listens on http://127.0.0.1:${PORT} only."
echo "Next steps:"
echo "  1. Set OPEN_PUBLIC_BASE_URL=https://<your-fqdn> in .env if users open documents from the browser."
echo "  2. Configure host nginx from deploy/host-nginx/knovas-platform.conf.example"
echo "     (proxy_pass http://127.0.0.1:${PORT};)"
echo "  3. Verify: curl -fsS http://127.0.0.1:${PORT}/health"
echo "             VERIFY_BASE_URL=https://<your-fqdn> ./scripts/verify_deploy.sh"
echo "Guide: docs/deployment/host-nginx-internal.md"
