#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# shellcheck source=lib/stack_identity.sh
source "$ROOT_DIR/scripts/lib/stack_identity.sh"
# shellcheck source=lib/old_connector_name.sh
source "$ROOT_DIR/scripts/lib/old_connector_name.sh"

ENV_FILE="$ROOT_DIR/knovas.env"
knovas_load_compose_project "$ENV_FILE" "$ROOT_DIR"
knovas_retire_old_connector_container "$COMPOSE_PROJECT_NAME"
if [[ -f "$ENV_FILE" ]]; then
  docker compose --env-file "$ENV_FILE" down
else
  docker compose down
fi
