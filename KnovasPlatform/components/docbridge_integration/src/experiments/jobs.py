"""Background work of the experiments module: a small job queue in platform-db.

Why PostgreSQL and not a broker: every gunicorn worker already holds a
connection to platform-db, the jobs must survive a restart, and the volume is
tiny (a handful of re-indexes and evaluations per minute). A second moving
part (Redis, Celery) would add an outage mode without adding capacity.

How the queue stays correct with several processes polling it:

* A job is claimed with ``FOR UPDATE SKIP LOCKED`` and a lease
  (``locked_until``). A worker that dies mid-job loses nothing: once the lease
  runs out another worker claims the job again.
* Every state change after the claim is *fenced*: it only applies while the
  row is still ``running`` under the same ``locked_by`` and ``attempts`` the
  claim returned. A worker whose lease expired and was taken over can no
  longer complete, retry or fail a job it does not own any more.
* ``dedupe_key`` coalesces requests: while a job with that key is pending, a
  new request only pulls it forward (earlier ``run_after``, lower
  ``priority``). A job that would go back to pending while a newer pending job
  with its key exists is closed as superseded instead -- the newer one carries
  the work.
* Knovas document inits are rate limited per tenant, so ``index`` jobs also
  take a shared slot from ``exp_rate_slots``; every worker of every process
  takes it from the same row.

``last_error`` is shown to experiments managers (index status page), so it
only ever holds fixed German messages. Exception text goes to the log.

Plan: docs/superpowers/plans/2026-09-28-experiments-module.md (sections 11, 12)
"""

from __future__ import annotations

import logging
import math
import os
import random
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from experiments.errors import ExperimentsError

logger = logging.getLogger(__name__)

#: Every kind the exp_jobs CHECK constraint allows.
JOB_KINDS: Tuple[str, ...] = ("index", "unindex", "evaluate", "pipeline")
#: Thread A: Knovas and in-process work. Thread B: the evaluation runner,
#: whose jobs may take minutes and must not hold up re-indexing.
INDEX_THREAD_KINDS: Tuple[str, ...] = ("index", "unindex", "pipeline")
EVALUATE_THREAD_KINDS: Tuple[str, ...] = ("evaluate",)
INDEX_THREAD_LEASE_SECONDS = 600
EVALUATE_LEASE_EXTRA_SECONDS = 120

#: The rate slot every Knovas document init takes (see the migration).
RATE_SLOT_KNOVAS_INIT = "knovas_init"

BASE_BACKOFF_SECONDS = 30
MAX_BACKOFF_SECONDS = 3600
#: A job deferred again and again (rate slot, runner busy) is given up after a
#: day, so a permanently blocked dependency shows up as a dead job.
DEFER_CAP_SECONDS = 24 * 3600
MAX_DELAY_SECONDS = 7 * 24 * 3600
MAX_ERROR_CHARS = 500

MSG_UNEXPECTED = "Unerwarteter Fehler bei der Bearbeitung (Details im Protokoll)."
MSG_DEFERRED_TOO_LONG = "Zu lange zur\u00fcckgestellt."
MSG_SUPERSEDED = "Durch einen neueren Auftrag ersetzt."
MSG_LEASE_EXPIRED = "Die Bearbeitung wurde wiederholt nicht abgeschlossen."
MSG_NO_HANDLER = "F\u00fcr diese Auftragsart gibt es keine Bearbeitung."
MSG_PERMANENT = "Der Auftrag ist endg\u00fcltig fehlgeschlagen."
MSG_UNKNOWN_ERROR = "Ohne Angabe."


class RetryLater(Exception):
    """Run the job again after ``delay_seconds`` without using up an attempt.

    For waiting on something that is expected to become free (the rate slot,
    a busy runner, a Retry-After), not for failures. ``reason`` is stored as
    the job's last_error, so it must be a fixed German message (or empty).
    """

    def __init__(self, delay_seconds: float, reason: str = "") -> None:
        super().__init__(reason or f"retry in {delay_seconds} s")
        try:
            delay = float(delay_seconds)
        except (TypeError, ValueError):
            delay = 60.0
        if not math.isfinite(delay):
            delay = 60.0
        self.delay_seconds = max(0.0, min(float(MAX_DELAY_SECONDS), delay))
        self.reason = str(reason or "")


