"""Where the identity database is and how to reach it.

Settings come from the environment the compose file sets, and the password
comes from a *file* by preference. A Docker secret is a file rather than an
environment variable for a reason worth keeping: environment variables show up
in ``docker inspect``, in ``/proc/<pid>/environ``, in a crash report, and in the
support bundle someone eventually e-mails.

Two small properties earn their tests:

    The trailing newline is stripped. ``docker secret`` and ``echo >`` both add
    one, and a password with a newline appended fails authentication in a way
    that takes an afternoon to find.

    ``repr`` and ``safe_dsn`` never carry the password. A settings object caught
    in a traceback should not publish the database credential into a log the
    firm forwards to Knovas.

Plan: docs/superpowers/plans/2026-08-14-section-b-buildout.md (KC-F1)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

# Defaults match the platform-db service in KnovasComponents/docker-compose.yml.
# Keeping them here rather than only in compose means a developer running the
# app outside Docker gets the same names.
DEFAULT_HOST = "platform-db"
DEFAULT_PORT = 5432
DEFAULT_DATABASE = "knovas_platform"
DEFAULT_USER = "platform"


class ConfigurationError(RuntimeError):
    """The identity database is not configured. The message is for an operator."""


@dataclass(frozen=True)
class DatabaseSettings:
    host: str
    port: int
    database: str
    user: str
    password: str = field(repr=False)  # never in a traceback

    @property
    def dsn(self) -> str:
        """A libpq URI. Percent-encoded, because generated secrets contain
        ``@``, ``/``, ``:`` and ``#``, any of which would otherwise be parsed as
        DSN syntax and produce a confusing connection error."""
        return (
            f"postgresql://{quote(self.user, safe='')}:{quote(self.password, safe='')}"
            f"@{self.host}:{self.port}/{quote(self.database, safe='')}"
        )

    @property
    def safe_dsn(self) -> str:
        """The same thing, for a log line."""
        return f"postgresql://{self.user}@{self.host}:{self.port}/{self.database}"


def _password_from_env() -> str:
    secret_file = (os.environ.get("PLATFORM_DB_PASSWORD_FILE") or "").strip()
    if secret_file:
        path = Path(secret_file)
        if not path.is_file():
            raise ConfigurationError(
                f"PLATFORM_DB_PASSWORD_FILE points at {secret_file}, which does not "
                "exist. In the standard setup this file is created by "
                "scripts/setup.sh and mounted as a Docker secret."
            )
        value = path.read_text(encoding="utf-8").strip()
        if not value:
            raise ConfigurationError(
                f"PLATFORM_DB_PASSWORD_FILE ({secret_file}) is empty."
            )
        return value

    value = (os.environ.get("PLATFORM_DB_PASSWORD") or "").strip()
    if not value:
        raise ConfigurationError(
            "The identity database password is not set. Provide "
            "PLATFORM_DB_PASSWORD_FILE (preferred — a file does not appear in "
            "`docker inspect`) or PLATFORM_DB_PASSWORD."
        )
    return value


def settings_from_env() -> DatabaseSettings:
    """Read connection settings, preferring the secret file for the password."""
    return DatabaseSettings(
        host=os.environ.get("PLATFORM_DB_HOST") or DEFAULT_HOST,
        port=int(os.environ.get("PLATFORM_DB_PORT") or DEFAULT_PORT),
        database=os.environ.get("PLATFORM_DB_NAME") or DEFAULT_DATABASE,
        user=os.environ.get("PLATFORM_DB_USER") or DEFAULT_USER,
        password=_password_from_env(),
    )


def connect(settings: DatabaseSettings | None = None, *, autocommit: bool = True):
    """Open one connection to the identity database.

    Imported lazily so that reading settings — and the whole test suite — does
    not require the driver to be installed.
    """
    import psycopg

    resolved = settings or settings_from_env()
    return psycopg.connect(resolved.dsn, autocommit=autocommit)
