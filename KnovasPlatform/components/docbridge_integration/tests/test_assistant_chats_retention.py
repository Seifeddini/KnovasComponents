"""PMVP-23: deleting chats 90 days after their last message (decision D3), and the list's paging (FU-03), beyond the
frozen acceptance file.

``purge_expired_chats``: the days it takes, a run with nothing to delete, yesterday's run, an audit row that cannot be
written (the deletion is undone), and its log line. ``ChatRetentionJob``: one tick on a connection of its own, a tick
that never raises, the thread's cadence, and that it does not run in a test app. ``list_chats``: what ``limit`` and
``offset`` accept. This file is ASCII-only.
"""
from __future__ import annotations

import json
import logging

import pytest

import conftest
from identity import chats
from identity.chats import ChatRetentionJob, ChatStore, purge_expired_chats


def _chat(conn, person, title, days):
    store = ChatStore(conn)
    chat_id = store.create_chat(owner=person.id, title=title)
    store.add_message(chat_id, owner=person.id, role="user", text=f"{title}?")
    store.add_message(chat_id, owner=person.id, role="assistant", text="{}",
                      references=[{"number": 1, "pointer": "mandate/x.pdf", "page": 1, "title": "x", "text": "t"}])
    conn.execute("UPDATE assistant_chats SET updated_at = now() - make_interval(days => %s) WHERE id = %s",
                 (days, chat_id))
    return str(chat_id)


def _runs(conn):
    return [row[0] if isinstance(row[0], dict) else json.loads(row[0]) for row in conn.execute(
        "SELECT detail FROM audit_log WHERE action = 'assistant.chats.retention' ORDER BY occurred_at")]


