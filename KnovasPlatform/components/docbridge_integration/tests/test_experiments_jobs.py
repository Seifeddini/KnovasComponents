"""The experiments job queue, its worker threads, the task wiring and the CLI.

The queue is only correct under concurrency, so these tests use the real
PostgreSQL: two connections for SKIP LOCKED, threads racing for claims,
fencing after a lease was taken over, dedupe and supersede, the shared rate
slot, the defer cap, and a worker thread that loses its connection.
"""

import json
import os
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from conftest import PLATFORM_DB_TEST_DSN, platform_db_reachable

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN"
)

from experiments import jobs  # noqa: E402
from experiments.errors import Unavailable, ValidationError  # noqa: E402
from experiments.jobs import (  # noqa: E402
    Job, JobQueue, JobWorker, PermanentError, RetryLater, take_rate_slot,
)
from experiments.settings import ExperimentsSettings  # noqa: E402

COMPONENT_DIR = Path(__file__).resolve().parents[1]


# -- helpers --------------------------------------------------------------------


@pytest.fixture
def db(platform_db):
    return platform_db


@pytest.fixture
def connect(platform_db):
    """Opens further connections into the test's schema; closes leftovers."""
    import psycopg

    schema = platform_db.execute("SELECT current_schema()").fetchone()[0]
    opened = []

    def _connect():
        conn = psycopg.connect(PLATFORM_DB_TEST_DSN, autocommit=True,
                               options=f"-c search_path={schema}")
        opened.append(conn)
        return conn

    _connect.opened = opened
    yield _connect
    for conn in opened:
        if not conn.closed:
            conn.close()


def job_row(conn, job_id):
    row = conn.execute(
        "SELECT status, attempts, priority, last_error, locked_by, dedupe_key, "
        "finished_at, EXTRACT(EPOCH FROM run_after - clock_timestamp())::float8, payload "
        "FROM exp_jobs WHERE id = %s", (job_id,)
    ).fetchone()
    if row is None:
        return None
    keys = ("status", "attempts", "priority", "last_error", "locked_by", "dedupe_key",
            "finished_at", "due_in", "payload")
    return dict(zip(keys, row))


def expire_lease(conn, job_id):
    conn.execute("UPDATE exp_jobs SET locked_until = clock_timestamp() - interval '1 second' "
                 "WHERE id = %s", (job_id,))


def make_due(conn, job_id):
    conn.execute("UPDATE exp_jobs SET run_after = clock_timestamp() - interval '1 second' "
                 "WHERE id = %s", (job_id,))


def free_slot(conn):
    conn.execute("UPDATE exp_rate_slots SET next_at = clock_timestamp() - interval '1 second'")


def job_count(conn):
    return conn.execute("SELECT count(*)::int FROM exp_jobs").fetchone()[0]


def worker(connect=None, handlers=None, on_dead=None, kinds=jobs.JOB_KINDS, **kw):
    kw.setdefault("lease_seconds", 60)
    kw.setdefault("poll_seconds", 0.05)
    return JobWorker(connect=connect or (lambda: None), handlers=handlers or {},
                     on_dead=on_dead or {}, kinds=kinds, **kw)


def make_experiment(conn, key="MKT-1", *, index_state="pending", index_error=None,
                    indexed_at=None):
    """A bare exp_experiments row (and the domain and type it needs), for
    the SQL the tasks run themselves; returns its id."""
    domain_id = conn.execute(
        "INSERT INTO exp_domains (key, name, id_prefix) VALUES ('marketing', 'Marketing', 'MKT') "
        "ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name RETURNING id::text").fetchone()[0]
    row = conn.execute("SELECT id::text FROM exp_types WHERE domain_id = %s AND key = 'plain'",
                       (domain_id,)).fetchone()
    if row is None:
        row = conn.execute("INSERT INTO exp_types (domain_id, key, name) "
                           "VALUES (%s, 'plain', 'Schlicht') RETURNING id::text",
                           (domain_id,)).fetchone()
        conn.execute("INSERT INTO exp_type_versions (type_id, version, definition) "
                     "VALUES (%s, 1, '{}'::jsonb)", (row[0],))
    return conn.execute(
        "INSERT INTO exp_experiments (key, domain_id, type_id, type_version, title, status, "
        "  index_state, index_error, indexed_at) "
        "VALUES (%s, %s, %s, 1, %s, 'draft', %s, %s, %s) RETURNING id::text",
        (key, domain_id, row[0], f"Titel {key}", index_state, index_error, indexed_at),
    ).fetchone()[0]


def index_state(conn, experiment_id):
    return conn.execute("SELECT index_state, index_error FROM exp_experiments WHERE id = %s",
                        (experiment_id,)).fetchone()


# -- enqueue ----------------------------------------------------------------------


def test_enqueue_inserts_a_pending_job(db):
    q = JobQueue(db)
    job_id = q.enqueue("pipeline", {"experiment_id": "e1"}, priority=50, max_attempts=3)
    assert isinstance(job_id, int)
    row = job_row(db, job_id)
    assert row["status"] == "pending"
    assert row["priority"] == 50
    assert row["payload"] == {"experiment_id": "e1"}
    assert row["due_in"] <= 0.5


def test_enqueue_refuses_unknown_kind_and_non_dict_payload(db):
    q = JobQueue(db)
    with pytest.raises(ValueError):
        q.enqueue("mine_bitcoin", {})
    with pytest.raises(ValueError):
        q.enqueue("index", ["not", "a", "dict"])
    assert job_count(db) == 0


def test_enqueue_delay_moves_run_after(db):
    q = JobQueue(db)
    job_id = q.enqueue("index", {"experiment_id": "e1"}, delay_seconds=60)
    assert 58 <= job_row(db, job_id)["due_in"] <= 60.5


def test_enqueue_with_same_dedupe_key_coalesces_to_earliest_and_most_urgent(db):
    q = JobQueue(db)
    first = q.enqueue("index", {"experiment_id": "e1"}, dedupe_key="index:e1",
                      delay_seconds=60, priority=200)
    second = q.enqueue("index", {"experiment_id": "e1"}, dedupe_key="index:e1",
                       delay_seconds=0, priority=10)
    assert first == second
    assert job_count(db) == 1
    row = job_row(db, first)
    assert row["priority"] == 10
    assert row["due_in"] <= 0.5
    # A later, less urgent request never pushes the job back.
    third = q.enqueue("index", {"experiment_id": "e1"}, dedupe_key="index:e1",
                      delay_seconds=600, priority=300)
    assert third == first
    row = job_row(db, first)
    assert row["priority"] == 10 and row["due_in"] <= 0.5


def test_dedupe_only_coalesces_with_pending_jobs(db):
    q = JobQueue(db)
    first = q.enqueue("index", {"experiment_id": "e1"}, dedupe_key="index:e1")
    claimed = q.claim("w1", kinds=("index",), lease_seconds=60)
    assert claimed.id == first
    # While it runs, a new edit must produce a new job: the running one may
    # already have read the old state.
    second = q.enqueue("index", {"experiment_id": "e1"}, dedupe_key="index:e1")
    assert second != first
    assert job_row(db, second)["status"] == "pending"


