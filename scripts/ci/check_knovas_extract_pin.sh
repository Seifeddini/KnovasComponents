#!/usr/bin/env bash
# One knovas-extract for both customer images and both CI test jobs.
#
# Reads the defaults of ARG KNOVAS_EXTRACT_VERSION and ARG
# KNOVAS_EXTRACT_GIT_REF from the Knovas Connector's and the Platform's
# Dockerfile, fails when the two files disagree or a value is neither a
# release version nor (for the ref) empty or a full commit sha, and prints
# the pin as KEY=value lines on stdout -- for $GITHUB_ENV or eval:
#
#   bash scripts/ci/check_knovas_extract_pin.sh >> "$GITHUB_ENV"
#   eval "$(bash scripts/ci/check_knovas_extract_pin.sh)"
#
# Usage: check_knovas_extract_pin.sh [CONNECTOR_DOCKERFILE PLATFORM_DOCKERFILE]
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RC_FILE="${1:-$ROOT_DIR/KnovasConnector/Dockerfile}"
PF_FILE="${2:-$ROOT_DIR/KnovasPlatform/components/docbridge_integration/Dockerfile}"
VERSION_RE='^[0-9]+(\.[0-9]+)*((a|b|rc)[0-9]+)?(\.post[0-9]+)?(\.dev[0-9]+)?$'
SHA_RE='^[0-9a-f]{40}$'

fail() { echo "check_knovas_extract_pin: $*" >&2; exit 1; }

# The default of `ARG <name>=` in <file>: exactly one such line, CR stripped
# (a Windows checkout may carry CRLF).
arg_default() {
  local file="$1" name="$2" lines
  [[ -f "$file" ]] || fail "$file: no such file"
  lines="$(tr -d '\r' < "$file" | grep -E "^ARG ${name}=" || true)"
  [[ -n "$lines" ]] || fail "$file: no 'ARG ${name}=' line"
  [[ "$(printf '%s\n' "$lines" | wc -l)" -eq 1 ]] || fail "$file: 'ARG ${name}=' appears more than once"
  printf '%s' "${lines#"ARG ${name}="}"
}

rc_version="$(arg_default "$RC_FILE" KNOVAS_EXTRACT_VERSION)"
rc_ref="$(arg_default "$RC_FILE" KNOVAS_EXTRACT_GIT_REF)"
pf_version="$(arg_default "$PF_FILE" KNOVAS_EXTRACT_VERSION)"
pf_ref="$(arg_default "$PF_FILE" KNOVAS_EXTRACT_GIT_REF)"

if [[ "$rc_version" != "$pf_version" || "$rc_ref" != "$pf_ref" ]]; then
  {
    echo "check_knovas_extract_pin: the two images pin different knovas-extract builds:"
    echo "  $RC_FILE: KNOVAS_EXTRACT_VERSION=$rc_version KNOVAS_EXTRACT_GIT_REF=$rc_ref"
    echo "  $PF_FILE: KNOVAS_EXTRACT_VERSION=$pf_version KNOVAS_EXTRACT_GIT_REF=$pf_ref"
    echo "Set the same defaults in both Dockerfiles; the CI test jobs install exactly that pin."
  } >&2
  exit 1
fi
[[ "$rc_version" =~ $VERSION_RE ]] \
  || fail "KNOVAS_EXTRACT_VERSION '$rc_version' is not a release version (e.g. 0.4.0a1)"
[[ -z "$rc_ref" || "$rc_ref" =~ $SHA_RE ]] \
  || fail "KNOVAS_EXTRACT_GIT_REF '$rc_ref' is neither empty (PyPI) nor a full 40-character commit sha"

if [[ -n "$rc_ref" ]]; then source_text="git $rc_ref"; else source_text="PyPI"; fi
echo "knovas-extract pin: $rc_version from $source_text (both Dockerfiles agree)" >&2
printf 'KNOVAS_EXTRACT_VERSION=%s\nKNOVAS_EXTRACT_GIT_REF=%s\n' "$rc_version" "$rc_ref"
