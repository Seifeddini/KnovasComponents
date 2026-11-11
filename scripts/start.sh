#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# shellcheck source=lib/stack_identity.sh
source "$ROOT_DIR/scripts/lib/stack_identity.sh"
# shellcheck source=lib/old_connector_name.sh
source "$ROOT_DIR/scripts/lib/old_connector_name.sh"

KNOVAS_ENV="$ROOT_DIR/knovas.env"
if [[ ! -f "$KNOVAS_ENV" ]]; then
  echo "Run ./scripts/setup.sh first." >&2
  exit 1
fi
knovas_adopt_old_connector_files "$ROOT_DIR"
if [[ ! -f "$ROOT_DIR/KnovasConnector/.env.generated" ]]; then
  echo "Run ./scripts/setup.sh first (missing .env.generated)." >&2
  exit 1
fi

knovas_prepare_stack "$KNOVAS_ENV" "$ROOT_DIR"
knovas_load_compose_project "$KNOVAS_ENV" "$ROOT_DIR"

knovas_retire_old_connector_container "$COMPOSE_PROJECT_NAME"
# The KnowledgeBase read token for the docbridge-web build (brain-runner). --env-file
# replaces .env for compose, so a token kept in .env is handed over from here.
KB_TOKEN="$(read_env_var KB_READ_TOKEN "" "$KNOVAS_ENV")"
[[ -z "$KB_TOKEN" && -f "$ROOT_DIR/.env" ]] && KB_TOKEN="$(read_env_var KB_READ_TOKEN "" "$ROOT_DIR/.env")"
# Ask GitHub before building: a token it refuses would stop the whole image at the brain-runner step.
# The token is never printed; only its length, its first characters and GitHub's answer are.
if [[ -n "$KB_TOKEN" ]]; then
  kb_source="knovas.env"
  grep -qE '^[[:space:]]*KB_READ_TOKEN=' "$KNOVAS_ENV" || kb_source=".env"
  kb_lines="$(grep -cE '^[[:space:]]*(export[[:space:]]+)?KB_READ_TOKEN=' "$ROOT_DIR/$kb_source" || true)"
  if [[ "$kb_lines" -gt 1 ]]; then
    echo "Note: $kb_source sets KB_READ_TOKEN $kb_lines times; the last line is used." >&2
  fi
  if [[ "$KB_TOKEN" =~ [[:space:]\"\'#] ]]; then
    echo "Warning: KB_READ_TOKEN in $kb_source contains a space, quote or # (a comment on the same line?)." >&2
  fi
  if command -v curl >/dev/null 2>&1; then
    kb_status="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
      -H "Authorization: Bearer $KB_TOKEN" https://api.github.com/repos/Seifeddini/KnowledgeBase || true)"
    case "$kb_status" in
      200) echo "KB_READ_TOKEN from $kb_source: GitHub accepts it." ;;
      401|403|404)
        case "$kb_status" in
          401) why="GitHub does not know this token (wrong, expired or revoked)" ;;
          403) why="GitHub refuses this token (blocked, or the organisation has not approved it)" ;;
          *) why="the token cannot see Seifeddini/KnowledgeBase (repository access: add it, Contents: read)" ;;
        esac
        echo "Warning: KB_READ_TOKEN from $kb_source (${#KB_TOKEN} characters, starts with ${KB_TOKEN:0:4}): $why." >&2
        echo "         docbridge-web builds without brain-runner. Put a working token in knovas.env and run this again." >&2
        KB_TOKEN="" ;;
      *) echo "Note: GitHub could not be asked about KB_READ_TOKEN (HTTP ${kb_status:-none}); building with it." >&2 ;;
    esac
  fi
  unset kb_source kb_lines kb_status why
fi
if [[ -n "$KB_TOKEN" ]]; then
  export KB_READ_TOKEN="$KB_TOKEN"
else
  # Empty, not unset: compose reads knovas.env itself (--env-file) and the shell's value wins over the file's,
  # so only an empty one keeps a refused token (or one exported in the login shell) out of the build.
  export KB_READ_TOKEN=""
fi
if [[ -n "${KB_READ_TOKEN:-}" ]]; then
  :
elif ! grep -qE '^[[:space:]]*KB_READ_TOKEN=.' "$KNOVAS_ENV" "$ROOT_DIR/.env" 2>/dev/null; then
  echo "Note: no KB_READ_TOKEN in knovas.env or .env -- docbridge-web builds without brain-runner" >&2
  echo "      (Brain Builder sources, dry run and publish, Sync von GitHub stay unavailable)." >&2
fi
unset KB_TOKEN
docker compose --env-file "$KNOVAS_ENV" up -d --build
unset KB_READ_TOKEN
if docker compose --env-file "$KNOVAS_ENV" exec -T docbridge-web python -c "import brain_runner; print('brain-runner', brain_runner.__version__)" 2>/dev/null; then
  :
else
  echo "Warning: brain-runner is not installed in docbridge-web; check KB_READ_TOKEN (a GitHub token with Contents: read on the KnowledgeBase repository)." >&2
fi

echo "==> Health checks"
sleep 3
BIND="$(read_env_var DOCBRIDGE_WEB_BIND "" "$KNOVAS_ENV")"
PORT="$(read_env_var DOCBRIDGE_WEB_PORT 8081 "$KNOVAS_ENV")"
RC_PORT="$(read_env_var RC_HOST_PORT 5001 "$KNOVAS_ENV")"
PROBE_HOST="127.0.0.1"
[[ -n "${BIND:-}" && "$BIND" != "0.0.0.0" ]] && PROBE_HOST="$BIND"

echo "    project ${COMPOSE_PROJECT_NAME}  UI http://${PROBE_HOST}:${PORT}  RC http://127.0.0.1:${RC_PORT}"

curl -fsS "http://127.0.0.1:${RC_PORT}/health" || echo "RC health: not ready yet"
# nginx serves /health itself, so it answers even when the app behind it does
# not. Probing only that reported a healthy stack while every actual page 502'd,
# so the app is probed through the proxy as well -- that is the one that means
# the Platform is up.
curl -fsS "http://${PROBE_HOST}:${PORT}/health" >/dev/null \
  && echo "nginx: ok" || echo "nginx: not ready yet"
curl -fsS "http://${PROBE_HOST}:${PORT}/api/stats" >/dev/null \
  && echo "Platform (through nginx): ok" \
  || echo "Platform (through nginx): NOT reachable — docker compose logs docbridge-web docbridge-web-nginx"
docker compose --env-file "$KNOVAS_ENV" ps

# OneDrive/SharePoint: prove the folder can be read now, not at the first
# empty search. Signs in as the app, resolves the address, reads the folder.
if [[ -n "$(read_env_var KNOVAS_DOCUMENTS_URL "" "$KNOVAS_ENV")" ]]; then
  echo "==> OneDrive/SharePoint folder"
  docker compose --env-file "$KNOVAS_ENV" exec -T -e PYTHONWARNINGS=ignore \
    knovas-connector python -m m365.check \
    || echo "    Fix the line marked FAIL in knovas.env, then ./scripts/setup.sh && ./scripts/start.sh"
fi