def test_jobs_without_dedupe_key_never_coalesce(db):
    q = JobQueue(db)
    a = q.enqueue("unindex", {"pointer": "p"})
    b = q.enqueue("unindex", {"pointer": "p"})
    assert a != b


# -- claim ------------------------------------------------------------------------


def test_claim_returns_job_and_marks_it_running(db):
    q = JobQueue(db)
    job_id = q.enqueue("pipeline", {"experiment_id": "e1"}, dedupe_key="pipeline:e1",
                       max_attempts=5)
    job = q.claim("worker-a", kinds=("pipeline",), lease_seconds=60)
    assert isinstance(job, Job)
    assert (job.id, job.kind, job.payload, job.attempts, job.max_attempts, job.locked_by) == (
        job_id, "pipeline", {"experiment_id": "e1"}, 1, 5, "worker-a")
    assert job.dedupe_key == "pipeline:e1"
    assert job.created_at.tzinfo is not None
    row = job_row(db, job_id)
    assert row["status"] == "running" and row["locked_by"] == "worker-a"
    assert q.claim("worker-b", kinds=("pipeline",), lease_seconds=60) is None


def test_claim_orders_by_priority_then_run_after_then_id(db):
    q = JobQueue(db)
    low = q.enqueue("pipeline", {"n": 1}, priority=200)
    high_late = q.enqueue("pipeline", {"n": 2}, priority=10)
    high_early = q.enqueue("pipeline", {"n": 3}, priority=10)
    db.execute("UPDATE exp_jobs SET run_after = clock_timestamp() - interval '1 minute' "
               "WHERE id = %s", (high_early,))
    order = [q.claim("w", kinds=("pipeline",), lease_seconds=60).id for _ in range(3)]
    assert order == [high_early, high_late, low]


def test_claim_skips_jobs_not_yet_due_and_other_kinds(db):
    q = JobQueue(db)
    q.enqueue("pipeline", {}, delay_seconds=120)
    q.enqueue("evaluate", {"evaluation_id": "v"})
    assert q.claim("w", kinds=("pipeline", "index"), lease_seconds=60) is None
    assert q.claim("w", kinds=(), lease_seconds=60) is None
    assert q.claim("w", kinds=("evaluate",), lease_seconds=60).kind == "evaluate"


def test_claim_skip_locked_between_two_connections(db, connect):
    other = connect()
    other.execute("SET statement_timeout = '3s'")  # a blocked claim fails loudly
    q = JobQueue(db)
    first = q.enqueue("pipeline", {"n": 1}, priority=1)
    second = q.enqueue("pipeline", {"n": 2}, priority=2)
    with db.transaction():
        # Connection 1 claims inside an open transaction: the row stays locked.
        mine = JobQueue(db).claim("conn-1", kinds=("pipeline",), lease_seconds=60)
        assert mine.id == first
        started = time.monotonic()
        theirs = JobQueue(other).claim("conn-2", kinds=("pipeline",), lease_seconds=60)
        assert time.monotonic() - started < 2.0
        assert theirs.id == second
        assert JobQueue(other).claim("conn-2", kinds=("pipeline",), lease_seconds=60) is None
    assert job_row(db, first)["locked_by"] == "conn-1"