class PermanentError(Exception):
    """The job cannot succeed; mark it dead without further attempts.

    ``str(exc)`` is stored as the job's last_error and shown to managers:
    a fixed German message without internal detail. ``handled=True`` says the
    handler has already recorded the outcome where people see it (for example
    the experiment's index error), so the kind's on_dead hook is skipped and
    cannot overwrite the more specific message.
    """

    def __init__(self, message: str = "", *, handled: bool = False) -> None:
        super().__init__(message)
        self.handled = bool(handled)


@dataclass
class Job:
    id: int
    kind: str
    payload: dict
    attempts: int
    max_attempts: int
    locked_by: str
    created_at: datetime
    #: Not part of the fencing token; filled in by the queue as the job moves.
    dedupe_key: Optional[str] = None
    status: str = "running"
    last_error: Optional[str] = None


_JOB_COLUMNS = "id, kind, payload, attempts, max_attempts, locked_by, created_at, dedupe_key"


def _job_from_row(row: Any, *, status: str = "running", last_error: Optional[str] = None) -> Job:
    payload = row[2]
    if not isinstance(payload, dict):
        payload = {}
    return Job(
        id=int(row[0]), kind=str(row[1]), payload=payload, attempts=int(row[3]),
        max_attempts=int(row[4]), locked_by=str(row[5] or ""), created_at=row[6],
        dedupe_key=row[7], status=status, last_error=last_error,
    )


def _clip(message: Optional[str]) -> Optional[str]:
    if message is None:
        return None
    text = " ".join(str(message).split())
    if not text:
        return None
    return text[:MAX_ERROR_CHARS]


def _finite_seconds(value: Any, *, default: float, lo: float, hi: float) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(x):
        return default
    return max(lo, min(hi, x))


def backoff_seconds(attempts: int) -> int:
    """30 s, 60 s, 120 s, ... capped at an hour."""
    exponent = max(0, int(attempts) - 1)
    if exponent >= 12:  # 30 * 2**12 is already far above the cap
        return MAX_BACKOFF_SECONDS
    return min(MAX_BACKOFF_SECONDS, BASE_BACKOFF_SECONDS * 2 ** exponent)


