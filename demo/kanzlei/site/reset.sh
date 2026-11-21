#!/usr/bin/env bash
# Empty the share volume and drop the database; the domain stays (load.sh does this itself before loading).
# --all: delete every volume instead, so the next up.sh provisions the domain again (needed after directory.json
# changes).
. "$(dirname "$0")/_lib.sh"
load_env
if [ "${1:-}" = --all ]; then exec bash ./down.sh --volumes; fi
compose up -d --wait --wait-timeout 600 dc sql
compose --profile load run --rm -T loader reset
