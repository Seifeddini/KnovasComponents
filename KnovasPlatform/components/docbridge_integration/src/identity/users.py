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
    They are the running direct grants plus the Core groups of the person's
    roles, at most 32 of them (RB-2, ``identity.roles``).

    A person's functions come from their roles (RB-1, RB-2). ``roles_of``
    returns the keys of the person's roles (``identity.roles``), so every gate
    reads them as before; the old per-key grants of migration 0001 are kept
    for one release as the way back and read by nothing. ``grant_role`` and
    ``revoke_role`` keep their meaning through roles.

Plan: docs/superpowers/plans/2026-08-14-section-b-buildout.md (KC-B1-1, B1-5);
roles: FEAT-2026-11-19-platform-mvp, RB-1 and RB-2 (docs/platform-mvp/work-orders/RB-1-2.md)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence
from uuid import UUID

from identity import passwords, roles as roles_mod

logger = logging.getLogger(__name__)

#: Failed attempts against ONE account before it locks. Deliberately separate
#: from the per-IP throttle already in app.py:708 — that one bounds an address,
#: this one bounds an account, and an attacker with many addresses defeats the
#: first without touching the second.
#:
#: Both are defaults, not policy: IDENTITY_ACCOUNT_LOCKOUT_ATTEMPTS and
#: IDENTITY_ACCOUNT_LOCKOUT_MINUTES override them, and 0 attempts switches
#: account locking off. A deployment on a trusted LAN may decide a locked-out
#: colleague costs more than the protection buys — but with it off, an online
#: password guess is bounded only by the per-IP throttle.
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION = timedelta(minutes=15)


