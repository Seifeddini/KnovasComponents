#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

KNOVAS_ENV="$ROOT_DIR/knovas.env"
if [[ ! -f "$KNOVAS_ENV" ]]; then
  echo "Run ./scripts/setup.sh first." >&2
  exit 1
fi
if [[ ! -f "$ROOT_DIR/RemoteController/.env.generated" ]]; then
  echo "Run ./scripts/setup.sh first (missing .env.generated)." >&2
  exit 1
fi

docker compose --env-file "$KNOVAS_ENV" up -d --build

echo "==> Health checks"
sleep 3
curl -fsS http://127.0.0.1:5001/health || echo "RC health: not ready yet"
curl -fsS http://127.0.0.1:8081/health || echo "Platform health: not ready yet"
docker compose --env-file "$KNOVAS_ENV" ps
