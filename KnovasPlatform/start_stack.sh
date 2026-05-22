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

# Full rebuild so the running UI matches this repo (avoids stale "Semantix" labels from cached layers).
echo "Building docbridge-web (no cache)..."
docker compose build --no-cache docbridge-web

echo "Starting docbridge-web and docbridge-web-nginx..."
docker compose up -d --force-recreate docbridge-web docbridge-web-nginx
docker compose ps
