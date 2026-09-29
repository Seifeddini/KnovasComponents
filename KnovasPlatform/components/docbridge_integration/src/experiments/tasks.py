"""What the experiments workers do for each job kind.

``build_handlers`` binds the job kinds to the indexer and the service with
the process's settings, index client and runner, and returns the three
things ``jobs.JobWorker`` needs: handlers, on_dead hooks, and the periodic
maintenance function.

Plan: docs/superpowers/plans/2026-09-28-experiments-module.md (section 12)
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Tuple

from experiments import indexer
from experiments.jobs import Job, JobQueue, PermanentError

logger = logging.getLogger(__name__)

MSG_INDEX_DEAD = "Knovas war nicht erreichbar."
MSG_INCOMPLETE = "Der Auftrag ist unvollst\u00e4ndig."
#: Maintenance re-queues stranded experiments behind people's edits (10) and
#: ahead of bulk re-indexing (200).
MAINTENANCE_PRIORITY = 100


def _store():
    from experiments import store

    return store


def _service():
    from experiments import service

    return service


def _payload_value(job: Job, key: str) -> str:
    value = (job.payload or {}).get(key)
    if value is None or str(value).strip() == "":
        raise PermanentError(MSG_INCOMPLETE)
    return str(value)


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
        experiment_id = (job.payload or {}).get("experiment_id")
        if not experiment_id:
            logger.warning("Dead index job %s has no experiment id.", job.id)
            return
        _store().set_index_state(conn, str(experiment_id), "error", MSG_INDEX_DEAD)

    def evaluate_dead(conn: Any, job: Job) -> None:
        evaluation_id = (job.payload or {}).get("evaluation_id")
        if not evaluation_id:
            logger.warning("Dead evaluate job %s has no evaluation id.", job.id)
            return
        _service().on_evaluation_dead(conn, str(evaluation_id))

    def log_dead(conn: Any, job: Job) -> None:
        logger.warning("Experiments %s job %s is dead: %s", job.kind, job.id, job.last_error)

    def maintenance(conn: Any) -> None:
        """Re-queue experiments whose Knovas copy is pending or failed but
        that no job is working on (a job lost to a purge, a dead job, an
        upload refused while no access group was configured)."""
        if not settings.index_enabled or index_client is None:
            return
        if not getattr(settings, "index_access_groups", ()) and not settings.index_unrestricted:
            return
        ids = [str(i) for i in (_store().experiments_for_reindex(
            conn, states=("pending", "error")) or [])]
        if not ids:
            return
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

    handlers = {
        "index": handle_index,
        "unindex": handle_unindex,
        "evaluate": handle_evaluate,
        "pipeline": handle_pipeline,
    }
    on_dead = {
        "index": index_dead,
        "evaluate": evaluate_dead,
        "unindex": log_dead,
        "pipeline": log_dead,
    }
    return handlers, on_dead, maintenance
