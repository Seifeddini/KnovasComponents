#!/usr/bin/env bash
# The Connector is called Knovas Connector: folder KnovasConnector/, Docker
# service knovas-connector, Python names knovas_connector. Fails when its
# earlier name comes back anywhere in the repository.
#
# Allowed, and nowhere else:
#   - the upgrade code that has to name the old folder, service, host and
#     config file to migrate an existing installation, and its tests;
#   - applied Platform migrations: their checksums are recorded in every
#     database, so a changed comment stops the Platform from starting;
#   - POST /remote_controller/verify_operator, an endpoint of the Knovas
#     server, not ours to rename.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

ALLOWED=(
  ':!scripts/ci/check_old_connector_name.sh'
  ':!scripts/lib/old_connector_name.sh'
  ':!scripts/lib/test_old_connector_name.sh'
  ':!scripts/lib/expand_knovas_env.sh'
  ':!scripts/lib/test_expand_knovas_env.sh'
  ':!KnovasConnector/src/sync/sync_config.py'
  ':!KnovasConnector/tests/unit/test_sync_config.py'
  ':!KnovasPlatform/components/docbridge_integration/src/identity/migrations/*.sql'
)

hits="$(git grep -n -I -i -E 'remote[ _-]?controller' -- . "${ALLOWED[@]}" \
  | grep -v -E '/remote_controller/verify_operator' || true)"

if [[ -n "$hits" ]]; then
  echo "The Connector's old name is back (use Knovas Connector / KnovasConnector /" >&2
  echo "knovas-connector / knovas_connector):" >&2
  echo "$hits" >&2
  exit 1
fi
echo "Old Connector name: none outside the upgrade code"
