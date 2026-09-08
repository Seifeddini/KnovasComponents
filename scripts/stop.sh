#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# shellcheck source=lib/stack_identity.sh
source "$ROOT_DIR/scripts/lib/stack_identity.sh"

ENV_FILE="$ROOT_DIR/knovas.env"
knovas_load_compose_project "$ENV_FILE" "$ROOT_DIR"
if [[ -f "$ENV_FILE" ]]; then
  docker compose --env-file "$ENV_FILE" down
else
  docker compose down
fi
