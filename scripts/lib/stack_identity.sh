#!/usr/bin/env bash
# Identify this checkout as its own Compose project, with its own host ports,
# so two copies of the stack can run on one machine.
#
# Tests set KNOVAS_BUSY_PORTS / KNOVAS_OWN_PORTS (even to empty) to stub the
# bind checks. Unset those in production.

_STACK_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_STACK_ROOT="$(cd "$_STACK_LIB_DIR/../.." && pwd)"
# shellcheck source=../../KnovasPlatform/scripts/lib/read_env.sh
source "$_STACK_ROOT/KnovasPlatform/scripts/lib/read_env.sh"

knovas_compose_project_from_dir() {
  local base
  base="$(basename "$1")"
  base="$(printf '%s' "$base" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9_-]//g')"
  if [[ -z "$base" ]]; then
    base="knovas"
  elif [[ ! "$base" =~ ^[a-z0-9] ]]; then
    base="k${base}"
  fi
  printf '%s' "$base"
}

knovas_localhost_url_with_port() {
  local url="$1" port="$2"
  case "$url" in
    http://127.0.0.1:*|http://localhost:*|http://[::1]:*)
      printf '%s' "$url" | sed -E "s#(https?://(127\\.0\\.0\\.1|localhost|\\[::1\\])):[0-9]+#\\1:${port}#"
      ;;
    *)
      printf '%s' "$url"
      ;;
  esac
}

knovas_host_port_in_use() {
  local port="$1"
  if [[ "${KNOVAS_BUSY_PORTS+x}" == "x" ]]; then
    [[ ",${KNOVAS_BUSY_PORTS}," == *",${port},"* ]]
    return
  fi
  local py=""
  command -v python3 >/dev/null && py=python3
  [[ -z "$py" ]] && command -v python >/dev/null && py=python
  if [[ -n "$py" ]]; then
    "$py" - "$port" <<'PY'
import socket, sys
port = int(sys.argv[1])
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.bind(("127.0.0.1", port))
except OSError:
    sys.exit(0)
finally:
    s.close()
sys.exit(1)
PY
    return
  fi
  if command -v ss >/dev/null; then
    ss -H -ltn "sport = :$port" | grep -q .
    return
  fi
  echo "Cannot check whether port $port is in use (need python3 or ss)." >&2
  return 1
}

knovas_port_is_ours() {
  local port="$1"
  if [[ "${KNOVAS_OWN_PORTS+x}" == "x" ]]; then
    [[ ",${KNOVAS_OWN_PORTS}," == *",${port},"* ]]
    return
  fi
  local env_file="${KNOVAS_COMPOSE_ENV_FILE:-}"
  local dir="${KNOVAS_COMPOSE_DIR:-}"
  [[ -n "$env_file" && -f "$env_file" && -n "$dir" ]] || return 1
  (
    cd "$dir" || exit 1
    export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-$(knovas_compose_project_from_dir "$dir")}"
    docker compose --env-file "$env_file" ps --format '{{.Ports}}' 2>/dev/null
  ) | grep -qE ":${port}->"
}

knovas_pick_host_port() {
  local port="$1"
  local n=0
  while knovas_host_port_in_use "$port"; do
    if knovas_port_is_ours "$port"; then
      printf '%s' "$port"
      return 0
    fi
    port=$((port + 1))
    n=$((n + 1))
    if (( n > 100 )); then
      echo "No free host port near the requested value." >&2
      return 1
    fi
  done
  printf '%s' "$port"
}

knovas_upsert_env() {
  local file="$1" key="$2" value="$3"
  local tmp
  tmp="$(mktemp)"
  if [[ -f "$file" ]] && grep -qE "^[[:space:]]*${key}=" "$file"; then
    awk -v k="$key" -v v="$value" '
      BEGIN { re = "^[[:space:]]*" k "=" }
      $0 ~ re { print k "=" v; next }
      { print }
    ' "$file" > "$tmp"
    mv "$tmp" "$file"
  else
    rm -f "$tmp"
    [[ -f "$file" && -s "$file" && "$(tail -c1 "$file" | wc -l)" -eq 0 ]] && printf '\n' >> "$file"
    printf '%s=%s\n' "$key" "$value" >> "$file"
  fi
}

knovas_prepare_stack() {
  local env_file="$1"
  local checkout="${2:-}"
  local web rc url project new_url kp

  [[ -n "$checkout" ]] || checkout="$(cd "$(dirname "$env_file")" && pwd)"
  KNOVAS_COMPOSE_ENV_FILE="$env_file"
  KNOVAS_COMPOSE_DIR="$checkout"

  project="$(knovas_compose_project_from_dir "$checkout")"
  export COMPOSE_PROJECT_NAME="$project"

  web="$(read_env_var DOCBRIDGE_WEB_PORT 8081 "$env_file")"
  [[ -n "$web" ]] || web=8081
  web="$(knovas_pick_host_port "$web")"

  rc="$(read_env_var RC_HOST_PORT 5001 "$env_file")"
  [[ -n "$rc" ]] || rc=5001
  rc="$(knovas_pick_host_port "$rc")"
  if [[ "$rc" == "$web" ]]; then
    rc="$(knovas_pick_host_port $((rc + 1)))"
  fi

  knovas_upsert_env "$env_file" COMPOSE_PROJECT_NAME "$project"
  knovas_upsert_env "$env_file" DOCBRIDGE_WEB_PORT "$web"
  knovas_upsert_env "$env_file" RC_HOST_PORT "$rc"

  url="$(read_env_var KNOVAS_PLATFORM_URL "" "$env_file")"
  if [[ -n "$url" ]]; then
    new_url="$(knovas_localhost_url_with_port "$url" "$web")"
    if [[ "$new_url" != "$url" ]]; then
      knovas_upsert_env "$env_file" KNOVAS_PLATFORM_URL "$new_url"
      url="$new_url"
    fi
  fi

  kp="$checkout/KnovasPlatform/.env.generated"
  if [[ -f "$kp" ]]; then
    knovas_upsert_env "$kp" DOCBRIDGE_WEB_PORT "$web"
    if [[ -n "$url" ]]; then
      knovas_upsert_env "$kp" OPEN_PUBLIC_BASE_URL "$url"
      knovas_upsert_env "$kp" KNOVAS_PLATFORM_URL "$url"
    fi
  fi
}

knovas_load_compose_project() {
  local env_file="$1"
  local checkout="$2"
  local name=""
  if [[ -f "$env_file" ]]; then
    name="$(read_env_var COMPOSE_PROJECT_NAME "" "$env_file")"
  fi
  [[ -n "$name" ]] || name="$(knovas_compose_project_from_dir "$checkout")"
  export COMPOSE_PROJECT_NAME="$name"
}
