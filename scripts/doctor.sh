#!/usr/bin/env bash
# Report what the running deployment actually looks like, in one pass.
#
# Written after an install where each fault was found one round-trip at a time:
# a setting that never reached the container, a mount that was not there, a role
# that was never granted. Each was one command away from being obvious; the cost
# was not knowing which command. This runs all of them.
#
# Read-only. It changes nothing and is safe on a running system. With the shared
# company login it also signs in and searches once, the way a person would: a
# stack can pass every probe and still turn each search away, and only a real
# search shows whether the files it returns can be previewed and opened.
#
# Usage: ./scripts/doctor.sh ["search words"]
#   Words that are certainly in the firm's documents. Default: Rechnung.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
KNOVAS_ENV="$ROOT_DIR/knovas.env"
# shellcheck source=lib/stack_identity.sh
source "$ROOT_DIR/scripts/lib/stack_identity.sh"
knovas_load_compose_project "$KNOVAS_ENV" "$ROOT_DIR"
DC=(docker compose --env-file "$KNOVAS_ENV")
QUERY="${*:-Rechnung}"

ok()   { printf '  \033[32mOK\033[0m   %s\n' "$*"; }
warn() { printf '  \033[33mWARN\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$*"; }
head_() { printf '\n\033[1m%s\033[0m\n' "$*"; }

[[ -f "$KNOVAS_ENV" ]] || { echo "Missing knovas.env — run ./scripts/setup.sh" >&2; exit 1; }

# Everything below is copied into REPORT as it is printed, so the verdict at the
# end counts what was actually said -- including the FAIL lines the Python probes
# print themselves, which bad() never sees.
REPORT="$(mktemp)"
trap 'rm -f "$REPORT"' EXIT
{
head_ "Containers"
"${DC[@]}" ps --format '  {{.Name}}\t{{.State}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null \
  || bad "docker compose ps failed"

running() { "${DC[@]}" ps --status running "$1" 2>/dev/null | grep -q "$1"; }

# "unhealthy" on its own says nothing about why. Docker keeps the last few
# probe outputs; print them rather than making the operator go find them.
for svc in docbridge-web docbridge-web-nginx platform-db; do
  cid="$("${DC[@]}" ps -q "$svc" 2>/dev/null | head -1)"
  [[ -n "$cid" ]] || continue
  state="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$cid" 2>/dev/null)"
  [[ "$state" == "unhealthy" ]] || continue
  bad "$svc is unhealthy — last probe output:"
  docker inspect -f '{{range .State.Health.Log}}{{.ExitCode}}|{{.Output}}{{end}}' "$cid" 2>/dev/null \
    | tail -c 600 | sed 's/^/       /'
done

# nginx resolves the app once at startup unless it is running the config with a
# resolver directive. A recreate of docbridge-web alone leaves an older nginx
# pointing at an address nothing answers on, which reads as "search broke".
web_started="$(docker inspect -f '{{.State.StartedAt}}' "$("${DC[@]}" ps -q docbridge-web 2>/dev/null | head -1)" 2>/dev/null)"
ngx_started="$(docker inspect -f '{{.State.StartedAt}}' "$("${DC[@]}" ps -q docbridge-web-nginx 2>/dev/null | head -1)" 2>/dev/null)"
# ...unless its config carries a `resolver` directive, which is exactly what the
# shipped one does: the upstream sits in a variable so nginx re-resolves per
# request. Warning there sends an operator to recreate a container that is
# already correct, which costs a round trip and buys nothing.
if [[ -n "$web_started" && -n "$ngx_started" && "$ngx_started" < "$web_started" ]] \
   && ! "${DC[@]}" exec -T docbridge-web-nginx \
        grep -qs resolver /etc/nginx/conf.d/default.conf; then
  warn "nginx started BEFORE docbridge-web, so it may hold a stale address for it."
  echo "       Recreate it too: ${DC[*]} up -d --force-recreate docbridge-web-nginx"
fi


if ! running docbridge-web; then
  bad "docbridge-web is not running — the rest of this report needs it."
  echo "       ${DC[*]} logs --tail 50 docbridge-web"
  exit 1
fi

env_in_app() { "${DC[@]}" exec -T docbridge-web printenv "$1" 2>/dev/null | tr -d '\r'; }

# The same reading config_loader.get_bool gives these switches: unset is on.
flag_on() {
  case "$(printf '%s' "${1:-true}" | tr '[:upper:]' '[:lower:]')" in
    true|yes|1|on) return 0 ;;
    *) return 1 ;;
  esac
}
IDENT_ON=true
flag_on "$(env_in_app IDENTITY_ENABLED)" || IDENT_ON=false
CORTEX_ON=true
flag_on "$(env_in_app CORTEX_ENABLED)" || CORTEX_ON=false

# The prefix RemoteController puts in front of every pointer it sends, asked of
# RemoteController itself so the answer follows its rules and not a copy of them.
# There are two sources: a sync request somebody saved (the console), or -- with
# none saved -- the automatic sync the unified stack starts, whose request is
# built from KNOVAS_IDENTIFIER_PREFIX. The second is every firm without the admin
# console, and reading only the saved request left those at "cannot compare"
# for good.
RC_UP=false
running remote-controller && RC_UP=true
RC_PREFIX=""
RC_PREFIX_FROM=""
if [[ "$RC_UP" == true ]]; then
  rc_line="$("${DC[@]}" exec -T -e PYTHONWARNINGS=ignore remote-controller python - 2>/dev/null <<'PY' | tail -1
from config import get_config
from sync.default_sync_body import build_default_sync_body
from sync.sync_scheduler import load_last_sync_body


def prefix_of(body):
    ingestion = (body or {}).get("ingestion") or {}
    # rc-sync is what the uploader falls back to (knovas_uploader.py).
    return str(ingestion.get("identifier_prefix") or "rc-sync").strip().strip("/")


cfg = get_config()
saved = load_last_sync_body()
if saved:
    print(f"{prefix_of(saved)}\tsaved sync request")
