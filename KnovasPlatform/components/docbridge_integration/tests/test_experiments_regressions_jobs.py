"""Regressions of the experiments background work (jobs, tasks, indexer, CLI).

Each test pins one reviewed defect against the real PostgreSQL:

* a Knovas deletion whose job died is repeated by the maintenance
  (review-jobs-1);
* a document Knovas refused is not uploaded again every few minutes, while
  outages still heal on their own (review-jobs-3);
* purge-index keeps the index state true to what was deleted when the Knovas
  listing fails, and reports it in German (review-jobs-4);
* a dead index job never overwrites a newer success, and names its cause
  (review-jobs-7);
* a process that ends hands its running jobs back instead of leaving them to
  the lease (review-jobs-8);
* CLI reindex takes the row locks in the service's order (review-jobs-9).

The upload lock of review-jobs-2 is covered in test_experiments_indexer.py.
"""

import argparse
import os
import subprocess
import sys
import textwrap
import threading
import time
import types
from pathlib import Path

import pytest
import requests

from conftest import (PLATFORM_DB_TEST_DSN, FakeIndexClient, _person,
                      platform_db_reachable)

pytestmark = pytest.mark.skipif(
    not platform_db_reachable(), reason="No PostgreSQL at the identity test DSN"
)

from experiments import indexer, jobs, store, tasks  # noqa: E402
from experiments.errors import Unavailable  # noqa: E402
from experiments.jobs import JOB_KINDS, Job, JobQueue, JobWorker  # noqa: E402
from experiments.settings import ExperimentsSettings  # noqa: E402

COMPONENT_DIR = Path(__file__).resolve().parents[1]
SETTINGS = ExperimentsSettings(enabled=True, index_enabled=True, index_access_groups=("g-exp",),
                               index_debounce_seconds=60, worker_enabled=False)
META = {"ip": "10.1.2.3", "user_agent": "pytest", "token_id": None}


# -- helpers --------------------------------------------------------------------------


@pytest.fixture
def db(platform_db):
    return platform_db


@pytest.fixture
def schema(db):
    return db.execute("SELECT current_schema()").fetchone()[0]


@pytest.fixture
def connect(schema):
    import psycopg

    opened = []

    def _connect():
        conn = psycopg.connect(PLATFORM_DB_TEST_DSN, autocommit=True,
                               options=f"-c search_path={schema}")
        opened.append(conn)
        return conn

    yield _connect
    for conn in opened:
        if not conn.closed:
            conn.close()


def make_experiment(conn, key="MKT-1", *, index_state="pending", index_error=None,
                    indexed_at=None):
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


def record(conn, pointer, experiment_id):
    conn.execute("INSERT INTO exp_index_documents (pointer, experiment_id) VALUES (%s, %s)",
                 (pointer, experiment_id))


def dead_unindex(conn, pointer, error, finished_ago_seconds):
    conn.execute(
        "INSERT INTO exp_jobs (kind, dedupe_key, payload, status, attempts, last_error, "
        "  finished_at) "
        "VALUES ('unindex', %s, jsonb_build_object('pointer', %s::text), 'dead', 8, %s, "
        "  clock_timestamp() - make_interval(secs => %s))",
        (f"unindex:{pointer}", pointer, error, float(finished_ago_seconds)))


def state_of(conn, experiment_id):
    return conn.execute("SELECT index_state, index_error FROM exp_experiments WHERE id = %s",
                        (experiment_id,)).fetchone()


def free_slot(conn):
    conn.execute("UPDATE exp_rate_slots SET next_at = clock_timestamp() - interval '1 second'")


def worker(handlers=None, on_dead=None, kinds=JOB_KINDS, **kw):
    kw.setdefault("lease_seconds", 60)
    kw.setdefault("poll_seconds", 0.05)
    kw.setdefault("connect", lambda: None)
    return JobWorker(handlers=handlers or {}, on_dead=on_dead or {}, kinds=kinds, **kw)


def drain(conn, w, limit=60):
    ran = 0
    for _ in range(limit):
        conn.execute("UPDATE exp_jobs SET run_after = clock_timestamp() - interval '1 second' "
                     "WHERE status = 'pending'")
        free_slot(conn)
        if not w.run_once(conn):
            return ran
        ran += 1
    raise AssertionError("the queue did not drain")


