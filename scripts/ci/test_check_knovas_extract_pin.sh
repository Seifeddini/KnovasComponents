#!/usr/bin/env bash
# The two customer images and both CI test jobs must run one knovas-extract.
# check_knovas_extract_pin.sh reads the ARG defaults of both Dockerfiles; this
# pins what it accepts, what it refuses, and what it prints.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CHECK="$ROOT_DIR/scripts/ci/check_knovas_extract_pin.sh"
cd "$ROOT_DIR"

fail() { echo "FAIL: $*" >&2; exit 1; }

[[ -f "$CHECK" ]] || fail "$CHECK is missing"

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT
SHA=b5d45404a6df0aa5fb2b934c8ae4efab9fe764a1
OTHER_SHA=1c42621aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
RC="$WORKDIR/rc.Dockerfile"
PF="$WORKDIR/pf.Dockerfile"

dockerfile() {  # path version ref
  printf 'FROM python:3.12-slim\nARG KNOVAS_EXTRACT_VERSION=%s\nARG KNOVAS_EXTRACT_GIT_REF=%s\nRUN true\n' \
    "$2" "$3" > "$1"
}

# refused <what> <expected message part>: the check must exit non-zero with
# a message naming the problem, and print nothing on stdout.
refused() {
  if bash "$CHECK" "$RC" "$PF" >"$WORKDIR/out" 2>"$WORKDIR/err"; then
    fail "$1 was accepted"
  fi
  grep -qF "$2" "$WORKDIR/err" || fail "$1: the message lacks '$2': $(cat "$WORKDIR/err")"
  [[ ! -s "$WORKDIR/out" ]] || fail "$1: stdout must stay empty, got: $(cat "$WORKDIR/out")"
}

# --- The repository itself: one pin, printed as two KEY=value lines --------
out="$(bash "$CHECK" 2>/dev/null)" || fail "the two Dockerfiles of this repository disagree"
[[ "$(printf '%s\n' "$out" | wc -l)" -eq 2 ]] || fail "stdout must be exactly two lines: $out"
grep -qE '^KNOVAS_EXTRACT_VERSION=[0-9]' <<<"$out" || fail "no version printed: $out"
grep -qE '^KNOVAS_EXTRACT_GIT_REF=([0-9a-f]{40})?$' <<<"$out" || fail "no ref printed: $out"

# --- Equal pins pass, from git and from PyPI -------------------------------
dockerfile "$RC" 0.4.0a1 "$SHA"
dockerfile "$PF" 0.4.0a1 "$SHA"
out="$(bash "$CHECK" "$RC" "$PF" 2>"$WORKDIR/err")" || fail "equal git pins were refused"
[[ "$out" == "KNOVAS_EXTRACT_VERSION=0.4.0a1"$'\n'"KNOVAS_EXTRACT_GIT_REF=$SHA" ]] \
  || fail "unexpected output for a git pin: $out"
grep -qF "knovas-extract pin: 0.4.0a1 from git $SHA" "$WORKDIR/err" \
  || fail "the pin is not named on stderr: $(cat "$WORKDIR/err")"
dockerfile "$RC" 0.4.0a1 ""
dockerfile "$PF" 0.4.0a1 ""
out="$(bash "$CHECK" "$RC" "$PF" 2>/dev/null)" || fail "equal PyPI pins were refused"
[[ "$out" == "KNOVAS_EXTRACT_VERSION=0.4.0a1"$'\n'"KNOVAS_EXTRACT_GIT_REF=" ]] \
  || fail "unexpected output for a PyPI pin: $out"

# --- A Windows checkout (CRLF) reads the same pin --------------------------
printf 'ARG KNOVAS_EXTRACT_VERSION=0.4.0a1\r\nARG KNOVAS_EXTRACT_GIT_REF=%s\r\n' "$SHA" > "$RC"
dockerfile "$PF" 0.4.0a1 "$SHA"
out="$(bash "$CHECK" "$RC" "$PF" 2>/dev/null)" || fail "a CRLF Dockerfile was read differently"
[[ "$out" == "KNOVAS_EXTRACT_VERSION=0.4.0a1"$'\n'"KNOVAS_EXTRACT_GIT_REF=$SHA" ]] \
  || fail "CR left in the output: $(od -c <<<"$out" | head -3)"

# --- Refusals ----------------------------------------------------------------
dockerfile "$RC" 0.4.0a1 "$SHA"
dockerfile "$PF" 0.4.0a2 "$SHA"
refused "two versions" "pin different knovas-extract builds"
dockerfile "$PF" 0.4.0a1 ""
refused "git in one image and PyPI in the other" "pin different knovas-extract builds"
dockerfile "$PF" 0.4.0a1 "$OTHER_SHA"
refused "two git revisions" "pin different knovas-extract builds"
dockerfile "$RC" 0.4.0a1 main
dockerfile "$PF" 0.4.0a1 main
refused "a branch as the default ref" "full 40-character commit sha"
dockerfile "$RC" "" ""
dockerfile "$PF" "" ""
refused "an empty version" "is not a release version"
dockerfile "$RC" ">=0.4" ""
dockerfile "$PF" ">=0.4" ""
refused "a version range" "is not a release version"
printf 'FROM python:3.12-slim\nARG KNOVAS_EXTRACT_GIT_REF=\n' > "$RC"
dockerfile "$PF" 0.4.0a1 ""
refused "a Dockerfile without the version ARG" "no 'ARG KNOVAS_EXTRACT_VERSION=' line"
{ cat "$PF"; echo "ARG KNOVAS_EXTRACT_GIT_REF=$SHA"; } > "$RC"
refused "a ref defined twice" "appears more than once"
rm -f "$RC"
refused "a missing Dockerfile" "no such file"

echo "check_knovas_extract_pin smoke OK"