def _lockout_policy() -> tuple[int, timedelta]:
    """Attempts before an account locks, and for how long.

    Read per call rather than at import, so changing it is a restart of the
    container and not a rebuild of the image.
    """
    raw_attempts = (os.environ.get("IDENTITY_ACCOUNT_LOCKOUT_ATTEMPTS") or "").strip()
    raw_minutes = (os.environ.get("IDENTITY_ACCOUNT_LOCKOUT_MINUTES") or "").strip()
    default_minutes = int(LOCKOUT_DURATION.total_seconds() // 60)
    try:
        attempts = int(raw_attempts) if raw_attempts else MAX_FAILED_ATTEMPTS
    except ValueError:
        logger.warning(
            "IDENTITY_ACCOUNT_LOCKOUT_ATTEMPTS=%r is not a number; using %d",
            raw_attempts, MAX_FAILED_ATTEMPTS,
        )
        attempts = MAX_FAILED_ATTEMPTS
    try:
        minutes = int(raw_minutes) if raw_minutes else default_minutes
    except ValueError:
        logger.warning(
            "IDENTITY_ACCOUNT_LOCKOUT_MINUTES=%r is not a number; using %d",
            raw_minutes, default_minutes,
        )
        minutes = default_minutes
    return max(0, attempts), timedelta(minutes=max(1, minutes))


class UserError(Exception):
    """Base for account errors that a form should show verbatim."""


class EmailTakenError(UserError):
    """That address already has an account."""


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
        max_attempts, duration = _lockout_policy()
        # 0 disables locking. Failures are still counted, so an operator can see
        # what has been tried and switching locking back on takes effect at once.
        locked_until = (
            _now() + duration if max_attempts > 0 and attempts >= max_attempts else None
        )
        self._conn.execute(
            "UPDATE users SET failed_attempts = %s, locked_until = %s WHERE id = %s",
            (attempts, locked_until, str(row["id"])),
        )

    # ── roles ──────────────────────────────────────────────────────────────

    def roles_of(self, user_id: UUID | str) -> frozenset[str]:
        """The person's functions: the keys of all their roles (RB-2). Every gate reads them here."""
        rows = self._conn.execute(
            "SELECT DISTINCT f.function FROM tenant_role_members m "
            "JOIN tenant_role_functions f ON f.role_id = m.role_id "
            "WHERE m.user_id = %s",
            (str(user_id),),
        ).fetchall()
        return frozenset(r[0] for r in rows)

    def grant_role(
        self, user_id: UUID | str, role_key: str, *, by: UUID | str | None = None
    ) -> None:
        """Give the person the function ``role_key`` through a role (RB-1, rule 8).

        ``admin`` puts the person into Administration, ``member`` into
        Mitglied, any other key into the role whose functions are exactly that
        key (``RoleStore.role_for_function``, created on first use). Granting
        twice is no change.

        Raises:
            UnknownRoleError: no such key. Nothing changes.
            RoleError: the person does not exist, or the role's Core group
                would be the person's 33rd group. Nothing changes.
        """
        role = self._conn.execute(
            "SELECT id FROM roles WHERE key = %s", (role_key,)
        ).fetchone()
        if role is None:
            known = ", ".join(
                sorted(r[0] for r in self._conn.execute("SELECT key FROM roles"))
            )
            raise UnknownRoleError(f"No role {role_key!r}. Known roles: {known}.")
        store = roles_mod.RoleStore(self._conn)
        with self._conn.transaction():
            # Every role's row first, in id order (identity.roles: one lock
            # order), so two role changes that cross roles queue, never deadlock.
            store.lock_all()
            target = store.role_for_function(role_key, by=by)
            store.add_member(target.id, user_id, by=by)

    def revoke_role(self, user_id: UUID | str, role_key: str) -> None:
        """Take exactly the function ``role_key`` from the person; every other function stays (RB-1, rule 8).

        The person leaves each role that gives the key, and gets that role's
        other functions back through single-function roles (``admin`` and
        ``member`` through Administration and Mitglied). ``admin`` is leaving
        Administration and ``member`` leaving Mitglied. A key the person holds
        through nothing is no change.

        Raises:
            LastAdministratorError: ``admin`` of the last active administrator.
            RoleError: the key comes from Administration or Mitglied and is not
                ``admin`` or ``member`` itself, so leaving would take more.
            Nothing changes on any refusal.
        """
        store = roles_mod.RoleStore(self._conn)
        with self._conn.transaction():
            # Every role's row first, in id order, as in grant_role: this may
            # leave several roles and join others.
            store.lock_all()
            holding = [r for r in store.roles_of_user(user_id) if role_key in r.functions]
            for role in holding:
                if (role.kind == roles_mod.ADMINISTRATION and role_key != roles_mod.ADMIN) or (
                        role.kind == roles_mod.MITGLIED and role_key != roles_mod.MEMBER):
                    raise roles_mod.RoleError(
                        "Diese Funktion kommt aus der Rolle Administration oder Mitglied "
                        "und kann nicht einzeln entzogen werden.")
            left = frozenset().union(*(r.functions for r in holding)) - {role_key}
            for role in holding:
                store.remove_member(role.id, user_id)
            for function in sorted(left - self.roles_of(user_id)):
                store.add_member(store.role_for_function(function).id, user_id)

    # ── access groups (what B2 signs) ──────────────────────────────────────

    def access_groups_of(self, user_id: UUID | str) -> tuple[str, ...]:
        """The groups this user's claim carries (RB-2), sorted and deduplicated.

        The direct grants that run today (a grant past its end date no longer
        counts, B14) plus the Core groups of the person's roles. What is stored
        comes back, even more than 32: the people page still shows such a
        person; the principal broker refuses to sign the claim.
        """
        return roles_mod.claim_groups(self._conn, user_id)

    def direct_access_groups_of(self, user_id: UUID | str) -> tuple[str, ...]:
        """The person's own grants that run today, sorted: without the groups of her roles.

        What the people page shows in its groups field and saves back with
        ``set_access_groups``. Showing the claim's groups there would copy a
        role's Core group into the direct grants on an unchanged save, and the
        copy would outlive the role.
        """
        rows = self._conn.execute(
            "SELECT group_id FROM user_access_groups WHERE user_id = %s "
            "AND (ends_on IS NULL OR ends_on >= current_date) ORDER BY group_id",
            (str(user_id),),
        ).fetchall()
        return tuple(r[0] for r in rows)

    def access_grants_of(self, user_id: UUID | str) -> list[dict]:
        """Every group grant of this user with its end date, ended ones too (the access window shows them)."""
        rows = self._conn.execute(
            "SELECT group_id, source, ends_on, granted_at FROM user_access_groups WHERE user_id = %s ORDER BY group_id",
            (str(user_id),),
        ).fetchall()
        return [{"group_id": r[0], "source": r[1], "ends_on": r[2].isoformat() if r[2] else None,
                 "granted_at": r[3].isoformat() if r[3] else None} for r in rows]

    def grant_access_group(self, user_id: UUID | str, group_id: str, *, ends_on: str | None = None,
                           by: UUID | str | None = None) -> None:
        """Grant one group (a second grant replaces the first, with its end date).

        Raises:
            TooManyGroupsError: the person would hold more than 32 groups,
                directly and through roles together. Nothing is written.
        """
        with self._conn.transaction(), roles_mod.group_cap(self._conn, [user_id]):
            self._conn.execute(
                "INSERT INTO user_access_groups (user_id, group_id, source, granted_by, ends_on) "
                "VALUES (%s, %s, 'manual', %s, %s) ON CONFLICT (user_id, group_id) DO UPDATE "
                "SET ends_on = EXCLUDED.ends_on, granted_by = EXCLUDED.granted_by, granted_at = now()",
                (str(user_id), str(group_id), str(by) if by else None, ends_on),
            )

    def revoke_access_group(self, user_id: UUID | str, group_id: str) -> bool:
        row = self._conn.execute(
            "DELETE FROM user_access_groups WHERE user_id = %s AND group_id = %s RETURNING group_id",
            (str(user_id), str(group_id)),
        ).fetchone()
        return row is not None

    def members_of_groups(self) -> dict[str, list[str]]:
        """{group id: [user ids]} of every person's claim groups, as ``access_groups_of`` reads them for one.

        The direct grants that run today plus the Core groups of the person's roles (FU-42): a person who holds a
        group through a role is a member of it too. Each list sorted, each person once per group.
        """
        rows = self._conn.execute(
            f"SELECT c.group_id, c.user_id FROM ({roles_mod.CLAIM_GROUP_ROWS}) c ORDER BY c.group_id, c.user_id"
        ).fetchall()
        out: dict[str, list[str]] = {}
        for gid, uid in rows:
            out.setdefault(str(gid), []).append(str(uid))
        return out

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

        Raises:
            TooManyGroupsError: the person would hold more than 32 groups,
                directly and through roles together. Nothing is written: the
                old grants stay.
        """
        wanted = sorted({g.strip() for g in group_ids if g and g.strip()})
        with self._conn.transaction(), roles_mod.group_cap(self._conn, [user_id]):
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