def run_one(conn, w):
    """Make every pending job due and run one."""
    conn.execute("UPDATE exp_jobs SET run_after = clock_timestamp() - interval '1 second' "
                 "WHERE status = 'pending'")
    free_slot(conn)
    return w.run_once(conn)


def http_error(status):
    response = requests.Response()
    response.status_code = status
    err = requests.exceptions.HTTPError(f"{status} Error: secret-internal-detail")
    err.response = response
    return err


def retry_error(inner):
    import tenacity

    attempt = tenacity.Future(3)
    attempt.set_exception(inner)
    return tenacity.RetryError(attempt)


class World:
    """The real service on the test schema, with the marketing pack."""

    def __init__(self, conn, repo):
        from experiments import store
        from experiments.service import ExperimentService

        self.conn = conn
        store.ensure_builtin_evaluators(conn)
        store.ensure_core_pack(conn)
        eva = _person(repo, "eva@knovas.ch", "Eva", "experimenter")
        chef = _person(repo, "max@knovas.ch", "Max", "experiments_manager")
        self.experimenter = ExperimentService(conn, eva, SETTINGS, request_meta=dict(META))
        self.manager = ExperimentService(conn, chef, SETTINGS, request_meta=dict(META))
        self.manager.install_pack("marketing")

    def create(self, **data):
        body = {"domain": "marketing", "type": "ab_test", "title": "LinkedIn Karussell",
                "hypothesis": "Karussell-Posts erh\u00f6hen die Klickrate."}
        body.update(data)
        return self.experimenter.create_experiment(body)


@pytest.fixture
def world(platform_db, identity_repo):
    return World(platform_db, identity_repo)


class OutageClient(FakeIndexClient):
    """Knovas unreachable while ``down`` is set."""

    def __init__(self):
        super().__init__()
        self.down = False

    def upload_text_document(self, identifier, **kw):
        if self.down:
            raise requests.exceptions.ConnectionError("down")
        return super().upload_text_document(identifier, **kw)

    def delete_information_object(self, pointer):
        if self.down:
            raise requests.exceptions.ConnectionError("down")
        return super().delete_information_object(pointer)


# -- review-jobs-1: failed deletions are repeated -------------------------------------


def test_deletion_that_died_in_a_long_outage_is_repeated_by_the_maintenance(world):
    conn = world.conn
    client = OutageClient()
    handlers, on_dead, maintenance = tasks.build_handlers(settings=SETTINGS, index_client=client,
                                                          runner=None)
    w = worker(handlers=handlers, on_dead=on_dead)
    key = world.create()["key"]
    drain(conn, w)
    pointer = f"experiments/marketing/{key}"
    assert [u["identifier"] for u in client.uploads] == [pointer]

    world.manager.delete_experiment(key)
    client.down = True  # a Knovas outage longer than the job's eight attempts
    for _ in range(8):
        assert run_one(conn, w)
    job = conn.execute("SELECT status, attempts, last_error FROM exp_jobs "
                       "WHERE kind = 'unindex'").fetchone()
    assert job == ("dead", 8, "Knovas nicht erreichbar.")
    assert tasks.orphan_count(conn) == 1

    # Right after the death the deletion waits (it just spent an hour trying).
    maintenance(conn)
    assert conn.execute("SELECT count(*) FROM exp_jobs WHERE status = 'pending'").fetchone()[0] == 0

    # Later, with Knovas back, the maintenance repeats it -- also while
    # indexing is switched off: deletions must always reach Knovas.
    conn.execute("UPDATE exp_jobs SET finished_at = finished_at - interval '2 hours'")
    client.down = False
    off = ExperimentsSettings(enabled=True, index_enabled=False, worker_enabled=False)
    _, _, maintenance_off = tasks.build_handlers(settings=off, index_client=client, runner=None)
    maintenance_off(conn)
    pending = conn.execute("SELECT kind, dedupe_key, priority FROM exp_jobs "
                           "WHERE status = 'pending'").fetchall()
    assert pending == [("unindex", f"unindex:{pointer}", tasks.MAINTENANCE_PRIORITY)]
    maintenance_off(conn)  # no second job for the same pointer
    assert conn.execute("SELECT count(*) FROM exp_jobs WHERE status = 'pending'").fetchone()[0] == 1
    drain(conn, w)
    assert client.deleted == [pointer]
    assert conn.execute("SELECT count(*) FROM exp_index_documents").fetchone()[0] == 0
    assert tasks.orphan_count(conn) == 0
    maintenance(conn)
    assert conn.execute("SELECT count(*) FROM exp_jobs WHERE status = 'pending'").fetchone()[0] == 0


