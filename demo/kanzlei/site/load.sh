#!/usr/bin/env bash
# Load the generated data into the site: empty the share volume and drop the database, copy the share tree (mtimes
# kept), run db/*.sql, create the SQL logins, then apply acl.json inside dc. Re-runnable.
# Data folders come from .env; to load other data, set DEMO_SITE_DIR / DEMO_SHARE_DIR in the environment (saved).
. "$(dirname "$0")/_lib.sh"
for v in DEMO_SITE_DIR DEMO_SHARE_DIR; do
  if [ -n "${!v:-}" ]; then set_env "$v" "$(host_path "${!v}")"; fi
done
load_env
t0=$SECONDS
compose up -d --wait --wait-timeout 600 dc sql
compose --profile load run --rm -T loader load
echo "load: applying acl.json"
compose exec -T dc python3 /opt/site/apply_acls.py < "$DEMO_SITE_DIR/acl.json"
echo "load: done in $((SECONDS - t0)) s"
