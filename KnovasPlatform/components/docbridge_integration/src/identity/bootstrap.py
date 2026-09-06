"""The firm's first administrator, created once and never shipped.

A product that installs with a known default credential has, in practice, a
public one. So there is no seeded account here: on the first boot of an empty
database this generates a random one-time password, writes it to a file only
root can read, and forces the administrator to replace it before the session
becomes usable.

Two guards are worth naming:

    It runs when the database has *no accounts at all*, not merely no admins.
    An installation where someone has already created users has started; making
    a fresh administrator there would be a way in, not a convenience.

    A supplied password goes through the same policy as everyone else's,
    including the placeholder set ``app.py:701`` already refuses for
    ``COMPANY_LOGIN_PASSWORD``. An operator who worked around that check by
    pasting the placeholder here would recreate exactly the credential the
    check exists to stop.

Plan: docs/superpowers/plans/2026-08-14-section-b-buildout.md (KC-F3)
"""
from __future__ import annotations

import logging
import os
import secrets
import stat
from pathlib import Path

from identity import audit, passwords, users

logger = logging.getLogger(__name__)

#: Long enough that the generated value is not the weak link, short enough to
#: be retyped from a terminal once.
_GENERATED_PASSWORD_BYTES = 24


class BootstrapError(RuntimeError):
    """First-boot setup cannot proceed. The message is for an operator."""


def _validate_email(email: str) -> str:
    address = (email or "").strip()
    if not address:
        raise BootstrapError(
            "PLATFORM_ADMIN_EMAIL is not set. The Platform needs one address to "
            "create the firm's first administrator; there is no default account."
        )
    if "@" not in address or address.startswith("@") or address.endswith("@"):
        raise BootstrapError(
            f"PLATFORM_ADMIN_EMAIL={address!r} is not an e-mail address."
        )
    return address


def _write_secret(path: Path, password: str) -> None:
    """Write the one-time password so only the owner can read it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Create with restrictive permissions rather than relaxing them afterwards:
    # between an open() and a chmod() the file is world-readable, and that is
    # the whole window an attacker needs.
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(password + "\n")
    finally:
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            # Windows has no POSIX mode; the O_CREAT mode above is best effort.
            logger.debug("Could not set 0600 on %s", path)


def ensure_admin(
    conn,
    *,
    email: str,
    password: str | None = None,
    secret_path: str | Path = "/run/platform-admin-bootstrap",
) -> bool:
    """Create the first administrator if this database has no accounts.

    Returns True when an account was created, False when one already existed —
    so a restart is a no-op rather than a new credential being handed out.

    Raises:
        BootstrapError: the address is missing or malformed, or the supplied
            password fails the policy. Nothing is created.
    """
    address = _validate_email(email)
    repo = users.UserRepository(conn)

    existing = conn.execute("SELECT count(*) FROM users").fetchone()[0]
    if existing:
        logger.debug("Bootstrap skipped: %s account(s) already exist", existing)
        return False

    generated = password is None
    secret = password or _generate_password()
    try:
        created = repo.create(
            email=address,
            display_name=address.split("@")[0],
            password=secret,
            must_change_password=True,
        )
    except passwords.WeakPasswordError as exc:
        raise BootstrapError(
            f"The administrator password was refused: {exc}. "
            "Set PLATFORM_ADMIN_PASSWORD to a strong value, or leave it unset "
            "and one will be generated."
        ) from exc

    repo.grant_role(created.id, "admin")
    audit.record(
        conn,
        action="bootstrap.admin_created",
        actor=created,
        target_type="user",
        target_id=str(created.id),
        detail={"email": address, "password_generated": generated},
    )

    if generated:
        _write_secret(Path(secret_path), secret)
        logger.warning(
            "First boot: created administrator %s. The one-time password is in "
            "%s — sign in, change it, and delete the file.",
            address, secret_path,
        )
    else:
        # The operator already holds this secret; echoing it to disk would only
        # create a second copy to forget about.
        logger.warning(
            "First boot: created administrator %s with the supplied password. "
            "It must be changed at first sign-in.", address,
        )
    return True


def _generate_password() -> str:
    """A random one-time password that satisfies the policy by construction."""
    return secrets.token_urlsafe(_GENERATED_PASSWORD_BYTES)
