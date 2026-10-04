#!/usr/bin/env bash
# The Knovas Connector's OCR settings in knovas.env, checked by the rules the
# Connector applies (KnovasConnector/src/sync/document_text.py, spec E5). The
# Connector replaces an invalid value by its default and logs one warning per
# document -- in a log nobody reads while the sync seems to work. Before that
# check existed, RC_TESSERACT_LANG="deu eng" skipped every PDF for good.
#
#   knovas_rc_extraction_problems ENV_FILE
#
# prints one line per invalid setting (the setting, what is wrong, what the
# Connector uses instead) and nothing when every setting is valid or unset.
# Numbers are ASCII digits only, the Connector's own rule.

_RC_SETTINGS_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../KnovasPlatform/scripts/lib/read_env.sh
source "$_RC_SETTINGS_LIB_DIR/../../KnovasPlatform/scripts/lib/read_env.sh"

# $1 value, $2 minimum, $3 maximum (empty: none). True when $1 is a whole
# number in range.
_rc_whole_number_in_range() {
  local value="$1" min="$2" max="${3:-}" digits
  [[ "$value" =~ ^[0-9]+$ ]] || return 1
  digits="${value#"${value%%[!0]*}"}"   # leading zeros off: 08 is eight, not octal
  digits="${digits:-0}"
  if (( ${#digits} > 9 )); then         # far beyond every limit here; no overflow
    [[ -z "$max" ]]
    return
  fi
  (( digits >= min )) || return 1
  [[ -z "$max" ]] || (( digits <= max ))
}

knovas_rc_extraction_problems() {
  local env_file="$1" value
  local LC_ALL=C   # [A-Za-z] means ASCII letters, as in the Connector
  value="$(read_env_var RC_TESSERACT_LANG "" "$env_file")"
  if [[ -n "$value" && ! "$value" =~ ^[A-Za-z0-9_]+(\+[A-Za-z0-9_]+)*$ ]]; then
    echo "RC_TESSERACT_LANG=$value is not a list of language packs joined by + (like deu+eng); the Knovas Connector uses deu+eng"
  fi
  value="$(read_env_var RC_OCR_DPI "" "$env_file")"
  if [[ -n "$value" ]] && ! _rc_whole_number_in_range "$value" 30 1200; then
    echo "RC_OCR_DPI=$value is not a resolution from 30 to 1200; the Knovas Connector renders every page at its native resolution"
  fi
  value="$(read_env_var RC_OCR_PAGE_TIMEOUT_SECONDS "" "$env_file")"
  if [[ -n "$value" ]] && ! _rc_whole_number_in_range "$value" 1; then
    echo "RC_OCR_PAGE_TIMEOUT_SECONDS=$value is not a whole number of seconds of at least 1; the Knovas Connector uses 60"
  fi
  value="$(read_env_var RC_OCR_MAX_PAGES "" "$env_file")"
  if [[ -n "$value" ]] && ! _rc_whole_number_in_range "$value" 1; then
    echo "RC_OCR_MAX_PAGES=$value is not a page count of at least 1; the Knovas Connector uses 500"
  fi
}