elif cfg.rc_sync_auto_start_continuous and not cfg.rc_sync_auto_start_requires_saved_body:
    print(f"{prefix_of(build_default_sync_body(cfg))}\tautomatic sync, KNOVAS_IDENTIFIER_PREFIX")
PY
  )"
  if [[ "$rc_line" == *$'\t'* ]]; then
    RC_PREFIX="${rc_line%%$'\t'*}"
    RC_PREFIX_FROM="${rc_line#*$'\t'}"
  fi
fi

head_ "Image freshness"
# Templates, JS and Python are COPYed into the image, so `up -d --force-recreate`
# restarts the same code and a pull appears to change nothing. Only the compose
# file itself takes effect without a rebuild, which is the worst kind of
# half-applied: some of what you just pulled is running and some is not.
IMG_ID="$("${DC[@]}" images -q docbridge-web 2>/dev/null | head -1)"
if [[ -n "$IMG_ID" ]]; then
  IMG_TS="$(docker inspect -f '{{.Created}}' "$IMG_ID" 2>/dev/null | cut -c1-19)"
  IMG_EPOCH="$(date -d "${IMG_TS/T/ }" +%s 2>/dev/null || echo 0)"
  SRC_DIR="$ROOT_DIR/KnovasPlatform/components/docbridge_integration/src"
  NEWEST="$(find "$SRC_DIR" -type f -newermt "@$IMG_EPOCH" 2>/dev/null | head -5)"
  if [[ "$IMG_EPOCH" != "0" && -n "$NEWEST" ]]; then
    bad "the running image is older than files in src/ — those changes are NOT running."
    echo "       Rebuild: ./scripts/start.sh   (--force-recreate alone reuses the old image)"
    echo "       Newer than the image, for example:"
    printf '       %s\n' $(echo "$NEWEST" | sed "s|$ROOT_DIR/||") | head -5
  else
    ok "image built $IMG_TS — no source file is newer"
  fi
fi

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
# The administrator settings only mean something with personal accounts. With the
# company login PLATFORM_ADMIN_EMAIL is still demanded by compose and then never
# read, and printing it here made it look like an account that exists.
if [[ "$IDENT_ON" == true ]]; then
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
else
  echo "  SEMANTIX_CUSTOMER_ID = $(env_in_app SEMANTIX_CUSTOMER_ID)"
fi

head_ "Cortex (Wissensgraph)"
if [[ "$CORTEX_ON" == false ]]; then
  # Switched off, its data source is never read: a missing fixture there is
  # not a fault, and reporting one sent operators to fix a feature nobody uses.
  ok "switched off (CORTEX_ENABLED=false) — its data source is not used, nothing to check"
else
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
      echo "       switch to the real thing with ONTOLOGY_SOURCE=graph, or turn Cortex off"
      echo "       with CORTEX_ENABLED=false if the firm does not use it."
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
fi

head_ "Sign-in and Cortex"
# Two switches an operator sets in knovas.env and then cannot see the effect of
# without opening the app. Both are read from the container so what is reported
# is what the app actually got, not what the file says.
if [[ "$CORTEX_ON" == false ]]; then
  ok "Cortex is switched off — not in the navigation, and its routes refuse"
else
  echo "  Cortex is on (CORTEX_ENABLED=false takes it out of the navigation)"
fi
if [[ "$IDENT_ON" == false ]]; then
  SHARED="$(env_in_app COMPANY_LOGIN_NAME)"
  if [[ -n "$SHARED" ]]; then
    ok "one shared company login ($SHARED) — per-user accounts are off"
    echo "       The audit record says \"the company\", not who, and everyone who"
    echo "       signs in can open every document the tenant holds."
  else
    bad "IDENTITY_ENABLED=false but no COMPANY_LOGIN_NAME reached the app — nobody can sign in."
    echo "       Set COMPANY_LOGIN_NAME and COMPANY_LOGIN_PASSWORD in knovas.env, or"
    echo "       drop IDENTITY_ENABLED to go back to per-user accounts."
  fi
else
  ok "per-user accounts"
fi

head_ "Accounts"
if [[ "$IDENT_ON" == false ]]; then
  # No personal accounts exist with the company login, and the Platform never
  # creates the tables for them -- so the query below can only fail, and used to
  # report that as a fault on a perfectly healthy shared-login firm.
  echo "  none to check — the company login keeps no personal accounts"
else
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
fi

head_ "Search result snippets"
# The text under a result title does not come from the API: /secured/query
# returns match locations without chunk text (knovas_client.py:738). It is read
# from a sidecar in the context store, and context_store.load_context returns
# None when that directory is absent — silently, so results render with a title
# and nothing beneath it and no error anywhere.
STORE="$(env_in_app SEARCH_CONTEXT_STORE_PATH)"
if [[ -z "$STORE" ]]; then
  warn "SEARCH_CONTEXT_STORE_PATH is unset — snippets fall back to /mnt/autodoc/.search_context"
  STORE="/mnt/autodoc/.search_context"
fi
echo "  SEARCH_CONTEXT_STORE_PATH = $STORE"
if ! "${DC[@]}" exec -T docbridge-web test -d "$STORE" 2>/dev/null; then
  bad "that directory does not exist in the container — every result will show a title and no text."
  echo "       Sidecars are written during ingestion. Either point this at an existing store,"
  echo "       e.g. SEARCH_CONTEXT_STORE_PATH=/mnt/autodoc/preview_storage in knovas.env,"
  echo "       or re-run ingestion so RemoteController fills the current one."
else
  COUNT="$("${DC[@]}" exec -T docbridge-web sh -c "find '$STORE' -type f 2>/dev/null | head -1000 | wc -l" 2>/dev/null | tr -d '[:space:]')"
  if [[ "${COUNT:-0}" -gt 0 ]]; then
    ok "context store holds ${COUNT} sidecar file(s) — snippets available"
  else
    bad "context store exists but is EMPTY — results will show a title and no text."
    echo "       A freshly created rc-state volume starts empty; the sidecars from an earlier"
    echo "       deployment are wherever that one wrote them. Point SEARCH_CONTEXT_STORE_PATH"
    echo "       at them, or re-run ingestion to repopulate this one."
  fi