def test_orphan_sweep_picks_only_deletions_nobody_is_working_on(db):
    live = make_experiment(db, "MKT-1", index_state="indexed")
    record(db, "experiments/marketing/MKT-1", live)
    gone = "0b6f6c1e-35d1-4c8a-9d65-2f2a4f8f1a99"
    refused = indexer.MSG_DELETE_REJECTED.format(code=400)
    cases = {
        "experiments/marketing/MKT-2": None,                        # no job left: repeat
        "experiments/marketing/MKT-3": ("pending", None),           # still waiting
        "experiments/marketing/MKT-4": ("dead", 600),               # died 10 minutes ago
        "experiments/marketing/MKT-5": ("dead", 7200),              # died 2 hours ago: repeat
        "experiments/marketing/MKT-6": ("refused", 7200),           # refused 2 hours ago
        "experiments/marketing/MKT-7": ("refused", 25 * 3600),      # refused yesterday: repeat
    }
    for pointer, job in cases.items():
        record(db, pointer, gone)
        if job is None:
            continue
        if job[0] == "pending":
            JobQueue(db).enqueue("unindex", {"pointer": pointer}, dedupe_key=f"unindex:{pointer}",
                                 delay_seconds=300)
        else:
            dead_unindex(db, pointer, refused if job[0] == "refused" else
                         "Knovas nicht erreichbar.", job[1])
    record(db, "experiments/marketing/MKT-8", None)  # recorded without an experiment
    assert sorted(tasks.orphan_pointers(db)) == [
        "experiments/marketing/MKT-2", "experiments/marketing/MKT-5",
        "experiments/marketing/MKT-7", "experiments/marketing/MKT-8"]
    assert tasks.orphan_count(db) == 7
    assert tasks.requeue_failed_deletions(db) == 4
    assert tasks.orphan_pointers(db) == []


def test_maintenance_without_a_knovas_client_leaves_deletions_alone(db):
    record(db, "experiments/marketing/MKT-2", None)
    _, _, maintenance = tasks.build_handlers(settings=SETTINGS, index_client=None, runner=None)
    maintenance(db)
    assert db.execute("SELECT count(*) FROM exp_jobs").fetchone()[0] == 0


# -- review-jobs-3: refused documents stay put ----------------------------------------


def test_maintenance_requeues_only_errors_that_may_heal(db):
    retry = {
        make_experiment(db, "MKT-1", index_state="pending"),
        make_experiment(db, "MKT-2", index_state="error", index_error=tasks.MSG_INDEX_DEAD),
        make_experiment(db, "MKT-3", index_state="error", index_error=indexer.MSG_NO_GROUP),
        make_experiment(db, "MKT-4", index_state="error",
                        index_error=tasks.MSG_INDEX_INCOMPLETE),
        make_experiment(db, "MKT-5", index_state="error", index_error=None),
    }
    make_experiment(db, "MKT-6", index_state="error",
                    index_error=indexer.MSG_REJECTED.format(code=400))
    make_experiment(db, "MKT-7", index_state="error",
                    index_error=indexer.MSG_REJECTED.format(code=413))
    make_experiment(db, "MKT-8", index_state="error", index_error=indexer.MSG_BAD_KEY)
    _, _, maintenance = tasks.build_handlers(settings=SETTINGS, index_client=object(),
                                             runner=None)
    maintenance(db)
    queued = {r[0] for r in db.execute(
        "SELECT payload->>'experiment_id' FROM exp_jobs WHERE kind = 'index'").fetchall()}
    assert queued == retry


def test_refused_document_is_not_sent_again_until_the_next_edit(world):
    conn = world.conn
    client = FakeIndexClient()
    handlers, on_dead, maintenance = tasks.build_handlers(settings=SETTINGS, index_client=client,
                                                          runner=None)
    w = worker(handlers=handlers, on_dead=on_dead)
    exp = world.create()
    client.fail_with = http_error(400)
    drain(conn, w)
    snapshot = world.experimenter.get_experiment(exp["key"])
    assert snapshot["index"]["state"] == "error"
    assert snapshot["index"]["error"] == "Knovas hat das Dokument abgelehnt (HTTP 400)."
    for _ in range(3):
        maintenance(conn)
        assert drain(conn, w) == 0
    assert client.uploads == []
    # The next edit sends it again.
    world.experimenter.update_experiment(exp["key"], {"title": "Neu",
                                                      "row_version": snapshot["row_version"]})
    drain(conn, w)
    assert [u["title"] for u in client.uploads] == [f"{exp['key']} \u00b7 Neu"]


