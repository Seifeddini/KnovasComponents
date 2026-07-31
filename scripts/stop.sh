#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ENV_FILE="$ROOT_DIR/knovas.env"
if [[ -f "$ENV_FILE" ]]; then
  docker compose --env-file "$ENV_FILE" down
else
  docker compose down
fi
