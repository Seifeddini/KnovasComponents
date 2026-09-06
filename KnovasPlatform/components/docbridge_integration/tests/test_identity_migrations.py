"""Migration runner and identity schema, against a real PostgreSQL (KC-F2).

These are integration tests on purpose. The schema uses citext, uuid, jsonb,
inet and a CHECK constraint that enforces four-eyes at the storage layer;
exercising it against anything but PostgreSQL would prove nothing about what
ships.

Point PLATFORM_DB_TEST_DSN at a throwaway database. Without it the module
skips, so the suite still runs on a machine with no Docker.

    docker run -d --name knovas-platform-db-test \
      -e POSTGRES_PASSWORD=testpw -e POSTGRES_USER=platform \
      -e POSTGRES_DB=knovas_platform_test -p 55433:5432 postgres:15-alpine
"""

import os
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from identity import migrate  # noqa: E402

_DSN = os.environ.get(
    "PLATFORM_DB_TEST_DSN",
    "postgresql://platform:testpw@127.0.0.1:55433/knovas_platform_test",
)


def _reachable() -> bool:
    try:
        with psycopg.connect(_DSN, connect_timeout=3):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _reachable(), reason=f"No PostgreSQL at {_DSN}; see this module's docstring"
)


@pytest.fixture
def db():
    """A fresh, empty schema per test. Rolling back is not enough — the runner
    commits DDL, so each test gets its own namespace instead."""
    schema = f"t{uuid.uuid4().hex[:12]}"
    with psycopg.connect(_DSN, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
    conn = psycopg.connect(_DSN, autocommit=True)
    conn.execute(f'SET search_path TO "{schema}"')
    try:
        yield conn
    finally:
        conn.close()
        with psycopg.connect(_DSN, autocommit=True) as cleanup:
            cleanup.execute(f'DROP SCHEMA "{schema}" CASCADE')


def _tables(conn) -> set[str]:
    schema = conn.execute("SELECT current_schema()").fetchone()[0]
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
        (schema,),
    ).fetchall()
    return {r[0] for r in rows}


class TestTheRunner:
    def test_apply_creates_the_ledger(self, db):
        migrate.apply(db)
        assert "schema_migrations" in _tables(db)

    def test_apply_records_every_migration_it_ran(self, db):
        migrate.apply(db)
        applied = {r[0] for r in db.execute("SELECT version FROM schema_migrations")}
        assert applied == {m.version for m in migrate.discover()}

    def test_apply_is_idempotent(self, db):
        migrate.apply(db)
        first = db.execute("SELECT count(*) FROM schema_migrations").fetchone()[0]
        migrate.apply(db)
        assert db.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == first

    def test_migrations_run_in_filename_order(self):
        versions = [m.version for m in migrate.discover()]
        assert versions == sorted(versions)

    def test_pending_lists_everything_before_apply_and_nothing_after(self, db):
        assert migrate.pending(db)
        migrate.apply(db)
        assert migrate.pending(db) == []

    def test_an_edited_applied_migration_is_refused(self, db):
        """A checksum mismatch means the file changed after it ran. Applying
        more on top of an unknown schema state is how a database becomes
        unrecoverable, so this fails loudly instead."""
        migrate.apply(db)
        db.execute(
            "UPDATE schema_migrations SET checksum = 'tampered' WHERE version = %s",
            (migrate.discover()[0].version,),
        )
        with pytest.raises(migrate.MigrationChecksumError) as excinfo:
            migrate.apply(db)
        assert migrate.discover()[0].version in str(excinfo.value)

    def test_a_failing_migration_leaves_no_ledger_row(self, db):
        """Statement and ledger row commit together or not at all."""
        broken = migrate.Migration(version="9999_broken", sql="SELECT 1/0;")
        with pytest.raises(Exception):
            migrate.apply(db, migrations=[broken])
        applied = {r[0] for r in db.execute("SELECT version FROM schema_migrations")}
        assert "9999_broken" not in applied


