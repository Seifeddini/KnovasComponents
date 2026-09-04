#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
if [[ ! -f .cache/alloy.jar ]]; then
  mkdir -p .cache
  curl -fsSL -o .cache/alloy.jar "$(sed -n 2p ci/alloy.version)"
fi
python3 ci/alloy_driver.py "$@"
