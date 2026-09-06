#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

KNOVAS_ENV="$ROOT_DIR/knovas.env"
if [[ ! -f "$KNOVAS_ENV" ]]; then
  if [[ -f "$ROOT_DIR/knovas.env.example" ]]; then
    cp "$ROOT_DIR/knovas.env.example" "$KNOVAS_ENV"
    echo "Created knovas.env from knovas.env.example — edit it, then re-run setup."
    exit 1
  fi
  echo "Missing knovas.env" >&2
  exit 1
fi

CERTS_DIR="$ROOT_DIR/certs"
mkdir -p "$CERTS_DIR"
for f in client-cert.pem client-key.pem ca-root.pem; do
  if [[ ! -f "$CERTS_DIR/$f" ]]; then
    echo "Missing $CERTS_DIR/$f — place tenant mTLS certs before setup." >&2
    exit 1
  fi
done

echo "==> Installing tenant certs for RemoteController"
bash "$ROOT_DIR/RemoteController/scripts/install_tenant_certs.sh"

echo "==> Platform cert symlinks"
cd "$CERTS_DIR"
ln -sf client-cert.pem client.crt
ln -sf ca-root.pem ca.crt
if [[ -f client-key.plain.pem ]]; then
  ln -sf client-key.plain.pem client.key
else
  ln -sf client-key.pem client.key
fi

echo "==> Identity database secret"
SECRETS_DIR="$ROOT_DIR/secrets"
DB_SECRET="$SECRETS_DIR/platform_db_password"
mkdir -p "$SECRETS_DIR"
chmod 700 "$SECRETS_DIR"
if [[ -s "$DB_SECRET" ]]; then
  echo "    Keeping the existing password — rotating it would orphan the volume."
else
  # 0600 before anything is written: between a create and a chmod the file is
  # world-readable, and that is the whole window an attacker needs.
  ( umask 077; head -c 32 /dev/urandom | base64 | tr -d '\n=' > "$DB_SECRET" )
  chmod 600 "$DB_SECRET"
  echo "    Generated $DB_SECRET (mode 0600)."
fi

echo "==> Expanding knovas.env"
bash "$ROOT_DIR/scripts/lib/expand_knovas_env.sh" "$KNOVAS_ENV"

# shellcheck source=../KnovasPlatform/scripts/lib/read_env.sh
source "$ROOT_DIR/KnovasPlatform/scripts/lib/read_env.sh"
PLATFORM_URL="$(read_env_var KNOVAS_PLATFORM_URL "" "$KNOVAS_ENV")"
DOCS_PATH="$(read_env_var KNOVAS_DOCUMENTS_PATH "" "$KNOVAS_ENV")"
ADMIN_EMAIL="$(read_env_var PLATFORM_ADMIN_EMAIL "" "$KNOVAS_ENV")"

if [[ ! -d "$DOCS_PATH" ]]; then
  echo "WARNING: KNOVAS_DOCUMENTS_PATH does not exist yet: $DOCS_PATH"
fi

# There is no default account, so this cannot be defaulted either.
if [[ -z "$ADMIN_EMAIL" ]]; then
  echo "ERROR: PLATFORM_ADMIN_EMAIL is not set in knovas.env." >&2
  echo "       The Platform creates the firm's first administrator from it on" >&2
  echo "       first start, and ships no default account." >&2
  exit 1
fi

echo ""
echo "Setup complete."
echo "  Platform URL: $PLATFORM_URL"
echo "  Documents:    $DOCS_PATH"
echo "  Administrator: $ADMIN_EMAIL"
echo ""
echo "On first start the Platform writes a one-time password for that account to"
echo "the docbridge-web container at /run/platform-admin-bootstrap. Read it with:"
echo "  docker compose exec docbridge-web cat /run/platform-admin-bootstrap"
echo "Sign in, change it, then delete the file."
echo ""
echo "Back up the platform_db_data volume. It holds every account and every"
echo "access-group grant; without it nobody can sign in."
echo "Next: ./scripts/start.sh"
echo "Then configure host nginx for $PLATFORM_URL → 127.0.0.1:8081"
