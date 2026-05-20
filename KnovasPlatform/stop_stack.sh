#!/usr/bin/env bash
set -euo pipefail

# Stops all services from this bundle.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

docker compose --profile sync down
