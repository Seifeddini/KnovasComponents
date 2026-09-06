"""Sessions that can be taken away.

The shipped Platform authenticated by putting one boolean in a signed cookie —
``session['company_login_ok']`` (``app.py:978``). That is not a session, it is
an assertion the browser carries: nothing on the server can withdraw it, so an
account disabled at 09:00 keeps working until the cookie happens to lapse.

Pflichtenheft B1 asks for a joiner-mover-**leaver** lifecycle, and leaver is the
half that has to be architectural. Here the cookie carries only an opaque id;
every request looks the row up, and re-checks the account behind it. Disabling,
locking or revoking therefore takes effect on the next request, not eventually.

The re-check on every resolve is the point, and it is why ``resolve`` takes a
user repository rather than trusting the row: a session row that outlives its
account's right to exist is exactly the bug this module is here to prevent.

Plan: docs/superpowers/plans/2026-08-14-section-b-buildout.md (KC-B1-2)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

DEFAULT_LIFETIME = timedelta(hours=12)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Session:
    id: UUID
    user: Any
    created_at: datetime
    expires_at: datetime
    mfa_passed: bool


class SessionStore:
    """Live sessions in the Platform's own PostgreSQL."""

    def __init__(self, conn: Any, user_repo: Any, lifetime: timedelta = DEFAULT_LIFETIME) -> None:
        self._conn = conn
        self._users = user_repo
        self._lifetime = lifetime

    def open(
        self,
        user: Any,
        *,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> Session:
        """Start a session. The returned id is what the cookie carries."""
        row = self._conn.execute(
            "INSERT INTO sessions (user_id, expires_at, ip, user_agent) "
            "VALUES (%s, %s, %s, %s) RETURNING id, created_at, expires_at, mfa_passed",
            (str(user.id), _now() + self._lifetime, ip, user_agent),
        ).fetchone()
        return Session(
            id=row[0], user=user, created_at=row[1], expires_at=row[2], mfa_passed=row[3]
        )

    def resolve(self, session_id: UUID | str | None) -> Session | None:
        """Return the live session for ``session_id``, or None.

        None covers every reason — unknown, expired, revoked, and the account
        behind it no longer being allowed in. The caller redirects to the login
        page in all of them, so there is nothing to gain from telling them apart
        and something to lose: a distinguishable "revoked" would tell a stolen
        cookie's holder that they were noticed.

        Never raises on a malformed id. The value arrives in a cookie and is
        therefore attacker-controlled.
        """
        if not session_id:
            return None
        try:
            row = self._conn.execute(
                "SELECT id, user_id, created_at, expires_at, mfa_passed FROM sessions "
                "WHERE id = %s AND revoked_at IS NULL AND expires_at > now()",
                (str(session_id),),
            ).fetchone()
        except Exception:  # noqa: BLE001 - bad uuid text, not a server fault
            logger.debug("Unparseable session id presented")
            return None
        if row is None:
            return None

        user = self._users.get(row[1])
        # The re-check that makes "leaver" immediate. A row alone is not
        # authorisation; the account behind it has to still be allowed in.
        if user is None or not user.is_active or user.is_locked:
            self.revoke(row[0])
            logger.info("Session revoked: account no longer permitted")
            return None

        self._conn.execute(
            "UPDATE sessions SET last_seen_at = now() WHERE id = %s", (str(row[0]),)
        )
        return Session(
            id=row[0], user=user, created_at=row[2], expires_at=row[3], mfa_passed=row[4]
        )

    def mark_mfa_passed(self, session_id: UUID | str) -> None:
        self._conn.execute(
            "UPDATE sessions SET mfa_passed = TRUE WHERE id = %s", (str(session_id),)
        )

    def revoke(self, session_id: UUID | str) -> None:
        self._conn.execute(
            "UPDATE sessions SET revoked_at = now() WHERE id = %s AND revoked_at IS NULL",
            (str(session_id),),
        )

    def revoke_all_for_user(self, user_id: UUID | str) -> int:
        """End every session for one account. Returns how many were live.

        What the admin console's "sign this person out everywhere" does, and
        what disabling an account should call alongside :meth:`UserRepository.disable`.
        """
        rows = self._conn.execute(
            "UPDATE sessions SET revoked_at = now() "
            "WHERE user_id = %s AND revoked_at IS NULL RETURNING id",
            (str(user_id),),
        ).fetchall()
        return len(rows)

    def list_for_user(self, user_id: UUID | str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, created_at, last_seen_at, expires_at, ip, user_agent "
            "FROM sessions WHERE user_id = %s AND revoked_at IS NULL "
            "AND expires_at > now() ORDER BY last_seen_at DESC",
            (str(user_id),),
        ).fetchall()
        keys = ("id", "created_at", "last_seen_at", "expires_at", "ip", "user_agent")
        return [dict(zip(keys, r)) for r in rows]

    def purge_expired(self) -> int:
        """Delete rows that can no longer authorise anything. Returns the count.

        Housekeeping, not security — ``resolve`` already refuses them. Kept so
        the table does not grow without bound on a busy installation.
        """
        rows = self._conn.execute(
            "DELETE FROM sessions WHERE expires_at <= now() RETURNING id"
        ).fetchall()
        return len(rows)
