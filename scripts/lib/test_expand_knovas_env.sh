#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
FIXTURE="$ROOT_DIR/scripts/lib/fixtures/knovas.env.fixture"

cd "$ROOT_DIR"
bash scripts/lib/expand_knovas_env.sh "$FIXTURE"
grep -q 'SEMANTIX_SECURE_BASE_URL=https://api.test:8443' RemoteController/.env.generated
grep -q 'OPEN_PUBLIC_BASE_URL=https://knovas.test.internal' KnovasPlatform/.env.generated
rm -f RemoteController/.env.generated KnovasPlatform/.env.generated
echo "expand_knovas_env smoke OK"
