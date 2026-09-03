"""Bring the identity schema and first administrator up at process start.

Gunicorn loads ``web_interface.wsgi:app`` in each worker. Two workers must
not interleave DDL or both try to create the first admin, so this takes a
session-level advisory lock, applies pending migrations, then runs the
idempotent bootstrap. A restart is a no-op.
"""
from __future__ import annotations

from pathlib import Path

from identity import bootstrap, migrate

# ('KNOV', 'IDTY') as two int4 keys. Stable across workers and restarts so
# every process contends on the same lock, not a per-connection one.
BOOT_LOCK_CLASS = 0x4B4E4F56
BOOT_LOCK_OBJECT = 0x49445459

DEFAULT_SECRET_PATH = "/run/platform-admin-bootstrap"


def prepare_identity(
    conn,
    *,
    email: str,
    password: str | None = None,
    secret_path: str | Path = DEFAULT_SECRET_PATH,
) -> bool:
    """Apply identity migrations and ensure the first administrator.

    Returns True when an administrator was created, False when one already
    existed. Raises ``bootstrap.BootstrapError`` if the email is missing or
    the password fails policy — the Platform must not start in that state.
    """
    conn.execute(
        "SELECT pg_advisory_lock(%s, %s)",
        (BOOT_LOCK_CLASS, BOOT_LOCK_OBJECT),
    )
    try:
        migrate.apply(conn)
        return bootstrap.ensure_admin(
            conn, email=email, password=password, secret_path=secret_path
        )
    finally:
        conn.execute(
            "SELECT pg_advisory_unlock(%s, %s)",
            (BOOT_LOCK_CLASS, BOOT_LOCK_OBJECT),
        )
