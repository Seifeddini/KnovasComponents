#!/usr/bin/env bash
# Read KEY=value from .env without shell-sourcing (safe for unquoted spaces).

read_env_var() {
  local key="$1"
  local default="${2:-}"
  local env_file="${3:-.env}"
  if [[ ! -f "$env_file" ]]; then
    printf '%s' "$default"
    return 0
  fi
  local line
  line="$(grep -E "^[[:space:]]*${key}=" "$env_file" | tail -1 || true)"
  if [[ -z "$line" ]]; then
    printf '%s' "$default"
    return 0
  fi
  local val="${line#*=}"
  val="${val#"${val%%[![:space:]]*}"}"
  val="${val%"${val##*[![:space:]]}"}"
  if [[ "$val" == \"*\" && "$val" == *\" ]]; then
    val="${val:1:${#val}-2}"
  elif [[ "$val" == \'*\' && "$val" == *\' ]]; then
    val="${val:1:${#val}-2}"
  fi
  printf '%s' "$val"
}
