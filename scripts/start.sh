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
BIND="$(grep -E '^[[:space:]]*DOCBRIDGE_WEB_BIND=' "$KNOVAS_ENV" | tail -1 | cut -d= -f2- | tr -d '[:space:]')"
PORT="$(grep -E '^[[:space:]]*DOCBRIDGE_WEB_PORT=' "$KNOVAS_ENV" | tail -1 | cut -d= -f2- | tr -d '[:space:]')"
PROBE_HOST="127.0.0.1"
[[ -n "${BIND:-}" && "$BIND" != "0.0.0.0" ]] && PROBE_HOST="$BIND"

curl -fsS http://127.0.0.1:5001/health || echo "RC health: not ready yet"
# nginx serves /health itself, so it answers even when the app behind it does
# not. Probing only that reported a healthy stack while every actual page 502'd,
# so the app is probed through the proxy as well -- that is the one that means
# the Platform is up.
curl -fsS "http://${PROBE_HOST}:${PORT:-8081}/health" >/dev/null \
  && echo "nginx: ok" || echo "nginx: not ready yet"
curl -fsS "http://${PROBE_HOST}:${PORT:-8081}/api/stats" >/dev/null \
  && echo "Platform (through nginx): ok" \
  || echo "Platform (through nginx): NOT reachable — docker compose logs docbridge-web docbridge-web-nginx"
docker compose --env-file "$KNOVAS_ENV" ps
