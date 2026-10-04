#!/usr/bin/env bash
# An installation from before the Knovas Connector rename: start.sh must move
# its generated settings into the new folder and retire the old container,
# so two Connectors never share rc-state.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=old_connector_name.sh
source "$ROOT_DIR/scripts/lib/old_connector_name.sh"

fail() { echo "FAIL: $*" >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# --- Generated settings follow the folder -----------------------------------
mkdir -p "$WORK/co/$_OLD_CONNECTOR_DIR" "$WORK/co/KnovasConnector"
echo "RC_CLIENT_ID=old" > "$WORK/co/$_OLD_CONNECTOR_DIR/.env.generated"
knovas_adopt_old_connector_files "$WORK/co" >/dev/null
[[ "$(cat "$WORK/co/KnovasConnector/.env.generated")" == "RC_CLIENT_ID=old" ]] \
  || fail ".env.generated was not moved into KnovasConnector/"
[[ -d "$WORK/co/$_OLD_CONNECTOR_DIR" ]] && fail "the emptied old folder was left behind"

# --- Never overwrites a file the new folder already has ---------------------
mkdir -p "$WORK/co/$_OLD_CONNECTOR_DIR"
echo "RC_CLIENT_ID=stale" > "$WORK/co/$_OLD_CONNECTOR_DIR/.env.generated"
knovas_adopt_old_connector_files "$WORK/co" >/dev/null
[[ "$(cat "$WORK/co/KnovasConnector/.env.generated")" == "RC_CLIENT_ID=old" ]] \
  || fail "an existing KnovasConnector/.env.generated was overwritten"
[[ -f "$WORK/co/$_OLD_CONNECTOR_DIR/.env.generated" ]] || fail "the old file was removed without being used"

# --- A fresh checkout has nothing to adopt ----------------------------------
mkdir -p "$WORK/fresh/KnovasConnector"
knovas_adopt_old_connector_files "$WORK/fresh" >/dev/null || fail "a fresh checkout failed"

# --- The old container of THIS project is stopped and removed ---------------
CALLS="$WORK/docker-calls"
docker() {
  echo "$*" >> "$CALLS"
  if [[ "$1" == "ps" ]]; then
    [[ "$*" == *"com.docker.compose.project=demo"* && "$*" == *"com.docker.compose.service=$_OLD_CONNECTOR_SERVICE"* ]] \
      && echo "abc123"
  fi
  return 0
}
knovas_retire_old_connector_container demo >/dev/null
grep -q "^stop -t 120 abc123$" "$CALLS" || fail "the old container was not stopped gracefully"
grep -q "^rm abc123$" "$CALLS" || fail "the old container was not removed"

: > "$CALLS"
knovas_retire_old_connector_container other >/dev/null
grep -q "^stop\|^rm" "$CALLS" && fail "another project's container was touched"

echo "old_connector_name OK"