fi

head_ "Documents, preview and opening"
# Search can work perfectly while every file-backed feature is dead, and the UI
# says nothing useful about why. The snippets under a result come from the
# context sidecars, keyed by the Knovas pointer; the thumbnail, the preview, the
# download and "Oeffnen" all come from the file itself, found by stripping the
# pointer prefix and joining the rest onto /mnt/autodoc. So a prefix that does
# not match, or a mount that holds a different corpus than the one that was
# ingested, leaves results that read correctly and cannot be opened or
# previewed. That pair of symptoms is what this section is for.
# PYTHONWARNINGS: importing the app imports requests, which warns about its own
# dependency versions on every run -- noise in the one place an operator reads.
"${DC[@]}" exec -T -e PYTHONWARNINGS=ignore -e "DOCTOR_RC_PREFIX=$RC_PREFIX" \
  -e "DOCTOR_RC_PREFIX_FROM=$RC_PREFIX_FROM" -e "DOCTOR_IDENTITY=$IDENT_ON" -e "DOCTOR_RC_UP=$RC_UP" \
  docbridge-web python - <<'PY' 2>&1 | sed 's/^/  /'
import json
import os

from web_interface.app import (
    _autodoc_identifier_prefixes,
    _confine_to_autodoc,
    _rel_path_for_autodoc,
)

root = os.environ.get("AUTODOC_PATH") or "/mnt/autodoc"
print(f"mount = {root}")

if not os.path.isdir(root):
    raise SystemExit(
        "   FAIL  that directory does not exist in the container.\n"
        "         KNOVAS_DOCUMENTS_PATH is not mounted. Check it in knovas.env,\n"
        "         then ./scripts/setup.sh && ./scripts/start.sh"
    )

sample = None
files = 0
for current, dirs, names in os.walk(root):
    dirs[:] = [d for d in dirs if not d.startswith(".")]
    for name in names:
        if name.startswith("."):
            continue
        files += 1
        if sample is None:
            sample = os.path.relpath(os.path.join(current, name), root)
    if files >= 200:
        break

if not files:
    print("   FAIL  the mount is EMPTY — no thumbnail, no preview, no open, for any result.")
    print("         KNOVAS_DOCUMENTS_PATH points at the wrong folder, or the corpus was")
    print("         never generated there. It must be the same folder the documents were")
    print("         ingested from, not its parent and not a sibling.")
else:
    print(f"     OK  the mount holds documents (counted {files}, stopped early)")

# What the app strips, against what RemoteController actually sent. The saved
# request body is the ground truth: the console's "Kennung" field is free text
# with no default, so a profile saved with anything other than
# AUTODOC_IDENTIFIER_PREFIX silently breaks every file lookup.
app_prefixes = _autodoc_identifier_prefixes()
print(f"AUTODOC_IDENTIFIER_PREFIX = {','.join(app_prefixes) or '<unset>'}")

# Asked of RemoteController itself where it is running (see the top of
# doctor.sh), which also covers the automatic sync that never saves a request.
# The file is the fallback for a RemoteController that is down: it still says
# what the last ingestion was sent as.
rc_prefix = (os.environ.get("DOCTOR_RC_PREFIX") or "").strip("/")
rc_prefix_from = os.environ.get("DOCTOR_RC_PREFIX_FROM") or ""
body_path = "/var/rc-state/.rc-sync-last-request.json"
if not rc_prefix:
    try:
        with open(body_path, encoding="utf-8") as handle:
            body = json.load(handle)
        rc_prefix = str((body.get("ingestion") or {}).get("identifier_prefix") or "").strip("/")
        rc_prefix_from = "saved sync request"
    except (OSError, ValueError, AttributeError):
        rc_prefix = ""
if not rc_prefix and os.environ.get("DOCTOR_RC_UP") != "true":
    print("   WARN  RemoteController is not running, so the prefix it ingests with cannot be")
    print("         compared — see the RemoteController section below.")
elif not rc_prefix:
    print("   WARN  RemoteController has no sync request and no automatic sync, so nothing")
    print("         is being ingested and the prefix cannot be compared.")
    if os.environ.get("DOCTOR_IDENTITY") == "true":
        print("         Save one in Verwaltung -> Übernahme.")
    else:
        print("         The unified stack starts one by itself: re-run ./scripts/setup.sh and")
        print("         ./scripts/start.sh, then check the RemoteController section below.")
elif not app_prefixes:
    print(f"   FAIL  RemoteController ingests as '{rc_prefix}/…' and the app strips nothing.")
    print(f"         Every pointer resolves to {root}/{rc_prefix}/… which does not exist.")
    print(f"         Set KNOVAS_IDENTIFIER_PREFIX={rc_prefix} in knovas.env, then setup + start.")
elif not any(p.lower() == rc_prefix.lower() for p in app_prefixes):
    print(f"   FAIL  prefix mismatch: RemoteController ingests as '{rc_prefix}/…', the app")
    print(f"         strips '{','.join(app_prefixes)}'. Results still carry text, because the")
    print("         snippets come from the sidecars — but no file is ever found, so the")
    print("         thumbnail, the preview and Öffnen all fail on every hit.")
    if rc_prefix_from == "saved sync request":
        print(f"         Set KNOVAS_IDENTIFIER_PREFIX={rc_prefix} in knovas.env, then setup + start,")
        print("         or change the Kennung on the profile in Verwaltung -> Übernahme to")
        print(f"         '{app_prefixes[0]}' and re-ingest.")
    else:
        print("         Both come from KNOVAS_IDENTIFIER_PREFIX in knovas.env: set it to the")
        print("         prefix the documents already in Knovas carry, then setup + start.")
else:
    print(f"     OK  the app strips the prefix RemoteController ingests with ('{rc_prefix}')")