class JobQueue:
    """All SQL of exp_jobs. Takes a connection (autocommit, like every
    platform-db connection); methods that change several rows open their own
    transaction, which becomes a savepoint inside a caller's transaction."""

    def __init__(self, conn: Any) -> None:
        self.conn = conn

    # -- producing ---------------------------------------------------------

    def enqueue(self, kind: str, payload: dict, *, dedupe_key: Optional[str] = None,
                delay_seconds: float = 0, priority: int = 100,
                max_attempts: int = 8) -> Optional[int]:
        """Add a job, or pull the pending job with the same dedupe_key forward.

        Returns the id of the job that will do the work (the existing one when
        coalesced).
        """
        from psycopg.types.json import Jsonb

        if kind not in JOB_KINDS:
            raise ValueError(f"unknown job kind {kind!r}")
        if not isinstance(payload, dict):
            raise ValueError("payload must be a dict")
        delay = _finite_seconds(delay_seconds, default=0.0, lo=0.0, hi=float(MAX_DELAY_SECONDS))
        prio = max(-32768, min(32767, int(priority)))
        attempts = max(1, min(1000, int(max_attempts)))
        key = None if dedupe_key is None else str(dedupe_key)
        row = self.conn.execute(
            "INSERT INTO exp_jobs (kind, dedupe_key, payload, priority, max_attempts, run_after) "
            "VALUES (%s, %s, %s, %s, %s, clock_timestamp() + make_interval(secs => %s)) "
            "ON CONFLICT (dedupe_key) WHERE status = 'pending' DO UPDATE SET "
            "  run_after = LEAST(exp_jobs.run_after, EXCLUDED.run_after), "
            "  priority = LEAST(exp_jobs.priority, EXCLUDED.priority), "
            "  updated_at = clock_timestamp() "
            "RETURNING id",
            (kind, key, Jsonb(payload), prio, attempts, delay),
        ).fetchone()
        return int(row[0]) if row else None

    # -- consuming ---------------------------------------------------------

    def claim(self, worker_id: str, *, kinds: Tuple[str, ...], lease_seconds: int) -> Optional[Job]:
        """Take the most urgent due job of these kinds, or None.

        Also takes over jobs whose lease ran out while attempts remain. Index
        jobs are only handed out while the Knovas rate slot is free, so a
        worker does not claim one just to put it back.
        """
        wanted = [k for k in dict.fromkeys(kinds or ()) if k in JOB_KINDS]
        if not wanted:
            return None
        lease = int(_finite_seconds(lease_seconds, default=600.0, lo=1.0, hi=86400.0))
        row = self.conn.execute(
            "UPDATE exp_jobs SET status = 'running', attempts = attempts + 1, "
            "  locked_by = %s, locked_until = clock_timestamp() + make_interval(secs => %s), "
            "  updated_at = clock_timestamp() "
            "WHERE id = ("
            "  SELECT id FROM exp_jobs "
            "  WHERE kind = ANY(%s) "
            "    AND ((status = 'pending' AND run_after <= clock_timestamp()) "
            "         OR (status = 'running' AND locked_until < clock_timestamp() "
            "             AND attempts < max_attempts)) "
            "    AND (kind <> 'index' OR "
            "         (SELECT next_at FROM exp_rate_slots WHERE name = %s) <= clock_timestamp()) "
            "  ORDER BY priority, run_after, id "
            "  LIMIT 1 "
            "  FOR UPDATE SKIP LOCKED) "
            f"RETURNING {_JOB_COLUMNS}",
            (str(worker_id), lease, wanted, RATE_SLOT_KNOVAS_INIT),
        ).fetchone()
        return _job_from_row(row) if row else None

    def _fence(self) -> str:
        return "id = %s AND status = 'running' AND locked_by = %s AND attempts = %s"

    @staticmethod
    def _fence_args(job: Job) -> Tuple[Any, ...]:
        return (int(job.id), str(job.locked_by), int(job.attempts))

    @staticmethod
    def _lease_lost(job: Job, action: str) -> None:
        logger.warning(
            "Experiments job %s (%s): lease lost before %s; another worker owns it now.",
            job.id, job.kind, action,
        )

    def complete(self, job: Job) -> bool:
        cur = self.conn.execute(
            "UPDATE exp_jobs SET status = 'done', finished_at = clock_timestamp(), "
            "  locked_until = NULL, updated_at = clock_timestamp() "
            f"WHERE {self._fence()}",
            self._fence_args(job),
        )
        if cur.rowcount != 1:
            self._lease_lost(job, "complete")
            return False
        job.status = "done"
        return True

    def fail(self, job: Job, error: str) -> bool:
        """Dead, no further attempts."""
        message = _clip(error) or MSG_PERMANENT
        cur = self.conn.execute(
            "UPDATE exp_jobs SET status = 'dead', finished_at = clock_timestamp(), "
            "  last_error = %s, locked_until = NULL, updated_at = clock_timestamp() "
            f"WHERE {self._fence()}",
            (message,) + self._fence_args(job),
        )
        if cur.rowcount != 1:
            self._lease_lost(job, "fail")
            return False
        job.status = "dead"
        job.last_error = message
        return True

    def retry(self, job: Job, error: str) -> bool:
        """Back to pending after a backoff that grows with the attempts, or dead
        when the attempts are used up."""
        return self._reschedule(job, error=_clip(error) or MSG_UNEXPECTED, delay=None,
                                consume_attempt=True) is not None

    def defer(self, job: Job, delay_seconds: float, reason: str = "") -> bool:
        """Back to pending after ``delay_seconds`` without using up an attempt;
        dead when the job is older than a day."""
        delay = _finite_seconds(delay_seconds, default=60.0, lo=0.0, hi=float(MAX_DELAY_SECONDS))
        return self._reschedule(job, error=_clip(reason), delay=delay,
                                consume_attempt=False) is not None

    def _reschedule(self, job: Job, *, error: Optional[str], delay: Optional[float],
                    consume_attempt: bool) -> Optional[str]:
        """Shared path of retry and defer. Returns the outcome ('pending',
        'dead' or 'superseded') or None when the lease was lost."""
        import psycopg

        with self.conn.transaction():
            row = self.conn.execute(
                "SELECT dedupe_key, priority, "
                "  created_at < clock_timestamp() - make_interval(secs => %s) "
                f"FROM exp_jobs WHERE {self._fence()} FOR UPDATE",
                (float(DEFER_CAP_SECONDS),) + self._fence_args(job),
            ).fetchone()
            if row is None:
                self._lease_lost(job, "retry" if consume_attempt else "defer")
                return None
            dedupe_key, priority, too_old = row[0], int(row[1]), bool(row[2])

            # A newer request for the same work is already waiting: it will do
            # what this job would have retried, so this one is closed.
            if dedupe_key is not None:
                other = self._pending_duplicate(dedupe_key, job.id)
                if other is not None:
                    return self._supersede(job, other, priority)

            if consume_attempt and job.attempts >= job.max_attempts:
                return self._mark_dead(job, error or MSG_UNEXPECTED)
            if not consume_attempt and too_old:
                return self._mark_dead(job, MSG_DEFERRED_TOO_LONG)

            wait = float(backoff_seconds(job.attempts)) if consume_attempt else float(delay or 0.0)
            for _ in range(3):
                try:
                    with self.conn.transaction():
                        self.conn.execute(
                            "UPDATE exp_jobs SET status = 'pending', "
                            "  run_after = clock_timestamp() + make_interval(secs => %s), "
                            "  attempts = GREATEST(attempts - %s, 0), "
                            "  locked_by = NULL, locked_until = NULL, "
                            "  last_error = COALESCE(%s, last_error), "
                            "  updated_at = clock_timestamp() "
                            "WHERE id = %s",
                            (wait, 0 if consume_attempt else 1, error, int(job.id)),
                        )
                except psycopg.errors.UniqueViolation:
                    # A pending job with the same key was enqueued between the
                    # check above and this update; it carries the work now.
                    other = self._pending_duplicate(dedupe_key, job.id)
                    if other is not None:
                        return self._supersede(job, other, priority)
                    continue
                job.status = "pending"
                if error is not None:
                    job.last_error = error
                if not consume_attempt:
                    job.attempts = max(job.attempts - 1, 0)
                return "pending"
            # Three races in a row: a pending twin kept appearing, so the work
            # is covered by one of them either way.
            return self._supersede(job, None, priority)

    def _pending_duplicate(self, dedupe_key: str, exclude_id: int) -> Optional[int]:
        row = self.conn.execute(
            "SELECT id FROM exp_jobs WHERE dedupe_key = %s AND status = 'pending' AND id <> %s "
            "FOR UPDATE",
            (dedupe_key, int(exclude_id)),
        ).fetchone()
        return int(row[0]) if row else None

    def _supersede(self, job: Job, other_id: Optional[int], priority: int) -> str:
        self.conn.execute(
            "UPDATE exp_jobs SET status = 'done', finished_at = clock_timestamp(), "
            "  last_error = %s, locked_until = NULL, updated_at = clock_timestamp() "
            "WHERE id = %s",
            (MSG_SUPERSEDED, int(job.id)),
        )
        if other_id is not None:
            # The waiting job inherits the urgency of the one it replaces.
            self.conn.execute(
                "UPDATE exp_jobs SET priority = LEAST(priority, %s), updated_at = clock_timestamp() "
                "WHERE id = %s",
                (int(priority), int(other_id)),
            )
        job.status = "superseded"
        job.last_error = MSG_SUPERSEDED
        logger.info("Experiments job %s (%s) superseded by pending job %s.",
                    job.id, job.kind, other_id)
        return "superseded"

    def _mark_dead(self, job: Job, message: str) -> str:
        message = _clip(message) or MSG_PERMANENT
        self.conn.execute(
            "UPDATE exp_jobs SET status = 'dead', finished_at = clock_timestamp(), "
            "  last_error = %s, locked_until = NULL, updated_at = clock_timestamp() "
            "WHERE id = %s",
            (message, int(job.id)),
        )
        job.status = "dead"
        job.last_error = message
        return "dead"

    # -- housekeeping ------------------------------------------------------

    def sweep_expired(self) -> List[Job]:
        """Jobs whose lease ran out with no attempts left: dead, returned so
        the worker can run their on_dead hooks."""
        rows = self.conn.execute(
            "UPDATE exp_jobs SET status = 'dead', finished_at = clock_timestamp(), "
            "  last_error = %s, locked_until = NULL, updated_at = clock_timestamp() "
            "WHERE status = 'running' AND locked_until < clock_timestamp() "
            "  AND attempts >= max_attempts "
            f"RETURNING {_JOB_COLUMNS}",
            (MSG_LEASE_EXPIRED,),
        ).fetchall()
        return [_job_from_row(r, status="dead", last_error=MSG_LEASE_EXPIRED) for r in rows]

    def counts(self) -> Dict[str, int]:
        out = {"pending": 0, "running": 0, "done": 0, "dead": 0}
        for status, n in self.conn.execute(
            "SELECT status, count(*)::int FROM exp_jobs GROUP BY status"
        ).fetchall():
            out[str(status)] = int(n)
        return out

    def recent_failures(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Dead jobs, newest first: {"kind","error","at"}."""
        n = max(1, min(500, int(limit)))
        rows = self.conn.execute(
            "SELECT kind, last_error, COALESCE(finished_at, updated_at) FROM exp_jobs "
            "WHERE status = 'dead' ORDER BY COALESCE(finished_at, updated_at) DESC, id DESC "
            "LIMIT %s",
            (n,),
        ).fetchall()
        out = []
        for kind, error, at in rows:
            out.append({
                "kind": str(kind),
                "error": _clip(error) or MSG_UNKNOWN_ERROR,
                "at": at.isoformat() if at is not None else None,
            })
        return out

    def purge_finished(self, older_than_days: int = 7) -> int:
        days = max(0, int(older_than_days))
        cur = self.conn.execute(
            "DELETE FROM exp_jobs WHERE status IN ('done', 'dead') "
            "AND finished_at < clock_timestamp() - make_interval(days => %s)",
            (days,),
        )
        return max(0, int(cur.rowcount or 0))

    def active_dedupe_keys(self, keys: Iterable[str]) -> Set[str]:
        """Which of these dedupe keys have a pending or running job."""
        wanted = [str(k) for k in dict.fromkeys(keys or ()) if k is not None]
        if not wanted:
            return set()
        rows = self.conn.execute(
            "SELECT DISTINCT dedupe_key FROM exp_jobs "
            "WHERE dedupe_key = ANY(%s) AND status IN ('pending', 'running')",
            (wanted,),
        ).fetchall()
        return {str(r[0]) for r in rows}

    def cancel_pending(self, kinds: Tuple[str, ...]) -> int:
        """Delete pending jobs of these kinds (purge-index: nothing may upload
        the documents again right after they were removed)."""
        wanted = [k for k in dict.fromkeys(kinds or ()) if k in JOB_KINDS]
        if not wanted:
            return 0
        cur = self.conn.execute(
            "DELETE FROM exp_jobs WHERE status = 'pending' AND kind = ANY(%s)", (wanted,)
        )
        return max(0, int(cur.rowcount or 0))


def take_rate_slot(conn: Any, name: str, per_minute: int) -> float:
    """Take the shared slot: 0.0 when taken, else the seconds until it frees.

    The slot is a single row every process updates, so N workers together stay
    under ``per_minute`` document inits.
    """
    try:
        rate = max(1, int(per_minute))
    except (TypeError, ValueError):
        rate = 1
    interval = 60.0 / rate
    for _ in range(3):
        row = conn.execute(
            "UPDATE exp_rate_slots "
            "SET next_at = GREATEST(next_at, clock_timestamp()) + make_interval(secs => %s) "
            "WHERE name = %s AND next_at <= clock_timestamp() RETURNING 0.0::float8",
            (interval, name),
        ).fetchone()
        if row is not None:
            return 0.0
        row = conn.execute(
            "SELECT GREATEST(0.0, EXTRACT(EPOCH FROM (next_at - clock_timestamp())))::float8 "
            "FROM exp_rate_slots WHERE name = %s",
            (name,),
        ).fetchone()
        if row is None:
            # The migration creates the row; recreate it if someone removed it
            # rather than blocking every upload for good.
            conn.execute(
                "INSERT INTO exp_rate_slots (name, next_at) VALUES (%s, clock_timestamp()) "
                "ON CONFLICT (name) DO NOTHING",
                (name,),
            )
            continue
        wait = float(row[0] or 0.0)
        if wait > 0.0:
            return wait
        # Freed between the two statements: try to take it again.
    return 1.0


def ensure_rate_slot(conn: Any, name: str = RATE_SLOT_KNOVAS_INIT) -> None:
    conn.execute(
        "INSERT INTO exp_rate_slots (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (name,)
    )


def reset_stuck_rate_slots(conn: Any, max_ahead_seconds: float = 300.0) -> int:
    """A slot is never legitimately more than one interval (at most 60 s)
    ahead. One far in the future (the database clock jumped back, a manual
    edit) would stop every index job, so it is pulled back to now."""
    cur = conn.execute(
        "UPDATE exp_rate_slots SET next_at = clock_timestamp() "
        "WHERE next_at > clock_timestamp() + make_interval(secs => %s)",
        (float(max_ahead_seconds),),
    )
    return max(0, int(cur.rowcount or 0))


def _default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


class JobWorker(threading.Thread):
    """A daemon thread that claims and runs jobs of some kinds.

    It owns one database connection, reconnects with backoff when the
    database goes away, and never lets an exception end the thread: a job that
    fails is retried or marked dead, a database outage is waited out.
    """

    #: Seconds between housekeeping passes (sweep, maintenance hook, purge).
    maintenance_interval = 600.0
    #: First housekeeping pass shortly after start, spread over this range so
    #: several processes started together do not run it at the same moment.
    first_maintenance_after = (20.0, 90.0)
    reconnect_initial = 1.0
    reconnect_max = 60.0
    application_name = "knovas-experiments-worker"

    def __init__(self, *, connect: Callable[[], Any], handlers: Dict[str, Callable],
                 on_dead: Dict[str, Callable], kinds: Tuple[str, ...], lease_seconds: int,
                 poll_seconds: float, worker_id: Optional[str] = None,
                 maintenance: Optional[Callable[[Any], None]] = None) -> None:
        kinds = tuple(k for k in dict.fromkeys(kinds or ()) if k in JOB_KINDS)
        super().__init__(name=f"experiments-worker-{'-'.join(kinds) or 'none'}", daemon=True)
        self.connect = connect
        self.handlers = dict(handlers or {})
        self.on_dead = dict(on_dead or {})
        self.kinds = kinds
        self.lease_seconds = int(_finite_seconds(lease_seconds, default=600.0, lo=1.0, hi=86400.0))
        self.poll_seconds = _finite_seconds(poll_seconds, default=5.0, lo=0.01, hi=3600.0)
        self.worker_id = str(worker_id or _default_worker_id())
        self.maintenance = maintenance
        self._halt = threading.Event()
        self._conn: Any = None
        lo, hi = self.first_maintenance_after
        self._next_maintenance_at = time.monotonic() + random.uniform(lo, hi)

    # -- lifecycle ---------------------------------------------------------

    def stop(self) -> None:
        """Ask the thread to finish the job at hand and end; join to wait."""
        self._halt.set()

    @property
    def stopping(self) -> bool:
        return self._halt.is_set()

    def run(self) -> None:
        backoff = self.reconnect_initial
        try:
            while not self._halt.is_set():
                try:
                    conn = self._ensure_connection()
                except Exception as exc:  # noqa: BLE001 - keep polling whatever broke
                    logger.warning(
                        "Experiments worker %s: database not reachable (%s); retrying in %.0f s.",
                        self.worker_id, type(exc).__name__, backoff,
                    )
                    self._halt.wait(backoff * random.uniform(0.8, 1.2))
                    backoff = min(backoff * 2, self.reconnect_max)
                    continue
                backoff = self.reconnect_initial
                try:
                    if time.monotonic() >= self._next_maintenance_at:
                        self._next_maintenance_at = (
                            time.monotonic() + self.maintenance_interval * random.uniform(0.9, 1.1))
                        self.run_maintenance(conn)
                    worked = self.run_once(conn)
                except BaseException:  # noqa: BLE001 - the thread must not die
                    logger.exception("Experiments worker %s: polling failed; reconnecting.",
                                     self.worker_id)
                    self._drop_connection()
                    self._halt.wait(self.reconnect_initial * random.uniform(0.8, 1.2))
                    continue
                if not worked:
                    self._halt.wait(self.poll_seconds * random.uniform(0.8, 1.2))
        finally:
            self._drop_connection()

    def _ensure_connection(self) -> Any:
        from psycopg import pq

        conn = self._conn
        if conn is not None:
            if not conn.closed and not getattr(conn, "broken", False):
                status = conn.info.transaction_status
                if status == pq.TransactionStatus.IDLE:
                    return conn
                if status in (pq.TransactionStatus.INTRANS, pq.TransactionStatus.INERROR):
                    try:
                        conn.rollback()
                        return conn
                    except Exception:  # noqa: BLE001
                        pass
            self._drop_connection()
        conn = self.connect()
        try:
            if not conn.autocommit:
                conn.autocommit = True
            conn.execute("SET statement_timeout = '120s'")
            conn.execute("SET idle_in_transaction_session_timeout = '60s'")
            conn.execute("SELECT set_config('application_name', %s, false)",
                         (self.application_name,))
            ensure_rate_slot(conn)
        except BaseException:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            raise
        self._conn = conn
        return conn

    def _drop_connection(self) -> None:
        conn, self._conn = self._conn, None
        if conn is None:
            return
        try:
            conn.close()
        except Exception:  # noqa: BLE001 - closing a dead connection may fail too
            pass

    # -- one job -----------------------------------------------------------

    def run_once(self, conn: Any) -> bool:
        """Claim one due job and run it. False when there was nothing to do."""
        queue = JobQueue(conn)
        job = queue.claim(self.worker_id, kinds=self.kinds, lease_seconds=self.lease_seconds)
        if job is None:
            return False
        self._process(conn, queue, job)
        return True

    def _process(self, conn: Any, queue: JobQueue, job: Job) -> None:
        handler = self.handlers.get(job.kind)
        if handler is None:
            logger.error("Experiments job %s: no handler for kind %r.", job.id, job.kind)
            if queue.fail(job, MSG_NO_HANDLER):
                self._run_on_dead(conn, job)
            return
        started = time.monotonic()
        try:
            handler(conn, job)
        except RetryLater as exc:
            self._settle(conn)
            logger.info("Experiments job %s (%s) deferred by %.0f s: %s",
                        job.id, job.kind, exc.delay_seconds, exc.reason or "-")
            outcome = queue._reschedule(job, error=_clip(exc.reason), delay=exc.delay_seconds,
                                        consume_attempt=False)
            if outcome == "dead":
                self._run_on_dead(conn, job)
        except PermanentError as exc:
            self._settle(conn)
            message = _clip(str(exc)) or MSG_PERMANENT
            logger.warning("Experiments job %s (%s) failed for good: %s", job.id, job.kind, message)
            if queue.fail(job, message) and not exc.handled:
                self._run_on_dead(conn, job)
        except ExperimentsError as exc:
            # errors.py messages are German and free of internal detail.
            self._settle(conn)
            logger.warning("Experiments job %s (%s) attempt %s failed: %s",
                           job.id, job.kind, job.attempts, exc.message)
            if queue.retry(job, exc.message) and job.status == "dead":
                self._run_on_dead(conn, job)
        except BaseException as exc:  # noqa: BLE001 - a job must never take the thread down
            logger.exception("Experiments job %s (%s) attempt %s failed unexpectedly.",
                             job.id, job.kind, job.attempts)
            self._settle(conn)
            if queue.retry(job, MSG_UNEXPECTED) and job.status == "dead":
                self._run_on_dead(conn, job)
            if isinstance(exc, KeyboardInterrupt):
                # Only the CLI's foreground run sees this; the job is recorded,
                # now let the operator's Ctrl+C through.
                raise
        else:
            queue.complete(job)
            logger.debug("Experiments job %s (%s) done in %.2f s.",
                         job.id, job.kind, time.monotonic() - started)

    @staticmethod
    def _settle(conn: Any) -> None:
        """Leave the connection usable after a handler raised: roll back a
        transaction the handler left open. A broken connection raises, and the
        run loop reconnects (the job's lease then hands it to the next claim)."""
        from psycopg import pq

        status = conn.info.transaction_status
        if status in (pq.TransactionStatus.INTRANS, pq.TransactionStatus.INERROR):
            conn.rollback()

    def _run_on_dead(self, conn: Any, job: Job) -> None:
        hook = self.on_dead.get(job.kind)
        if hook is None:
            logger.warning("Experiments job %s (%s) is dead: %s", job.id, job.kind, job.last_error)
            return
        try:
            hook(conn, job)
        except BaseException:  # noqa: BLE001
            logger.exception("on_dead hook for experiments job %s (%s) failed.", job.id, job.kind)
            try:
                self._settle(conn)
            except Exception:  # noqa: BLE001
                pass

    # -- housekeeping ------------------------------------------------------

    def run_maintenance(self, conn: Any) -> None:
        """Sweep expired leases (running their on_dead hooks), run the
        maintenance hook, repair a stuck rate slot, purge old finished jobs.
        Each step is independent: one failing does not skip the others."""
        queue = JobQueue(conn)
        try:
            for job in queue.sweep_expired():
                logger.warning("Experiments job %s (%s) dead: lease expired %s times.",
                               job.id, job.kind, job.attempts)
                self._run_on_dead(conn, job)
        except Exception:  # noqa: BLE001
            logger.exception("Experiments worker %s: sweep failed.", self.worker_id)
            self._settle(conn)
        try:
            reset_stuck_rate_slots(conn)
        except Exception:  # noqa: BLE001
            logger.exception("Experiments worker %s: rate slot check failed.", self.worker_id)
            self._settle(conn)
        if self.maintenance is not None:
            try:
                self.maintenance(conn)
            except Exception:  # noqa: BLE001
                logger.exception("Experiments worker %s: maintenance failed.", self.worker_id)
                self._settle(conn)
        try:
            purged = queue.purge_finished()
            if purged:
                logger.info("Experiments worker %s: purged %s finished jobs.", self.worker_id, purged)
        except Exception:  # noqa: BLE001
            logger.exception("Experiments worker %s: purge failed.", self.worker_id)
            self._settle(conn)


_workers_lock = threading.Lock()
_workers_by_pid: Dict[int, List[JobWorker]] = {}


def start_workers_once(*, settings: Any, connect: Callable[[], Any], handlers: Dict[str, Callable],
                       on_dead: Dict[str, Callable],
                       maintenance: Optional[Callable[[Any], None]]) -> List[JobWorker]:
    """Start the two worker threads of this process, once.

    Keyed by pid: create_app may run more than once in a process (tests, a
    reload), and gunicorn forks workers from a master that may have imported
    the app -- threads do not survive a fork, so each child starts its own.
    """
    if not getattr(settings, "enabled", False) or not getattr(settings, "worker_enabled", False):
        return []
    pid = os.getpid()
    with _workers_lock:
        existing = _workers_by_pid.get(pid)
        if existing is not None:
            return list(existing)
        poll = float(getattr(settings, "worker_poll_seconds", 5.0) or 5.0)
        runner_timeout = int(getattr(settings, "runner_timeout_seconds", 90) or 90)
        workers = [
            JobWorker(connect=connect, handlers=handlers, on_dead=on_dead,
                      kinds=INDEX_THREAD_KINDS, lease_seconds=INDEX_THREAD_LEASE_SECONDS,
                      poll_seconds=poll, maintenance=maintenance),
            JobWorker(connect=connect, handlers=handlers, on_dead=on_dead,
                      kinds=EVALUATE_THREAD_KINDS,
                      lease_seconds=runner_timeout + EVALUATE_LEASE_EXTRA_SECONDS,
                      poll_seconds=poll, maintenance=None),
        ]
        for worker in workers:
            worker.start()
        _workers_by_pid[pid] = workers
        logger.info("Experiments workers started in process %s.", pid)
        return list(workers)