def test_an_outage_still_heals_through_the_maintenance(world):
    conn = world.conn
    client = OutageClient()
    handlers, on_dead, maintenance = tasks.build_handlers(settings=SETTINGS, index_client=client,
                                                          runner=None)
    w = worker(handlers=handlers, on_dead=on_dead)
    exp = world.create()
    client.down = True
    for _ in range(8):
        assert run_one(conn, w)
    snapshot = world.experimenter.get_experiment(exp["key"])
    assert (snapshot["index"]["state"], snapshot["index"]["error"]) == \
        ("error", "Knovas war nicht erreichbar.")
    client.down = False
    maintenance(conn)
    drain(conn, w)
    assert world.experimenter.get_experiment(exp["key"])["index"]["state"] == "indexed"


# -- review-jobs-7: a dead index job and newer work -----------------------------------


def index_hooks():
    _, on_dead, _ = tasks.build_handlers(settings=SETTINGS, index_client=None, runner=None)
    return on_dead


def test_dead_index_job_keeps_a_newer_success(db):
    """The in-flight retry path: while job A's last attempt uploads, job B
    for the same experiment succeeds; A then fails for good."""
    e = make_experiment(db)
    queue = JobQueue(db)
    queue.enqueue("index", {"experiment_id": e}, dedupe_key=f"index:{e}", max_attempts=1)

    def handler(conn, job):
        conn.execute("UPDATE exp_experiments SET index_state = 'indexed', indexed_at = now(), "
                     "index_error = NULL WHERE id = %s", (e,))  # B is done
        raise Unavailable("Knovas nicht erreichbar.")

    free_slot(db)
    assert worker(handlers={"index": handler}, on_dead=index_hooks()).run_once(db)
    assert db.execute("SELECT status FROM exp_jobs").fetchone()[0] == "dead"
    assert state_of(db, e) == ("indexed", None)


def test_expired_lease_sweep_keeps_a_newer_success(db):
    e = make_experiment(db)
    queue = JobQueue(db)
    queue.enqueue("index", {"experiment_id": e}, dedupe_key=f"index:{e}", max_attempts=1)
    free_slot(db)
    assert queue.claim("gone-worker", kinds=("index",), lease_seconds=60) is not None
    db.execute("UPDATE exp_experiments SET index_state = 'indexed', indexed_at = now() "
               "WHERE id = %s", (e,))
    db.execute("UPDATE exp_jobs SET locked_until = clock_timestamp() - interval '1 second'")
    worker(on_dead=index_hooks()).run_maintenance(db)
    assert db.execute("SELECT status FROM exp_jobs").fetchone()[0] == "dead"
    assert state_of(db, e) == ("indexed", None)


def test_dead_index_job_leaves_the_outcome_to_an_active_twin(db):
    e = make_experiment(db, index_state="pending")
    JobQueue(db).enqueue("index", {"experiment_id": e}, dedupe_key=f"index:{e}")
    dead = Job(id=99, kind="index", payload={"experiment_id": e}, attempts=8, max_attempts=8,
               locked_by="w", created_at=None, last_error="Knovas nicht erreichbar.")
    index_hooks()["index"](db, dead)
    assert state_of(db, e) == ("pending", None)


def test_dead_index_job_marks_older_success_as_failed(db):
    e = make_experiment(db, index_state="indexed")
    db.execute("UPDATE exp_experiments SET indexed_at = now() - interval '1 hour' WHERE id = %s",
               (e,))
    created = db.execute("SELECT now() - interval '10 minutes'").fetchone()[0]
    dead = Job(id=99, kind="index", payload={"experiment_id": e}, attempts=8, max_attempts=8,
               locked_by="w", created_at=created, last_error="Knovas nicht erreichbar.")
    index_hooks()["index"](db, dead)
    assert state_of(db, e) == ("error", "Knovas war nicht erreichbar.")
    dead.last_error = jobs.MSG_LEASE_EXPIRED
    index_hooks()["index"](db, dead)
    assert state_of(db, e) == ("error", "Der Upload wurde nicht abgeschlossen "
                                        "(Details im Protokoll).")