# The decisive check: one real file, through the code the endpoints use.
if sample:
    pointer = f"{rc_prefix}/{sample}" if rc_prefix else sample
    resolved = _confine_to_autodoc(root, pointer)
    if resolved and os.path.exists(resolved):
        print(f"     OK  a pointer resolves to a file on disk ({_rel_path_for_autodoc(pointer)})")
    else:
        print(f"   FAIL  the pointer '{pointer}' does not resolve to a file.")
        print(f"         The app looks for {resolved or '<refused>'}.")
        print("         /preview, /thumbnail, /download and /client-path all answer 404 for it.")
PY

# Öffnen is a client-side launch: the browser asks for the path THIS PC should
# use for the same file. Without a mapping there is no such path and the
# endpoint answers 503 — on every click, for every document, whether or not the
# file is on the mount. A deployment whose documents live only on this server's
# local disk has nothing to map, and needs the download fallback instead.
UNC_ROOT="$(env_in_app OPEN_UNC_ROOT)"
CLIENT_ROOT="$(env_in_app OPEN_CLIENT_LOCAL_ROOT)"
DEGRADED="$(env_in_app OPEN_ALLOW_DEGRADED_DOWNLOAD_OPEN)"
echo "  OPEN_UNC_ROOT = ${UNC_ROOT:-<unset>}"
echo "  OPEN_CLIENT_LOCAL_ROOT = ${CLIENT_ROOT:-<unset>}"
if [[ -n "$UNC_ROOT" || -n "$CLIENT_ROOT" ]]; then
  ok "Öffnen has a client path to hand out"
  echo "       It still only works if the user's own PC can reach that path."
else
  case "${DEGRADED:-false}" in
    true|True|1|yes|Yes)
      warn "no share mapping — Öffnen answers 503, but the Download button is available."
      echo "       The preview dialog offers Download instead, which streams the file"
      echo "       through the browser and needs nothing on the user's PC." ;;
    "false"|"False"|"0"|"no")
      bad "no way to open a document at all: no share to point at, and the"
      echo "       download refused by OPEN_ALLOW_DEGRADED_DOWNLOAD_OPEN=$DEGRADED."
      echo "       Öffnen launches the file on the USER'S PC, so it needs a path that PC has:"
      echo "         KNOVAS_SHARE_UNC=\\\\fileserver\\share    (Windows clients on a share), or"
      echo "         OPEN_CLIENT_LOCAL_ROOT=/mnt/kanzlei      (the path the CLIENT PC mounts it at)"
      echo "       Or drop OPEN_ALLOW_DEGRADED_DOWNLOAD_OPEN and the download comes back." ;;
    *)
      ok "no share configured, so Öffnen hands over the file itself (download)."
      echo "       That is the default where documents live only on this server: the"
      echo "       browser downloads instead of starting the file from a share."
      echo "       A share would open it in place instead — KNOVAS_SHARE_UNC (Windows)"
      echo "       or OPEN_CLIENT_LOCAL_ROOT (clients that mount it themselves)." ;;
  esac
fi

# The wall on those same routes. It is decided here, from what each person's own
# search returned -- not by asking the Secure API, whose document_readable route
# does not exist in any deployed version and answered 404 for every document.
# With the company login there is no person to hold a grant and the gate stands
# aside (app.py, require_readable_document), so the store stays empty for good
# and warning about it would be a permanent false alarm.
if [[ "$IDENT_ON" == true ]]; then
  GRANTS="$("${DC[@]}" exec -T docbridge-web sh -c '
  python - <<PYEOF 2>/dev/null
import os, sqlite3
path = os.environ.get("OPEN_GRANT_STORE_PATH") or "/app/data/document_grants.sqlite3"
if not os.path.exists(path):
    print("none"); raise SystemExit
conn = sqlite3.connect(path)
print(conn.execute("SELECT count(*) FROM document_grants").fetchone()[0])
PYEOF' 2>/dev/null | tr -d "[:space:]")"
  case "$GRANTS" in
    ""|none)
      warn "no document grants recorded yet — nobody has searched since the last start."
      echo "       A preview or Öffnen before the first search answers 404 by design:"
      echo "       the file routes serve what that person's own search returned." ;;
    0)
      warn "the grant store is empty. Previews will 404 until someone searches." ;;
    *)
      ok "$GRANTS document grant(s) recorded — previews and Öffnen have something to serve" ;;
  esac
fi

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

# The graph serves Cortex and nothing else; with Cortex switched off its state
# cannot affect the firm, so it is not probed.
if (os.environ.get("CORTEX_ENABLED") or "true").strip().lower() not in ("true", "yes", "1", "on"):
    raise SystemExit(0)

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

head_ "RemoteController"
# Whether anything new reaches the index, and whether the text under a result
# exists at all: RemoteController writes the context sidecars as it reads each
# file. Every section above passes while it stands still.
if [[ "$RC_UP" != true ]]; then
  bad "remote-controller is not running — nothing new is ingested and no snippet text is written."
  echo "       ${DC[*]} logs --tail 50 remote-controller"
else
  if [[ -n "$RC_PREFIX" ]]; then
    echo "  ingests as '$RC_PREFIX/…' ($RC_PREFIX_FROM)"
  fi
  # Per-document upload failures do not change the state below: the cycle
  # finishes, counts them, and carries on. Its closing line is the one place
  # they add up -- and the one place a scan cut short by its cap is recorded.
  LAST_CYCLE="$("${DC[@]}" logs --no-log-prefix --tail 5000 remote-controller 2>/dev/null \
    | grep 'Sync cycle finished' | tail -1)"
  LAST_PAUSED="$(printf '%s' "$LAST_CYCLE" | sed -n 's/.*paused=\([a-z_]*\).*/\1/p')"
  "${DC[@]}" exec -T -e PYTHONWARNINGS=ignore -e "DOCTOR_LAST_PAUSED=$LAST_PAUSED" \
    -e "DOCTOR_ROOT_DIR=$ROOT_DIR" remote-controller python - <<'PY' 2>&1 | sed 's/^/  /'
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from config import get_config
from sync.sync_executor import _max_scan_entries_per_cycle


def get(path):
    try:
        with urllib.request.urlopen("http://127.0.0.1:5001" + path, timeout=15) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except ValueError:
            return exc.code, {}
    except Exception as exc:  # reported, not raised: this is a diagnosis
        return None, {"error": str(exc)}


