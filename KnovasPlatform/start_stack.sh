#!/usr/bin/env bash
set -euo pipefail

# Starts the web app stack (without sync workers).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ ! -f ".env" ]]; then
  cp .env.example .env
  echo "Created .env from .env.example. Update it, then re-run."
  exit 1
fi

if [[ ! -d "components/docbridge_integration" ]]; then
  echo "Missing components/docbridge_integration."
  exit 1
fi

docker compose up -d docbridge-web docbridge-web-nginx
docker compose ps
