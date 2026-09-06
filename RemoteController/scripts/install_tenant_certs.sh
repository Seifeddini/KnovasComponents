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

PLAIN_KEY="$CERTS_DIR/client-key.plain.pem"

# RC/requests cannot use a passphrase at runtime — decrypt when Knovas ships client-key.password.txt.
#
# Written through a temporary file in the same directory and renamed into place,
# never with `openssl -out` onto the destination. A previous run leaves the plain
# key owned by uid 10001 mode 0600, so opening it for writing as the invoking
# user fails -- and setup.sh calls this script on every run, which turned the
# second `./scripts/setup.sh` into "pkey: Can't open ... Permission denied".
# Replacing a file needs write permission on the *directory*, which the operator
# has, not on the file, which they do not.
if [[ -f "$CERTS_DIR/client-key.password.txt" ]]; then
  if [[ -s "$PLAIN_KEY" && "$PLAIN_KEY" -nt "$CERTS_DIR/client-key.pem" ]]; then
    echo "==> client-key.plain.pem is present and newer than the encrypted key — keeping it"
  else
    echo "==> Decrypting client-key.pem using client-key.password.txt (not a passphrase you chose — from Knovas)"
    PASS="$(tr -d '[:space:]' < "$CERTS_DIR/client-key.password.txt")"
    TMP_KEY="$(mktemp "$CERTS_DIR/.client-key.plain.XXXXXX")"
    trap 'rm -f "${TMP_KEY:-}"' EXIT
    ( umask 077; openssl pkey -in "$CERTS_DIR/client-key.pem" -passin "pass:${PASS}" -out "$TMP_KEY" )
    mv -f "$TMP_KEY" "$PLAIN_KEY"
    TMP_KEY=""
    echo "    Use SEMANTIX_CLIENT_KEY_PATH=/certs/client-key.plain.pem in .env"
  fi
fi

# uid 10001 needs the key RC actually loads, and only that one. When a plain key
# exists RC is pointed at it (SEMANTIX_CLIENT_KEY_PATH=/certs/client-key.plain.pem),
# so the encrypted original stays with the operator. Handing that away as well --
# which this script used to do via the client-key*.pem glob -- made key rotation
# impossible without root: the next run could no longer read its own input, and
# openssl failed with "Permission denied" on client-key.pem.
if [[ -s "$PLAIN_KEY" ]]; then
  RC_KEY="$PLAIN_KEY"
else
  RC_KEY="$CERTS_DIR/client-key.pem"
fi
KEY_FILES=("$RC_KEY")
PUB_FILES=("$CERTS_DIR/client-cert.pem" "$CERTS_DIR/ca-root.pem")

# Directory mode 700 + owner master blocks rcuser even when files are chown 10001.
ownership_ok() {
  local f
  [[ "$(stat -c %a "$CERTS_DIR")" == "711" ]] || return 1
  for f in "${KEY_FILES[@]}" "${PUB_FILES[@]}"; do
    [[ "$(stat -c %u "$f")" == "10001" ]] || return 1
  done
  for f in "${KEY_FILES[@]}"; do
    [[ "$(stat -c %a "$f")" == "600" ]] || return 1
  done
  for f in "${PUB_FILES[@]}"; do
    [[ "$(stat -c %a "$f")" == "644" ]] || return 1
  done
}

if ownership_ok; then
  echo "==> Ownership and modes already correct for uid 10001 — nothing to change"
else
  echo "==> Docker rcuser (uid 10001) must traverse the directory and read the key"
  # chown and chmod go through the same escalation. Splitting them was the second
  # half of the bug: once `sudo chown` handed the files to uid 10001, a following
  # unprivileged `chmod` could no longer touch them, so even a first run died with
  # "Operation not permitted" after appearing to succeed.
  SUDO=""
  if [[ "$(id -u)" -ne 0 ]]; then
    if ! command -v sudo >/dev/null 2>&1; then
      echo "Need root to give the certs to uid 10001, and sudo is not available." >&2
      echo "Re-run as root: sudo $0" >&2
      exit 1
    fi
    SUDO="sudo"
  fi
  $SUDO chmod 711 "$CERTS_DIR"
  $SUDO chown 10001:10001 "${KEY_FILES[@]}" "${PUB_FILES[@]}"
  $SUDO chmod 644 "${PUB_FILES[@]}"
  $SUDO chmod 600 "${KEY_FILES[@]}"
fi

# An older run of this script chowned the encrypted key to uid 10001 too. That is
# harmless until Knovas ships a replacement, at which point the decryption step
# cannot read it. Say so once, with the remedy, rather than failing later.
if [[ "$RC_KEY" == "$PLAIN_KEY" && -f "$CERTS_DIR/client-key.pem" ]]; then
  if [[ ! -r "$CERTS_DIR/client-key.pem" ]]; then
    echo ""
    echo "NOTE: client-key.pem is not readable by $(id -un) — an earlier version of this"
    echo "      script gave it to uid 10001. Nothing is broken now, but re-decrypting a"
    echo "      rotated key will fail. To hand it back:"
    echo "        sudo chown $(id -un) $CERTS_DIR/client-key.pem"
  fi
fi

ls -la "$CERTS_DIR/"
echo "==> Done. Compose mounts $CERTS_DIR -> /certs (see docker-compose.yml)"
