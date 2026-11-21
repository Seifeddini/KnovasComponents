#!/usr/bin/env bash
# Create .env: host paths of the generated data plus random passwords. Re-running keeps every existing password and
# only adds missing keys (e.g. a new service account). Usage: ./init-site.sh <site-dir> <share-dir>
. "$(dirname "$0")/_lib.sh"
site=${1:-${DEMO_SITE_DIR:-}}; share=${2:-${DEMO_SHARE_DIR:-}}
[ -n "$site" ] && [ -n "$share" ] || { echo "usage: $0 <site-dir with directory.json> <share-dir>" >&2; exit 2; }
[ -f "$site/directory.json" ] || { echo "no directory.json in $site" >&2; exit 2; }
touch .env && chmod 600 .env

# 21 random letters/digits plus one upper, one lower, one digit (AD and SQL Server complexity rules). No symbols, so
# the values are safe in .env, compose and shell.
rand_pw() {
  local a b; a=$(head -c 64 /dev/urandom | base64 | tr -dc 'A-Za-z0-9'); b=$(head -c 96 /dev/urandom | base64)
  local up=${b//[^A-Z]/} lo=${b//[^a-z]/} di=${b//[^0-9]/}
  echo "${a:0:21}${up:0:1}${lo:0:1}${di:0:1}"
}
add_pw() { grep -q "^$1=" .env || { echo "$1=$(rand_pw)" >> .env; echo "  added $1"; }; }

set_env DEMO_SITE_DIR "$(host_path "$site")"
set_env DEMO_SHARE_DIR "$(host_path "$share")"
grep -q '^DEMO_PUBLISH_PORTS=' .env || echo 'DEMO_PUBLISH_PORTS=0' >> .env
for k in PW_DOMAIN_ADMIN PW_SA PW_PEOPLE PW_SQL_KNOVAS_RO PW_SQL_PRAXIS_APP; do add_pw "$k"; done
# One password per service account ("service": true): svc-knovas -> PW_SVC_KNOVAS. Flat user objects assumed.
for sam in $(tr -d '\r\n\t ' < "$site/directory.json" | sed 's/},{/}\n{/g' | grep '"service":true' \
             | grep -o '"sam":"[^"]*"' | cut -d'"' -f4); do
  add_pw "PW_$(echo "$sam" | tr 'a-z' 'A-Z' | tr -c 'A-Z0-9\n' '_')"
done
echo ".env ready with $(grep -c '^PW_' .env) passwords. It is gitignored: never commit it."
