#!/usr/bin/env bash
# Expand knovas.env into component .env.generated files.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=../../KnovasPlatform/scripts/lib/read_env.sh
source "$ROOT_DIR/KnovasPlatform/scripts/lib/read_env.sh"

KNOVAS_ENV="${1:-$ROOT_DIR/knovas.env}"

if [[ ! -f "$KNOVAS_ENV" ]]; then
  echo "Missing $KNOVAS_ENV — copy knovas.env.example to knovas.env first." >&2
  exit 1
fi

read_knovas() {
  read_env_var "$1" "${2:-}" "$KNOVAS_ENV"
}

KNOVAS_API_URL="$(read_knovas KNOVAS_API_URL)"
KNOVAS_PLATFORM_URL="$(read_knovas KNOVAS_PLATFORM_URL)"
KNOVAS_DOCUMENTS_PATH="$(read_knovas KNOVAS_DOCUMENTS_PATH)"
COMPANY_LOGIN_PASSWORD="$(read_knovas COMPANY_LOGIN_PASSWORD)"
KNOVAS_TENANT_ID="$(read_knovas KNOVAS_TENANT_ID)"
KNOVAS_IDENTIFIER_PREFIX="$(read_knovas KNOVAS_IDENTIFIER_PREFIX tenant)"
COMPANY_LOGIN_NAME="$(read_knovas COMPANY_LOGIN_NAME company)"
KNOVAS_SHARE_UNC="$(read_knovas KNOVAS_SHARE_UNC)"
WEB_SECRET_KEY="$(read_knovas WEB_SECRET_KEY)"

missing=()
[[ -z "$KNOVAS_API_URL" ]] && missing+=("KNOVAS_API_URL")
[[ -z "$KNOVAS_PLATFORM_URL" ]] && missing+=("KNOVAS_PLATFORM_URL")
[[ -z "$KNOVAS_DOCUMENTS_PATH" ]] && missing+=("KNOVAS_DOCUMENTS_PATH")
[[ -z "$COMPANY_LOGIN_PASSWORD" ]] && missing+=("COMPANY_LOGIN_PASSWORD")
if (( ${#missing[@]} > 0 )); then
  echo "Missing required values in $KNOVAS_ENV: ${missing[*]}" >&2
  exit 1
fi

if [[ -z "$WEB_SECRET_KEY" ]]; then
  if command -v openssl >/dev/null 2>&1; then
    WEB_SECRET_KEY="$(openssl rand -hex 32)"
  else
    WEB_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  fi
fi

CERTS_DIR="$ROOT_DIR/certs"
ORG_FILE="$CERTS_DIR/organisation_id.txt"
if [[ -z "$KNOVAS_TENANT_ID" && -f "$ORG_FILE" ]]; then
  KNOVAS_TENANT_ID="$(tr -d '[:space:]' < "$ORG_FILE")"
fi
if [[ -z "$KNOVAS_TENANT_ID" && -f "$CERTS_DIR/client-cert.pem" ]]; then
  KNOVAS_TENANT_ID="$(openssl x509 -in "$CERTS_DIR/client-cert.pem" -noout -subject 2>/dev/null \
    | sed -n 's/.*CN=\([^,/]*\).*/\1/p' | head -1 || true)"
fi
if [[ -z "$KNOVAS_TENANT_ID" ]]; then
  echo "Set KNOVAS_TENANT_ID in knovas.env or add certs/organisation_id.txt" >&2
  exit 1
fi

KEY_PATH="/certs/client-key.pem"
if [[ -f "$CERTS_DIR/client-key.plain.pem" ]]; then
  KEY_PATH="/certs/client-key.plain.pem"
fi

RC_ENV="$ROOT_DIR/RemoteController/.env.generated"
KP_ENV="$ROOT_DIR/KnovasPlatform/.env.generated"

cat > "$RC_ENV" <<EOF
# Generated from knovas.env — do not edit; re-run ./scripts/setup.sh
KNOVAS_INTERNAL_API_URL=
RC_INSTANCE_TOKEN=
RC_CLIENT_ID=${KNOVAS_TENANT_ID}
RC_WATCH_ROOTS=/mnt/documents
SEMANTIX_SECURE_BASE_URL=${KNOVAS_API_URL}
SEMANTIX_CLIENT_CERT_PATH=/certs/client-cert.pem
SEMANTIX_CLIENT_KEY_PATH=${KEY_PATH}
SEMANTIX_CA_CERT_PATH=/certs/ca-root.pem
RC_INTERNAL_LOCAL_BYPASS=true
RC_LOCAL_BYPASS_TRUSTED_CIDRS=172.16.0.0/12
RC_SYNC_STATE_PATH=/var/rc-state/.rc-sync-state.json
RC_SYNC_AUTO_START_CONTINUOUS=true
RC_SYNC_AUTO_START_REQUIRES_SAVED_BODY=false
RC_SYNC_DEFAULT_WINDOW_START=00:00
RC_SYNC_DEFAULT_WINDOW_END=23:59
SEARCH_CONTEXT_STORE_PATH=/var/rc-state/search_context
KNOVAS_IDENTIFIER_PREFIX=${KNOVAS_IDENTIFIER_PREFIX}
EOF

OPEN_UNC_LINE=""
if [[ -n "$KNOVAS_SHARE_UNC" ]]; then
  OPEN_UNC_LINE="OPEN_UNC_ROOT=${KNOVAS_SHARE_UNC}"
fi

cat > "$KP_ENV" <<EOF
# Generated from knovas.env — do not edit; re-run ./scripts/setup.sh
ENVIRONMENT=production
WEB_SECRET_KEY=${WEB_SECRET_KEY}
WEB_SESSION_COOKIE_SECURE=true
COMPANY_LOGIN_ENABLED=true
COMPANY_DISPLAY_NAME=Knovas
COMPANY_LOGIN_NAME=${COMPANY_LOGIN_NAME}
COMPANY_LOGIN_PASSWORD=${COMPANY_LOGIN_PASSWORD}
DOCBRIDGE_WEB_PORT=8081
SEMANTIX_API_URL=${KNOVAS_API_URL}
SEMANTIX_USE_SECURED_API=true
SEMANTIX_ALLOW_LEGACY_API_FALLBACK=false
SEMANTIX_CLIENT_CERT=/app/certs/client.crt
SEMANTIX_CLIENT_KEY=/app/certs/client.key
SEMANTIX_CA_CERT=/app/certs/ca.crt
SEMANTIX_CUSTOMER_ID=${KNOVAS_TENANT_ID}
SEMANTIX_CERT_AUTO_RENEW_ENABLED=true
OPEN_BROWSER_CLIENT_PATH=true
OPEN_COMPANION_ENABLED=false
OPEN_PUBLIC_BASE_URL=${KNOVAS_PLATFORM_URL}
KNOVAS_PLATFORM_URL=${KNOVAS_PLATFORM_URL}
OPEN_LOCAL_ROOT=/mnt/autodoc
OPEN_PDF_INLINE_IN_BROWSER=true
AUTODOC_MOUNT_PATH=${KNOVAS_DOCUMENTS_PATH}
AUTODOC_IDENTIFIER_PREFIX=${KNOVAS_IDENTIFIER_PREFIX}
SEARCH_CONTEXT_STORE_PATH=/var/rc-state/search_context
${OPEN_UNC_LINE}
EOF

echo "Wrote $RC_ENV"
echo "Wrote $KP_ENV"
