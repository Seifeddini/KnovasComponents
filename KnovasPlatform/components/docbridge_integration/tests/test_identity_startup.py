"""Identity schema and first admin must be created at process start.

`bootstrap.ensure_admin` and `migrate.apply` existing as libraries is not
enough: gunicorn workers load `web_interface.wsgi:app` → `create_app`, and
until that path invokes them, IDENTITY_ENABLED=true is a Platform nobody can
log into.
"""
from __future__ import annotations

import json
import threading
import uuid

import pytest

from conftest import (
    DummyFileHandler,
    DummyKnovasClient,
    PLATFORM_DB_TEST_DSN,
    platform_db_reachable,
)

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(),
    reason=f"No PostgreSQL at {PLATFORM_DB_TEST_DSN}",
)


@pytest.fixture
def unmigrated_schema():
    """An empty schema — first boot, before any identity DDL has run."""
    import psycopg

    schema = f"t{uuid.uuid4().hex[:12]}"
    with psycopg.connect(PLATFORM_DB_TEST_DSN, autocommit=True) as setup:
        setup.execute(f'CREATE SCHEMA "{schema}"')
    conn = psycopg.connect(PLATFORM_DB_TEST_DSN, autocommit=True)
    conn.execute(f'SET search_path TO "{schema}"')
    try:
        yield conn, schema
    finally:
        conn.close()
        with psycopg.connect(PLATFORM_DB_TEST_DSN, autocommit=True) as cleanup:
            cleanup.execute(f'DROP SCHEMA "{schema}" CASCADE')


def _tables(conn, schema: str) -> set[str]:
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
        (schema,),
    ).fetchall()
    return {r[0] for r in rows}


def _identity_config(tmp_path) -> str:
    broker_dir = tmp_path / "broker_keys"
    broker_dir.mkdir(exist_ok=True)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "web:\n"
        '  secret_key: "a-strong-secret-for-tests-0123456789"\n'
        "  session_lifetime: 3600\n"
        "  session_cookie_secure: false\n"
        "  login:\n"
        "    enabled: true\n"
        '    company_name: "TestCo"\n'
        "  search:\n"
        "    results_per_page: 20\n"
        "identity:\n"
        "  enabled: true\n"
        f"  broker_key_dir: {json.dumps(str(broker_dir))}\n"
        "api:\n"
        '  base_url: "http://example.test"\n'
        '  client_id: "tenant-a"\n'
        "open:\n"
        "  companion_enabled: false\n",
        encoding="utf-8",
    )
    return str(config_path)


def _create_identity_app(tmp_path, monkeypatch, schema: str):
    monkeypatch.setenv(
        "PLATFORM_DB_DSN",
        f"{PLATFORM_DB_TEST_DSN}?options=-csearch_path%3D{schema}",
    )
    monkeypatch.delenv("PLATFORM_DB_PASSWORD_FILE", raising=False)
    DummyKnovasClient.health_result = True
    from web_interface import app as web_app

    monkeypatch.setattr(web_app, "KnovasAPIClient", DummyKnovasClient)
    monkeypatch.setattr(web_app, "AutoDocFileHandler", DummyFileHandler)
    return web_app.create_app(_identity_config(tmp_path))


def test_create_app_applies_migrations_and_creates_the_admin(
    unmigrated_schema, tmp_path, monkeypatch
):
    conn, schema = unmigrated_schema
    monkeypatch.setenv("PLATFORM_ADMIN_EMAIL", "chef@kanzlei.ch")
    monkeypatch.setenv("PLATFORM_ADMIN_PASSWORD", "korrektes-pferd-batterie")

    _create_identity_app(tmp_path, monkeypatch, schema)

    assert "users" in _tables(conn, schema)
    emails = [str(r[0]) for r in conn.execute("SELECT email FROM users")]
    assert "chef@kanzlei.ch" in emails
    roles = [
        r[0]
        for r in conn.execute(
            "SELECT r.key FROM user_roles ur "
            "JOIN roles r ON r.id = ur.role_id "
            "JOIN users u ON u.id = ur.user_id "
            "WHERE u.email = %s",
            ("chef@kanzlei.ch",),
        )
    ]
    assert "admin" in roles


def test_create_app_refuses_identity_without_admin_email(
    unmigrated_schema, tmp_path, monkeypatch
):
    _, schema = unmigrated_schema
    monkeypatch.delenv("PLATFORM_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("PLATFORM_ADMIN_PASSWORD", raising=False)

    from identity.bootstrap import BootstrapError

    with pytest.raises(BootstrapError, match="PLATFORM_ADMIN_EMAIL"):
        _create_identity_app(tmp_path, monkeypatch, schema)


def test_second_start_does_not_create_another_admin(
    unmigrated_schema, tmp_path, monkeypatch
):
    conn, schema = unmigrated_schema
    monkeypatch.setenv("PLATFORM_ADMIN_EMAIL", "chef@kanzlei.ch")
    monkeypatch.setenv("PLATFORM_ADMIN_PASSWORD", "korrektes-pferd-batterie")

    _create_identity_app(tmp_path, monkeypatch, schema)
    _create_identity_app(tmp_path, monkeypatch, schema)

    assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == 1


def test_concurrent_prepare_creates_exactly_one_admin(unmigrated_schema):
    """Two gunicorn workers must not race-corrupt migrations or duplicate admin."""
    import psycopg

    from identity.startup import prepare_identity

    conn, schema = unmigrated_schema
    dsn = f"{PLATFORM_DB_TEST_DSN}?options=-csearch_path%3D{schema}"
    errors: list[BaseException] = []

    def worker():
        worker_conn = psycopg.connect(dsn, autocommit=True)
        try:
            prepare_identity(
                worker_conn,
                email="chef@kanzlei.ch",
                password="korrektes-pferd-batterie",
            )
        except BaseException as exc:  # noqa: BLE001 — collect any worker crash
            errors.append(exc)
        finally:
            worker_conn.close()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == 1