# -- review-jobs-8: jobs in progress go back at shutdown ---------------------------------


def test_release_hands_a_running_job_back_without_using_an_attempt(db):
    queue = JobQueue(db)
    job_id = queue.enqueue("pipeline", {"experiment_id": "e1"}, dedupe_key="pipeline:e1")
    db.execute("UPDATE exp_jobs SET created_at = now() - interval '2 days' WHERE id = %s",
               (job_id,))
    job = queue.claim("w", kinds=("pipeline",), lease_seconds=600)
    assert job.attempts == 1
    assert queue.release(job)
    row = db.execute("SELECT status, attempts, locked_by, locked_until, "
                     "run_after <= clock_timestamp() FROM exp_jobs WHERE id = %s",
                     (job_id,)).fetchone()
    # Pending at once; not given up although older than the defer cap.
    assert row == ("pending", 0, None, None, True)
    assert not queue.complete(job)  # the old thread is fenced out
    again = queue.claim("w2", kinds=("pipeline",), lease_seconds=600)
    assert again.attempts == 1
    assert queue.complete(again)
    assert not queue.release(again)  # finished: nothing to hand back


def test_release_with_a_pending_twin_closes_the_job(db):
    queue = JobQueue(db)
    queue.enqueue("pipeline", {"experiment_id": "e1"}, dedupe_key="pipeline:e1")
    job = queue.claim("w", kinds=("pipeline",), lease_seconds=600)
    twin = queue.enqueue("pipeline", {"experiment_id": "e1"}, dedupe_key="pipeline:e1")
    assert queue.release(job)
    rows = dict(db.execute("SELECT id, status FROM exp_jobs").fetchall())
    assert rows == {job.id: "done", twin: "pending"}


def test_stop_workers_hands_back_a_job_that_does_not_finish_in_time(db, connect):
    entered, leave = threading.Event(), threading.Event()

    def slow(conn, job):
        entered.set()
        leave.wait(20)

    job_id = JobQueue(db).enqueue("pipeline", {"experiment_id": "e1"})
    w = worker(handlers={"pipeline": slow}, kinds=("pipeline",), connect=connect,
               lease_seconds=600)
    w._next_maintenance_at = time.monotonic() + 3600
    w.start()
    try:
        assert entered.wait(20)
        assert w.current_job is not None and w.current_job.id == job_id
        started = time.monotonic()
        assert jobs.stop_workers([w], timeout=0.3) == 1
        assert time.monotonic() - started < 10
        row = db.execute("SELECT status, attempts, locked_by FROM exp_jobs WHERE id = %s",
                         (job_id,)).fetchone()
        assert row == ("pending", 0, None)
    finally:
        leave.set()
        w.join(20)
    assert not w.is_alive()
    # The old thread's late completion is fenced out; the job runs again.
    assert db.execute("SELECT status FROM exp_jobs WHERE id = %s", (job_id,)).fetchone()[0] \
        == "pending"


def test_stop_workers_lets_a_short_job_finish(db, connect):
    entered = threading.Event()

    def short(conn, job):
        entered.set()
        time.sleep(0.2)

    job_id = JobQueue(db).enqueue("pipeline", {"experiment_id": "e1"})
    w = worker(handlers={"pipeline": short}, kinds=("pipeline",), connect=connect)
    w._next_maintenance_at = time.monotonic() + 3600
    w.start()
    assert entered.wait(20)
    assert jobs.stop_workers([w], timeout=10) == 0
    assert not w.is_alive()
    assert db.execute("SELECT status FROM exp_jobs WHERE id = %s", (job_id,)).fetchone()[0] \
        == "done"


def test_start_workers_once_registers_one_exit_hook_per_process(monkeypatch):
    registered, stopped = [], []
    monkeypatch.setattr(JobWorker, "start", lambda self: None)
    monkeypatch.setattr(jobs, "_workers_by_pid", {})
    monkeypatch.setattr(jobs, "_exit_hooks_for_pids", set())
    monkeypatch.setattr(jobs.atexit, "register", lambda fn, *a: registered.append((fn, a)))
    s = ExperimentsSettings(enabled=True, worker_enabled=True)
    kw = dict(settings=s, connect=lambda: None, handlers={}, on_dead={}, maintenance=None)
    workers = jobs.start_workers_once(**kw)
    jobs.start_workers_once(**kw)
    assert registered == [(jobs._shutdown_at_exit, (os.getpid(),))]
    monkeypatch.setattr(jobs, "stop_workers", lambda ws, **k: stopped.append(list(ws)) or 0)
    jobs._shutdown_at_exit(os.getpid() + 1)  # a forked child: not its threads
    assert stopped == []
    jobs._shutdown_at_exit(os.getpid())
    assert stopped == [workers]
    assert jobs._workers_by_pid == {}


