"""The work channel in link mode (MVP-20 … MVP-22): jobs from Knovas Link, answered with parts and a result.

The component long-polls ``GET /local/v1/jobs?wait=25`` for the job kinds it named at registration (``fetch``,
``write``, ``probe``). A probe is answered at once. A fetch or a write goes to the function for its kind of source
(``HANDLERS``: a new kind of source is one function and one entry, as in ``fetchers.py``), with the source as Link
assigned it and its credential from this component's environment. Every job gets exactly one result, unless Link took
a part no more (its lease ended, or the fetch failed there): then the job has ended and nothing more is sent.

Logs carry job kinds, statuses, codes, counts and durations only: never a path, a file name, a value or a token.
"""
from __future__ import annotations

import concurrent.futures
import logging
import threading
import time
from typing import Any, Callable, Mapping, Optional

from sync_component import github_jobs
from sync_component.client import Refused
from sync_component.engine import EngineError, RunnerEngine
from sync_component.fetchers import credential_for

log = logging.getLogger("sync_component.jobs")

JOB_KINDS = ["fetch", "write", "probe"]
#: (job kind, source kind) → the function that answers it.
HANDLERS: dict[tuple[str, str], Callable[..., dict]] = {
    ("fetch", "github"): github_jobs.fetch_original,
    ("write", "github"): github_jobs.write_proposal,
}
#: What the component declares at registration besides its own capabilities: fetch:<kind> and write:<kind>.
JOB_CAPABILITIES = sorted(f"{job}:{kind}" for job, kind in HANDLERS)
#: The kinds of source it can write to: a source switched to read_write is still read for dry runs and applies.
WRITABLE_KINDS = frozenset(kind for job, kind in HANDLERS if job == "write")
BACKOFF_MAX_SECONDS = 60.0
#: How long the sources Link assigned are taken as they were before the configuration is read again.
SOURCES_SECONDS = 60.0
#: Link keeps registrations in memory: registering again this often brings a restarted Link the job kinds back.
REGISTER_SECONDS = 300.0


