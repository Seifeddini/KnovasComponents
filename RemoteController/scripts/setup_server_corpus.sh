#!/usr/bin/env bash
# Setup RemoteController for KnovasInternal corpus on server_01_home.
# Run from: /home/master/KnovasInternal/RemoteController
set -euo pipefail

RC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MONOREPO_ROOT="$(cd "$RC_DIR/.." && pwd)"
CORPUS_DIR="$MONOREPO_ROOT/corpus"
CERTS_SRC="$MONOREPO_ROOT/semantix-certs"
ORG_ID_FILE="$CERTS_SRC/organisation_id.txt"

cd "$RC_DIR"

echo "==> Step 1: Verify corpus exists"
test -d "$CORPUS_DIR"
FILE_COUNT="$(find "$CORPUS_DIR" -type f | wc -l)"
echo "    Corpus: $CORPUS_DIR ($FILE_COUNT files)"

echo "==> Step 2: Prepare data directory (corpus mounted via docker-compose.internal.yml)"
mkdir -p data

echo "==> Step 3: Install tenant mTLS certs for Docker (/certs mount)"
mkdir -p certs
install -m 644 "$CERTS_SRC/client.crt" certs/tenant-client.pem
install -m 600 "$CERTS_SRC/client.key" certs/tenant-client.key
install -m 644 "$CERTS_SRC/ca.crt" certs/ca.pem
# Container runs as rcuser (uid 10001); key mode 600 must be owned by 10001 to be readable
if command -v sudo >/dev/null 2>&1; then
  sudo chown 10001:10001 certs/tenant-client.key certs/tenant-client.pem certs/ca.pem 2>/dev/null \
    || chown 10001:10001 certs/tenant-client.key certs/tenant-client.pem certs/ca.pem
else
  chown 10001:10001 certs/tenant-client.key certs/tenant-client.pem certs/ca.pem
fi
ls -la certs/

echo "==> Step 4: Read organisation UUID for RC_CLIENT_ID"
RC_CLIENT_ID="$(tr -d '[:space:]' < "$ORG_ID_FILE")"
echo "    RC_CLIENT_ID=$RC_CLIENT_ID"

echo "==> Step 5: Write .env with container paths"
if [[ ! -f .env ]]; then
  cp .env.example .env
fi

# Patch known values (preserve RC_INSTANCE_TOKEN if already set to a real token)
python3 <<PY
from pathlib import Path
import re

env_path = Path(".env")
lines = env_path.read_text().splitlines()
updates = {
    "KNOVAS_INTERNAL_API_URL": "http://api.knovas.ch:8080",
    "RC_CLIENT_ID": "$RC_CLIENT_ID",
    "RC_WATCH_ROOTS": "/data/corpus",
    "SEMANTIX_SECURE_BASE_URL": "https://api.knovas.ch:8443",
    "SEMANTIX_CLIENT_CERT_PATH": "/certs/tenant-client.pem",
    "SEMANTIX_CLIENT_KEY_PATH": "/certs/tenant-client.key",
    "SEMANTIX_CA_CERT_PATH": "/certs/ca.pem",
    "RC_MTLS_DEV_BYPASS": "true",
    "RC_SYNC_DEFAULT_WINDOW_START": "00:00",
    "RC_SYNC_DEFAULT_WINDOW_END": "23:59",
    "RC_SYNC_DEFAULT_MAX_INGESTION_REQUESTS_PER_MINUTE": "4",
    "RC_SYNC_STATE_PATH": "/var/rc-state/.rc-sync-state.json",
}
out = []
seen = set()
for line in lines:
    key = line.split("=", 1)[0] if "=" in line and not line.strip().startswith("#") else None
    if key in updates:
        out.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        out.append(line)
for key, val in updates.items():
    if key not in seen:
        out.append(f"{key}={val}")
env_path.write_text("\\n".join(out) + "\\n")
PY

echo "==> Step 6: Ensure latest sync source (if monorepo checkout is behind local fixes)"
# Optional: copy from a dev machine when server git tree lacks recent RC changes.

echo "==> Step 7: Build and start (internal mode, localhost:5001 only)"
docker compose -f docker-compose.yml -f docker-compose.internal.yml up -d --build

echo "==> Step 8: Fix Docker volume ownership (rcuser must write config + state)"
docker exec -u root remotecontroller-remote-controller-1 \
  chown -R rcuser:rcuser /app/config /var/rc-state

echo "==> Step 9: Restart and wait for health"
docker restart remotecontroller-remote-controller-1
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:5001/health >/tmp/rc-health.json 2>/dev/null; then
    if grep -q '"status":"ok"' /tmp/rc-health.json; then
      cat /tmp/rc-health.json
      echo
      exit 0
    fi
  fi
  sleep 2
done
echo "Health check did not reach status ok within 60s — check: docker compose logs remote-controller"
cat /tmp/rc-health.json 2>/dev/null || true
exit 1