def test_a_process_that_exits_hands_its_running_job_back(db, schema):
    """What a gunicorn worker does on a graceful restart: sys.exit with the
    daemon threads in the middle of a job. The atexit hook releases it."""
    job_id = JobQueue(db).enqueue("pipeline", {"experiment_id": "e1"}, dedupe_key="pipeline:e1")
    script = textwrap.dedent("""
        import sys, threading, time
        import psycopg
        from experiments import jobs
        from experiments.settings import ExperimentsSettings

        dsn, schema = sys.argv[1], sys.argv[2]
        connect = lambda: psycopg.connect(dsn, autocommit=True,
                                          options=f"-c search_path={schema}")
        entered = threading.Event()

        def slow(conn, job):
            entered.set()
            time.sleep(120)

        jobs.JobWorker.first_maintenance_after = (3600.0, 3600.0)
        settings = ExperimentsSettings(enabled=True, worker_enabled=True, worker_poll_seconds=0.05)
        jobs.start_workers_once(settings=settings, connect=connect, handlers={"pipeline": slow},
                                on_dead={}, maintenance=None)
        if not entered.wait(30):
            sys.exit(3)
        sys.exit(0)
    """)
    env = dict(os.environ, PYTHONPATH=str(COMPONENT_DIR / "src"))
    started = time.monotonic()
    proc = subprocess.run([sys.executable, "-c", script, PLATFORM_DB_TEST_DSN, schema],
                          cwd=COMPONENT_DIR, env=env, capture_output=True, text=True, timeout=90)
    assert proc.returncode == 0, proc.stderr
    assert time.monotonic() - started < 60
    row = db.execute("SELECT status, attempts, locked_by FROM exp_jobs WHERE id = %s",
                     (job_id,)).fetchone()
    assert row == ("pending", 0, None)


# -- review-jobs-9: CLI reindex lock order ---------------------------------------------


def test_cli_reindex_waits_for_an_edit_instead_of_deadlocking(db, connect):
    """An edit holds the experiment row and then queues its index job; the
    CLI must wait on the experiment row before it touches the job's slot."""
    from experiments import cli

    e = make_experiment(db, index_state="indexed")
    JobQueue(db).enqueue("index", {"experiment_id": e}, dedupe_key=f"index:{e}",
                         delay_seconds=60)
    edit, cli_conn = connect(), connect()
    cli_pid = cli_conn.info.backend_pid
    out, result = [], []
    ctx = types.SimpleNamespace(settings=SETTINGS, conn=cli_conn, out=out.append)
    edit.execute("BEGIN")
    edit.execute("SELECT 1 FROM exp_experiments WHERE id = %s FOR UPDATE", (e,))
    thread = threading.Thread(target=lambda: result.append(
        cli.cmd_reindex(ctx, argparse.Namespace(all=True, keys=[]))))
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            waiting = db.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s",
                                 (cli_pid,)).fetchone()
            if waiting and waiting[0] == "Lock":
                break
            time.sleep(0.05)
        else:
            raise AssertionError("the CLI never waited for the experiment row")
        edit.execute("SET LOCAL lock_timeout = '5s'")
        # The edit's own enqueue must not wait for the CLI.
        JobQueue(edit).enqueue("index", {"experiment_id": e}, dedupe_key=f"index:{e}",
                               delay_seconds=60, priority=10)
        edit.execute("COMMIT")
    finally:
        if edit.info.transaction_status != 0:
            edit.execute("ROLLBACK")
        thread.join(20)
    assert result == [0], out
    rows = db.execute("SELECT status, priority FROM exp_jobs WHERE kind = 'index'").fetchall()
    assert rows == [("pending", 10)]
    assert state_of(db, e) == ("pending", None)


# -- review-jobs-4: purge-index when the listing fails ----------------------------------