def _count(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def _own_connection(conn):
    """A new connection to the test schema, as the job opens one."""
    import psycopg

    schema = conn.execute("SELECT current_schema()").fetchone()[0]
    return psycopg.connect(f"{conftest.PLATFORM_DB_TEST_DSN}?options=-csearch_path%3D{schema}", autocommit=True)


# --- purge_expired_chats --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("days", [0, -1, True, "90", 1.5])
def test_the_days_are_a_whole_number_from_1(platform_db, days):
    with pytest.raises(ValueError):
        purge_expired_chats(platform_db, days=days)
    assert _runs(platform_db) == []


def test_a_run_with_nothing_to_delete_is_recorded_and_ends_the_day(alice, platform_db):
    young = _chat(platform_db, alice, "Jung", 10)
    assert purge_expired_chats(platform_db) == 0
    assert _runs(platform_db) == [{"deleted": 0}]
    assert purge_expired_chats(platform_db) is None
    assert _count(platform_db, "assistant_chats") == 1 and young


def test_yesterdays_run_does_not_end_today(alice, platform_db):
    from identity import audit

    audit.record(platform_db, action=chats.RETENTION_ACTION, target_type=chats.RETENTION_TARGET_TYPE,
                 detail={"deleted": 4})
    platform_db.execute("UPDATE audit_log SET occurred_at = now() - interval '1 day' "
                        "WHERE action = 'assistant.chats.retention'")
    _chat(platform_db, alice, "Alt", 95)
    assert purge_expired_chats(platform_db) == 1


def test_another_days_limit_is_kept(alice, platform_db):
    _chat(platform_db, alice, "Monat", 31)
    _chat(platform_db, alice, "Woche", 7)
    assert purge_expired_chats(platform_db, days=30) == 1
    assert [c.title for c in ChatStore(platform_db).list_chats(owner=alice.id)] == ["Woche"]


def test_an_audit_row_that_cannot_be_written_undoes_the_deletion(alice, platform_db, monkeypatch):
    from identity import audit

    _chat(platform_db, alice, "Alt", 95)

    def broken(*args, **kwargs):
        raise RuntimeError("audit gone")

    monkeypatch.setattr(audit, "record_or_raise", broken)
    with pytest.raises(RuntimeError):
        purge_expired_chats(platform_db)
    assert _count(platform_db, "assistant_chats") == 1 and _count(platform_db, "assistant_messages") == 2
    assert _runs(platform_db) == []


def test_the_log_line_holds_the_count_and_nothing_else(alice, platform_db, caplog):
    chat_id = _chat(platform_db, alice, "Geheimer Mandant", 120)
    caplog.set_level(logging.DEBUG, logger="identity")
    purge_expired_chats(platform_db)
    lines = [r.getMessage() for r in caplog.records if r.name == "identity.chats"]
    assert lines == ["Chats: 1 deleted 90 days after their last message"]
    assert chat_id not in caplog.text and "Geheim" not in caplog.text and str(alice.id) not in caplog.text


# --- ChatRetentionJob ------------------------------------------------------------------------------------------------


class FakeConnection:
    def __init__(self):
        self.autocommit = False
        self.closed = False

    def close(self):
        self.closed = True


def test_a_tick_purges_on_a_connection_of_its_own_and_closes_it():
    opened, purged = [], []

    def connect():
        opened.append(FakeConnection())
        return opened[-1]

    job = ChatRetentionJob(connect=connect, purge=lambda conn: purged.append(conn) or 3)
    assert job.run_once() == 3 and job.run_once() == 3
    assert purged == opened and len(opened) == 2
    assert all(c.closed and c.autocommit for c in opened)


def test_a_tick_never_raises(caplog):
    def no_database():
        raise OSError("connection refused: secret-host")

    opened = []

    def connect():
        opened.append(FakeConnection())
        return opened[-1]

    def failing(conn):
        raise RuntimeError("Geheimer Mandant")

    with caplog.at_level(logging.DEBUG, logger="identity.chats"):
        assert ChatRetentionJob(connect=no_database).run_once() is None
        assert ChatRetentionJob(connect=connect, purge=failing).run_once() is None
    assert opened[0].closed
    assert [r.getMessage() for r in caplog.records] == ["Chats: the retention tick failed (OSError)",
                                                        "Chats: the retention tick failed (RuntimeError)"]


def test_a_tick_on_the_database_deletes_once_a_day(alice, platform_db):
    _chat(platform_db, alice, "Alt", 91)
    job = ChatRetentionJob(connect=lambda: _own_connection(platform_db))
    assert job.run_once() == 1
    assert job.run_once() is None
    assert _runs(platform_db) == [{"deleted": 1}]


def test_the_thread_ticks_every_hour_and_first_after_one_interval():
    waits, ticks = [], []
    answers = iter([False, False, True])                                    # two intervals pass, then halt

    def wait(seconds):
        waits.append(seconds)
        return next(answers)

    job = ChatRetentionJob(connect=FakeConnection, purge=lambda conn: ticks.append(1), wait=wait)
    job.run()
    assert chats.RETENTION_INTERVAL_SECONDS == 3600.0
    assert waits == [3600.0] * 3 and len(ticks) == 2


def test_the_thread_ends_without_a_tick_in_a_test_app():
    ticks = []
    job = ChatRetentionJob(connect=FakeConnection, purge=lambda conn: ticks.append(1), wait=lambda s: False,
                           keep_running=lambda: False)
    job.run()
    assert ticks == []


def test_install_starts_the_job_outside_testing_only(monkeypatch):
    from flask import Flask

    from web_interface import assistant_routes

    class Gate:
        def open_connection(self):
            return FakeConnection()

    def install(testing):
        app = Flask(f"app{testing}")
        app.testing = testing
        assistant_routes.install_assistant(app, client=None, identity_gate=Gate(), split=None, csrf_valid=bool,
                                           grant=lambda rows: None, page_context=dict, pdf_preview=lambda: False)
        return app, app.extensions[assistant_routes.EXTENSION]["retention"]

    _, none = install(True)
    assert none is None
    app, job = install(False)
    try:
        assert isinstance(job, ChatRetentionJob) and job._thread.is_alive() and job._thread.daemon
        assert job._keep_running() is True
        app.testing = True
        assert job._keep_running() is False                                  # ends at its next tick
    finally:
        job.stop()
        job._thread.join(timeout=5)
    assert not job._thread.is_alive()


def test_the_test_apps_job_does_not_run(workbench_app):
    job = workbench_app.extensions["assistant"]["retention"]
    assert job is None or job._keep_running() is False


# --- list_chats: limit and offset (FU-03) -----------------------------------------------------------------------------


@pytest.mark.parametrize("arguments", [{"limit": 0}, {"limit": -1}, {"limit": True}, {"limit": "3"},
                                       {"offset": -1}, {"offset": None}, {"offset": 1.0}])
def test_limit_and_offset_are_checked_before_the_database(alice, platform_db, arguments):
    with pytest.raises(ValueError):
        ChatStore(platform_db).list_chats(owner=alice.id, **arguments)


def test_an_offset_past_the_end_lists_nothing(alice, platform_db):
    store = ChatStore(platform_db)
    store.create_chat(owner=alice.id, title="Eins")
    assert store.list_chats(owner=alice.id, offset=1) == []
    assert [c.title for c in store.list_chats(owner=alice.id, limit=5, offset=0)] == ["Eins"]