class JobWorker:
    def __init__(self, client: Any, engine: RunnerEngine, *, environ: Optional[Mapping[str, str]] = None,
                 handlers: Optional[Mapping[tuple[str, str], Callable[..., dict]]] = None, session: Any = None,
                 workers: int = 4, clock: Callable[[], float] = time.monotonic,
                 register: Optional[Callable[[], Any]] = None):
        self.client = client
        self.register = register                      # registers with Link again (link mode's start registers first)
        self._registered_at = clock()
        self.engine = engine
        self.environ = environ
        self.handlers = dict(HANDLERS if handlers is None else handlers)
        self.session = session                        # the GitHub session (tests); None: a new one per job
        self.clock = clock
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=workers, thread_name_prefix="job")
        self._sources: dict[str, dict] = {}
        self._sources_at: Optional[float] = None

    # -------------------------------------------------------- sources, credentials and the package

    def source(self, key: Any) -> Optional[dict]:
        """The source Link assigned under this key, as the configuration said at most a minute ago; it is read again
        when the key is unknown."""
        now = self.clock()
        if key not in self._sources or self._sources_at is None or now - self._sources_at > SOURCES_SECONDS:
            config = self.client.config()
            self._sources = {s["key"]: s for s in config.get("sources") or [] if isinstance(s, dict) and s.get("key")}
            self._sources_at = now
        return self._sources.get(key)

    def credential(self, source: Mapping[str, Any]) -> Optional[str]:
        kwargs = {} if self.environ is None else {"environ": self.environ}
        return credential_for(str(source.get("key") or ""), str(source.get("kind") or ""), **kwargs)

    def compile_change(self, source: Mapping[str, Any]) -> Callable[[Mapping[str, Any]], tuple[str, Any]]:
        def run(job: Mapping[str, Any]) -> tuple[str, Any]:
            package = {w.get("workspace"): w for w in self.client.package().get("workspaces") or [] if isinstance(w, dict)}
            files = (package.get(source.get("workspace")) or {}).get("files")
            if not files:
                return "failed", "package.missing"
            try:
                root = self.engine.source_root(source["workspace"], source["key"])
            except EngineError as exc:
                return "failed", str(exc)
            return self.engine.compile_write(root, files, {k: v for k, v in job.items() if k != "lease"})
        return run

    # -------------------------------------------------------- one job

    def answer(self, job: Mapping[str, Any]) -> Optional[dict]:
        """The job's result, or None when it ended at Link while its parts were sent."""
        kind = job.get("kind")
        if kind == "probe":
            return github_jobs.result("ok", None)
        if kind not in ("fetch", "write"):
            return github_jobs.result("unsupported", "unsupported")
        source = self.source(job.get("source"))
        handler = self.handlers.get((kind, str((source or {}).get("kind")))) if source else None
        if handler is None:
            return github_jobs.result("unsupported", "unsupported")
        credential = self.credential(source)
        if kind == "fetch":
            jid = str(job.get("id"))

            def post_part(n: int, data: bytes) -> None:
                try:
                    self.client.post_part(jid, n, data)
                except Refused as exc:
                    log.info("job.part_refused fetch %d %s", exc.status, exc.code)
                    raise github_jobs.PartRefused(exc.code) from None
                except OSError as exc:
                    log.info("job.part_not_sent fetch (%s)", type(exc).__name__)
                    raise github_jobs.PartRefused("unreachable") from None

            try:
                return handler(job, source, credential, post_part=post_part, session=self.session)
            except github_jobs.PartRefused:
                return None
        return handler(job, source, credential, compile_change=self.compile_change(source), session=self.session)

    def handle(self, job: Mapping[str, Any]) -> Optional[dict]:
        """Answer one job and post its result to Link; the result as posted (None when the job ended at Link)."""
        started = self.clock()
        kind = job.get("kind") if job.get("kind") in JOB_KINDS else "unknown"
        jid = str(job.get("id") or "")
        try:
            out = self.answer(job)
        except Exception as exc:                          # noqa: BLE001 - one job's failure; the type is logged
            log.error("job.failed %s (%s)", kind, type(exc).__name__)
            out = github_jobs.result("failed", "internal")
        if out is None:
            log.info("job.ended %s at Link", kind)
            return None
        out["duration_ms"] = max(0, int((self.clock() - started) * 1000))
        if out.get("status") == "ok" and out.get("code") is None:
            out.pop("code")
        try:
            self.client.post_result(jid, out)
        except Refused as exc:
            log.warning("job.result_refused %s %d %s", kind, exc.status, exc.code)
            return out
        except OSError as exc:
            log.warning("job.result_not_sent %s (%s)", kind, type(exc).__name__)
            return out
        log.info("job.done %s %s %s %dms parts %s bytes %s", kind, out["status"], out.get("code") or "-",
                 out["duration_ms"], out.get("parts", "-"), out.get("bytes", "-"))
        return out

    # -------------------------------------------------------- the long-poll

    def poll_once(self, wait: int = 25) -> bool:
        """Take one job, if one comes within ``wait`` seconds, and hand it on; whether one came."""
        job = self.client.next_job(wait)
        if job is None:
            return False
        if job.get("kind") == "probe":
            self.handle(job)                              # at once: the probe measures the round trip
        else:
            self.pool.submit(self.handle, job)
        return True

    def forever(self, stop: Optional[threading.Event] = None) -> None:
        stop = stop or threading.Event()
        backoff = 1.0
        while not stop.is_set():
            try:
                if self.register is not None and self.clock() - self._registered_at > REGISTER_SECONDS:
                    self._registered_at = self.clock()
                    self.register()
                self.poll_once()
                backoff = 1.0
            except Refused as exc:
                log.warning("jobs.refused %d %s, retry in %.0fs", exc.status, exc.code, backoff)
                stop.wait(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX_SECONDS)
            except OSError as exc:                        # Link not reachable: the next poll tries again
                log.warning("jobs.link_unreachable (%s), retry in %.0fs", type(exc).__name__, backoff)
                stop.wait(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX_SECONDS)
            except Exception as exc:                      # noqa: BLE001 - the loop goes on; the type is logged
                log.error("jobs.failed (%s), retry in %.0fs", type(exc).__name__, backoff)
                stop.wait(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX_SECONDS)

    def close(self) -> None:
        self.pool.shutdown(wait=True)