@pytest.fixture
def cli_env(schema, tmp_path, monkeypatch):
    import config_loader

    monkeypatch.setattr(config_loader, "_global_config", config_loader._global_config)
    monkeypatch.setenv("PLATFORM_DB_DSN",
                       f"{PLATFORM_DB_TEST_DSN}?options=-csearch_path%3D{schema}")
    path = tmp_path / "config.yaml"
    path.write_text(
        'api:\n  base_url: "http://example.test"\n  customer_id: "tenant-a"\n'
        'identity:\n  enabled: true\n'
        'experiments:\n  enabled: "true"\n  index:\n    access_groups: "g-exp"\n'
        '  worker:\n    enabled: "false"\n', encoding="utf-8")
    return str(path)


def run_cli(*argv):
    from experiments import cli

    lines = []
    code = cli.main(list(argv), out=lines.append)
    return code, "\n".join(lines)


class PurgeClient(FakeIndexClient):
    def __init__(self, listing_error, refuse=()):
        super().__init__()
        self.listing_error = listing_error
        self.refuse = set(refuse)

    def delete_information_object(self, pointer):
        if pointer in self.refuse:
            raise http_error(400)
        return super().delete_information_object(pointer)

    def iter_documents(self, **kwargs):
        raise self.listing_error
        yield  # pragma: no cover - makes this a generator like the real one


@pytest.mark.parametrize("listing_error, code, message", [
    (retry_error(requests.exceptions.ConnectionError("down")), 1,
     "Abgebrochen: Knovas konnte die Liste der Experiment-Dokumente nicht liefern; "
     "der Befehl kann wiederholt werden."),
    (http_error(403), 1,
     "Knovas hat die Liste der Dokumente abgelehnt (HTTP 403); nicht erfasste "
     "Experiment-Dokumente wurden nicht gesucht."),
])
def test_purge_index_keeps_the_index_state_true_when_the_listing_fails(
        db, cli_env, monkeypatch, listing_error, code, message):
    deleted_one = make_experiment(db, "MKT-1", index_state="indexed")
    refused_one = make_experiment(db, "MKT-2", index_state="indexed")
    never_sent = make_experiment(db, "MKT-3", index_state="pending")
    # Turned 'off' while indexing was switched off, its old copy recorded.
    switched_off = make_experiment(db, "MKT-4", index_state="off")
    record(db, "experiments/marketing/MKT-4", switched_off)
    record(db, "experiments/marketing/MKT-1", deleted_one)
    record(db, "experiments/marketing/MKT-2", refused_one)
    JobQueue(db).enqueue("index", {"experiment_id": never_sent}, dedupe_key=f"index:{never_sent}")
    client = PurgeClient(listing_error, refuse={"experiments/marketing/MKT-2"})
    monkeypatch.setattr(indexer, "make_index_client", lambda config: client)
    rc, out = run_cli("--config", cli_env, "purge-index", "--yes")
    assert rc == code, out
    assert message in out
    assert "2 Dokument(e) aus Knovas gel\u00f6scht" in out
    assert "RetryError" not in out and "HTTPError" not in out and "secret" not in out
    # Deleted -> "aus"; still in Knovas -> unchanged; never sent -> "aus",
    # marked as purged, so the maintenance does not upload it again -- right
    # after the purge nor once indexing is back on (review-jobs-5), which is
    # also why the switched-off one is marked now.
    assert state_of(db, deleted_one) == ("off", store.INDEX_OFF_PURGED)
    assert state_of(db, refused_one) == ("indexed", None)
    assert state_of(db, never_sent) == ("off", store.INDEX_OFF_PURGED)
    assert state_of(db, switched_off) == ("off", store.INDEX_OFF_PURGED)
    _, _, maintenance = tasks.build_handlers(settings=SETTINGS, index_client=object(),
                                             runner=None)
    maintenance(db)
    assert db.execute("SELECT count(*) FROM exp_jobs WHERE kind = 'index' "
                      "AND status = 'pending'").fetchone()[0] == 0


def test_purge_index_dry_run_no_longer_promises_the_whole_prefix(db, cli_env):
    rc, out = run_cli("--config", cli_env, "purge-index")
    assert rc == 1
    assert "alles, was Knovas" not in out
    assert "Zugriffsgruppe" in out


def test_status_counts_deleted_experiments_still_in_knovas(db, cli_env):
    record(db, "experiments/marketing/MKT-9", None)
    rc, out = run_cli("--config", cli_env, "status")
    assert rc == 0, out
    assert "Gel\u00f6schte Experimente noch in Knovas: 1" in out
