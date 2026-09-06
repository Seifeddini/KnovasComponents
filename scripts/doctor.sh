#!/usr/bin/env bash
# Report what the running deployment actually looks like, in one pass.
#
# Written after an install where each fault was found one round-trip at a time:
# a setting that never reached the container, a mount that was not there, a role
# that was never granted. Each was one command away from being obvious; the cost
# was not knowing which command. This runs all of them.
#
# Read-only. It changes nothing and is safe on a running system.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
KNOVAS_ENV="$ROOT_DIR/knovas.env"
DC=(docker compose --env-file "$KNOVAS_ENV")

ok()   { printf '  \033[32mOK\033[0m   %s\n' "$*"; }
warn() { printf '  \033[33mWARN\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$*"; }
head_() { printf '\n\033[1m%s\033[0m\n' "$*"; }

[[ -f "$KNOVAS_ENV" ]] || { echo "Missing knovas.env — run ./scripts/setup.sh" >&2; exit 1; }

head_ "Containers"
"${DC[@]}" ps --format '  {{.Name}}\t{{.State}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null \
  || bad "docker compose ps failed"

running() { "${DC[@]}" ps --status running "$1" 2>/dev/null | grep -q "$1"; }
if ! running docbridge-web; then
  bad "docbridge-web is not running — the rest of this report needs it."
  echo "       ${DC[*]} logs --tail 50 docbridge-web"
  exit 1
fi

env_in_app() { "${DC[@]}" exec -T docbridge-web printenv "$1" 2>/dev/null | tr -d '\r'; }

head_ "Settings as the app actually sees them"
BIND="$("${DC[@]}" ps --format '{{.Ports}}' docbridge-web-nginx 2>/dev/null | head -1)"
echo "  published: ${BIND:-<none>}"
COOKIE="$(env_in_app WEB_SESSION_COOKIE_SECURE)"
case "${COOKIE:-true}" in
  false|False|0|no) ok "WEB_SESSION_COOKIE_SECURE=$COOKIE (plain HTTP will keep a session)" ;;
  *) if [[ "$BIND" == *"127.0.0.1"* ]]; then
       ok "WEB_SESSION_COOKIE_SECURE=${COOKIE:-<unset, defaults true>} (localhost only)"
     else
       bad "WEB_SESSION_COOKIE_SECURE=${COOKIE:-<unset, defaults true>} but the UI is published beyond localhost."
       echo "       Over plain HTTP the browser drops the session cookie: login succeeds, then bounces"
       echo "       back to the form. Set WEB_SESSION_COOKIE_SECURE=false in knovas.env, or put TLS in front."
     fi ;;
esac
for v in PLATFORM_ADMIN_EMAIL PLATFORM_ADMIN_BOOTSTRAP_PATH IDENTITY_ACCOUNT_LOCKOUT_ATTEMPTS SEMANTIX_CUSTOMER_ID; do
  echo "  $v = $(env_in_app "$v")"
done
ADMIN_PW="$(env_in_app PLATFORM_ADMIN_PASSWORD)"
[[ -n "$ADMIN_PW" ]] && ok "PLATFORM_ADMIN_PASSWORD is set (nothing written to disk)" \
                     || warn "PLATFORM_ADMIN_PASSWORD is empty — a generated password is used instead"

head_ "Cortex (Wissensgraph)"
FIXTURE="$(env_in_app ONTOLOGY_FIXTURE_PATH)"
if [[ -z "$FIXTURE" ]]; then
  bad "ONTOLOGY_FIXTURE_PATH is not set — edits are kept in memory only and vanish on reload."
  echo "       Set ONTOLOGY_FIXTURE_PATH=/mnt/ontology/ontology_fixture.json in knovas.env."
else
  echo "  ONTOLOGY_FIXTURE_PATH = $FIXTURE"
  if ! "${DC[@]}" exec -T docbridge-web test -f "$FIXTURE" 2>/dev/null; then
    bad "that file does not exist in the container — the /mnt/ontology mount is missing."
    echo "       git pull, then ${DC[*]} up -d --force-recreate docbridge-web"
  elif "${DC[@]}" exec -T docbridge-web test -w "$FIXTURE" 2>/dev/null; then
    ok "fixture exists and is writable — new types and entities will survive a reload"
  else
    bad "fixture is NOT writable — the UI reports success and the change is lost on reload."
    echo "       The mount must not be :ro. Check docker-compose.yml."
  fi
fi

head_ "Accounts"
"${DC[@]}" exec -T docbridge-web python - <<'PY' 2>/dev/null || bad "could not query the identity database"
from identity import db
conn = db.connect()
rows = conn.execute("""
    SELECT u.email, u.status, u.must_change_password, u.failed_attempts, u.locked_until,
           COALESCE(string_agg(r.key, ',' ORDER BY r.key), '-')
    FROM users u
    LEFT JOIN user_roles ur ON ur.user_id = u.id
    LEFT JOIN roles r ON r.id = ur.role_id
    GROUP BY u.id, u.email, u.status, u.must_change_password, u.failed_attempts, u.locked_until
    ORDER BY u.email
""").fetchall()
if not rows:
    print("  FAIL no accounts at all. Run ./scripts/admin-password.sh to create one.")
for email, status, must_change, fails, locked, roles in rows:
    flags = []
    if must_change: flags.append("must-change-password")
    if locked:      flags.append(f"LOCKED until {locked:%H:%M}")
    if fails:       flags.append(f"{fails} failed")
    mark = "OK  " if "admin" in roles.split(",") else "WARN"
    print(f"  {mark} {email}  status={status}  roles={roles}  {' '.join(flags)}")
    if "admin" not in roles.split(","):
        print("       No 'admin' role, so the Verwaltung console is hidden and would 403.")
        print("       Fix: ./scripts/admin-password.sh --grant-admin " + email)
PY

head_ "Reachable"
PORT="$(grep -E '^[[:space:]]*DOCBRIDGE_WEB_PORT=' "$KNOVAS_ENV" | tail -1 | cut -d= -f2- | tr -d '[:space:]')"
PORT="${PORT:-8081}"
code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "http://127.0.0.1:${PORT}/api/stats" 2>/dev/null)"
case "$code" in
  200) ok "the app answers through nginx (HTTP $code)" ;;
  502|504) bad "nginx is up but cannot reach the app (HTTP $code) — ${DC[*]} logs --tail 30 docbridge-web" ;;
  *)   warn "unexpected status from /api/stats: ${code:-no response}" ;;
esac

printf '\n'
