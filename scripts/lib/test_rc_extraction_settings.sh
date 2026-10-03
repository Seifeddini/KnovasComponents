#!/usr/bin/env bash
# doctor.sh's check of the Connector OCR settings (spec E5): the rules of
# RemoteController/src/sync/document_text.py (tesseract_language, ocr_dpi,
# _env_int_at_least). The value tables here and in the Connector's
# tests/unit/test_document_text.py are the same.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=rc_extraction_settings.sh
source "$ROOT_DIR/scripts/lib/rc_extraction_settings.sh"

fail() { echo "FAIL: $*" >&2; exit 1; }

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT
ENV_FILE="$WORKDIR/knovas.env"

problems_for() {
  printf '%s\n' "$@" > "$ENV_FILE"
  knovas_rc_extraction_problems "$ENV_FILE"
}

# --- Unset or valid: nothing to say -------------------------------------------
[[ -z "$(problems_for 'KNOVAS_API_URL=https://api.test:8443')" ]] || fail "unset settings are valid"
for line in 'RC_TESSERACT_LANG=deu' 'RC_TESSERACT_LANG=deu+eng+fra' 'RC_TESSERACT_LANG="chi_sim+eng"' \
            'RC_OCR_DPI=30' 'RC_OCR_DPI=1200' 'RC_OCR_DPI=0150' \
            'RC_OCR_PAGE_TIMEOUT_SECONDS=1' 'RC_OCR_MAX_PAGES=1' 'RC_OCR_MAX_PAGES=5000'; do
  [[ -z "$(problems_for "$line")" ]] || fail "valid: $line"
done

# --- Each rule names its setting and what the Connector uses instead ----------
check() {  # $1 line, $2 setting, $3 fallback text
  local out
  out="$(problems_for "$1")"
  [[ "$out" == "$2="* && "$out" == *"$3"* ]] || fail "$1 -> '$out'"
}
for bad in 'deu eng' 'deu,eng' 'deu+' '+eng' 'deu++eng' '../deu' 'deu/eng' 'dé'; do
  check "RC_TESSERACT_LANG=$bad" RC_TESSERACT_LANG "uses deu+eng"
done
for bad in 29 1201 0 -300 300dpi 3e2; do
  check "RC_OCR_DPI=$bad" RC_OCR_DPI "native resolution"
done
for bad in 0 -1 sixty; do
  check "RC_OCR_PAGE_TIMEOUT_SECONDS=$bad" RC_OCR_PAGE_TIMEOUT_SECONDS "uses 60"
done
for bad in 0 -5 many; do
  check "RC_OCR_MAX_PAGES=$bad" RC_OCR_MAX_PAGES "uses 500"
done

# --- One line per invalid setting -----------------------------------------------
[[ "$(problems_for 'RC_OCR_DPI=5' 'RC_OCR_MAX_PAGES=0' 'RC_TESSERACT_LANG=deu+eng' | grep -c .)" == 2 ]] \
  || fail "two invalid settings, two lines"

echo "OK: Connector extraction settings contract"
