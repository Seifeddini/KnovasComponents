#!/usr/bin/env bash
# Compare every file on the share with a manifest of the generator (path and SHA-256).
# Usage: ./check-share.sh <folder with manifest.jsonl>   (the build's <work>, or <work>/truth/changes/tag-1)
. "$(dirname "$0")/_lib.sh"
[ -f "${1:-}/manifest.jsonl" ] || { echo "usage: $0 <folder with manifest.jsonl>" >&2; exit 2; }
folder=$(host_path "$1")
load_env
compose up -d --wait --wait-timeout 600 dc sql >/dev/null 2>&1 || { echo "FAIL  dc/sql not healthy"; exit 1; }
compose --profile tools run --rm -T -v "$folder/manifest.jsonl:/in/truth/manifest.jsonl:ro" client python3 /opt/site/check_share.py
