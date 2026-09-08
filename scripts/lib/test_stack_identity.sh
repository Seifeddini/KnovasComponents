#!/usr/bin/env bash
# A second checkout on the same host used to fail at start.sh because every
# stack claimed the container name platform-db and the same loopback ports.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

fail() { echo "FAIL: $*" >&2; exit 1; }

# --- Compose file: no global names, host ports are interpolable ------------
if grep -E '^[[:space:]]*container_name:' docker-compose.yml >/dev/null; then
  fail "docker-compose.yml pins container_name — a second checkout cannot start"
fi
grep -qE 'RC_HOST_PORT' docker-compose.yml \
  || fail "remote-controller host port is hardcoded; a second stack will lose :5001"

# --- Helper ----------------------------------------------------------------
# shellcheck source=stack_identity.sh
source "$ROOT_DIR/scripts/lib/stack_identity.sh"

[[ "$(knovas_compose_project_from_dir /home/master/KnovasDemo)" == "knovasdemo" ]] \
  || fail "directory KnovasDemo should become compose project knovasdemo"
[[ "$(knovas_compose_project_from_dir /home/master/Knovas-Internal)" == "knovas-internal" ]] \
  || fail "hyphens in the directory name should be kept"

[[ "$(knovas_localhost_url_with_port 'http://127.0.0.1:8081' 8082)" == "http://127.0.0.1:8082" ]] \
  || fail "loopback platform URL should follow the chosen web port"
[[ "$(knovas_localhost_url_with_port 'http://localhost:8081/' 8082)" == "http://localhost:8082/" ]] \
  || fail "localhost URL with trailing slash should keep the path"
[[ "$(knovas_localhost_url_with_port 'https://knovas.example.internal' 8082)" == "https://knovas.example.internal" ]] \
  || fail "a real hostname (host nginx) must not be rewritten to a loopback port"

KNOVAS_BUSY_PORTS=8081,8082
KNOVAS_OWN_PORTS=
[[ "$(knovas_pick_host_port 8081)" == "8083" ]] \
  || fail "should skip ports already taken by another stack"
KNOVAS_BUSY_PORTS=8081
KNOVAS_OWN_PORTS=8081
[[ "$(knovas_pick_host_port 8081)" == "8081" ]] \
  || fail "a port this stack already publishes must be kept"

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT
ENV_FILE="$WORKDIR/knovas.env"
cat > "$ENV_FILE" <<'EOF'
KNOVAS_PLATFORM_URL=http://127.0.0.1:8081
PLATFORM_ADMIN_EMAIL=you@example.com
EOF

KNOVAS_BUSY_PORTS=8081,5001
KNOVAS_OWN_PORTS=
knovas_prepare_stack "$ENV_FILE" "$WORKDIR/KnovasDemo"

last_value() { grep -E "^$1=" "$2" | tail -1 | cut -d= -f2-; }
[[ "$(last_value DOCBRIDGE_WEB_PORT "$ENV_FILE")" == "8082" ]] \
  || fail "prepare_stack should persist a free web port"
[[ "$(last_value RC_HOST_PORT "$ENV_FILE")" == "5002" ]] \
  || fail "prepare_stack should persist a free RemoteController port"
[[ "$(last_value KNOVAS_PLATFORM_URL "$ENV_FILE")" == "http://127.0.0.1:8082" ]] \
  || fail "loopback KNOVAS_PLATFORM_URL should match the web port"
[[ "$(last_value COMPOSE_PROJECT_NAME "$ENV_FILE")" == "knovasdemo" ]] \
  || fail "prepare_stack should persist the compose project name"

# Idempotent: already-assigned free ports stay put.
KNOVAS_BUSY_PORTS=
knovas_prepare_stack "$ENV_FILE" "$WORKDIR/KnovasDemo"
[[ "$(last_value DOCBRIDGE_WEB_PORT "$ENV_FILE")" == "8082" ]] \
  || fail "prepare_stack must not bounce a port that is still free"

echo "stack_identity smoke OK"
