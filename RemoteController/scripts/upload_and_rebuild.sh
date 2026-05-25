#!/usr/bin/env bash
set -euo pipefail
export RC_SSH_PASSWORD='M0n4t0m1c-8Ip4rt1t3-P1ty'
PY=/tmp/sshvenv/bin/python
H=/mnt/e/KnovasComponents/RemoteController/scripts/ssh_helper.py
SRC=/mnt/e/KnovasComponents/RemoteController/src
DST=/home/master/KnovasInternal/RemoteController/src
files=(
  sync/sync_config.py
  sync/sync_scheduler.py
  sync/sync_state.py
  sync/knovas_uploader.py
  sync/sync_executor.py
  routes/sync.py
  routes/sync_control.py
)
for f in "${files[@]}"; do
  "$PY" "$H" upload "$SRC/$f" "$DST/$f"
done
"$PY" "$H" run 'cd /home/master/KnovasInternal/RemoteController && docker compose -f docker-compose.yml -f docker-compose.internal.yml build --no-cache remote-controller && docker compose -f docker-compose.yml -f docker-compose.internal.yml up -d && sleep 15 && curl -sS http://127.0.0.1:5001/health'