class TestTheSchema:
    def test_every_planned_table_exists(self, db):
        migrate.apply(db)
        assert {
            "users",
            "roles",
            "user_roles",
            "user_access_groups",
            "access_group_cache",
            "sessions",
            "approval_requests",
            "audit_log",
            "ingestion_profiles",
            "settings",
        } <= _tables(db)

    def test_email_is_unique_case_insensitively(self, db):
        """citext, not lower(). Two people cannot hold one mailbox."""
        migrate.apply(db)
        db.execute(
            "INSERT INTO users (id, email, display_name, status) "
            "VALUES (gen_random_uuid(), 'Anna.Meier@kanzlei.ch', 'Anna Meier', 'active')"
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            db.execute(
                "INSERT INTO users (id, email, display_name, status) "
                "VALUES (gen_random_uuid(), 'anna.meier@kanzlei.ch', 'Impostor', 'active')"
            )

    def test_status_is_constrained_to_the_three_states(self, db):
        migrate.apply(db)
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                "INSERT INTO users (id, email, display_name, status) "
                "VALUES (gen_random_uuid(), 'x@kanzlei.ch', 'X', 'probably_fine')"
            )

    def test_a_federated_account_may_have_no_password(self, db):
        """password_hash is NULL for OIDC-only users; that must be legal."""
        migrate.apply(db)
        db.execute(
            "INSERT INTO users (id, email, display_name, status, idp_subject) "
            "VALUES (gen_random_uuid(), 'sso@kanzlei.ch', 'SSO', 'active', 'entra|abc')"
        )
        row = db.execute(
            "SELECT password_hash FROM users WHERE email = 'sso@kanzlei.ch'"
        ).fetchone()
        assert row[0] is None

    def test_the_builtin_roles_are_seeded(self, db):
        migrate.apply(db)
        keys = {r[0] for r in db.execute("SELECT key FROM roles")}
        assert {"admin", "approver", "ingestion_manager", "member"} <= keys

    def test_deleting_a_user_removes_their_group_grants(self, db):
        migrate.apply(db)
        uid = db.execute(
            "INSERT INTO users (id, email, display_name, status) VALUES "
            "(gen_random_uuid(), 'g@kanzlei.ch', 'G', 'active') RETURNING id"
        ).fetchone()[0]
        db.execute(
            "INSERT INTO user_access_groups (user_id, group_id, source) "
            "VALUES (%s, 'litigation', 'manual')",
            (uid,),
        )
        db.execute("DELETE FROM users WHERE id = %s", (uid,))
        assert db.execute("SELECT count(*) FROM user_access_groups").fetchone()[0] == 0


class TestFourEyesIsEnforcedByTheDatabase:
    """KC-B5-1 enforces this in the service too. Two places on purpose: a
    service bug must not be able to permit self-approval."""

    def _two_users(self, db):
        return [
            db.execute(
                "INSERT INTO users (id, email, display_name, status) VALUES "
                "(gen_random_uuid(), %s, 'U', 'active') RETURNING id",
                (email,),
            ).fetchone()[0]
            for email in ("req@kanzlei.ch", "apr@kanzlei.ch")
        ]

    def test_a_different_approver_is_accepted(self, db):
        migrate.apply(db)
        requester, approver = self._two_users(db)
        db.execute(
            "INSERT INTO approval_requests (id, kind, target_ref, payload, requested_by, "
            "expires_at, status, approved_by) VALUES (gen_random_uuid(), 'matter_delete', "
            "'node:1', '{}'::jsonb, %s, now() + interval '1 day', 'approved', %s)",
            (requester, approver),
        )
        assert db.execute("SELECT count(*) FROM approval_requests").fetchone()[0] == 1

    def test_self_approval_is_rejected_by_the_check_constraint(self, db):
        migrate.apply(db)
        requester, _ = self._two_users(db)
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                "INSERT INTO approval_requests (id, kind, target_ref, payload, requested_by, "
                "expires_at, status, approved_by) VALUES (gen_random_uuid(), 'matter_delete', "
                "'node:1', '{}'::jsonb, %s, now() + interval '1 day', 'approved', %s)",
                (requester, requester),
            )

    def test_a_pending_request_may_have_no_approver_yet(self, db):
        migrate.apply(db)
        requester, _ = self._two_users(db)
        db.execute(
            "INSERT INTO approval_requests (id, kind, target_ref, payload, requested_by, "
            "expires_at, status) VALUES (gen_random_uuid(), 'matter_delete', 'node:1', "
            "'{}'::jsonb, %s, now() + interval '1 day', 'pending')",
            (requester,),
        )
        assert db.execute(
            "SELECT approved_by FROM approval_requests"
        ).fetchone()[0] is None
