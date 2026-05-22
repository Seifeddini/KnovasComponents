#!/usr/bin/env bash
# Knovas Open Companion (Linux)
# Handles semantix-doc:open?token=...&apiBase=https%3A%2F%2Fhost%2F from the browser,
# redeems at DocBridge /api/open-tokens/redeem, then opens path or UNC via xdg-open.
set -euo pipefail

url="${1:-}"
if [[ -z "$url" ]]; then
  echo "Usage: knovas-open-companion.sh 'semantix-doc:open?token=...&apiBase=...'" >&2
  exit 1
fi

payload="$(python3 - "$url" <<'PY'
import sys
import urllib.parse

raw = sys.argv[1].strip().strip('"')
if not raw.lower().startswith("semantix-doc:"):
    raise SystemExit("Expected semantix-doc: URL")
q = raw.find("?")
if q < 0:
    raise SystemExit("Missing query string")
params = urllib.parse.parse_qs(raw[q + 1 :], keep_blank_values=False)
token = (params.get("token") or [""])[0]
api_base = (params.get("apiBase") or [""])[0].rstrip("/")
if not token or not api_base:
    raise SystemExit("token and apiBase required")
print(token)
print(api_base)
PY
)"

token="$(echo "$payload" | sed -n '1p')"
api_base="$(echo "$payload" | sed -n '2p')"

body="$(curl -fsS -X POST "${api_base}/api/open-tokens/redeem" \
  -H "Authorization: Bearer ${token}" \
  -H "Content-Type: application/json" \
  -d '{}')"

open_target="$(python3 -c "import json,sys; d=json.load(sys.stdin); 
assert d.get('success'), d.get('error','redeem failed');
print(d.get('path') or d.get('unc') or '')" <<<"$body")"

if [[ -z "$open_target" ]]; then
  echo "Redeem response had no path or unc: $body" >&2
  exit 2
fi

exec xdg-open "$open_target"
