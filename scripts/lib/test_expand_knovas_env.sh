#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FIXTURES="$ROOT_DIR/scripts/lib/fixtures"
RC_ENV="$ROOT_DIR/RemoteController/.env.generated"
KP_ENV="$ROOT_DIR/KnovasPlatform/.env.generated"

cd "$ROOT_DIR"
cleanup() { rm -f "$RC_ENV" "$KP_ENV"; }
trap cleanup EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

# --- Default: per-user identity, no shared credential anywhere -------------
bash scripts/lib/expand_knovas_env.sh "$FIXTURES/knovas.env.fixture"
grep -q 'SEMANTIX_SECURE_BASE_URL=https://api.test:8443' "$RC_ENV" \
  || fail "RC env missing SEMANTIX_SECURE_BASE_URL"
grep -q 'OPEN_PUBLIC_BASE_URL=https://knovas.test.internal' "$KP_ENV" \
  || fail "Platform env missing OPEN_PUBLIC_BASE_URL"
grep -q '^DOCBRIDGE_WEB_PORT=8081$' "$KP_ENV" \
  || fail "default DOCBRIDGE_WEB_PORT should be 8081 when knovas.env does not set it"

# The regression this test exists for: the expander used to write
# COMPANY_LOGIN_NAME=company and a password into every generated file, which is
# precisely the state web_interface/app.py refuses to start in. An empty value
# counts as present -- match the key, not the value.
if grep -q '^COMPANY_LOGIN_NAME=\|^COMPANY_LOGIN_PASSWORD=\|^COMPANY_LOGIN_ENABLED=' "$KP_ENV"; then
  fail "COMPANY_LOGIN_* written while identity is on — the Platform will refuse to start"
fi

# --- Staged cutover: identity off, shared credential passed through --------
bash scripts/lib/expand_knovas_env.sh "$FIXTURES/knovas.env.cutover.fixture"
grep -q '^IDENTITY_ENABLED=false' "$KP_ENV" || fail "cutover did not disable identity"
grep -q '^COMPANY_LOGIN_NAME=company' "$KP_ENV" || fail "cutover dropped COMPANY_LOGIN_NAME"
grep -q '^COMPANY_LOGIN_PASSWORD=test-password' "$KP_ENV" || fail "cutover dropped COMPANY_LOGIN_PASSWORD"

# --- Both doors open: must be refused, and must not leave a file behind ----
rm -f "$KP_ENV"
if bash scripts/lib/expand_knovas_env.sh "$FIXTURES/knovas.env.bothdoors.fixture" 2>/dev/null; then
  fail "identity on + COMPANY_LOGIN_* was accepted"
fi
[[ -f "$KP_ENV" ]] && fail "refused config still wrote $KP_ENV"

# --- A missing first administrator is named, not deferred to compose -------
MISSING_ADMIN="$(mktemp)"
grep -v '^PLATFORM_ADMIN_EMAIL=' "$FIXTURES/knovas.env.fixture" > "$MISSING_ADMIN"
if bash scripts/lib/expand_knovas_env.sh "$MISSING_ADMIN" 2>/dev/null; then
  rm -f "$MISSING_ADMIN"
  fail "missing PLATFORM_ADMIN_EMAIL was accepted"
fi
rm -f "$MISSING_ADMIN"

# --- Overrides: knovas.env carries more than the orchestration values ------
bash scripts/lib/expand_knovas_env.sh "$FIXTURES/knovas.env.overrides.fixture"

# The generated file states a default first and the override after it. Compose
# resolves a duplicate key in an env_file to the LAST occurrence, so the check
# that matters is the last value, not merely that the key appears.
last_value() { grep -E "^$1=" "$2" | tail -1 | cut -d= -f2-; }

[[ "$(last_value WEB_SESSION_COOKIE_SECURE "$KP_ENV")" == "false" ]] \
  || fail "plain-HTTP override lost — the session cookie would be dropped and login would not stick"
[[ "$(last_value ENVIRONMENT "$KP_ENV")" == "local" ]] || fail "ENVIRONMENT override lost"
[[ "$(last_value ONTOLOGY_FIXTURE_PATH "$KP_ENV")" == "/mnt/ontology/ontology_fixture.json" ]] \
  || fail "Cortex fixture path override lost"
[[ "$(last_value DOCBRIDGE_WEB_PORT "$KP_ENV")" == "18081" ]] \
  || fail "DOCBRIDGE_WEB_PORT override lost — a second stack on 8081 would still bind 8081"

# An RC_* key belongs to RemoteController and must not leak into the Platform.
[[ "$(last_value RC_SYNC_AUTO_START_CONTINUOUS "$RC_ENV")" == "false" ]] || fail "RC override lost"
grep -q '^RC_SYNC_AUTO_START_CONTINUOUS=' "$KP_ENV" && fail "RC key leaked into the Platform env"

# A consumed key must not be echoed back as an override.
[[ "$(grep -c '^KNOVAS_API_URL=' "$KP_ENV")" == "0" ]] || fail "orchestration key passed through verbatim"

echo "expand_knovas_env smoke OK"
