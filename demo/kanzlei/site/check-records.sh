#!/usr/bin/env bash
# Compare the loaded database, read with the Knovas login, with the build's truth files, row for row.
# Usage: ./check-records.sh <truth-dir>     (the build's <work>/truth; it holds records/<TABLE>.jsonl)
# The truth folder is mounted read-only into a throwaway client container and is not copied anywhere.
. "$(dirname "$0")/_lib.sh"
[ -d "${1:-}/records" ] || { echo "usage: $0 <truth-dir with records/*.jsonl>" >&2; exit 2; }
truth=$(host_path "$1")
load_env
compose up -d --wait --wait-timeout 600 dc sql >/dev/null 2>&1 || { echo "FAIL  dc/sql not healthy"; exit 1; }
compose --profile tools run --rm -T -v "$truth:/in/truth:ro" client python3 /opt/site/check_records.py
