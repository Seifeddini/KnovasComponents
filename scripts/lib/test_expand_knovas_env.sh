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

echo "expand_knovas_env smoke OK"