code, health = get("/health")
if code is None:
    raise SystemExit(f"   FAIL  it does not answer on port 5001: {health.get('error')}")
checks = health.get("checks") or {}
if checks.get("watch_roots") == "ok":
    print("     OK  it can read the document share")
else:
    print("   FAIL  it cannot read the document share — check KNOVAS_DOCUMENTS_PATH")
if checks.get("config") != "ok":
    print("   FAIL  its configuration is incomplete — see its log")

# Read, not load_sync_config(): that one writes a default file when there is
# none, and this is a diagnosis.
try:
    sync_cfg = json.loads(Path(get_config().rc_sync_config_path).read_text(encoding="utf-8"))
except (OSError, ValueError):
    sync_cfg = {}
sequential = bool(sync_cfg.get("sequential_subfolders"))
state = str(checks.get("scheduler_state") or "unknown")
last_paused = os.environ.get("DOCTOR_LAST_PAUSED") or ""
window = sync_cfg.get("window") or {}
start, end = window.get("start_local") or "00:00", window.get("end_local") or "23:59"

# A cycle stops walking after max_scan_entries_per_cycle folders. Folder by
# folder (sequential_subfolders) it keeps its place and the next cycle goes on
# from there. Otherwise the next cycle starts again at the top of the share, so
# on a share with more folders than the cap the same first folders are read
# every time and the rest never: new documents there never reach the index,
# and nothing counts as an error.
if "scan_limit_reached" in (state, last_paused) and not sequential:
    fix = ("from sync.sync_config import load_sync_config as l, save_sync_config as s; "
           "c = l(); c['max_scan_entries_per_cycle'] = 0; s(c)")
    print(f"   FAIL  each cycle stops after {_max_scan_entries_per_cycle(sync_cfg)} folders and the next starts again at")
    print("         the top of the share, so the folders past that point are never read: new and")
    print("         changed documents there never reach the index. Lift the cap — the whole share")
    print("         is then read each cycle, backing off to hourly while nothing changes:")
    print(f'           cd "{os.environ.get("DOCTOR_ROOT_DIR") or "."}"')
    print("           docker compose --env-file knovas.env exec -T remote-controller python -c \\")
    print(f'             "{fix}"')
    print("           docker compose --env-file knovas.env restart remote-controller")
elif state in ("running", "backlog_pending", "idle_between_cycles", "completed"):
    print(f"     OK  the sync is active ({state})")
elif sequential and state in ("scan_limit_reached", "cycle_time_limit"):
    print(f"     OK  the sync works through the share folder by folder ({state});")
    print("         the next cycle goes on where this one stopped")
elif state == "subfolders_complete":
    print("   WARN  the folder-by-folder pass over the share is finished, and nothing more is")
    print("         read: new and changed files are not ingested. For a sync that keeps going,")
    print("         set sequential_subfolders to false and max_scan_entries_per_cycle to 0 in")
    print("         the sync config, then restart remote-controller.")
elif state == "rate_limited":
    print("   WARN  Knovas is rate-limiting the uploads; the next cycle carries on")
elif state == "stop_requested":
    print("   WARN  the sync was stopped by request — new and changed files are not ingested")
elif state == "paused_outside_window":
    # A window is a decision (a firm that syncs only at night), not a fault.
    print(f"     OK  the sync waits for its window, {start}–{end}; it goes on at {start}")
elif state == "awaiting_initial_sync_body":
    print("   WARN  the sync waits for a sync request — nothing is ingested until one is saved")
elif state in ("not_running", "disabled"):
    print(f"   WARN  the sync is not running ({state}) — new and changed files are not ingested")
else:
    print(f"   FAIL  the sync is in state '{state}'")

# The window is read on RemoteController's clock, which is UTC unless
# RC_TIMEZONE says otherwise -- "20:00" is then 22:00 in Zurich in summer.
if (start, end) != ("00:00", "23:59"):
    if get_config().rc_timezone:
        print(f"         sync window {start}–{end}, {get_config().rc_timezone}")
    else:
        print(f"   WARN  the sync window {start}–{end} is read in UTC, the container's clock.")
        print("         For local time set RC_TIMEZONE=Europe/Zurich (or yours) in knovas.env,")
        print("         then ./scripts/setup.sh && ./scripts/start.sh")

# From inside the container the peer is loopback, which the stack's local bypass
# always admits; where the bypass is off this answers 401 and is skipped.
code, status = get("/sync/status")
if code == 200:
    if status.get("last_worker_error"):
        print(f"   FAIL  last error: {str(status['last_worker_error'])[:200]}")
    print(f"         last cycle finished: {status.get('last_run_at') or 'none yet'}"
          f" — files tracked: {status.get('files_synced_local', 0)}")
PY
  if [[ -z "$LAST_CYCLE" ]]; then
    echo "  no sync cycle has finished yet — the first over a large share takes a while"
  else
    CYCLE_ERRORS="$(printf '%s' "$LAST_CYCLE" | sed -n 's/.*errors=\([0-9][0-9]*\).*/\1/p')"
    CYCLE="$(printf '%s' "$LAST_CYCLE" | sed 's/.*Sync cycle finished //')"
    if [[ "${CYCLE_ERRORS:-0}" == "0" ]]; then
      ok "last cycle had no upload errors: $CYCLE"
    else
      warn "last cycle: $CYCLE"
      echo "       Which files, and why: ${DC[*]} logs remote-controller | grep -iE 'error|fail' | tail -20"
    fi
  fi
fi

head_ "Reachable"
PORT="$(grep -E '^[[:space:]]*DOCBRIDGE_WEB_PORT=' "$KNOVAS_ENV" | tail -1 | cut -d= -f2- | tr -d '[:space:]')"
PORT="${PORT:-8081}"
code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "http://127.0.0.1:${PORT}/api/stats" 2>/dev/null)"
case "$code" in
  200) ok "the app answers through nginx (HTTP $code)" ;;
  502|504) bad "nginx is up but cannot reach the app (HTTP $code) — ${DC[*]} logs --tail 30 docbridge-web" ;;
  *)   warn "unexpected status from /api/stats: ${code:-no response}" ;;
