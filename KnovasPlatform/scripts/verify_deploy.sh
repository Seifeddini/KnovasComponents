#!/usr/bin/env bash
# Verify KnovasPlatform web stack after deploy.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

PORT="${DOCBRIDGE_WEB_PORT:-8081}"
if [[ -f .env ]]; then
  # shellcheck disable=SC1091
  set -a
  source .env
  set +a
  PORT="${DOCBRIDGE_WEB_PORT:-$PORT}"
fi

BASE_URL="${VERIFY_BASE_URL:-http://localhost:${PORT}}"
PROBE_MTLS="${VERIFY_MTLS:-false}"

echo "KnovasPlatform deploy verification"
echo "  Base URL: $BASE_URL"
echo ""

failures=0

check_http() {
  local path="$1"
  local desc="$2"
  local url="${BASE_URL}${path}"
  if curl -fsS --max-time 15 "$url" >/dev/null; then
    echo "  OK  $desc ($path)"
  else
    echo "  FAIL $desc ($path)"
    failures=$((failures + 1))
  fi
}

check_http "/health" "nginx liveness"
check_http "/api/stats" "app stats (compose healthcheck)"

echo ""
echo "API health JSON:"
if ! curl -fsS --max-time 15 "${BASE_URL}/api/health"; then
  echo ""
  echo "  FAIL /api/health"
  failures=$((failures + 1))
else
  echo ""
fi

if [[ "$PROBE_MTLS" == "true" || "$PROBE_MTLS" == "1" ]]; then
  echo ""
  echo "mTLS probe (from docbridge-web container):"
  if ! docker compose ps --status running docbridge-web 2>/dev/null | grep -q docbridge-web; then
    echo "  SKIP docbridge-web is not running"
  else
  SEMANTIX_URL="${SEMANTIX_API_URL:-}"
  if [[ -z "$SEMANTIX_URL" ]]; then
    echo "  SKIP SEMANTIX_API_URL not set in .env"
  else
    docker compose exec -T docbridge-web python -c "
import os, ssl, urllib.request
url = (os.environ.get('SEMANTIX_API_URL') or '').rstrip('/') + '/secured/health'
cert = os.environ.get('SEMANTIX_CLIENT_CERT', '')
key = os.environ.get('SEMANTIX_CLIENT_KEY', '')
ca = os.environ.get('SEMANTIX_CA_CERT', '')
if not url.startswith('http'):
    raise SystemExit('SEMANTIX_API_URL not set')
ctx = ssl.create_default_context(cafile=ca or None)
if cert and key and os.path.isfile(cert) and os.path.isfile(key):
    ctx.load_cert_chain(cert, key)
req = urllib.request.Request(url)
with urllib.request.urlopen(req, context=ctx, timeout=15) as r:
    print('  OK  mTLS health', r.status, r.read()[:200])
" || failures=$((failures + 1))
  fi
  fi
fi

echo ""
if [[ -d "./certs" ]]; then
  for f in client.crt client.key ca.crt; do
    if [[ -f "./certs/$f" ]]; then
      echo "  OK  certs/$f present"
    else
      echo "  WARN certs/$f missing (required for production mTLS)"
    fi
  done
else
  echo "  WARN ./certs/ directory not found (required for production mTLS)"
fi

echo ""
if [[ "$failures" -gt 0 ]]; then
  echo "Verification failed ($failures check(s))."
  exit 1
fi
echo "Verification passed."
exit 0