def test_concurrent_claims_hand_out_every_job_exactly_once(db, connect):
    q = JobQueue(db)
    ids = {q.enqueue("pipeline", {"n": i}) for i in range(40)}
    seen = []
    lock = threading.Lock()
    errors = []

    def drain(name):
        try:
            conn = connect()
            queue = JobQueue(conn)
            while True:
                job = queue.claim(name, kinds=("pipeline",), lease_seconds=60)
                if job is None:
                    return
                with lock:
                    seen.append(job.id)
                assert queue.complete(job)
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=drain, args=(f"t{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not errors
    assert sorted(seen) == sorted(ids)
    assert JobQueue(db).counts()["done"] == 40


def test_expired_lease_is_taken_over_and_fencing_stops_the_old_owner(db):
    q = JobQueue(db)
    job_id = q.enqueue("pipeline", {"n": 1}, dedupe_key="pipeline:x")
    old = q.claim("worker-old", kinds=("pipeline",), lease_seconds=60)
    expire_lease(db, job_id)
    new = q.claim("worker-new", kinds=("pipeline",), lease_seconds=60)
    assert new.id == job_id and new.attempts == 2
    # The old owner comes back late: nothing it does may land.
    assert q.complete(old) is False
    assert q.retry(old, "x") is False
    assert q.defer(old, 10) is False
    assert q.fail(old, "x") is False
    row = job_row(db, job_id)
    assert row["status"] == "running" and row["locked_by"] == "worker-new"
    assert q.complete(new) is True
    assert job_row(db, job_id)["status"] == "done"


def test_same_worker_id_is_still_fenced_by_attempts(db):
    q = JobQueue(db)
    job_id = q.enqueue("pipeline", {})
    first = q.claim("same", kinds=("pipeline",), lease_seconds=60)
    expire_lease(db, job_id)
    second = q.claim("same", kinds=("pipeline",), lease_seconds=60)
    assert q.complete(first) is False
    assert q.complete(second) is True


def test_expired_lease_without_attempts_left_is_swept_not_claimed(db):
    q = JobQueue(db)
    job_id = q.enqueue("evaluate", {"evaluation_id": "v"}, max_attempts=1)
    q.claim("w", kinds=("evaluate",), lease_seconds=60)
    expire_lease(db, job_id)
    assert q.claim("w2", kinds=("evaluate",), lease_seconds=60) is None
    swept = q.sweep_expired()
    assert [j.id for j in swept] == [job_id]
    assert swept[0].status == "dead" and swept[0].last_error == jobs.MSG_LEASE_EXPIRED
    row = job_row(db, job_id)
    assert row["status"] == "dead" and row["finished_at"] is not None
    assert q.sweep_expired() == []


def test_running_job_with_live_lease_is_not_swept(db):
    q = JobQueue(db)
    q.enqueue("evaluate", {}, max_attempts=1)
    q.claim("w", kinds=("evaluate",), lease_seconds=60)
    assert q.sweep_expired() == []


# -- retry, defer, fail, complete ----------------------------------------------------


def test_retry_backs_off_exponentially_and_dies_at_max_attempts(db):
    q = JobQueue(db)
    job_id = q.enqueue("pipeline", {}, max_attempts=3)
    expected = [30, 60]
    for attempt, delay in enumerate(expected, start=1):
        job = q.claim("w", kinds=("pipeline",), lease_seconds=60)
        assert job.attempts == attempt
        assert q.retry(job, "Knovas nicht erreichbar.") is True
        row = job_row(db, job_id)
        assert row["status"] == "pending"
        assert delay - 2 <= row["due_in"] <= delay + 0.5
        assert row["last_error"] == "Knovas nicht erreichbar."
        assert row["locked_by"] is None
        make_due(db, job_id)
    job = q.claim("w", kinds=("pipeline",), lease_seconds=60)
    assert q.retry(job, "Zum dritten Mal.") is True
    assert job.status == "dead"
    row = job_row(db, job_id)
    assert row["status"] == "dead" and row["last_error"] == "Zum dritten Mal."


def test_backoff_is_capped_at_an_hour():
    assert jobs.backoff_seconds(1) == 30
    assert jobs.backoff_seconds(2) == 60
    assert jobs.backoff_seconds(8) == 3600
    assert jobs.backoff_seconds(10_000) == 3600


def test_defer_keeps_attempts_and_stores_reason(db):
    q = JobQueue(db)
    job_id = q.enqueue("index", {"experiment_id": "e"}, max_attempts=2)
    for _ in range(5):  # far more deferrals than attempts
        job = q.claim("w", kinds=("index",), lease_seconds=60)
        free_slot(db)
        assert job.attempts == 1
        assert q.defer(job, 45, "Warten auf den Platz.") is True
        assert job.status == "pending" and job.attempts == 0
        row = job_row(db, job_id)
        assert row["attempts"] == 0 and 43 <= row["due_in"] <= 45.5
        assert row["last_error"] == "Warten auf den Platz."
        make_due(db, job_id)


def test_defer_gives_up_after_a_day(db):
    q = JobQueue(db)
    job_id = q.enqueue("index", {"experiment_id": "e"})
    db.execute("UPDATE exp_jobs SET created_at = clock_timestamp() - interval '25 hours' "
               "WHERE id = %s", (job_id,))
    job = q.claim("w", kinds=("index",), lease_seconds=60)
    assert q.defer(job, 30) is True
    assert job.status == "dead"
    row = job_row(db, job_id)
    assert row["status"] == "dead" and row["last_error"] == "Zu lange zur\u00fcckgestellt."


def test_fail_and_complete(db):
    q = JobQueue(db)
    a = q.enqueue("pipeline", {"n": 1})
    b = q.enqueue("pipeline", {"n": 2})
    ja = q.claim("w", kinds=("pipeline",), lease_seconds=60)
    jb = q.claim("w", kinds=("pipeline",), lease_seconds=60)
    assert q.fail(ja, "Kaputt.") and ja.status == "dead"
    assert q.complete(jb) and jb.status == "done"
    assert job_row(db, a)["status"] == "dead" and job_row(db, a)["last_error"] == "Kaputt."
    assert job_row(db, b)["status"] == "done" and job_row(db, b)["finished_at"] is not None


def test_error_messages_are_clipped_and_flattened(db):
    q = JobQueue(db)
    job_id = q.enqueue("pipeline", {})
    job = q.claim("w", kinds=("pipeline",), lease_seconds=60)
    q.fail(job, "Zeile\n" + "x" * 2000)
    stored = job_row(db, job_id)["last_error"]
    assert "\n" not in stored and len(stored) == jobs.MAX_ERROR_CHARS


# -- supersede ---------------------------------------------------------------------


def test_retry_with_pending_twin_closes_as_superseded(db):
    q = JobQueue(db)
    running_id = q.enqueue("index", {"experiment_id": "e"}, dedupe_key="index:e", priority=10)
    job = q.claim("w", kinds=("index",), lease_seconds=60)
    twin = q.enqueue("index", {"experiment_id": "e"}, dedupe_key="index:e", priority=200,
                     delay_seconds=60)
    assert q.retry(job, "Knovas nicht erreichbar.") is True
    assert job.status == "superseded"
    row = job_row(db, running_id)
    assert row["status"] == "done" and row["last_error"] == jobs.MSG_SUPERSEDED
    twin_row = job_row(db, twin)
    assert twin_row["status"] == "pending"
    assert twin_row["priority"] == 10  # inherits the urgency


def test_defer_with_pending_twin_closes_as_superseded(db):
    q = JobQueue(db)
    running_id = q.enqueue("index", {"experiment_id": "e"}, dedupe_key="index:e")
    job = q.claim("w", kinds=("index",), lease_seconds=60)
    q.enqueue("index", {"experiment_id": "e"}, dedupe_key="index:e")
    assert q.defer(job, 30) is True
    assert job_row(db, running_id)["status"] == "done"
    assert JobQueue(db).counts()["pending"] == 1


def test_supersede_wins_over_dying_at_max_attempts(db):
    q = JobQueue(db)
    q.enqueue("index", {"experiment_id": "e"}, dedupe_key="index:e", max_attempts=1)
    job = q.claim("w", kinds=("index",), lease_seconds=60)
    q.enqueue("index", {"experiment_id": "e"}, dedupe_key="index:e")
    q.retry(job, "x")
    assert job.status == "superseded"
    assert JobQueue(db).counts()["dead"] == 0


def test_unique_violation_race_takes_the_supersede_path(db, monkeypatch):
    q = JobQueue(db)
    running_id = q.enqueue("index", {"experiment_id": "e"}, dedupe_key="index:e")
    job = q.claim("w", kinds=("index",), lease_seconds=60)
    twin = q.enqueue("index", {"experiment_id": "e"}, dedupe_key="index:e")
    real = JobQueue._pending_duplicate
    calls = {"n": 0}

    def racing(self, key, exclude_id):
        # The first look happens "before" the concurrent enqueue committed.
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return real(self, key, exclude_id)

    monkeypatch.setattr(JobQueue, "_pending_duplicate", racing)
    assert q.retry(job, "x") is True
    assert calls["n"] == 2
    assert job.status == "superseded"
    assert job_row(db, running_id)["status"] == "done"
    assert job_row(db, twin)["status"] == "pending"
    # The failed UPDATE was rolled back to its savepoint; the connection is fine.
    assert db.execute("SELECT 1").fetchone() == (1,)


def test_retry_without_twin_goes_back_to_pending_with_its_key(db):
    q = JobQueue(db)
    job_id = q.enqueue("index", {"experiment_id": "e"}, dedupe_key="index:e")
    job = q.claim("w", kinds=("index",), lease_seconds=60)
    assert q.retry(job, "x") is True
    row = job_row(db, job_id)
    assert row["status"] == "pending" and row["dedupe_key"] == "index:e"
    # And it coalesces again with new requests.
    assert q.enqueue("index", {"experiment_id": "e"}, dedupe_key="index:e") == job_id


# -- rate slot ---------------------------------------------------------------------


def test_take_rate_slot_spaces_uploads(db):
    free_slot(db)
    assert take_rate_slot(db, "knovas_init", 2) == 0.0
    wait = take_rate_slot(db, "knovas_init", 2)
    assert 29.0 <= wait <= 30.5
    free_slot(db)
    assert take_rate_slot(db, "knovas_init", 60) == 0.0
    assert 0 < take_rate_slot(db, "knovas_init", 60) <= 1.01


def test_take_rate_slot_is_shared_between_connections(db, connect):
    other = connect()
    free_slot(db)
    assert take_rate_slot(db, "knovas_init", 1) == 0.0
    assert take_rate_slot(other, "knovas_init", 1) > 50


def test_take_rate_slot_tolerates_bad_rate_and_missing_row(db):
    db.execute("DELETE FROM exp_rate_slots WHERE name = 'knovas_init'")
    assert take_rate_slot(db, "knovas_init", 0) == 0.0  # recreated, rate floored to 1/min
    assert take_rate_slot(db, "knovas_init", "x") > 50


def test_index_jobs_wait_for_the_rate_slot_other_kinds_do_not(db):
    q = JobQueue(db)
    index_id = q.enqueue("index", {"experiment_id": "e"}, priority=1)
    pipeline_id = q.enqueue("pipeline", {"experiment_id": "e"}, priority=100)
    db.execute("UPDATE exp_rate_slots SET next_at = clock_timestamp() + interval '1 minute'")
    job = q.claim("w", kinds=("index", "pipeline"), lease_seconds=60)
    assert job.id == pipeline_id
    assert q.claim("w", kinds=("index",), lease_seconds=60) is None
    free_slot(db)
    assert q.claim("w", kinds=("index",), lease_seconds=60).id == index_id


def test_stuck_rate_slot_is_pulled_back(db):
    db.execute("UPDATE exp_rate_slots SET next_at = clock_timestamp() + interval '3 days'")
    assert jobs.reset_stuck_rate_slots(db) == 1
    assert take_rate_slot(db, "knovas_init", 2) == 0.0
    # A normal, one-interval-ahead slot is left alone.
    assert jobs.reset_stuck_rate_slots(db) == 0


# -- housekeeping queries ---------------------------------------------------------------


def test_counts_failures_and_purge(db):
    q = JobQueue(db)
    for i in range(3):
        q.enqueue("pipeline", {"n": i})
    j1 = q.claim("w", kinds=("pipeline",), lease_seconds=60)
    j2 = q.claim("w", kinds=("pipeline",), lease_seconds=60)
    q.fail(j1, "Erster Fehler.")
    q.complete(j2)
    db.execute("UPDATE exp_jobs SET status = 'dead', last_error = NULL, "
               "finished_at = clock_timestamp() + interval '1 second' "
               "WHERE status = 'pending'")
    assert q.counts() == {"pending": 0, "running": 0, "done": 1, "dead": 2}
    failures = q.recent_failures()
    assert [f["error"] for f in failures] == [jobs.MSG_UNKNOWN_ERROR, "Erster Fehler."]
    assert all(f["kind"] == "pipeline" and f["at"].endswith("+00:00") for f in failures)
    assert q.purge_finished() == 0
    db.execute("UPDATE exp_jobs SET finished_at = clock_timestamp() - interval '8 days' "
               "WHERE id = %s", (j2.id,))
    q.enqueue("pipeline", {"fresh": True})
    assert q.purge_finished() == 1
    assert q.counts()["pending"] == 1


def test_active_dedupe_keys_and_cancel_pending(db):
    q = JobQueue(db)
    q.enqueue("index", {"experiment_id": "a"}, dedupe_key="index:a")
    q.enqueue("index", {"experiment_id": "b"}, dedupe_key="index:b")
    q.claim("w", kinds=("index",), lease_seconds=60)  # a is running now
    q.enqueue("pipeline", {"experiment_id": "c"}, dedupe_key="pipeline:c")
    assert q.active_dedupe_keys(["index:a", "index:b", "index:c"]) == {"index:a", "index:b"}
    assert q.active_dedupe_keys([]) == set()
    assert q.cancel_pending(("index",)) == 1
    assert q.counts() == {"pending": 1, "running": 1, "done": 0, "dead": 0}


# -- JobWorker.run_once ---------------------------------------------------------------


def test_run_once_completes_a_successful_job(db):
    seen = []
    w = worker(handlers={"pipeline": lambda conn, job: seen.append(job.payload)})
    job_id = JobQueue(db).enqueue("pipeline", {"experiment_id": "e"})
    assert w.run_once(db) is True
    assert seen == [{"experiment_id": "e"}]
    assert job_row(db, job_id)["status"] == "done"
    assert w.run_once(db) is False


def test_run_once_defers_on_retry_later(db):
    def handler(conn, job):
        raise RetryLater(120, "Warten auf den Platz.")

    w = worker(handlers={"pipeline": handler})
    job_id = JobQueue(db).enqueue("pipeline", {})
    assert w.run_once(db)
    row = job_row(db, job_id)
    assert row["status"] == "pending" and row["attempts"] == 0
    assert 118 <= row["due_in"] <= 120.5
    assert row["last_error"] == "Warten auf den Platz."


def test_run_once_marks_permanent_errors_dead_and_runs_on_dead(db):
    dead = []

    def handler(conn, job):
        raise PermanentError("Knovas hat das Dokument abgelehnt (HTTP 400).")

    w = worker(handlers={"index": handler},
               on_dead={"index": lambda conn, job: dead.append((job.id, job.last_error))})
    free_slot(db)
    job_id = JobQueue(db).enqueue("index", {"experiment_id": "e"})
    w.run_once(db)
    assert job_row(db, job_id)["status"] == "dead"
    assert dead == [(job_id, "Knovas hat das Dokument abgelehnt (HTTP 400).")]


def test_handled_permanent_error_skips_on_dead(db):
    dead = []

    def handler(conn, job):
        raise PermanentError("Schon vermerkt.", handled=True)

    w = worker(handlers={"pipeline": handler}, on_dead={"pipeline": lambda c, j: dead.append(j)})
    job_id = JobQueue(db).enqueue("pipeline", {})
    w.run_once(db)
    assert job_row(db, job_id)["status"] == "dead"
    assert dead == []


def test_experiments_error_is_retried_with_its_german_message(db):
    def handler(conn, job):
        raise Unavailable("Knovas nicht erreichbar.")

    w = worker(handlers={"pipeline": handler})
    job_id = JobQueue(db).enqueue("pipeline", {})
    w.run_once(db)
    row = job_row(db, job_id)
    assert row["status"] == "pending" and row["attempts"] == 1
    assert row["last_error"] == "Knovas nicht erreichbar."


def test_unexpected_exception_text_never_reaches_the_database(db, caplog):
    def handler(conn, job):
        raise RuntimeError("password=hunter2 at /secret/path")

    w = worker(handlers={"pipeline": handler})
    job_id = JobQueue(db).enqueue("pipeline", {})
    with caplog.at_level("ERROR"):
        w.run_once(db)
    row = job_row(db, job_id)
    assert row["status"] == "pending"
    assert row["last_error"] == jobs.MSG_UNEXPECTED
    assert "hunter2" not in json.dumps(JobQueue(db).recent_failures())
    assert "hunter2" in caplog.text  # the detail is in the log, where it belongs


def test_job_dies_after_max_attempts_and_on_dead_sees_it(db):
    dead = []

    def handler(conn, job):
        raise ValueError("nope")

    w = worker(handlers={"pipeline": handler},
               on_dead={"pipeline": lambda conn, job: dead.append((job.status, job.last_error))})
    job_id = JobQueue(db).enqueue("pipeline", {}, max_attempts=2)
    w.run_once(db)
    make_due(db, job_id)
    w.run_once(db)
    assert job_row(db, job_id)["status"] == "dead"
    assert dead == [("dead", jobs.MSG_UNEXPECTED)]


def test_failing_on_dead_hook_does_not_escape(db):
    def boom(conn, job):
        raise RuntimeError("hook broke")

    w = worker(handlers={"pipeline": lambda c, j: (_ for _ in ()).throw(PermanentError("x"))},
               on_dead={"pipeline": boom})
    JobQueue(db).enqueue("pipeline", {})
    assert w.run_once(db) is True
    assert db.execute("SELECT 1").fetchone() == (1,)


def test_missing_handler_marks_the_job_dead(db):
    dead = []
    w = worker(handlers={}, on_dead={"unindex": lambda c, j: dead.append(j.last_error)})
    job_id = JobQueue(db).enqueue("unindex", {"pointer": "p"})
    w.run_once(db)
    assert job_row(db, job_id)["status"] == "dead"
    assert dead == [jobs.MSG_NO_HANDLER]


def test_handler_leaving_a_broken_transaction_is_cleaned_up(db):
    from psycopg import pq

    def handler(conn, job):
        conn.execute("BEGIN")
        conn.execute("SELECT 1 / 0")

    w = worker(handlers={"pipeline": handler})
    job_id = JobQueue(db).enqueue("pipeline", {})
    w.run_once(db)
    assert db.info.transaction_status == pq.TransactionStatus.IDLE
    assert job_row(db, job_id)["status"] == "pending"


def test_system_exit_in_a_handler_is_just_a_failure(db):
    def handler(conn, job):
        raise SystemExit(3)

    w = worker(handlers={"pipeline": handler})
    job_id = JobQueue(db).enqueue("pipeline", {})
    assert w.run_once(db) is True
    assert job_row(db, job_id)["last_error"] == jobs.MSG_UNEXPECTED


def test_keyboard_interrupt_is_recorded_then_passed_on(db):
    def handler(conn, job):
        raise KeyboardInterrupt

    w = worker(handlers={"pipeline": handler})
    job_id = JobQueue(db).enqueue("pipeline", {})
    with pytest.raises(KeyboardInterrupt):
        w.run_once(db)
    assert job_row(db, job_id)["status"] == "pending"


def test_run_maintenance_sweeps_runs_hook_and_purges_independently(db):
    q = JobQueue(db)
    swept_id = q.enqueue("evaluate", {"evaluation_id": "v"}, max_attempts=1)
    q.claim("w", kinds=("evaluate",), lease_seconds=60)
    expire_lease(db, swept_id)
    old_id = q.enqueue("pipeline", {})
    job = q.claim("w", kinds=("pipeline",), lease_seconds=60)
    q.complete(job)
    db.execute("UPDATE exp_jobs SET finished_at = clock_timestamp() - interval '30 days' "
               "WHERE id = %s", (old_id,))
    dead = []

    def failing_hook(conn):
        raise RuntimeError("maintenance broke")

    w = worker(on_dead={"evaluate": lambda c, j: dead.append(j.id)}, maintenance=failing_hook)
    w.run_maintenance(db)
    assert dead == [swept_id]
    assert job_row(db, old_id) is None  # purged although the hook failed


# -- JobWorker as a thread -------------------------------------------------------------


def wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_worker_thread_processes_jobs_and_closes_its_connection(db, connect):
    done = []
    w = worker(connect=connect, handlers={"pipeline": lambda c, j: done.append(j.id)},
               kinds=("pipeline",), poll_seconds=0.02)
    ids = [JobQueue(db).enqueue("pipeline", {"n": i}) for i in range(3)]
    w.start()
    try:
        assert wait_for(lambda: len(done) == 3)
    finally:
        w.stop()
        w.join(5)
    assert not w.is_alive()
    assert sorted(done) == sorted(ids)
    assert connect.opened and all(c.closed for c in connect.opened)
    # Session settings were applied to the worker's connection.
    assert w.daemon is True


def test_worker_thread_survives_connect_failures(db, connect):
    attempts = {"n": 0}

    def flaky_connect():
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise OSError("database is starting up")
        return connect()

    done = []
    w = worker(connect=flaky_connect, handlers={"pipeline": lambda c, j: done.append(j.id)},
               kinds=("pipeline",), poll_seconds=0.02)
    w.reconnect_initial = 0.01
    JobQueue(db).enqueue("pipeline", {})
    w.start()
    try:
        assert wait_for(lambda: len(done) == 1)
    finally:
        w.stop()
        w.join(5)
    assert attempts["n"] >= 3
    assert all(c.closed for c in connect.opened)


def test_worker_thread_reconnects_after_its_connection_is_killed(db, connect):
    done = []
    w = worker(connect=connect, handlers={"pipeline": lambda c, j: done.append(j.id)},
               kinds=("pipeline",), poll_seconds=0.02)
    w.reconnect_initial = 0.01
    JobQueue(db).enqueue("pipeline", {"n": 1})
    w.start()
    try:
        assert wait_for(lambda: len(done) == 1)
        first = connect.opened[0]
        db.execute("SELECT pg_terminate_backend(%s)", (first.info.backend_pid,))
        JobQueue(db).enqueue("pipeline", {"n": 2})
        assert wait_for(lambda: len(done) == 2)
    finally:
        w.stop()
        w.join(5)
    assert not w.is_alive()
    assert len(connect.opened) >= 2
    assert all(c.closed for c in connect.opened)


def test_worker_thread_keeps_running_through_failing_handlers(db, connect):
    calls = []

    def handler(conn, job):
        calls.append(job.id)
        if len(calls) == 1:
            raise SystemExit("a library called sys.exit")
        if len(calls) == 2:
            raise MemoryError("pretend")

    w = worker(connect=connect, handlers={"pipeline": handler}, kinds=("pipeline",),
               poll_seconds=0.02)
    for i in range(3):
        JobQueue(db).enqueue("pipeline", {"n": i})
    w.start()
    try:
        assert wait_for(lambda: len(calls) >= 3)
        assert w.is_alive()
    finally:
        w.stop()
        w.join(5)


def test_worker_runs_first_maintenance_on_schedule(db, connect):
    ran = []
    w = worker(connect=connect, handlers={}, kinds=("pipeline",), poll_seconds=0.02,
               maintenance=lambda conn: ran.append(conn))
    w._next_maintenance_at = time.monotonic()  # due now
    w.start()
    try:
        assert wait_for(lambda: len(ran) == 1)
    finally:
        w.stop()
        w.join(5)
    assert len(ran) == 1  # the next pass is ~10 minutes away


# -- start_workers_once ----------------------------------------------------------------


@pytest.fixture
def no_thread_start(monkeypatch):
    started = []
    monkeypatch.setattr(JobWorker, "start", lambda self: started.append(self))
    monkeypatch.setattr(jobs, "_workers_by_pid", {})
    # No exit hook of the test process may point at these unstarted workers.
    monkeypatch.setattr(jobs, "_exit_hooks_for_pids", set())
    monkeypatch.setattr(jobs.atexit, "register", lambda fn, *args: None)
    return started


def settings(**kw):
    kw.setdefault("enabled", True)
    kw.setdefault("worker_enabled", True)
    return ExperimentsSettings(**kw)


def test_start_workers_once_starts_two_threads_once_per_process(no_thread_start, monkeypatch):
    s = settings(runner_timeout_seconds=90, worker_poll_seconds=2.0)
    kw = dict(settings=s, connect=lambda: None, handlers={}, on_dead={}, maintenance=None)
    first = jobs.start_workers_once(**kw)
    again = jobs.start_workers_once(**kw)
    assert first == again and len(no_thread_start) == 2
    index_worker, evaluate_worker = first
    assert index_worker.kinds == ("index", "unindex", "pipeline")
    assert index_worker.lease_seconds == 600
    assert evaluate_worker.kinds == ("evaluate",)
    assert evaluate_worker.lease_seconds == 210
    assert index_worker.poll_seconds == 2.0
    assert index_worker.worker_id != evaluate_worker.worker_id
    assert str(os.getpid()) in index_worker.worker_id
    # A forked child (another pid) starts its own pair.
    monkeypatch.setattr(jobs.os, "getpid", lambda: -42)
    child = jobs.start_workers_once(**kw)
    assert child != first and len(no_thread_start) == 4


def test_start_workers_once_is_thread_safe(no_thread_start):
    s = settings()
    results = []
    barrier = threading.Barrier(8)

    def call():
        barrier.wait()
        results.append(jobs.start_workers_once(settings=s, connect=lambda: None, handlers={},
                                               on_dead={}, maintenance=None))

    threads = [threading.Thread(target=call) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert len(no_thread_start) == 2
    assert all(r == results[0] for r in results)


def test_start_workers_once_does_nothing_when_off(no_thread_start):
    kw = dict(connect=lambda: None, handlers={}, on_dead={}, maintenance=None)
    assert jobs.start_workers_once(settings=settings(enabled=False), **kw) == []
    assert jobs.start_workers_once(settings=settings(worker_enabled=False), **kw) == []
    assert no_thread_start == []


def test_retry_later_normalises_delay():
    assert RetryLater(float("nan")).delay_seconds == 60.0
    assert RetryLater(-5).delay_seconds == 0.0
    assert RetryLater(10 ** 9).delay_seconds == jobs.MAX_DELAY_SECONDS
    assert RetryLater("30", "x").reason == "x"


# -- tasks.build_handlers ---------------------------------------------------------------


class RecordingStore:
    INDEX_OFF_PURGED = "purged by purge-index"

    def __init__(self):
        self.calls = []
        self.reindex_ids = []

    def set_index_state(self, conn, experiment_id, state, error=None, *, if_updated_at=None):
        self.calls.append(("set_index_state", experiment_id, state, error))

    def experiments_for_reindex(self, conn, *, domain_id=None, type_id=None, states=None,
                                switched_off=False):
        self.calls.append(("experiments_for_reindex", tuple(states or ())))
        return list(self.reindex_ids)


class RecordingService:
    def __init__(self):
        self.calls = []

    def execute_evaluation(self, conn, evaluation_id, *, settings, runner):
        self.calls.append(("execute_evaluation", evaluation_id, settings, runner))

    def run_pipeline_job(self, conn, experiment_id, *, settings, runner):
        self.calls.append(("run_pipeline_job", experiment_id, settings, runner))

    def on_evaluation_dead(self, conn, evaluation_id):
        self.calls.append(("on_evaluation_dead", evaluation_id))


@pytest.fixture
def fake_store(monkeypatch):
    import experiments

    store = RecordingStore()
    monkeypatch.setitem(sys.modules, "experiments.store", store)
    monkeypatch.setattr(experiments, "store", store, raising=False)
    return store


@pytest.fixture
def fake_service(monkeypatch):
    import experiments

    service = RecordingService()
    monkeypatch.setitem(sys.modules, "experiments.service", service)
    monkeypatch.setattr(experiments, "service", service, raising=False)
    return service


def make_job(kind, payload, job_id=1):
    return Job(id=job_id, kind=kind, payload=payload, attempts=1, max_attempts=8,
               locked_by="w", created_at=None)


def test_build_handlers_routes_each_kind(monkeypatch, fake_service):
    from experiments import indexer, tasks

    calls = []
    monkeypatch.setattr(indexer, "index_experiment",
                        lambda conn, eid, client, s: calls.append(("index", eid, client, s)))
    monkeypatch.setattr(indexer, "unindex_pointer",
                        lambda conn, client, pointer: calls.append(("unindex", pointer, client)))
    s, client, runner = settings(), object(), object()
    handlers, on_dead, maintenance = tasks.build_handlers(settings=s, index_client=client,
                                                          runner=runner)
    assert set(handlers) == {"index", "unindex", "evaluate", "pipeline"}
    assert set(on_dead) == {"index", "unindex", "evaluate", "pipeline"}
    assert callable(maintenance)
    handlers["index"]("conn", make_job("index", {"experiment_id": "e1"}))
    handlers["unindex"]("conn", make_job("unindex", {"pointer": "experiments/m/MKT-1"}))
    handlers["evaluate"]("conn", make_job("evaluate", {"evaluation_id": "v1"}))
    handlers["pipeline"]("conn", make_job("pipeline", {"experiment_id": "e2"}))
    assert calls == [("index", "e1", client, s), ("unindex", "experiments/m/MKT-1", client)]
    assert fake_service.calls == [("execute_evaluation", "v1", s, runner),
                                  ("run_pipeline_job", "e2", s, runner)]


def test_handlers_refuse_incomplete_payloads(fake_service):
    from experiments import tasks

    handlers, _, _ = tasks.build_handlers(settings=settings(), index_client=None, runner=None)
    for kind in ("index", "unindex", "evaluate", "pipeline"):
        with pytest.raises(PermanentError) as exc:
            handlers[kind]("conn", make_job(kind, {}))
        assert str(exc.value) == "Der Auftrag ist unvollst\u00e4ndig."


def test_on_dead_hooks(db, fake_service):
    from experiments import tasks

    e1 = make_experiment(db, "MKT-1", index_state="pending")
    _, on_dead, _ = tasks.build_handlers(settings=settings(), index_client=None, runner=None)
    on_dead["index"](db, make_job("index", {"experiment_id": e1}))
    on_dead["evaluate"](db, make_job("evaluate", {"evaluation_id": "v1"}))
    on_dead["unindex"](db, make_job("unindex", {"pointer": "p"}))
    on_dead["pipeline"](db, make_job("pipeline", {"experiment_id": e1}))
    on_dead["index"](db, make_job("index", {}))  # nothing to mark, no crash
    on_dead["index"](db, make_job("index", {"experiment_id": "not-a-uuid"}))  # no crash either
    # Died without a Knovas error behind it: the log has the details.
    assert index_state(db, e1) == ("error", tasks.MSG_INDEX_INCOMPLETE)
    assert fake_service.calls == [("on_evaluation_dead", "v1")]


@pytest.mark.parametrize("last_error, shown", [
    ("Knovas nicht erreichbar.", "Knovas war nicht erreichbar."),
    ("Knovas ist ausgelastet; neuer Versuch folgt.", "Knovas war nicht erreichbar."),
    ("Knovas hat die Anfrage vor\u00fcbergehend nicht angenommen (HTTP 403).",
     "Knovas war nicht erreichbar."),
    (jobs.MSG_UNEXPECTED, "Der Upload wurde nicht abgeschlossen (Details im Protokoll)."),
    (jobs.MSG_LEASE_EXPIRED, "Der Upload wurde nicht abgeschlossen (Details im Protokoll)."),
    (jobs.MSG_DEFERRED_TOO_LONG, "Der Upload wurde nicht abgeschlossen (Details im Protokoll)."),
])
def test_index_dead_message_follows_the_cause(db, last_error, shown):
    from experiments import tasks

    e1 = make_experiment(db)
    _, on_dead, _ = tasks.build_handlers(settings=settings(), index_client=None, runner=None)
    job = make_job("index", {"experiment_id": e1})
    job.last_error = last_error
    on_dead["index"](db, job)
    assert index_state(db, e1) == ("error", shown)


def test_maintenance_requeues_only_stranded_experiments(db):
    from experiments import store, tasks

    a = make_experiment(db, "MKT-1", index_state="pending")
    b = make_experiment(db, "MKT-2", index_state="pending")
    c = make_experiment(db, "MKT-3", index_state="error", index_error=tasks.MSG_INDEX_DEAD)
    make_experiment(db, "MKT-4", index_state="indexed")
    # Taken out of Knovas by purge-index: stays out (switched-off rows are
    # test_maintenance_uploads_experiments_switched_off_earlier).
    make_experiment(db, "MKT-5", index_state="off", index_error=store.INDEX_OFF_PURGED)
    q = JobQueue(db)
    q.enqueue("index", {"experiment_id": a}, dedupe_key=f"index:{a}")
    q.enqueue("index", {"experiment_id": b}, dedupe_key=f"index:{b}")
    free_slot(db)
    q.claim("w", kinds=("index",), lease_seconds=60)  # a running now
    last = q.enqueue("index", {"experiment_id": b}, dedupe_key=f"index:{b}")  # b pending again
    _, _, maintenance = tasks.build_handlers(
        settings=settings(index_access_groups=("g-exp",)), index_client=object(), runner=None)
    maintenance(db)
    rows = db.execute("SELECT payload->>'experiment_id', priority, status FROM exp_jobs "
                      "WHERE id > %s", (last,)).fetchall()
    assert rows == [(c, 100, "pending")]
    maintenance(db)  # idempotent
    assert db.execute("SELECT count(*) FROM exp_jobs WHERE payload->>'experiment_id' = %s",
                      (c,)).fetchone()[0] == 1


def test_maintenance_uploads_experiments_switched_off_earlier(db, monkeypatch):
    """review-jobs-5: what was turned 'off' while indexing was switched off
    goes up again once it is back on -- marked 'pending' and queued behind
    edits and stranded uploads, a batch per pass, so the rate-limited index
    jobs spread the uploads. What purge-index took out stays out."""
    from experiments import store, tasks

    older = make_experiment(db, "MKT-1", index_state="off")
    newer = make_experiment(db, "MKT-2", index_state="off")
    purged = make_experiment(db, "MKT-3", index_state="off", index_error=store.INDEX_OFF_PURGED)
    db.execute("UPDATE exp_experiments SET updated_at = now() - interval '1 hour' WHERE id = %s",
               (older,))
    monkeypatch.setattr(tasks, "REUPLOAD_BATCH", 1)
    _, _, still_off = tasks.build_handlers(
        settings=settings(index_enabled=False, index_access_groups=("g-exp",)),
        index_client=object(), runner=None)
    still_off(db)
    assert job_count(db) == 0 and index_state(db, newer) == ("off", None)

    _, _, maintenance = tasks.build_handlers(
        settings=settings(index_access_groups=("g-exp",)), index_client=object(), runner=None)
    maintenance(db)
    jobs_now = "SELECT payload->>'experiment_id', priority, status FROM exp_jobs ORDER BY id"
    assert db.execute(jobs_now).fetchall() == [(newer, tasks.REUPLOAD_PRIORITY, "pending")]
    assert index_state(db, newer) == ("pending", None)
    assert index_state(db, older) == ("off", None)  # the next pass takes it
    maintenance(db)
    assert db.execute(jobs_now).fetchall() == [(newer, 200, "pending"), (older, 200, "pending")]
    maintenance(db)  # both have their job now; the purged one is never queued
    assert job_count(db) == 2
    assert index_state(db, purged) == ("off", store.INDEX_OFF_PURGED)


@pytest.mark.parametrize("overrides, client", [
    ({"index_enabled": False, "index_access_groups": ("g",)}, object()),
    ({"index_access_groups": ()}, object()),
    ({"index_access_groups": ("g",)}, None),
])
def test_maintenance_stays_quiet_when_indexing_cannot_work(db, overrides, client):
    from experiments import tasks

    make_experiment(db, "MKT-1", index_state="pending")
    _, _, maintenance = tasks.build_handlers(settings=settings(**overrides),
                                             index_client=client, runner=None)
    maintenance(db)
    assert job_count(db) == 0


def test_maintenance_works_unrestricted_without_groups(db):
    from experiments import tasks

    make_experiment(db, "MKT-1", index_state="pending")
    _, _, maintenance = tasks.build_handlers(
        settings=settings(index_unrestricted=True), index_client=object(), runner=None)
    maintenance(db)
    assert job_count(db) == 1


def test_worker_with_task_handlers_end_to_end(db, fake_store, fake_service):
    """What app.py wires: build_handlers + JobWorker.run_once."""
    from experiments import tasks

    handlers, on_dead, _ = tasks.build_handlers(settings=settings(), index_client=None,
                                                runner=None)
    w = worker(handlers=handlers, on_dead=on_dead)
    JobQueue(db).enqueue("pipeline", {"experiment_id": "e9"}, dedupe_key="pipeline:e9")
    JobQueue(db).enqueue("evaluate", {"evaluation_id": "v9"})
    assert w.run_once(db) and w.run_once(db) and not w.run_once(db)
    assert [c[0] for c in fake_service.calls] == ["run_pipeline_job", "execute_evaluation"]


def test_evaluate_retry_later_from_the_service_defers(db, fake_service):
    from experiments import tasks

    def unavailable(conn, evaluation_id, *, settings, runner):
        raise RetryLater(60)

    fake_service.execute_evaluation = unavailable
    handlers, on_dead, _ = tasks.build_handlers(settings=settings(), index_client=None,
                                                runner=None)
    job_id = JobQueue(db).enqueue("evaluate", {"evaluation_id": "v"})
    worker(handlers=handlers, on_dead=on_dead).run_once(db)
    row = job_row(db, job_id)
    assert row["status"] == "pending" and row["attempts"] == 0 and row["due_in"] > 55


# -- CLI -----------------------------------------------------------------------------------


def test_module_entry_point_runs_in_a_fresh_interpreter():
    env = dict(os.environ, PYTHONPATH=str(COMPONENT_DIR / "src"))
    proc = subprocess.run([sys.executable, "-m", "experiments", "--help"], cwd=COMPONENT_DIR,
                          env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    for command in ("status", "reindex", "purge-index", "install-pack", "worker"):
        assert command in proc.stdout


@pytest.fixture
def cli_env(platform_db, tmp_path, monkeypatch):
    """A config file and PLATFORM_DB_DSN pointing at the test schema."""
    import config_loader

    monkeypatch.setattr(config_loader, "_global_config", config_loader._global_config)
    schema = platform_db.execute("SELECT current_schema()").fetchone()[0]
    monkeypatch.setenv("PLATFORM_DB_DSN",
                       f"{PLATFORM_DB_TEST_DSN}?options=-csearch_path%3D{schema}")

    def write(experiments_yaml):
        path = tmp_path / "config.yaml"
        path.write_text(
            'api:\n  base_url: "http://example.test"\n  customer_id: "tenant-a"\n'
            'identity:\n  enabled: true\n' + experiments_yaml, encoding="utf-8")
        return str(path)

    return write


ON_YAML = ('experiments:\n  enabled: "true"\n  index:\n    access_groups: "g-exp"\n'
           '  worker:\n    enabled: "false"\n')


def run_cli(*argv):
    from experiments import cli

    lines = []
    code = cli.main(list(argv), out=lines.append)
    return code, "\n".join(lines)


class CliStore(RecordingStore):
    def __init__(self):
        super().__init__()
        self.snapshots = {}
        self.pointers = []
        self.forgotten = []

    def load_snapshot(self, conn, key_or_id, *, actor=None):
        return self.snapshots.get(key_or_id)

    def index_documents(self, conn, after=None, limit=500):
        items = sorted(p for p in self.pointers if after is None or p > after)
        return items[:limit]

    def forget_index_document(self, conn, pointer):
        self.forgotten.append(pointer)
        self.pointers.remove(pointer)

    def experiments_for_reindex(self, conn, *, domain_id=None, type_id=None, states=None,
                                switched_off=False):
        self.calls.append(("experiments_for_reindex", tuple(states or ())))
        if states == ("pending",):
            return ["p1"]
        return list(self.reindex_ids)


@pytest.fixture
def cli_store(monkeypatch):
    import experiments

    store = CliStore()
    monkeypatch.setitem(sys.modules, "experiments.store", store)
    monkeypatch.setattr(experiments, "store", store, raising=False)
    return store


def test_cli_status_json(cli_env, cli_store, db):
    JobQueue(db).enqueue("pipeline", {})
    code, out = run_cli("--config", cli_env(ON_YAML), "status", "--json")
    assert code == 0, out
    report = json.loads(out)
    assert report["enabled"] is True
    assert report["access_groups"] == ["g-exp"]
    assert report["jobs"]["pending"] == 1
    assert report["index"]["pending"] == 1
    assert report["runner"] == {"configured": False}


def test_cli_status_text_mentions_missing_group(cli_env, cli_store):
    code, out = run_cli("--config", cli_env('experiments:\n  enabled: "true"\n'), "status")
    assert code == 0
    assert "Experimente: eingeschaltet" in out
    assert "EXPERIMENTS_ACCESS_GROUPS" in out


def test_cli_reindex_keys_and_all(cli_env, cli_store, db):
    cli_store.snapshots["MKT-1"] = {"id": "11111111-1111-1111-1111-111111111111"}
    code, out = run_cli("--config", cli_env(ON_YAML), "reindex", "MKT-1", "mkt-404")
    assert code == 1  # one key was not found
    assert "MKT-404" in out
    rows = db.execute("SELECT kind, priority, dedupe_key FROM exp_jobs").fetchall()
    assert rows == [("index", 10, "index:11111111-1111-1111-1111-111111111111")]
    assert ("set_index_state", "11111111-1111-1111-1111-111111111111", "pending", None) \
        in cli_store.calls
    cli_store.reindex_ids = ["a", "b"]
    code, out = run_cli("--config", cli_env(ON_YAML), "reindex", "--all")
    assert code == 0
    assert db.execute("SELECT count(*) FROM exp_jobs WHERE priority = 200").fetchone()[0] == 2


def test_cli_reindex_refuses_when_off_or_without_group(cli_env, cli_store, db):
    code, out = run_cli("--config", cli_env(""), "reindex", "--all")
    assert code == 1 and "EXPERIMENTS_ENABLED" in out
    code, out = run_cli("--config", cli_env('experiments:\n  enabled: "true"\n'),
                        "reindex", "--all")
    assert code == 1 and "EXPERIMENTS_ACCESS_GROUPS" in out
    code, out = run_cli("--config", cli_env(ON_YAML), "reindex")
    assert code == 2
    assert job_count(db) == 0


def test_cli_purge_index_needs_yes_and_works_while_off(cli_env, cli_store, db, monkeypatch,
                                                       fake_index_client):
    from experiments import indexer

    monkeypatch.setattr(indexer, "make_index_client", lambda config: fake_index_client)
    cli_store.pointers = ["experiments/marketing/MKT-1", "experiments/sales/SAL-2"]
    cli_store.reindex_ids = ["e1"]
    JobQueue(db).enqueue("index", {"experiment_id": "e1"}, dedupe_key="index:e1")
    off = cli_env("")  # module switched off
    code, out = run_cli("--config", off, "purge-index")
    assert code == 1 and fake_index_client.deleted == []
    assert "--yes" in out
    code, out = run_cli("--config", off, "purge-index", "--yes")
    assert code == 0, out
    assert sorted(fake_index_client.deleted) == ["experiments/marketing/MKT-1",
                                                 "experiments/sales/SAL-2"]
    assert cli_store.pointers == []
    assert JobQueue(db).counts()["pending"] == 0  # nothing re-uploads right away
    # Marked as purged: the maintenance does not upload it again (review-jobs-5).
    assert ("set_index_state", "e1", "off", cli_store.INDEX_OFF_PURGED) in cli_store.calls


def test_cli_worker_once_processes_due_jobs(cli_env, cli_store, db, monkeypatch):
    from experiments import tasks

    seen = []
    monkeypatch.setattr(tasks, "build_handlers", lambda **kw: (
        {"pipeline": lambda conn, job: seen.append(job.payload["n"])}, {}, None))
    for i in range(3):
        JobQueue(db).enqueue("pipeline", {"n": i})
    code, out = run_cli("--config", cli_env(ON_YAML), "worker", "--once", "--max-jobs", "2")
    assert code == 0, out
    assert seen == [0, 1]
    code, out = run_cli("--config", cli_env(ON_YAML), "worker", "--once")
    assert seen == [0, 1, 2]
    code, out = run_cli("--config", cli_env(""), "worker", "--once")
    assert code == 1


def test_cli_install_pack_needs_a_manager_account(cli_env, cli_store, identity_repo,
                                                  monkeypatch):
    import experiments
    from conftest import _person

    installed = []

    class FakeService:
        def __init__(self, conn, actor, settings, **kw):
            self.actor = actor

        def install_pack(self, name):
            if name == "nope":
                raise ValidationError("Das Paket gibt es nicht.")
            installed.append((name, self.actor.email))
            return {"domain": 1, "types": 3, "metrics": 8, "evaluators": 0}

    module = types.SimpleNamespace(ExperimentService=FakeService)
    monkeypatch.setitem(sys.modules, "experiments.service", module)
    monkeypatch.setattr(experiments, "service", module, raising=False)
    monkeypatch.delenv("PLATFORM_ADMIN_EMAIL", raising=False)
    _person(identity_repo, "max@knovas.ch", "Max", "experiments_manager")
    _person(identity_repo, "eva@knovas.ch", "Eva", "experimenter")
    cfg = cli_env(ON_YAML)
    assert run_cli("--config", cfg, "install-pack", "marketing")[0] == 2
    code, out = run_cli("--config", cfg, "install-pack", "marketing", "--as", "eva@knovas.ch")
    assert code == 1 and installed == []
    code, out = run_cli("--config", cfg, "install-pack", "marketing", "--as", "max@knovas.ch")
    assert code == 0, out
    assert installed == [("marketing", "max@knovas.ch")]
    monkeypatch.setenv("PLATFORM_ADMIN_EMAIL", "max@knovas.ch")
    code, out = run_cli("--config", cfg, "install-pack", "nope")
    assert code == 1 and "Das Paket gibt es nicht." in out


def test_cli_reports_unreadable_config():
    code, out = run_cli("--config", "/nonexistent/config.yaml", "status")
    assert code == 1 and "Konfiguration" in out
