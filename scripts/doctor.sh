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
if [[ -n "$ADMIN_PW" ]]; then
  ok "PLATFORM_ADMIN_PASSWORD is set (nothing written to disk)"
else
  echo "  note PLATFORM_ADMIN_PASSWORD is empty — only relevant on a database with no"
  echo "       accounts, where a password is generated into the bootstrap file instead."
fi

head_ "Cortex (Wissensgraph)"
# Branch on the source first. The fixture and its mount are simply not used in
# graph mode, so checking them there produces a FAIL for a file that is supposed
# to be absent — a diagnostic that invents work is worse than none.
CORTEX_SRC="$(env_in_app ONTOLOGY_SOURCE)"
CORTEX_SRC="$(printf '%s' "${CORTEX_SRC:-fixture}" | tr '[:upper:]' '[:lower:]')"
echo "  ONTOLOGY_SOURCE = $CORTEX_SRC"
if [[ "$CORTEX_SRC" == "graph" ]]; then
  ok "types and entities are stored by Knovas, so curation persists without a local file"
  echo "       the fixture and its /mnt/ontology mount are unused in this mode"
  echo "       whether the graph is actually reachable is checked under 'Knovas API' below"
  warn "type-level relations are refused in graph mode by design (ontology_graph.py:373)"
  echo "       entity-level relations work normally"
else
  FIXTURE="$(env_in_app ONTOLOGY_FIXTURE_PATH)"
  if [[ -z "$FIXTURE" ]]; then
    bad "ONTOLOGY_FIXTURE_PATH is not set — edits are kept in memory only and vanish on reload."
    echo "       Set ONTOLOGY_FIXTURE_PATH=/mnt/ontology/ontology_fixture.json in knovas.env,"
    echo "       or switch to the real thing with ONTOLOGY_SOURCE=graph."
  else
    echo "  ONTOLOGY_FIXTURE_PATH = $FIXTURE"
    if ! "${DC[@]}" exec -T docbridge-web test -f "$FIXTURE" 2>/dev/null; then
      bad "that file does not exist in the container — the /mnt/ontology mount is missing."
      echo "       git pull, then ${DC[*]} up -d --force-recreate docbridge-web"
    elif "${DC[@]}" exec -T docbridge-web test -w "$FIXTURE" 2>/dev/null; then
      ok "fixture exists and is writable — new types and entities survive a reload"
    else
      bad "fixture is NOT writable — the UI reports success and the change is lost on reload."
      echo "       The mount must not be :ro. Check docker-compose.yml."
    fi
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

head_ "Knovas API (mTLS)"
# Probed from inside the container, with the same certificates the app uses, so
# a pass here means the app's own calls can get out. Search and graph-mode Cortex
# both ride this path, which is why one broken cert looks like two broken
# features.
"${DC[@]}" exec -T docbridge-web python - <<'PY' 2>&1 | sed 's/^/  /'
import json, os, ssl, urllib.error, urllib.request

base = (os.environ.get("SEMANTIX_API_URL") or "").rstrip("/")
cert = os.environ.get("SEMANTIX_CLIENT_CERT", "")
key = os.environ.get("SEMANTIX_CLIENT_KEY", "")
ca = os.environ.get("SEMANTIX_CA_CERT", "")

if not base.startswith("http"):
    raise SystemExit("FAIL SEMANTIX_API_URL is not set — nothing to reach.")
print(f"base = {base}")
for label, path in (("certificate", cert), ("key", key), ("CA", ca)):
    mark = "ok" if path and os.path.isfile(path) else "MISSING"
    print(f"{mark:>7}  {label}: {path or '<unset>'}")

ctx = ssl.create_default_context(cafile=ca or None)
if cert and key and os.path.isfile(cert) and os.path.isfile(key):
    ctx.load_cert_chain(cert, key)

def probe(path):
    try:
        with urllib.request.urlopen(base + path, context=ctx, timeout=15) as r:
            return r.status, r.read(400)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(400)
    except Exception as exc:
        return None, str(exc).encode()

code, body = probe("/secured/health")
if code == 200:
    print("     OK  /secured/health — mTLS works, the tenant answers")
else:
    print(f"   FAIL  /secured/health -> {code}: {body[:200]!r}")
    print("         Search cannot work until this does. Check the certs above and SEMANTIX_API_URL.")

code, body = probe("/secured/graph")
if code == 200:
    print("     OK  /secured/graph — Knowledge Graph is enabled; ONTOLOGY_SOURCE=graph will work")
elif code == 404:
    try:
        detail = json.loads(body or b"{}")
    except ValueError:
        detail = {}
    if detail.get("error_code") == "knowledge_graph_disabled":
        print("   FAIL  Knowledge Graph is NOT enabled for this tenant "
              "(error_code knowledge_graph_disabled).")
        print("         ONTOLOGY_SOURCE=graph cannot work — ask Knovas to enable it.")
    else:
        print(f"   WARN  /secured/graph -> 404: {body[:200]!r}")
else:
    print(f"   WARN  /secured/graph -> {code}: {body[:200]!r}")

src = (os.environ.get("ONTOLOGY_SOURCE") or "fixture").strip().lower()
print(f"ONTOLOGY_SOURCE = {src}"
      + ("  (entities live in Knovas; the fixture mount is unused)" if src == "graph"
         else "  (local JSON fixture)"))
if src == "graph":
    print("     note  type-level relations are refused in graph mode by design "
          "(ontology_graph.py:373) — entity-level relations work")
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
