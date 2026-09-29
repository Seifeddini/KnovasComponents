"""Operator commands for the experiments module.

Run inside the docbridge-web container (PYTHONPATH=/app/src)::

    python -m experiments status [--json]
    python -m experiments reindex KEY [KEY ...] | --all
    python -m experiments purge-index [--yes]
    python -m experiments install-pack NAME [--as E-MAIL]
    python -m experiments worker [--once] [--max-jobs N]

The configuration comes from config/config.yaml (``--config`` to point
elsewhere), the database from PLATFORM_DB_DSN or the PLATFORM_DB_* settings,
exactly as the web app reads them. ``purge-index`` also works while the
module is switched off: it is how the Knovas copies are removed for good.

Output is German, like everything operators read on this Platform.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import signal
import sys
import threading
from typing import Any, Callable, List, Optional, Sequence

from experiments import jobs
from experiments.errors import ExperimentsError

logger = logging.getLogger(__name__)

_KEY_RE = re.compile(r"[A-Z][A-Z0-9]{1,7}-[0-9]{1,9}")
INDEX_STATES = ("indexed", "pending", "error", "off")
JOB_STATUS_LABELS = {"pending": "wartend", "running": "laufend", "done": "erledigt",
                     "dead": "gescheitert"}

MSG_DISABLED = "Experimente sind nicht eingeschaltet (EXPERIMENTS_ENABLED)."
MSG_INDEX_OFF = "Die Indexierung ist ausgeschaltet (EXPERIMENTS_INDEX_ENABLED)."


class _Context:
    """Configuration, settings and a lazily opened database connection."""

    def __init__(self, args: argparse.Namespace, out: Callable[[str], None]) -> None:
        from config_loader import get_config, set_config
        from experiments.settings import load_settings

        self.config = set_config(args.config) if args.config else get_config()
        identity_enabled = self.config.get_bool("identity.enabled", True)
        self.settings = load_settings(self.config, identity_enabled=identity_enabled)
        self.out = out
        self._conn: Any = None

    @staticmethod
    def connect() -> Any:
        from identity import db

        # identity.db.connect prefers PLATFORM_DB_DSN and otherwise builds the
        # DSN from PLATFORM_DB_* (password from the secret file).
        return db.connect()

    @property
    def conn(self) -> Any:
        if self._conn is None:
            self._conn = self.connect()
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None


def _store():
    from experiments import store

    return store


def _yes_no(flag: bool) -> str:
    return "eingeschaltet" if flag else "ausgeschaltet"


def _index_counts(conn: Any) -> dict:
    store = _store()
    return {state: len(store.experiments_for_reindex(conn, states=(state,)) or [])
            for state in INDEX_STATES}


def _recorded_pointers(conn: Any) -> int:
    store = _store()
    total, after = 0, None
    while True:
        batch = list(store.index_documents(conn, after=after, limit=500) or [])
        total += len(batch)
        if len(batch) < 500:
            return total
        after = batch[-1]


def _runner(settings: Any):
    if not settings.runner_url:
        return None
    from experiments.runner_client import RunnerClient

    return RunnerClient(settings.runner_url, timeout_seconds=settings.runner_timeout_seconds)


# -- commands -------------------------------------------------------------------


def cmd_status(ctx: _Context, args: argparse.Namespace) -> int:
    from experiments import labels

    settings = ctx.settings
    queue = jobs.JobQueue(ctx.conn)
    runner = _runner(settings)
    report = {
        "enabled": settings.enabled,
        "index_enabled": settings.index_enabled,
        "index_per_minute": settings.index_per_minute,
        "access_groups": list(settings.index_access_groups),
        "unrestricted": settings.index_unrestricted,
        "pointer_prefix": settings.pointer_prefix,
        "worker_enabled": settings.worker_enabled,
        "runner": runner.health() if runner is not None else {"configured": False},
        "jobs": queue.counts(),
        "index": _index_counts(ctx.conn),
        "index_documents": _recorded_pointers(ctx.conn),
        "failures": queue.recent_failures(10),
    }
    if args.json:
        ctx.out(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    ctx.out(f"Experimente: {_yes_no(settings.enabled)}")
    ctx.out(f"Knovas-Index: {_yes_no(settings.index_enabled)}"
            f" ({settings.index_per_minute} Uploads pro Minute, Pr\u00e4fix "
            f"{settings.pointer_prefix}/)")
    if settings.index_access_groups:
        ctx.out("Zugriffsgruppen: " + ", ".join(settings.index_access_groups))
    elif settings.index_unrestricted:
        ctx.out("Zugriffsgruppen: keine (EXPERIMENTS_INDEX_UNRESTRICTED: f\u00fcr alle sichtbar, "
                "sofern keine Ordnerregel greift)")
    else:
        ctx.out("Zugriffsgruppen: keine -- es wird nichts hochgeladen "
                "(EXPERIMENTS_ACCESS_GROUPS setzen)")
    ctx.out(f"Hintergrundarbeit: {_yes_no(settings.worker_enabled)}")
    health = report["runner"]
    if not health.get("configured"):
        ctx.out("Rechenumgebung: nicht eingerichtet")
    elif health.get("ok"):
        langs = ", ".join(f"{k} {v}" for k, v in (health.get("languages") or {}).items())
        ctx.out(f"Rechenumgebung: erreichbar ({langs or 'ohne Angaben'}), "
                f"besch\u00e4ftigt: {health.get('busy', 0)}")
    else:
        ctx.out("Rechenumgebung: nicht erreichbar")
    ctx.out("Auftr\u00e4ge: " + ", ".join(
        f"{JOB_STATUS_LABELS.get(k, k)} {v}" for k, v in report["jobs"].items()))
    ctx.out("Indexstand: " + ", ".join(
        f"{labels.INDEX_STATE_LABELS.get(k, k)} {v}" for k, v in report["index"].items()))
    ctx.out(f"Dokumente in Knovas (erfasst): {report['index_documents']}")
    if report["failures"]:
        ctx.out("Letzte Fehler:")
        for failure in report["failures"]:
            ctx.out(f"  {failure['at'] or '-'}  {failure['kind']}: {failure['error']}")
    return 0


def cmd_reindex(ctx: _Context, args: argparse.Namespace) -> int:
    settings = ctx.settings
    if not settings.enabled:
        ctx.out(MSG_DISABLED)
        return 1
    if not settings.index_enabled:
        ctx.out(MSG_INDEX_OFF)
        return 1
    if not settings.index_access_groups and not settings.index_unrestricted:
        from experiments.indexer import MSG_NO_GROUP

        ctx.out(MSG_NO_GROUP)
        return 1
    if bool(args.all) == bool(args.keys):
        ctx.out("Entweder Schl\u00fcssel angeben oder --all, nicht beides.")
        return 2
    store = _store()
    conn = ctx.conn
    queue = jobs.JobQueue(conn)
    missing: List[str] = []
    if args.all:
        ids = [str(i) for i in (store.experiments_for_reindex(conn) or [])]
        priority = 200
    else:
        ids = []
        priority = 10
        for key in args.keys:
            key = key.strip().upper()
            snapshot = store.load_snapshot(conn, key) if _KEY_RE.fullmatch(key) else None
            if snapshot is None:
                missing.append(key)
                continue
            ids.append(str(snapshot["id"]))
    for experiment_id in ids:
        with conn.transaction():
            queue.enqueue("index", {"experiment_id": experiment_id},
                          dedupe_key=f"index:{experiment_id}", priority=priority)
            store.set_index_state(conn, experiment_id, "pending")
    ctx.out(f"{len(ids)} Experiment(e) zum Indexieren eingereiht.")
    if not settings.worker_enabled:
        ctx.out("Hinweis: die Hintergrundarbeit ist ausgeschaltet; "
                "\u00abpython -m experiments worker --once\u00bb arbeitet die Auftr\u00e4ge ab.")
    if missing:
        ctx.out("Nicht gefunden: " + ", ".join(missing))
        return 1
    return 0


def cmd_purge_index(ctx: _Context, args: argparse.Namespace) -> int:
    from experiments import indexer

    conn = ctx.conn
    recorded = _recorded_pointers(conn)
    if not args.yes:
        ctx.out(f"{recorded} erfasste Experiment-Dokumente w\u00fcrden aus Knovas gel\u00f6scht, "
                f"dazu alles, was Knovas unter {ctx.settings.pointer_prefix}/ noch f\u00fchrt.")
        ctx.out("Nichts gel\u00f6scht. Zum L\u00f6schen: python -m experiments purge-index --yes")
        return 1
    client = indexer.make_index_client(ctx.config)
    # Nothing may upload the documents again right after they are removed.
    cancelled = jobs.JobQueue(conn).cancel_pending(("index",))
    try:
        deleted = indexer.purge_all(conn, client, ctx.settings)
    except ExperimentsError as exc:
        ctx.out(f"Abgebrochen: {exc.message}")
        return 1
    store = _store()
    marked = 0
    for experiment_id in store.experiments_for_reindex(conn, states=("indexed", "pending", "error")) or []:
        store.set_index_state(conn, str(experiment_id), "off")
        marked += 1
    remaining = _recorded_pointers(conn)
    ctx.out(f"{deleted} Dokument(e) aus Knovas gel\u00f6scht; {cancelled} wartende "
            f"Index-Auftr\u00e4ge verworfen; {marked} Experiment(e) auf \u00abaus\u00bb gesetzt.")
    if remaining:
        ctx.out(f"{remaining} Dokument(e) konnten nicht gel\u00f6scht werden (siehe Protokoll); "
                "der Befehl kann wiederholt werden.")
        return 1
    if ctx.settings.enabled and ctx.settings.index_enabled:
        ctx.out("Hinweis: Ein gerade laufender Index-Auftrag kann ein Dokument erneut "
                "hochladen; f\u00fcr ein endg\u00fcltiges Entfernen zuerst "
                "EXPERIMENTS_INDEX_ENABLED=false setzen. Neu indexieren mit "
                "\u00abpython -m experiments reindex --all\u00bb.")
    return 0


def cmd_install_pack(ctx: _Context, args: argparse.Namespace) -> int:
    from experiments import permissions
    from experiments.service import ExperimentService
    from identity.users import UserRepository

    email = (args.as_email or os.environ.get("PLATFORM_ADMIN_EMAIL") or "").strip()
    if not email:
        ctx.out("Bitte mit --as E-MAIL ein Konto angeben, das Experimente verwalten darf "
                "(Rolle experiments_manager oder admin).")
        return 2
    conn = ctx.conn
    user = UserRepository(conn).get_by_email(email)
    if user is None or not user.is_active:
        ctx.out(f"Kein aktives Konto {email}.")
        return 1
    if not permissions.can_manage(user):
        ctx.out(f"Das Konto {email} darf Experimente nicht verwalten.")
        return 1
    service = ExperimentService(conn, user, ctx.settings,
                                request_meta={"ip": None, "user_agent": "experiments-cli",
                                              "token_id": None})
    try:
        result = service.install_pack(args.name)
    except ExperimentsError as exc:
        ctx.out(exc.message)
        return 1
    ctx.out(f"Paket {args.name} installiert: " + ", ".join(
        f"{k} {v}" for k, v in (result or {}).items()))
    return 0


def _build_worker_parts(ctx: _Context):
    from experiments import indexer, tasks

    index_client = indexer.make_index_client(ctx.config)
    runner = _runner(ctx.settings)
    return tasks.build_handlers(settings=ctx.settings, index_client=index_client, runner=runner)


def cmd_worker(ctx: _Context, args: argparse.Namespace) -> int:
    settings = ctx.settings
    if not settings.enabled:
        ctx.out(MSG_DISABLED)
        return 1
    handlers, on_dead, maintenance = _build_worker_parts(ctx)
    if args.once:
        conn = ctx.conn
        worker = jobs.JobWorker(
            connect=ctx.connect, handlers=handlers, on_dead=on_dead, kinds=jobs.JOB_KINDS,
            lease_seconds=max(jobs.INDEX_THREAD_LEASE_SECONDS,
                              settings.runner_timeout_seconds + jobs.EVALUATE_LEASE_EXTRA_SECONDS),
            poll_seconds=settings.worker_poll_seconds, maintenance=maintenance,
        )
        jobs.ensure_rate_slot(conn)
        worker.run_maintenance(conn)
        done = 0
        while done < max(1, int(args.max_jobs)):
            if not worker.run_once(conn):
                break
            done += 1
        counts = jobs.JobQueue(conn).counts()
        ctx.out(f"{done} Auftrag/Auftr\u00e4ge bearbeitet; wartend {counts['pending']}, "
                f"gescheitert {counts['dead']}.")
        return 0

    # Foreground: both threads, until SIGTERM or Ctrl+C.
    halt = threading.Event()

    def _signal(signum, frame):  # noqa: ARG001
        halt.set()

    previous = {sig: signal.signal(sig, _signal) for sig in (signal.SIGTERM, signal.SIGINT)}
    workers = [
        jobs.JobWorker(connect=ctx.connect, handlers=handlers, on_dead=on_dead,
                       kinds=jobs.INDEX_THREAD_KINDS,
                       lease_seconds=jobs.INDEX_THREAD_LEASE_SECONDS,
                       poll_seconds=settings.worker_poll_seconds, maintenance=maintenance),
        jobs.JobWorker(connect=ctx.connect, handlers=handlers, on_dead=on_dead,
                       kinds=jobs.EVALUATE_THREAD_KINDS,
                       lease_seconds=settings.runner_timeout_seconds + jobs.EVALUATE_LEASE_EXTRA_SECONDS,
                       poll_seconds=settings.worker_poll_seconds),
    ]
    try:
        for worker in workers:
            worker.start()
        ctx.out("Hintergrundarbeit l\u00e4uft (Strg+C beendet).")
        while not halt.wait(1.0):
            pass
    finally:
        for worker in workers:
            worker.stop()
        for worker in workers:
            if worker.is_alive():
                worker.join(timeout=30)
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    ctx.out("Hintergrundarbeit beendet.")
    return 0


# -- entry point ---------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m experiments",
        description="Betrieb des Moduls Experimente (Knovas Platform).",
    )
    parser.add_argument("--config", help="Pfad zur config.yaml (sonst die \u00fcbliche Suche)")
    parser.add_argument("-v", "--verbose", action="store_true", help="ausf\u00fchrliches Protokoll")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="Einstellungen, Auftr\u00e4ge und Indexstand zeigen")
    status.add_argument("--json", action="store_true", help="als JSON ausgeben")

    reindex = sub.add_parser("reindex", help="Experimente neu in Knovas schreiben")
    reindex.add_argument("keys", nargs="*", metavar="KEY", help="z. B. MKT-12")
    reindex.add_argument("--all", action="store_true", help="alle Experimente")

    purge = sub.add_parser("purge-index",
                           help="alle Experiment-Dokumente aus Knovas l\u00f6schen "
                                "(auch bei ausgeschaltetem Modul)")
    purge.add_argument("--yes", action="store_true", help="wirklich l\u00f6schen")

    pack = sub.add_parser("install-pack", help="ein mitgeliefertes Paket installieren")
    pack.add_argument("name", help="z. B. engineering, marketing, sales, product")
    pack.add_argument("--as", dest="as_email", metavar="E-MAIL",
                      help="Konto, als das installiert wird (Standard: PLATFORM_ADMIN_EMAIL)")

    worker = sub.add_parser("worker", help="Hintergrundauftr\u00e4ge abarbeiten")
    worker.add_argument("--once", action="store_true",
                        help="nur die f\u00e4lligen Auftr\u00e4ge abarbeiten, dann beenden")
    worker.add_argument("--max-jobs", type=int, default=500,
                        help="h\u00f6chstens so viele Auftr\u00e4ge mit --once (Standard 500)")
    return parser


COMMANDS = {
    "status": cmd_status,
    "reindex": cmd_reindex,
    "purge-index": cmd_purge_index,
    "install-pack": cmd_install_pack,
    "worker": cmd_worker,
}


def main(argv: Optional[Sequence[str]] = None, *, out: Callable[[str], None] = print) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        ctx = _Context(args, out)
    except Exception as exc:  # noqa: BLE001 - an operator needs the reason, not a traceback
        out(f"Konfiguration nicht lesbar: {exc}")
        return 1
    try:
        return COMMANDS[args.command](ctx, args)
    except ExperimentsError as exc:
        out(exc.message)
        return 1
    except Exception as exc:  # noqa: BLE001
        logger.debug("command failed", exc_info=True)
        out(f"Fehler: {type(exc).__name__}: {exc}")
        return 1
    finally:
        ctx.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
