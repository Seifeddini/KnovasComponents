#!/usr/bin/env bash
# Upgrade from before the Knovas Connector rename. Until then the Connector's
# folder was RemoteController/ and its Docker service remote-controller; these
# old names appear here, and only here, so an existing installation keeps its
# generated settings and never runs two Connectors on the same rc-state volume.
# scripts/ci/check_old_connector_name.sh allows this file by name.

_OLD_CONNECTOR_DIR="RemoteController"
_OLD_CONNECTOR_SERVICE="remote-controller"

# Move what setup.sh generated into the old folder (git leaves ignored files
# behind when it renames the folder) to the new one. Never overwrites.
knovas_adopt_old_connector_files() {
  local checkout="$1" f old new
  old="$checkout/$_OLD_CONNECTOR_DIR"
  new="$checkout/KnovasConnector"
  [[ -d "$old" && -d "$new" ]] || return 0
  for f in .env.generated .env; do
    if [[ -f "$old/$f" && ! -e "$new/$f" ]]; then
      mv "$old/$f" "$new/$f"
      echo "==> Moved $_OLD_CONNECTOR_DIR/$f to KnovasConnector/$f (the folder was renamed)"
    fi
  done
  if [[ -z "$(ls -A "$old" 2>/dev/null)" ]]; then
    rmdir "$old" 2>/dev/null || true
  else
    echo "    $old is left over from before the rename and is no longer used; it can be deleted."
  fi
}

# Stop and remove this project's container of the old service. Compose no
# longer knows the service, so `up` would start the new Connector beside it,
# both on rc-state and the old one still holding the host port.
knovas_retire_old_connector_container() {
  local project="${1:-${COMPOSE_PROJECT_NAME:-}}" ids
  [[ -n "$project" ]] || return 0
  ids="$(docker ps -aq \
    --filter "label=com.docker.compose.project=$project" \
    --filter "label=com.docker.compose.service=$_OLD_CONNECTOR_SERVICE" 2>/dev/null || true)"
  [[ -n "$ids" ]] || return 0
  echo "==> Stopping the Connector container from before the rename (up to 2 minutes for it to stop)"
  # shellcheck disable=SC2086 # one id per word
  docker stop -t 120 $ids >/dev/null
  # shellcheck disable=SC2086
  docker rm $ids >/dev/null
}
