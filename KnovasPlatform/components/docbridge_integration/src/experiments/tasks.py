"""What the experiments workers do for each job kind.

``build_handlers`` binds the job kinds to the indexer and the service with
the process's settings, index client and runner, and returns the three
things ``jobs.JobWorker`` needs: handlers, on_dead hooks, and the periodic
maintenance function.

The maintenance repeats what no job is working on any more:

* deletions: every pointer recorded in exp_index_documents whose experiment
  no longer exists is a document a deletion failed to remove from Knovas
  (the unindex job died, for example during a long Knovas outage). It gets a
  new unindex job -- also while indexing is switched off, because deletions
  must always reach Knovas;
* uploads: experiments still 'pending', or in 'error' for a reason that may
  go away by itself (Knovas was unreachable, no access group was set, the
  upload did not complete). A document Knovas refused is not sent again
  every few minutes: it goes up with the next edit or "Alles neu indexieren";
* switched back on: experiments set to 'off' while indexing was switched off
  (index_error NULL) are marked 'pending' and queued again, a batch per pass
  behind everything else, the upload rate limit spacing them as usual. The
  ones purge-index removed (store.INDEX_OFF_PURGED) stay out until someone
  re-indexes them.

Plan: docs/superpowers/plans/2026-09-28-experiments-module.md (section 12)
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from experiments import indexer
from experiments.jobs import Job, JobQueue, PermanentError

logger = logging.getLogger(__name__)

#: A dead index job whose last error says Knovas could not be reached.
MSG_INDEX_DEAD = "Knovas war nicht erreichbar."
#: A dead index job for any other reason (a lease that ran out, a job
#: deferred for a day, an unexpected error): the log has the details.
MSG_INDEX_INCOMPLETE = "Der Upload wurde nicht abgeschlossen (Details im Protokoll)."
MSG_INCOMPLETE = "Der Auftrag ist unvollst\u00e4ndig."
#: Maintenance re-queues stranded experiments behind people's edits (10) and
#: ahead of bulk re-indexing (200).
MAINTENANCE_PRIORITY = 100
#: Experiments switched off and on again are a bulk re-index: behind edits
#: and stranded uploads, at most REUPLOAD_BATCH per maintenance pass (the
#: uploads themselves wait for the rate slot; the next pass queues more).
REUPLOAD_PRIORITY = 200
REUPLOAD_BATCH = 500
#: Index errors the maintenance repeats on its own: causes outside the
#: document that may go away. A refusal by Knovas (MSG_REJECTED) or a key
#: Knovas cannot take (MSG_BAD_KEY) would fail the same way every time.
RETRYABLE_INDEX_ERRORS: Tuple[str, ...] = (MSG_INDEX_DEAD, MSG_INDEX_INCOMPLETE,
                                           indexer.MSG_NO_GROUP)
#: Last errors of index jobs that mean "Knovas could not be reached".
_UNREACHABLE_ERRORS = (indexer.MSG_UNREACHABLE, indexer.MSG_BUSY)
_TEMPORARY_ERROR_PREFIX = indexer.MSG_TEMPORARY.split("{", 1)[0]
_DELETE_REJECTED_PREFIX = indexer.MSG_DELETE_REJECTED.split("{", 1)[0]

#: At most this many failed deletions are re-queued per maintenance pass.
ORPHAN_BATCH = 500
#: A deletion whose job died is tried again after this long (its job has
#: already spent about an hour on retries) ...
ORPHAN_RETRY_AFTER_SECONDS = 3600
#: ... and after a day when Knovas refused it outright, so a refusal does not
#: fill the list of recent failures.
ORPHAN_REFUSED_RETRY_AFTER_SECONDS = 24 * 3600


def _service():
    from experiments import service

    return service


def _payload_value(job: Job, key: str) -> str:
    value = (job.payload or {}).get(key)
    if value is None or str(value).strip() == "":
        raise PermanentError(MSG_INCOMPLETE)
    return str(value)


def _uuid_text(value: Any) -> Optional[str]:
    try:
        return str(uuid.UUID(str(value).strip()))
    except (TypeError, ValueError, AttributeError):
        return None


# -- failed deletions --------------------------------------------------------------


_ORPHANS_SQL = (
    "SELECT d.pointer FROM exp_index_documents d "
    "WHERE NOT EXISTS (SELECT 1 FROM exp_experiments e WHERE e.id = d.experiment_id) "
)


def orphan_pointers(conn: Any, *, limit: int = ORPHAN_BATCH) -> List[str]:
    """Recorded Knovas pointers of experiments that no longer exist and that
    no job is deleting right now, the longest-standing first.

    A pointer whose unindex job died recently is left out for
    ORPHAN_RETRY_AFTER_SECONDS (ORPHAN_REFUSED_RETRY_AFTER_SECONDS when
    Knovas refused the deletion). The dedupe key is the one
    service.delete_experiment uses, so a deletion still waiting for its
    delay is not queued twice.
    """
    n = max(1, min(10_000, int(limit)))
    rows = conn.execute(
        _ORPHANS_SQL +
        "AND NOT EXISTS ("
        "  SELECT 1 FROM exp_jobs j WHERE j.dedupe_key = 'unindex:' || d.pointer AND ("
        "    j.status IN ('pending', 'running') OR ("
        "      j.status = 'dead' AND j.finished_at > clock_timestamp() - make_interval("
        "        secs => CASE WHEN j.last_error LIKE %s THEN %s ELSE %s END)))) "
        "ORDER BY d.indexed_at, d.pointer LIMIT %s",
        (_DELETE_REJECTED_PREFIX.replace("%", r"\%").replace("_", r"\_") + "%",
         float(ORPHAN_REFUSED_RETRY_AFTER_SECONDS), float(ORPHAN_RETRY_AFTER_SECONDS), n),
    ).fetchall()
    return [str(r[0]) for r in rows]


def orphan_count(conn: Any) -> int:
    """How many recorded Knovas documents belong to deleted experiments."""
    row = conn.execute(
        "SELECT count(*)::int FROM (" + _ORPHANS_SQL + ") AS orphans").fetchone()
    return int(row[0]) if row else 0


def requeue_failed_deletions(conn: Any) -> int:
    """Queue an unindex job for every orphan_pointers entry; returns how many."""
    queue = JobQueue(conn)
    queued = 0
    for pointer in orphan_pointers(conn):
        queue.enqueue("unindex", {"pointer": pointer}, dedupe_key=f"unindex:{pointer}",
                      priority=MAINTENANCE_PRIORITY)
        queued += 1
    if queued:
        logger.warning("Experiments maintenance re-queued %s Knovas deletion(s) of deleted "
                       "experiments.", queued)
    return queued


# -- stranded uploads ---------------------------------------------------------------


def stranded_experiments(conn: Any) -> List[str]:
    """Experiments whose Knovas copy is pending, or failed for a reason in
    RETRYABLE_INDEX_ERRORS (or without a recorded reason), newest first."""
    rows = conn.execute(
        "SELECT id::text FROM exp_experiments "
        "WHERE index_state = 'pending' OR (index_state = 'error' AND "
        "  (index_error IS NULL OR index_error = ANY(%s::text[]))) "
        "ORDER BY updated_at DESC, id DESC",
        (list(RETRYABLE_INDEX_ERRORS),),
    ).fetchall()
    return [str(r[0]) for r in rows]


def switched_off_experiments(conn: Any, *, limit: Optional[int] = None) -> List[str]:
    """Experiments turned 'off' while indexing was switched off (index_error
    NULL; purge-index leaves INDEX_OFF_PURGED), newest change first: their
    Knovas copy is missing or stale now that indexing is back on. At most
    ``limit`` (REUPLOAD_BATCH)."""
    n = REUPLOAD_BATCH if limit is None else limit
    rows = conn.execute(
        "SELECT id::text FROM exp_experiments WHERE index_state = 'off' AND index_error IS NULL "
        "ORDER BY updated_at DESC, id DESC LIMIT %s",
        (max(1, min(10_000, int(n))),),
    ).fetchall()
    return [str(r[0]) for r in rows]


def requeue_switched_off(conn: Any) -> int:
    """Mark each switched_off_experiments entry 'pending' and queue its
    upload; returns how many. The experiment row is written before the job's
    dedupe slot in one transaction, the order every writer keeps
    (service._queue_index), and only while it is still unmarked 'off'."""
    queue = JobQueue(conn)
    queued = 0
    for experiment_id in switched_off_experiments(conn):
        with conn.transaction():
            cur = conn.execute(
                "UPDATE exp_experiments SET index_state = 'pending' "
                "WHERE id = %s AND index_state = 'off' AND index_error IS NULL",
                (experiment_id,))
            if not cur.rowcount:
                continue
            queue.enqueue("index", {"experiment_id": experiment_id},
                          dedupe_key=f"index:{experiment_id}", priority=REUPLOAD_PRIORITY)
        queued += 1
    if queued:
        logger.info("Experiments maintenance queued %s experiment(s) switched off earlier for "
                    "upload.", queued)
    return queued


def index_dead_message(last_error: Optional[str]) -> str:
    """What the experiment shows for an index job that died with
    ``last_error``: MSG_INDEX_DEAD only when Knovas could not be reached."""
    text = str(last_error or "")
    if text in _UNREACHABLE_ERRORS or text.startswith(_TEMPORARY_ERROR_PREFIX):
        return MSG_INDEX_DEAD
    return MSG_INDEX_INCOMPLETE


def mark_index_dead(conn: Any, experiment_id: str, message: str, *,
                    job_created_at: Any = None) -> bool:
    """Record a dead index job on its experiment, unless a job that started
    after it has uploaded the experiment since: 'indexed' is only ever set
    for content still current at upload time, so an indexed_at at or after
    the dead job's creation means Knovas already has what that job was to
    send. One statement, so a success cannot slip in between check and
    write. True when the state was written."""
    cur = conn.execute(
        "UPDATE exp_experiments SET index_state = 'error', index_error = %s "
        "WHERE id = %s AND NOT (index_state = 'indexed' "
        "  AND COALESCE(indexed_at >= %s::timestamptz, FALSE))",
        (message, experiment_id, job_created_at),
    )
    return bool(cur.rowcount)


# -- wiring --------------------------------------------------------------------------


def build_handlers(*, settings: Any, index_client: Any,
                   runner: Any) -> Tuple[Dict[str, Callable], Dict[str, Callable], Callable]:
    def handle_index(conn: Any, job: Job) -> None:
        indexer.index_experiment(conn, _payload_value(job, "experiment_id"), index_client, settings)

    def handle_unindex(conn: Any, job: Job) -> None:
        indexer.unindex_pointer(conn, index_client, _payload_value(job, "pointer"))

    def handle_evaluate(conn: Any, job: Job) -> None:
        _service().execute_evaluation(conn, _payload_value(job, "evaluation_id"),
                                      settings=settings, runner=runner)

    def handle_pipeline(conn: Any, job: Job) -> None:
        _service().run_pipeline_job(conn, _payload_value(job, "experiment_id"),
                                    settings=settings, runner=runner)

    def index_dead(conn: Any, job: Job) -> None:
        raw = (job.payload or {}).get("experiment_id")
        experiment_id = _uuid_text(raw)
        if experiment_id is None:
            logger.warning("Dead index job %s has no usable experiment id.", job.id)
            return
        # A newer job for the experiment is still waiting or running: it
        # records the outcome itself.
        if JobQueue(conn).active_dedupe_keys([f"index:{raw}", f"index:{experiment_id}"]):
            logger.info("Dead index job %s: a newer job for experiment %s is active.",
                        job.id, experiment_id)
            return
        message = index_dead_message(job.last_error)
        if not mark_index_dead(conn, experiment_id, message, job_created_at=job.created_at):
            logger.info("Dead index job %s: experiment %s was indexed since or is gone.",
                        job.id, experiment_id)

    def evaluate_dead(conn: Any, job: Job) -> None:
        evaluation_id = (job.payload or {}).get("evaluation_id")
        if not evaluation_id:
            logger.warning("Dead evaluate job %s has no evaluation id.", job.id)
            return
        _service().on_evaluation_dead(conn, str(evaluation_id))

    def unindex_dead(conn: Any, job: Job) -> None:
        logger.warning("Experiments unindex job %s is dead (%s); the maintenance repeats the "
                       "deletion later.", job.id, job.last_error)

    def log_dead(conn: Any, job: Job) -> None:
        logger.warning("Experiments %s job %s is dead: %s", job.kind, job.id, job.last_error)

    def maintenance(conn: Any) -> None:
        """Repeat failed deletions, then re-queue experiments whose Knovas
        copy is pending or failed retryably but that no job is working on (a
        job lost to a purge, a dead job, an upload refused while no access
        group was configured), then queue the ones switched off earlier."""
        if index_client is not None:
            try:
                requeue_failed_deletions(conn)
            except Exception:  # noqa: BLE001 - the upload half must still run
                logger.exception("Experiments maintenance: re-queueing deletions failed.")
                _rollback_failed(conn)
        if not settings.index_enabled or index_client is None:
            return
        if not getattr(settings, "index_access_groups", ()) and not settings.index_unrestricted:
            return
        ids = stranded_experiments(conn)
        if ids:
            queue = JobQueue(conn)
            active = queue.active_dedupe_keys(f"index:{i}" for i in ids)
            queued = 0
            for experiment_id in ids:
                key = f"index:{experiment_id}"
                if key in active:
                    continue
                queue.enqueue("index", {"experiment_id": experiment_id}, dedupe_key=key,
                              priority=MAINTENANCE_PRIORITY)
                queued += 1
            if queued:
                logger.info("Experiments maintenance re-queued %s index jobs.", queued)
        # After the stranded ones: those marked 'pending' here have their job.
        requeue_switched_off(conn)

    handlers = {
        "index": handle_index,
        "unindex": handle_unindex,
        "evaluate": handle_evaluate,
        "pipeline": handle_pipeline,
    }
    on_dead = {
        "index": index_dead,
        "evaluate": evaluate_dead,
        "unindex": unindex_dead,
        "pipeline": log_dead,
    }
    return handlers, on_dead, maintenance


def _rollback_failed(conn: Any) -> None:
    try:
        from psycopg import pq

        if conn.info.transaction_status in (pq.TransactionStatus.INTRANS,
                                            pq.TransactionStatus.INERROR):
            conn.rollback()
    except Exception:  # noqa: BLE001
        pass