esac

SEEN_PREFIX=""
head_ "Sign-in and search, as a person would"
# Every probe above looks at a part. This goes through the front door: signs in,
# searches, and checks that what came back can be opened -- the three things a
# person does in the first minute, and the ones a migration breaks.
if [[ "$IDENT_ON" == true ]]; then
  echo "  skipped — with per-user accounts, signing in needs a person's own password."
  echo "       Search once in the browser, or ask Knovas directly: ./scripts/search-probe.sh \"$QUERY\""
else
  # Captured rather than piped straight out: its last line says which prefix
  # the index's pointers carry, and the snippet sample below needs exactly that.
  E2E="$("${DC[@]}" exec -T -e PYTHONWARNINGS=ignore -e "DOCTOR_QUERY=$QUERY" \
    docbridge-web python - <<'PY' 2>&1
import http.client
import json
import os
import re
import urllib.parse

query = os.environ.get("DOCTOR_QUERY") or "Rechnung"
root = os.environ.get("AUTODOC_PATH") or "/mnt/autodoc"
prefixes = [p.strip().strip("/") for p in (os.environ.get("AUTODOC_IDENTIFIER_PREFIX") or "").split(",") if p.strip()]
name = os.environ.get("COMPANY_LOGIN_NAME", "")
jar = {}


def call(method, path, body=None, headers=None, limit=None):
    # The app itself on :5000, not nginx: nginx has its own probe above.
    conn = http.client.HTTPConnection("127.0.0.1", 5000, timeout=180)
    headers = dict(headers or {})
    if jar:
        # By hand rather than http.cookiejar: the session cookie is Secure, a
        # cookie jar refuses to send it over plain HTTP, and plain HTTP is all
        # there is inside the container.
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in jar.items())
    conn.request(method, path, body=body, headers=headers)
    response = conn.getresponse()
    raw = response.read(limit) if limit else response.read()
    for key, value in response.getheaders():
        if key.lower() == "set-cookie":
            cookie, _, val = value.split(";", 1)[0].partition("=")
            jar[cookie.strip()] = val.strip()
    conn.close()
    return response.status, raw


status, raw = call("GET", "/login")
token = re.search(r'name="csrf_token" value="([^"]+)"', raw.decode("utf-8", "replace"))
if status != 200 or not token:
    raise SystemExit(f"   FAIL  /login answered HTTP {status} without a sign-in form")
form = urllib.parse.urlencode({
    "login_name": name,
    "password": os.environ.get("COMPANY_LOGIN_PASSWORD", ""),
    "csrf_token": token.group(1),
    "next": "/",
})
status, raw = call("POST", "/login", form, {"Content-Type": "application/x-www-form-urlencoded"})
if status != 302:
    error = re.search(r'class="login-error"[^>]*>([^<]+)<', raw.decode("utf-8", "replace"))
    raise SystemExit(f"   FAIL  the company login '{name}' is refused (HTTP {status}): "
                     + (error.group(1).strip() if error else "no message"))
print(f"     OK  the company login '{name}' signs in (the password from knovas.env)")

status, raw = call("GET", "/")
csrf = re.search(r'csrfToken:\s*"([^"]+)"', raw.decode("utf-8", "replace"))
if status != 200 or not csrf:
    raise SystemExit(f"   FAIL  the search page answered HTTP {status}")
status, raw = call("POST", "/api/search", json.dumps({"query": query, "limit": 10}),
                   {"Content-Type": "application/json", "X-CSRF-Token": csrf.group(1)})
try:
    data = json.loads(raw)
except ValueError:
    data = {}
if status != 200 or not data.get("success"):
    raise SystemExit(f"   FAIL  searching {query!r} answered HTTP {status}: {data.get('error') or raw[:200]!r}")
results = data.get("results") or []
if not results:
    print(f"   WARN  searching {query!r} finds nothing, so nothing below could be checked.")
    print('         Run again with words that are certainly in the documents: ./scripts/doctor.sh "…"')
    raise SystemExit(0)
print(f"     OK  searching {query!r} returns {len(results)} result(s), for example:")
top = results[:5]
for result in top[:3]:
    print(f"           {result.get('title') or '(no title)'}  <- {result.get('doc_id') or result.get('path')}")

pointers = [str(r.get("doc_id") or r.get("path") or "") for r in top]
if prefixes and all(any(p.startswith(x + "/") for x in prefixes) for p in pointers):
    print(f"     OK  every pointer starts with the prefix the app strips ({','.join(prefixes)}/)")
else:
    print(f"   FAIL  pointers such as '{pointers[0]}' do not start with {','.join(prefixes) or '<no prefix>'}/")
    print("         Set KNOVAS_IDENTIFIER_PREFIX in knovas.env to what they start with,")
    print("         then ./scripts/setup.sh && ./scripts/start.sh")

on_share = [r for r in top
            if r.get("autodoc_rel_path")
            and os.path.isfile(os.path.join(root, str(r["autodoc_rel_path"]).lstrip("/")))]
if len(on_share) == len(top):
    print("     OK  every top result is a file on the share")
else:
    missing = next(r for r in top if r not in on_share)
    print(f"   FAIL  {len(top) - len(on_share)} of the top {len(top)} results are not on the share, e.g.")
    print(f"         {root}/{missing.get('autodoc_rel_path') or missing.get('path')}")
    print("         No preview and no Öffnen for those: KNOVAS_DOCUMENTS_PATH must be the")
    print("         folder the documents were ingested from.")

with_text = sum(1 for r in top if r.get("context_snippet") or r.get("first_page_preview"))
if with_text == len(top):
    print("     OK  every top result shows text under the title")
else:
    print(f"   WARN  {len(top) - with_text} of the top {len(top)} results show no text under the title"
          " — see 'Snippet text across the share' below")

