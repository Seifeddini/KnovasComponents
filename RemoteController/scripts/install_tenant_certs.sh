#!/usr/bin/env bash
# Prepare ~/KnovasInternal/certs for RemoteController Docker (uid 10001 / rcuser).
# Run from: KnovasInternal/RemoteController
set -euo pipefail

RC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CERTS_DIR="$(cd "$RC_DIR/.." && pwd)/certs"

if [[ ! -d "$CERTS_DIR" ]]; then
  echo "Missing tenant cert directory: $CERTS_DIR" >&2
  exit 1
fi

for f in client-cert.pem client-key.pem ca-root.pem; do
  if [[ ! -f "$CERTS_DIR/$f" ]]; then
    echo "Missing $CERTS_DIR/$f" >&2
    exit 1
  fi
done

# RC/requests cannot use a passphrase at runtime — decrypt when Knovas ships client-key.password.txt.
if [[ -f "$CERTS_DIR/client-key.password.txt" ]]; then
  echo "==> Decrypting client-key.pem using client-key.password.txt (not a passphrase you chose — from Knovas)"
  PASS="$(tr -d '[:space:]' < "$CERTS_DIR/client-key.password.txt")"
  openssl pkey -in "$CERTS_DIR/client-key.pem" -passin "pass:${PASS}" \
    -out "$CERTS_DIR/client-key.plain.pem"
  chmod 600 "$CERTS_DIR/client-key.plain.pem"
  echo "    Use SEMANTIX_CLIENT_KEY_PATH=/certs/client-key.plain.pem in .env"
fi

echo "==> Docker rcuser (uid 10001) must traverse the directory and read the key"
# Directory mode 700 + owner master blocks rcuser even when files are chown 10001.
chmod 711 "$CERTS_DIR"
chown 10001:10001 "$CERTS_DIR"/client-key*.pem "$CERTS_DIR/client-cert.pem" "$CERTS_DIR/ca-root.pem" 2>/dev/null \
  || sudo chown 10001:10001 "$CERTS_DIR"/client-key*.pem "$CERTS_DIR/client-cert.pem" "$CERTS_DIR/ca-root.pem"
chmod 644 "$CERTS_DIR/client-cert.pem" "$CERTS_DIR/ca-root.pem"
chmod 600 "$CERTS_DIR"/client-key*.pem

ls -la "$CERTS_DIR/"
echo "==> Done. Compose mounts $CERTS_DIR -> /certs (see docker-compose.yml)"
