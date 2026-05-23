#!/usr/bin/env bash
set -euo pipefail

# Stops all services from this bundle (default and host-nginx compose files).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

COMPOSE=(docker compose -f docker-compose.yml)
if [[ -f docker-compose.host-nginx.yml ]]; then
  COMPOSE+=(-f docker-compose.host-nginx.yml)
fi

"${COMPOSE[@]}" --profile mock down