if on_share:
    first = on_share[0]
    doc = urllib.parse.quote(str(first.get("doc_id") or first.get("path")), safe="/")
    status, raw = call("GET", f"/api/document/{doc}/client-path?"
                       + urllib.parse.urlencode({"path": first.get("path")}))
    try:
        answer = json.loads(raw)
    except ValueError:
        answer = {}
    target = answer.get("unc") or answer.get("path")
    if status == 200 and target:
        print(f"     OK  Öffnen hands the browser {target}")
    elif status == 503 and answer.get("fallback") == "download":
        print("     OK  Öffnen falls back to the download (no share configured)")
    else:
        print(f"   FAIL  Öffnen answers HTTP {status}: {answer.get('error') or raw[:200]!r}")

    pdf = next((r for r in on_share if str(r.get("path") or "").lower().endswith(".pdf")), None)
    if pdf:
        doc = urllib.parse.quote(str(pdf.get("doc_id") or pdf.get("path")), safe="/")
        status, raw = call("GET", f"/api/document/{doc}/preview?"
                           + urllib.parse.urlencode({"path": pdf.get("path"), "q": query}), limit=8)
        if status == 200 and raw.startswith(b"%PDF"):
            print("     OK  the PDF preview opens")
        else:
            print(f"   FAIL  the PDF preview answers HTTP {status}")

seen = sorted({p.split("/", 1)[0] for p in pointers if "/" in p})
if len(seen) == 1:
    print(f"@@prefix {seen[0]}")
PY
  )"
  printf '%s\n' "$E2E" | grep -v '^@@' | sed 's/^/  /'
  SEEN_PREFIX="$(printf '%s\n' "$E2E" | sed -n 's/^@@prefix //p')"
fi

head_ "Snippet text across the share"
# A store that is not empty can still hold text only for what RemoteController
# has read since this deployment began -- by default the files changed in the
# last 30 days -- while every older document, found through an index built long
# before, shows a title and nothing under it. Sampled across the folders of the
# share, through the lookup the app uses, under the prefix the index's pointers
# actually carry when the search above saw them: sampling under a configured
# prefix that is wrong reports every file as missing, and would then recommend
# filling the store under that same wrong prefix.
if ! "${DC[@]}" exec -T docbridge-web test -d "$STORE" 2>/dev/null; then
  echo "  skipped — there is no context store to sample (see 'Search result snippets')"
else
  # A backfill already running is the answer to a low count, not a reason to
  # start a second one -- which would fail on the container name anyway.
  BACKFILL="$(docker ps --filter name=knovas-snippet-backfill --format '{{.Names}}|{{.Status}}' 2>/dev/null \
    | sed -n 's/^knovas-snippet-backfill|//p')"
  BACKFILL_LAST=""
  if [[ -n "$BACKFILL" ]]; then
    BACKFILL_LAST="$(docker logs --tail 1 knovas-snippet-backfill 2>&1 | tr -d '\r')"
  fi
  "${DC[@]}" exec -T -e PYTHONWARNINGS=ignore -e "DOCTOR_STORE=$STORE" \
    -e "DOCTOR_RC_PREFIX=$RC_PREFIX" -e "DOCTOR_SEEN_PREFIX=$SEEN_PREFIX" \
    -e "DOCTOR_ROOT_DIR=$ROOT_DIR" -e "DOCTOR_BACKFILL=$BACKFILL" \
    -e "DOCTOR_BACKFILL_LAST=$BACKFILL_LAST" docbridge-web python - <<'PY' 2>&1 | sed 's/^/  /'
import os
from pathlib import Path

from context_store import sidecar_path_for_pointer

root = os.environ.get("AUTODOC_PATH") or "/mnt/autodoc"
store = Path(os.environ["DOCTOR_STORE"])
prefixes = [p.strip().strip("/") for p in (os.environ.get("AUTODOC_IDENTIFIER_PREFIX") or "").split(",") if p.strip()]
rc_prefix = (os.environ.get("DOCTOR_RC_PREFIX") or "").strip().strip("/")
seen = (os.environ.get("DOCTOR_SEEN_PREFIX") or "").strip().strip("/")
if seen:
    candidates, basis = [seen], "as the search above returned them"
else:
    candidates = list(dict.fromkeys(p for p in (rc_prefix, *prefixes) if p)) or [""]
    basis = "from KNOVAS_IDENTIFIER_PREFIX"
# RemoteController's SYNCABLE_EXTENSIONS: no other file ever gets a sidecar.
extensions = {".md", ".txt", ".docx", ".pdf", ".eml", ".msg"}

sampled = covered = 0
for current, dirs, names in os.walk(root):
    dirs[:] = sorted(d for d in dirs if not d.startswith("."))
    taken = 0
    for name in sorted(names):
        if name.startswith(".") or os.path.splitext(name)[1].lower() not in extensions:
            continue
        rel = os.path.relpath(os.path.join(current, name), root).replace(os.sep, "/")
        sampled += 1
        taken += 1
        if any(sidecar_path_for_pointer(store, f"{p}/{rel}" if p else rel).is_file() for p in candidates):
            covered += 1
        if taken >= 5 or sampled >= 200:
            break
    if sampled >= 200:
        break

print(f"pointers '{candidates[0]}/…', {basis}")
if not sampled:
    print("         no .pdf .docx .txt .md .eml or .msg file on the share to sample")
    raise SystemExit(0)
if covered == sampled:
    print(f"     OK  all {sampled} sampled documents have snippet text")
    raise SystemExit(0)
print(f"   WARN  {covered} of {sampled} sampled documents have snippet text; results for the")
print("         others show a title and nothing under it.")
backfill = os.environ.get("DOCTOR_BACKFILL") or ""
if backfill:
    print(f"         A backfill is running ({backfill}) and fills in the rest. Its last line:")
    print(f"           {(os.environ.get('DOCTOR_BACKFILL_LAST') or '(nothing yet)')[:150]}")
    print("         Follow it with: docker logs -f knovas-snippet-backfill")
    raise SystemExit(0)
