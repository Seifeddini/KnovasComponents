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
PLATFORM_ADMIN_EMAIL="$(read_knovas PLATFORM_ADMIN_EMAIL)"
KNOVAS_TENANT_ID="$(read_knovas KNOVAS_TENANT_ID)"
KNOVAS_IDENTIFIER_PREFIX="$(read_knovas KNOVAS_IDENTIFIER_PREFIX tenant)"
KNOVAS_SHARE_UNC="$(read_knovas KNOVAS_SHARE_UNC)"
WEB_SECRET_KEY="$(read_knovas WEB_SECRET_KEY)"

# Per-user identity is the default. The shared company login is only read when
# someone has explicitly staged a cutover with IDENTITY_ENABLED=false -- and it
# is deliberately read WITHOUT a fallback, because a default of "company" here
# used to put COMPANY_LOGIN_NAME into .env.generated on every run, which is
# exactly the state web_interface/app.py refuses to start in.
IDENTITY_ENABLED="$(read_knovas IDENTITY_ENABLED true)"
COMPANY_LOGIN_NAME="$(read_knovas COMPANY_LOGIN_NAME)"
COMPANY_LOGIN_PASSWORD="$(read_knovas COMPANY_LOGIN_PASSWORD)"

missing=()
[[ -z "$KNOVAS_API_URL" ]] && missing+=("KNOVAS_API_URL")
[[ -z "$KNOVAS_PLATFORM_URL" ]] && missing+=("KNOVAS_PLATFORM_URL")
[[ -z "$KNOVAS_DOCUMENTS_PATH" ]] && missing+=("KNOVAS_DOCUMENTS_PATH")
# docker-compose.yml refuses to start without it; failing here names the file
# to edit instead of surfacing as a compose interpolation error at `up`.
[[ -z "$PLATFORM_ADMIN_EMAIL" ]] && missing+=("PLATFORM_ADMIN_EMAIL")
if (( ${#missing[@]} > 0 )); then
  echo "Missing required values in $KNOVAS_ENV: ${missing[*]}" >&2
  exit 1
fi

# Both doors open is the one configuration the Platform will not start in, and
# it is better caught here than as a container that builds for four minutes and
# then goes unhealthy with the reason buried in `docker compose logs`.
case "$IDENTITY_ENABLED" in
  false|False|FALSE|0|no|No|NO)
    IDENTITY_ON=false
    if [[ -z "$COMPANY_LOGIN_NAME" || -z "$COMPANY_LOGIN_PASSWORD" ]]; then
      echo "IDENTITY_ENABLED=false needs COMPANY_LOGIN_NAME and COMPANY_LOGIN_PASSWORD in $KNOVAS_ENV." >&2
      echo "That combination is the staged cutover; leaving identity on needs neither." >&2
      exit 1
    fi
    ;;
  *)
    IDENTITY_ON=true
    if [[ -n "$COMPANY_LOGIN_NAME" || -n "$COMPANY_LOGIN_PASSWORD" ]]; then
      echo "COMPANY_LOGIN_NAME/COMPANY_LOGIN_PASSWORD are set in $KNOVAS_ENV while per-user" >&2
      echo "identity is on. The Platform refuses to start with both doors open." >&2
      echo "Remove both values, or set IDENTITY_ENABLED=false to stage a cutover." >&2
      echo "See RELEASE_NOTES.md." >&2
      exit 1
    fi
    ;;
esac

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
  # sep_multiline, not the default one-line subject. OpenSSL 3 prints that as
  # "CN = value" -- with spaces around the equals -- which the previous "CN="
  # pattern never matched. The fallback therefore produced nothing on every
  # modern system, and setup.sh stopped to ask for a value it could have read
  # off the certificate itself. sep_multiline also survives a comma inside
  # another RDN, which the one-line form does not.
  KNOVAS_TENANT_ID="$(openssl x509 -in "$CERTS_DIR/client-cert.pem" -noout -subject \
    -nameopt sep_multiline,utf8 2>/dev/null | sed -n 's/^[[:space:]]*CN=//p' | head -1 || true)"
  KNOVAS_TENANT_ID="$(printf '%s' "$KNOVAS_TENANT_ID" | tr -d '[:space:]')"
fi
if [[ -z "$KNOVAS_TENANT_ID" ]]; then
  echo "Set KNOVAS_TENANT_ID in knovas.env, or add certs/organisation_id.txt." >&2
  echo "It is the tenant the Platform signs each user into, and it could not be" >&2
  echo "read from the client certificate. See what the certificate carries with:" >&2
  echo "  openssl x509 -in certs/client-cert.pem -noout -subject -nameopt sep_multiline,utf8" >&2
  exit 1
fi

KEY_PATH="/certs/client-key.pem"
if [[ -f "$CERTS_DIR/client-key.plain.pem" ]]; then
  KEY_PATH="/certs/client-key.plain.pem"
fi

# Keys the expander interprets itself. Everything else in knovas.env is an
# override passed straight through to the component it names: RC_* reaches
# RemoteController, the rest reaches the Platform. Without this, knovas.env
# could express five values and a deployment that needed a sixth -- a plain-HTTP
# session cookie, a Cortex fixture path, a tuned worker count -- had nowhere to
# put it, because .env.generated is rewritten on every setup.sh.
CONSUMED_KEYS="
KNOVAS_API_URL KNOVAS_PLATFORM_URL KNOVAS_DOCUMENTS_PATH KNOVAS_TENANT_ID
KNOVAS_IDENTIFIER_PREFIX KNOVAS_SHARE_UNC WEB_SECRET_KEY IDENTITY_ENABLED
COMPANY_LOGIN_NAME COMPANY_LOGIN_PASSWORD PLATFORM_ADMIN_EMAIL
PLATFORM_ADMIN_PASSWORD PLATFORM_ADMIN_BOOTSTRAP_PATH PLATFORM_DB_NAME
PLATFORM_DB_USER PLATFORM_BROKER_KEY_DIR
"

# $1: "rc" for RC_* keys, "platform" for the rest.
passthrough_overrides() {
  local want="$1" line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line#"${line%%[![:space:]]*}"}"
    [[ -z "$line" || "$line" == \#* ]] && continue
    [[ "$line" == *=* ]] || continue
    key="${line%%=*}"
    value="${line#*=}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    case " $(echo $CONSUMED_KEYS) " in *" $key "*) continue ;; esac
    if [[ "$want" == "rc" ]]; then
      [[ "$key" == RC_* ]] || continue
    else
      [[ "$key" == RC_* ]] && continue
    fi
    printf '%s=%s\n' "$key" "$value"
  done < "$KNOVAS_ENV"
}

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

passthrough_overrides rc >> "$RC_ENV"

OPEN_UNC_LINE=""
if [[ -n "$KNOVAS_SHARE_UNC" ]]; then
  OPEN_UNC_LINE="OPEN_UNC_ROOT=${KNOVAS_SHARE_UNC}"
fi

# Written only for a staged cutover. With identity on these keys must be absent
# from the generated file, not merely empty: app.py treats a name AND password
# as "configured", and an empty value read back from env_file is still a value
# someone can fill in by hand and then wonder why the container will not boot.
LOGIN_LINES=""
if [[ "$IDENTITY_ON" == "false" ]]; then
  LOGIN_LINES="IDENTITY_ENABLED=false
COMPANY_LOGIN_ENABLED=true
COMPANY_LOGIN_NAME=${COMPANY_LOGIN_NAME}
COMPANY_LOGIN_PASSWORD=${COMPANY_LOGIN_PASSWORD}"
fi

cat > "$KP_ENV" <<EOF
# Generated from knovas.env — do not edit; re-run ./scripts/setup.sh
ENVIRONMENT=production
WEB_SECRET_KEY=${WEB_SECRET_KEY}
WEB_SESSION_COOKIE_SECURE=true
COMPANY_DISPLAY_NAME=Knovas
${LOGIN_LINES}
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

passthrough_overrides platform >> "$KP_ENV"

echo "Wrote $RC_ENV"
echo "Wrote $KP_ENV"
