"""Apply the identity schema, once, in order, and refuse to guess.

Deliberately mirrors the shape of
``KnowledgeBase/app/src/CLI/manage_migrations.py`` — a ``schema_migrations``
ledger of ``(version, applied_at, checksum)``, dated ``.sql`` files applied in
filename order — so an operator who has run one stack can run the other without
learning a second tool.

The one behaviour worth stating outright: **an edited applied migration is an
error, not a warning.** A checksum mismatch means a file changed after it ran,
so the database's real shape is no longer described by anything on disk.
Applying more on top of an unknown state is how a database becomes
unrecoverable, so ``apply`` stops.

Plan: docs/superpowers/plans/2026-08-14-section-b-buildout.md (KC-F2)
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    VARCHAR(255) PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    checksum   VARCHAR(64) NOT NULL
)
"""


class MigrationChecksumError(RuntimeError):
    """An applied migration's file no longer matches what was applied."""


@dataclass(frozen=True)
class Migration:
    version: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def discover(directory: Path | None = None) -> list[Migration]:
    """Every migration on disk, in filename order.

    Filename order is version order by construction: the files are named
    ``NNNN_slug.sql``. Sorting on the name rather than on a parsed number keeps
    the rule visible in the directory listing, where an operator will look.
    """
    root = directory or MIGRATIONS_DIR
    return [
        Migration(version=path.stem, sql=path.read_text(encoding="utf-8"))
        for path in sorted(root.glob("*.sql"))
    ]


def _ensure_ledger(conn) -> None:
    conn.execute(_LEDGER_DDL)


def _applied(conn) -> dict[str, str]:
    _ensure_ledger(conn)
    return {
        row[0]: row[1]
        for row in conn.execute("SELECT version, checksum FROM schema_migrations")
    }


def pending(conn, migrations: list[Migration] | None = None) -> list[Migration]:
    """Migrations not yet recorded in the ledger, in order."""
    known = _applied(conn)
    return [m for m in (migrations or discover()) if m.version not in known]


def apply(conn, migrations: list[Migration] | None = None) -> list[str]:
    """Apply every pending migration. Returns the versions applied.

    Each migration and its ledger row commit together, so a failure leaves no
    row claiming a migration ran that did not.

    Raises:
        MigrationChecksumError: an already-applied file has changed on disk.
            Nothing further is applied.
    """
    todo = migrations or discover()
    known = _applied(conn)

    for migration in todo:
        recorded = known.get(migration.version)
        if recorded is not None and recorded != migration.checksum:
            raise MigrationChecksumError(
                f"{migration.version} was applied with checksum {recorded[:12]}… but the "
                f"file on disk now hashes to {migration.checksum[:12]}…. The database's "
                "shape is no longer described by this directory; reconcile by hand before "
                "applying anything else."
            )

    applied: list[str] = []
    for migration in todo:
        if migration.version in known:
            continue
        # One transaction per migration: PostgreSQL runs DDL transactionally,
        # so the statements and the ledger row land together or not at all.
        with conn.transaction():
            conn.execute(migration.sql)
            conn.execute(
                "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
                (migration.version, migration.checksum),
            )
        logger.info("Applied identity migration %s", migration.version)
        applied.append(migration.version)

    return applied
