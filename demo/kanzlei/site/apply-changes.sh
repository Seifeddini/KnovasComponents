#!/usr/bin/env bash
# Play one set of changes on the running, loaded site: a working day in the database and on the share.
# Usage: ./apply-changes.sh <changes-dir>     (the build's <work>/site/changes/tag-1: db.sql, share/, delete.txt)
# Afterwards the site equals <work>/truth/changes/tag-1 (check-records.sh and check-share.sh take that folder).
# Apply a set once. To go back to the build: ./load.sh
. "$(dirname "$0")/_lib.sh"
[ -f "${1:-}/db.sql" ] || { echo "usage: $0 <changes-dir with db.sql>" >&2; exit 2; }
changes=$(host_path "$1")
load_env
compose up -d --wait --wait-timeout 600 dc sql >/dev/null 2>&1 || { echo "FAIL  dc/sql not healthy"; exit 1; }
compose --profile load run --rm -T -v "$changes:/in/changes:ro" --entrypoint /opt/site/apply_changes.sh loader
echo "changes: applying acl.json"
compose exec -T dc python3 /opt/site/apply_acls.py < "$DEMO_SITE_DIR/acl.json"
