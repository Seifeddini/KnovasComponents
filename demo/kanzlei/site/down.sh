#!/usr/bin/env bash
# Stop and remove the site containers; the volumes stay. --volumes also deletes them: domain, share and database.
. "$(dirname "$0")/_lib.sh"
load_env
args=(down --remove-orphans)
if [ "${1:-}" = --volumes ] || [ "${1:-}" = -v ]; then
  echo "down: removing volumes demo-kanzlei_dc-state (domain), demo-kanzlei_share (files), demo-kanzlei_sql-data (database)"
  args+=(--volumes)
fi
compose --profile load --profile tools "${args[@]}"
