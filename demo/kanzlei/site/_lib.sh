# Shared helpers for the site scripts (sourced, not run).
set -euo pipefail
export MSYS_NO_PATHCONV=1                       # Git Bash: do not rewrite container paths like /opt/site
SITE_HOME=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$SITE_HOME"

# Absolute host path in a form Docker Desktop accepts (E:/x/y under Git Bash, /x/y elsewhere).
host_path() {
  local p; p=$(cd "$1" && pwd) || { echo "not a folder: $1" >&2; return 1; }
  if command -v cygpath >/dev/null; then cygpath -m "$p"; else echo "$p"; fi
}

# Set KEY=VALUE in .env (replace the line or append it).
set_env() {
  local tmp; tmp=$(mktemp)
  grep -v "^$1=" .env > "$tmp" 2>/dev/null || true
  echo "$1=\"$2\"" >> "$tmp"
  cat "$tmp" > .env && rm -f "$tmp"
}

load_env() {
  [ -f .env ] || { echo "No .env here: run ./init-site.sh <site-dir> <share-dir> first" >&2; exit 1; }
  set -a; . ./.env; set +a
}

compose() {
  local files=(-f compose.yml)
  [ "${DEMO_PUBLISH_PORTS:-0}" = 1 ] && files+=(-f compose.ports.yml)
  docker compose "${files[@]}" "$@"
}
