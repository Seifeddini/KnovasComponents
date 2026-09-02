"""Accounts, roles, and access-group grants.

The repository the rest of the identity package sits on. Three properties are
load-bearing and worth stating where they can be read:

    Authentication answers one question. ``authenticate`` returns a user or
    None. It never distinguishes "no such account" from "wrong password" from
    "disabled" from "locked" to its caller, because the login form must not
    become an account-enumeration oracle. The reason is logged, not returned.

    Disabling ends access. A disabled account fails ``authenticate`` and, via
    ``sessions``, loses any live session on its next request. That pairing is
    the whole of B1's "leaver".

    Access groups come back sorted and deduplicated. The B2 assertion signs
    this tuple, so a stable order keeps two assertions for the same user
    byte-comparable — useful in tests, in logs, and in a support conversation.

Plan: docs/superpowers/plans/2026-08-14-section-b-buildout.md (KC-B1-1, B1-5)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence
from uuid import UUID

from identity import passwords

logger = logging.getLogger(__name__)

#: Failed attempts against ONE account before it locks. Deliberately separate
#: from the per-IP throttle already in app.py:708 — that one bounds an address,
#: this one bounds an account, and an attacker with many addresses defeats the
#: first without touching the second.
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION = timedelta(minutes=15)


class UserError(Exception):
    """Base for account errors that a form should show verbatim."""


class EmailTakenError(UserError):
    """That address already has an account."""


class UnknownUserError(UserError):
    """No account with that id."""


class UnknownRoleError(UserError):
    """No such platform role."""


@dataclass(frozen=True)
class User:
    id: UUID
    email: str
    display_name: str
    status: str
    must_change_password: bool
    mfa_enrolled: bool
    locked_until: datetime | None
    roles: frozenset[str]

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    @property
    def is_locked(self) -> bool:
        if self.status == "locked":
            return True
        return bool(self.locked_until and self.locked_until > _now())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class UserRepository:
    """Accounts in the Platform's own PostgreSQL.

    Takes a live connection rather than a DSN so a caller can compose it into
    an existing transaction and so tests can hand it a throwaway schema.
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    # ── reads ──────────────────────────────────────────────────────────────

    def _row_by_email(self, email: str) -> Mapping[str, Any] | None:
        row = self._conn.execute(
            "SELECT id, email, display_name, password_hash, status, "
            "must_change_password, mfa_enrolled_at, failed_attempts, locked_until "
            "FROM users WHERE email = %s",
            (email,),
        ).fetchone()
        return None if row is None else self._as_mapping(row)

    def _row_by_id(self, user_id: UUID | str) -> Mapping[str, Any] | None:
        row = self._conn.execute(
            "SELECT id, email, display_name, password_hash, status, "
            "must_change_password, mfa_enrolled_at, failed_attempts, locked_until "
            "FROM users WHERE id = %s",
            (str(user_id),),
        ).fetchone()
        return None if row is None else self._as_mapping(row)

    @staticmethod
    def _as_mapping(row: Sequence[Any]) -> Mapping[str, Any]:
        keys = (
            "id", "email", "display_name", "password_hash", "status",
            "must_change_password", "mfa_enrolled_at", "failed_attempts",
            "locked_until",
        )
        return dict(zip(keys, row))

    def _to_user(self, row: Mapping[str, Any]) -> User:
        return User(
            id=row["id"],
            email=str(row["email"]),
            display_name=row["display_name"],
            status=row["status"],
            must_change_password=bool(row["must_change_password"]),
            mfa_enrolled=row["mfa_enrolled_at"] is not None,
            locked_until=row["locked_until"],
            roles=self.roles_of(row["id"]),
        )

    def get_by_email(self, email: str) -> User | None:
        row = self._row_by_email(email)
        return None if row is None else self._to_user(row)

    def get(self, user_id: UUID | str) -> User | None:
        row = self._row_by_id(user_id)
        return None if row is None else self._to_user(row)

    def list_all(self) -> list[User]:
        ids = [
            r[0] for r in self._conn.execute("SELECT id FROM users ORDER BY email")
        ]
        return [u for u in (self.get(i) for i in ids) if u is not None]

    # ── writes ─────────────────────────────────────────────────────────────

    def create(
        self,
        *,
        email: str,
        display_name: str,
        password: str | None = None,
        idp_subject: str | None = None,
        must_change_password: bool = False,
        created_by: UUID | str | None = None,
    ) -> User:
        """Create an account.

        ``password`` is hashed here, so the policy is enforced before the row
        exists — a rejected password leaves no half-made account behind.
        Omit it for a federated-only user; ``password_hash`` stays NULL and
        :meth:`authenticate` will never verify against it.

        Raises:
            WeakPasswordError: the policy rejected ``password``.
            EmailTakenError: that address already has an account.
        """
        password_hash = passwords.hash_password(password) if password else None
        if self._row_by_email(email) is not None:
            raise EmailTakenError(f"{email} already has an account.")
        try:
            row = self._conn.execute(
                "INSERT INTO users (email, display_name, password_hash, idp_subject, "
                "must_change_password, created_by) VALUES (%s, %s, %s, %s, %s, %s) "
                "RETURNING id",
                (
                    email, display_name, password_hash, idp_subject,
                    must_change_password,
                    str(created_by) if created_by else None,
                ),
            ).fetchone()
        except Exception as exc:  # noqa: BLE001 - narrowed by message below
            if "users_email_key" in str(exc) or "unique" in str(exc).lower():
                raise EmailTakenError(f"{email} already has an account.") from exc
            raise
        created = self.get(row[0])
        assert created is not None
        return created

    def set_password(self, user_id: UUID | str, password: str) -> None:
        """Replace the verifier and clear the forced-rotation flag.

        Raises:
            WeakPasswordError: the policy rejected ``password``. Nothing changes.
        """
        password_hash = passwords.hash_password(password)
        self._conn.execute(
            "UPDATE users SET password_hash = %s, must_change_password = FALSE, "
            "failed_attempts = 0, locked_until = NULL, updated_at = now() WHERE id = %s",
            (password_hash, str(user_id)),
        )

    def disable(self, user_id: UUID | str, *, by: UUID | str | None = None) -> None:
        """End this account's access. Sessions are revoked by ``sessions``."""
        self._conn.execute(
            "UPDATE users SET status = 'disabled', disabled_at = now(), "
            "disabled_by = %s, updated_at = now() WHERE id = %s",
            (str(by) if by else None, str(user_id)),
        )

    def enable(self, user_id: UUID | str) -> None:
        self._conn.execute(
            "UPDATE users SET status = 'active', disabled_at = NULL, disabled_by = NULL, "
            "failed_attempts = 0, locked_until = NULL, updated_at = now() WHERE id = %s",
            (str(user_id),),
        )

    def unlock(self, user_id: UUID | str) -> None:
        self._conn.execute(
            "UPDATE users SET failed_attempts = 0, locked_until = NULL, "
            "status = CASE WHEN status = 'locked' THEN 'active' ELSE status END, "
            "updated_at = now() WHERE id = %s",
            (str(user_id),),
        )

    # ── authentication ─────────────────────────────────────────────────────

    def authenticate(self, email: str, password: str) -> User | None:
        """Return the user when the password is right and the account may log in.

        Returns None for every kind of refusal — unknown account, wrong
        password, disabled, locked, federated-only. The distinction is logged
        for an operator and withheld from the caller, so the login form cannot
        be used to discover who has an account.
        """
        row = self._row_by_email(email)
        if row is None:
            # Not returning early on a missing account would be better still —
            # a dummy verify to level the timing — but the per-IP throttle at
            # app.py:708 already bounds how much timing an attacker can sample.
            logger.info("Login refused: no account for %s", email)
            return None

        user = self._to_user(row)
        if user.is_locked:
            logger.info("Login refused: account locked (%s)", email)
            return None
        if not user.is_active:
            logger.info("Login refused: account %s is %s", email, user.status)
            return None
        if not row["password_hash"]:
            logger.info("Login refused: %s is federated-only", email)
            return None

        if not passwords.verify_password(row["password_hash"], password):
            self._record_failure(row)
            logger.info("Login refused: wrong password for %s", email)
            return None

        if passwords.needs_rehash(row["password_hash"]):
            # The one moment the plaintext is available to re-hash with.
            self._conn.execute(
                "UPDATE users SET password_hash = %s WHERE id = %s",
                (passwords.hash_password(password), str(row["id"])),
            )
        self._conn.execute(
            "UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE id = %s",
            (str(row["id"]),),
        )
        return self.get(row["id"])

    def _record_failure(self, row: Mapping[str, Any]) -> None:
        attempts = int(row["failed_attempts"]) + 1
        locked_until = _now() + LOCKOUT_DURATION if attempts >= MAX_FAILED_ATTEMPTS else None
        self._conn.execute(
            "UPDATE users SET failed_attempts = %s, locked_until = %s WHERE id = %s",
            (attempts, locked_until, str(row["id"])),
        )

    # ── roles ──────────────────────────────────────────────────────────────

    def roles_of(self, user_id: UUID | str) -> frozenset[str]:
        rows = self._conn.execute(
            "SELECT r.key FROM user_roles ur JOIN roles r ON r.id = ur.role_id "
            "WHERE ur.user_id = %s",
            (str(user_id),),
        ).fetchall()
        return frozenset(r[0] for r in rows)

    def grant_role(
        self, user_id: UUID | str, role_key: str, *, by: UUID | str | None = None
    ) -> None:
        role = self._conn.execute(
            "SELECT id FROM roles WHERE key = %s", (role_key,)
        ).fetchone()
        if role is None:
            known = ", ".join(
                sorted(r[0] for r in self._conn.execute("SELECT key FROM roles"))
            )
            raise UnknownRoleError(f"No role {role_key!r}. Known roles: {known}.")
        self._conn.execute(
            "INSERT INTO user_roles (user_id, role_id, granted_by) VALUES (%s, %s, %s) "
            "ON CONFLICT (user_id, role_id) DO NOTHING",
            (str(user_id), role[0], str(by) if by else None),
        )

    def revoke_role(self, user_id: UUID | str, role_key: str) -> None:
        self._conn.execute(
            "DELETE FROM user_roles USING roles "
            "WHERE user_roles.role_id = roles.id AND user_roles.user_id = %s "
            "AND roles.key = %s",
            (str(user_id), role_key),
        )

    # ── access groups (what B2 signs) ──────────────────────────────────────

    def access_groups_of(self, user_id: UUID | str) -> tuple[str, ...]:
        rows = self._conn.execute(
            "SELECT group_id FROM user_access_groups WHERE user_id = %s ORDER BY group_id",
            (str(user_id),),
        ).fetchall()
        return tuple(r[0] for r in rows)

    def set_access_groups(
        self,
        user_id: UUID | str,
        group_ids: Iterable[str],
        *,
        source: str = "manual",
        by: UUID | str | None = None,
    ) -> tuple[str, ...]:
        """Replace this user's grants with ``group_ids``.

        Replace rather than merge, because the admin console shows the whole
        set and saves the whole set: a merge would make un-granting impossible
        through the only UI that exists.
        """
        wanted = sorted({g.strip() for g in group_ids if g and g.strip()})
        self._conn.execute(
            "DELETE FROM user_access_groups WHERE user_id = %s", (str(user_id),)
        )
        for group_id in wanted:
            self._conn.execute(
                "INSERT INTO user_access_groups (user_id, group_id, source, granted_by) "
                "VALUES (%s, %s, %s, %s)",
                (str(user_id), group_id, source, str(by) if by else None),
            )
        return tuple(wanted)