print("         RemoteController writes the text as it syncs, by default only for files")
print("         changed in the last 30 days. Build the rest from the files themselves —")
print("         nothing is sent to Knovas, it runs in the background, and a run that is")
print("         stopped picks up where it was:")
prefix = seen or (prefixes[0] if prefixes else rc_prefix)
if not str(store).startswith("/var/rc-state/"):
    print(f"         (not from here: {store} is not on RemoteController's volume /var/rc-state)")
elif not prefix:
    print("         (set KNOVAS_IDENTIFIER_PREFIX first — the text is filed under it)")
else:
    print(f'           cd "{os.environ.get("DOCTOR_ROOT_DIR") or "."}"')
    print("           docker compose --env-file knovas.env run -d --rm --name knovas-snippet-backfill \\")
    print('             -v "$PWD/RemoteController/scripts:/app/scripts:ro" remote-controller \\')
    print("             python /app/scripts/build_context_sidecars.py --jobs 2 \\")
    print(f"             --identifier-prefix {prefix} --store-dir {store}")
    print("         Follow it with: docker logs -f knovas-snippet-backfill")
    print("         (--jobs is how many documents at once; each takes one CPU core while it runs.)")
PY
fi

head_ "Public address"
# What people actually type. Everything above talks to the stack on this machine,
# and a reverse proxy that still points at another port -- or at the stack this
# one replaced -- answers just as well, from the wrong app. So the probe compares
# the build marker the address reports with the one this stack reports: HTTP 200
# alone would pass either.
PUBLIC_URL="$(read_env_var KNOVAS_PLATFORM_URL "" "$KNOVAS_ENV")"
PUBLIC_URL="${PUBLIC_URL%/}"
build_of() { sed -n 's/.*"asset_version": *"\([^"]*\)".*/\1/p'; }
if [[ -z "$PUBLIC_URL" ]]; then
  warn "KNOVAS_PLATFORM_URL is not set in knovas.env — nothing to probe."
else
  echo "  KNOVAS_PLATFORM_URL = $PUBLIC_URL"
  PUBLIC_HOSTPORT="${PUBLIC_URL#*://}"
  PUBLIC_HOSTPORT="${PUBLIC_HOSTPORT%%/*}"
  PUBLIC_HOST="${PUBLIC_HOSTPORT%%:*}"
  if [[ "$PUBLIC_HOSTPORT" == *:* ]]; then
    PUBLIC_PORT="${PUBLIC_HOSTPORT##*:}"
  elif [[ "$PUBLIC_URL" == https://* ]]; then
    PUBLIC_PORT=443
  else
    PUBLIC_PORT=80
  fi
  probe=(curl -sS -L --max-redirs 3 --max-time 10 -w '\n%{http_code}')
  notes=()
  out="$("${probe[@]}" "$PUBLIC_URL/api/stats" 2>/dev/null)"; rc=$?
  if [[ $rc -eq 6 ]]; then
    # Internal DNS the server itself does not use is common and harmless: ask
    # the proxy on this machine directly, under the same name.
    probe+=(--resolve "$PUBLIC_HOST:$PUBLIC_PORT:127.0.0.1")
    notes+=("this server cannot resolve $PUBLIC_HOST itself, so it was asked through 127.0.0.1")
    out="$("${probe[@]}" "$PUBLIC_URL/api/stats" 2>/dev/null)"; rc=$?
  fi
  case $rc in
    35|51|58|60|77)
      probe+=(-k)
      notes+=("its certificate is not trusted on this server — fine if the users' PCs trust it (an internal CA)")
      out="$("${probe[@]}" "$PUBLIC_URL/api/stats" 2>/dev/null)"; rc=$? ;;
  esac
  code="${out##*$'\n'}"
  PUBLIC_BUILD="$(printf '%s' "${out%$'\n'*}" | build_of)"
  LOCAL_BUILD="$(curl -sS --max-time 10 "http://127.0.0.1:${PORT}/api/stats" 2>/dev/null | build_of)"
  if [[ $rc -eq 0 && "$code" == "200" && -n "$PUBLIC_BUILD" && "$PUBLIC_BUILD" == "$LOCAL_BUILD" ]]; then
    if (( ${#notes[@]} )); then
      warn "$PUBLIC_URL reaches this stack, but:"
      printf '       %s\n' "${notes[@]}"
    else
      ok "$PUBLIC_URL reaches this stack"
    fi
  elif [[ $rc -eq 0 && "$code" == "200" && -n "$PUBLIC_BUILD" ]]; then
    bad "$PUBLIC_URL answers with a different Platform build than this stack's."
    echo "       The reverse proxy in front still points at another deployment — the one this"
    echo "       replaced? It must forward to 127.0.0.1:${PORT}, this stack's port."
  elif [[ $rc -eq 0 ]]; then
    bad "$PUBLIC_URL answers HTTP $code, not with the Platform."
    echo "       The reverse proxy in front must forward to 127.0.0.1:${PORT}, this stack's port."
  elif (( ${#notes[@]} )); then
    warn "could not check $PUBLIC_URL from this server (curl exit $rc):"
    printf '       %s\n' "${notes[@]}"
    echo "       Open it from a PC in the office; it should show the sign-in page."
  else
    bad "nothing answers at $PUBLIC_URL (curl exit $rc)."
    echo "       The reverse proxy for it is not running, or not listening there."
  fi
fi
} 2>&1 | tee "$REPORT"

# The verdict, counted from what was printed (colour codes stripped first).
FAILS="$(sed 's/\x1b\[[0-9;]*m//g' "$REPORT" | grep -cE '^[[:space:]]*FAIL([[:space:]]|$)')"
WARNS="$(sed 's/\x1b\[[0-9;]*m//g' "$REPORT" | grep -cE '^[[:space:]]*WARN([[:space:]]|$)')"
printf '\n'
if (( FAILS > 0 )); then
  printf '\033[31m%s FAIL\033[0m, %s WARN — start with the first FAIL above.\n\n' "$FAILS" "$WARNS"
  exit 1
elif (( WARNS > 0 )); then
  printf '\033[32mNo FAIL\033[0m, %s WARN — each WARN above says whether it matters here.\n\n' "$WARNS"
else
  printf '\033[32mNo FAIL, no WARN.\033[0m\n\n'
fi
