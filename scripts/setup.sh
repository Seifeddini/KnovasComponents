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

echo "==> Expanding knovas.env"
bash "$ROOT_DIR/scripts/lib/expand_knovas_env.sh" "$KNOVAS_ENV"

# shellcheck source=../KnovasPlatform/scripts/lib/read_env.sh
source "$ROOT_DIR/KnovasPlatform/scripts/lib/read_env.sh"
PLATFORM_URL="$(read_env_var KNOVAS_PLATFORM_URL "" "$KNOVAS_ENV")"
DOCS_PATH="$(read_env_var KNOVAS_DOCUMENTS_PATH "" "$KNOVAS_ENV")"

if [[ ! -d "$DOCS_PATH" ]]; then
  echo "WARNING: KNOVAS_DOCUMENTS_PATH does not exist yet: $DOCS_PATH"
fi

echo ""
echo "Setup complete."
echo "  Platform URL: $PLATFORM_URL"
echo "  Documents:    $DOCS_PATH"
echo "Next: ./scripts/start.sh"
echo "Then configure host nginx for $PLATFORM_URL → 127.0.0.1:8081"
