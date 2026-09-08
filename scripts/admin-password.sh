#!/usr/bin/env bash
# Set the password of a Platform account, creating it as an administrator if it
# does not exist yet.
#
# This exists because first-run onboarding had exactly one credential and no way
# to reissue it: ensure_admin only runs against a database with no accounts, so
# an operator who missed the generated password was locked out with no supported
# recovery short of dropping the identity volume. Reissuing is now a command.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

KNOVAS_ENV="$ROOT_DIR/knovas.env"
if [[ ! -f "$KNOVAS_ENV" ]]; then
  echo "Missing knovas.env — run ./scripts/setup.sh first." >&2
  exit 1
fi

# shellcheck source=../KnovasPlatform/scripts/lib/read_env.sh
source "$ROOT_DIR/KnovasPlatform/scripts/lib/read_env.sh"
# shellcheck source=lib/stack_identity.sh
source "$ROOT_DIR/scripts/lib/stack_identity.sh"
knovas_load_compose_project "$KNOVAS_ENV" "$ROOT_DIR"

GRANT_ONLY=false
if [[ "${1:-}" == "--grant-admin" ]]; then
  GRANT_ONLY=true
  shift
fi

EMAIL="${1:-$(read_env_var PLATFORM_ADMIN_EMAIL "" "$KNOVAS_ENV")}"
if [[ -z "$EMAIL" ]]; then
  echo "Usage: $0 [--grant-admin] [email]" >&2
  echo "No address given and PLATFORM_ADMIN_EMAIL is not set in knovas.env." >&2
  exit 1
fi

if ! docker compose --env-file "$KNOVAS_ENV" ps --status running docbridge-web 2>/dev/null | grep -q docbridge-web; then
  echo "docbridge-web is not running. Start the stack first: ./scripts/start.sh" >&2
  exit 1
fi

if [[ "$GRANT_ONLY" == true ]]; then
  # Granting the console role is not a password change: an account that did not
  # come from first-run bootstrap has no roles, so the Verwaltung link is hidden
  # and the routes answer 403. This repairs that without touching the credential.
  docker compose --env-file "$KNOVAS_ENV" exec -T -e ADMIN_EMAIL="$EMAIL" \
    docbridge-web python - <<'GRANT'
import os
from identity import db, users
email = os.environ["ADMIN_EMAIL"]
conn = db.connect()
repo = users.UserRepository(conn)
row = conn.execute("SELECT id FROM users WHERE email = %s", (email,)).fetchone()
if row is None:
    raise SystemExit(f"No account for {email}. Run without --grant-admin to create one.")
repo.grant_role(row[0], "admin")
print(f"{email} now holds: {', '.join(sorted(repo.roles_of(row[0]))) or '-'}")
GRANT
  exit 0
fi

# Read it here rather than as an argument: an argument lands in the shell
# history and in the process list of every user on this host.
printf 'New password for %s: ' "$EMAIL" >&2
read -rs NEW_PASSWORD; echo >&2
printf 'Repeat: ' >&2
read -rs CONFIRM; echo >&2
if [[ "$NEW_PASSWORD" != "$CONFIRM" ]]; then
  echo "Passwords do not match." >&2
  exit 1
fi

# Passed through the environment, not the command line, for the same reason.
docker compose --env-file "$KNOVAS_ENV" exec -T \
  -e ADMIN_EMAIL="$EMAIL" -e NEW_PASSWORD="$NEW_PASSWORD" \
  docbridge-web python - <<'PY'
import os
from identity import audit, db, passwords, users

email = os.environ["ADMIN_EMAIL"]
password = os.environ["NEW_PASSWORD"]

conn = db.connect()
repo = users.UserRepository(conn)
row = conn.execute("SELECT id FROM users WHERE email = %s", (email,)).fetchone()

try:
    if row is None:
        created = repo.create(
            email=email,
            display_name=email.split("@")[0],
            password=password,
            must_change_password=True,
        )
        repo.grant_role(created.id, "admin")
        audit.record(
            conn, action="admin.account_created_by_operator", actor=created,
            target_type="user", target_id=str(created.id), detail={"email": email},
        )
        print(f"Created {email} as an administrator. It must change this password at first sign-in.")
    else:
        # set_password also zeroes failed_attempts and clears locked_until, so
        # this doubles as the way out of a lockout.
        repo.set_password(row[0], password)
        print(f"Password reset for {email}. Any lockout is cleared.")

    account_id = created.id if row is None else row[0]
    roles = sorted(repo.roles_of(account_id))
    print(f"Roles: {', '.join(roles) or '-'}")
    if "admin" not in roles:
        print("NOTE: no 'admin' role, so the Verwaltung console stays hidden and would 403.")
        print(f"      Fix: ./scripts/admin-password.sh --grant-admin {email}")
except passwords.WeakPasswordError as exc:
    raise SystemExit(f"Refused: {exc}")
PY
