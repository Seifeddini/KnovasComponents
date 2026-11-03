"""The connector's loop: heartbeat, the Platform's commands, scheduled dry runs.

A document transfer can take hours, so runs happen on a thread of their own: heartbeats continue meanwhile, and a pause
stops a run after its current file. The Brain is written only on a person's command. Everything reported is codes and
counts (GI-DATA-02/04): an operation answers with an ``Outcome``, which has no room for a path.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Protocol

from platform_link.client import PlatformClient, Refused

log = logging.getLogger("platform_link")
CAPABILITIES = ["dry_run", "apply", "documents", "source_checks"]
#: Heartbeats between looks at whether the sources changed in the Platform.
CHECK_EVERY_TICKS = 10
DAYS = ("mo", "di", "mi", "do", "fr", "sa", "so")


@dataclass
class Outcome:
    status: str = "ok"                # ok | failed
    added: int = 0
    changed: int = 0
    retired: int = 0
    errors: int = 0
    codes: list[str] = field(default_factory=list)


class Operations(Protocol):
    def dry_run(self, config: Mapping[str, Any]) -> Outcome: ...
    def apply(self, config: Mapping[str, Any]) -> Outcome: ...
    def check(self, source: Mapping[str, Any], config: Mapping[str, Any]) -> tuple[str, dict]: ...
    def stop(self) -> None: ...


def _utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def schedule_due(schedule: Mapping[str, Any], last: Optional[dt.datetime], now: dt.datetime) -> bool:
    """Whether a scheduled dry run is due: the Platform's schedule, manual, interval or window."""
    mode = (schedule or {}).get("mode", "manual")
    if mode == "manual":
        return False
    every = dt.timedelta(minutes=int(schedule.get("every_minutes") or 60))
    if last is not None and now - last < every:
        return False
    if mode == "window":
        local = now.astimezone()
        if DAYS[local.weekday()] not in (schedule.get("days") or DAYS):
            return False
        return str(schedule.get("from") or "00:00") <= local.strftime("%H:%M") < str(schedule.get("to") or "24:00")
    return mode == "interval"


class Agent:
    def __init__(self, client: PlatformClient, operations: Operations, *, now: Callable[[], dt.datetime] = _utc,
                 background: bool = True):
        self.client = client
        self.operations = operations
        self.now = now
        self.background = background              # False: a run finishes inside the tick (tests)
        self.last_run: Optional[dt.datetime] = None
        self.open_errors = 0
        self.paused = False
        self._running: Optional[threading.Thread] = None
        self._checked: Optional[str] = None
        self._ticks = 0

    @property
    def busy(self) -> bool:
        return self._running is not None and self._running.is_alive()

    # -------------------------------------------------------- one run

    def _work(self, kind: str, command: Optional[str]) -> None:
        started = self.now()
        try:
            config = self.client.config()
            outcome = self.operations.dry_run(config) if kind == "dry_run" else self.operations.apply(config)
        except Refused as exc:
            outcome = Outcome(status="failed", errors=1, codes=[f"config.{exc.code}"])
        except Exception as exc:  # noqa: BLE001 - the run's failure is its report; an exception's text may name a file
            log.warning("%s failed (%s)", kind, type(exc).__name__)
            outcome = Outcome(status="failed", errors=1, codes=[f"run.{type(exc).__name__.lower()}"])
        finished = self.now()
        self.last_run = finished
        self.open_errors = outcome.errors
        try:
            self.client.report_run(kind=kind, status=outcome.status, started_at=started.isoformat(),
                                   finished_at=finished.isoformat(),
                                   duration_ms=int((finished - started).total_seconds() * 1000), added=outcome.added,
                                   changed=outcome.changed, retired=outcome.retired, errors=outcome.errors, command=command)
            if outcome.codes:
                self.client.events([{"code": code, "counts": {}} for code in dict.fromkeys(outcome.codes)][:100])
        except (Refused, OSError) as exc:
            log.warning("the run's report did not reach the Platform (%s)", type(exc).__name__)
        log.info("%s: %s, added %d, changed %d, errors %d", kind, outcome.status, outcome.added, outcome.changed, outcome.errors)

    def _start(self, kind: str, command: Optional[str]) -> None:
        if not self.background:
            self._work(kind, command)
            return
        self._running = threading.Thread(target=self._work, args=(kind, command), daemon=True)
        self._running.start()

    # -------------------------------------------------------- can it read its sources?

    def check_sources(self, *, force: bool = False) -> None:
        """Tell the Platform, per source, whether this connector can read it: right after it starts, when a source
        changed in the Platform, and when a person asks."""
        config = self.client.config()
        sources = config.get("sources") or []
        seen = json.dumps([[s.get("workspace"), s.get("key"), s.get("settings")] for s in sources], sort_keys=True)
        if seen == self._checked and not force:
            return
        items = []
        for s in sources:
            code, counts = self.operations.check(s, config)
            items.append({"workspace": s.get("workspace"), "source": s.get("key"), "code": code, "counts": counts})
        self.client.report_checks(items)
        self._checked = seen

    # -------------------------------------------------------- commands

    def handle(self, command: Mapping[str, Any]) -> None:
        name, cid, args = command.get("command"), str(command.get("id")), command.get("args") or {}
        if name == "run":
            kind = args.get("kind")
            if self.paused or self.busy or kind not in ("dry_run", "apply"):
                self.client.command_done(cid, "refused")
                return
            self._start(kind, cid)
        elif name == "check":
            self.check_sources(force=True)
            self.client.command_done(cid, "ok")
        elif name == "pause":
            self.operations.stop()                # the run in progress ends after its current file
            self.client.command_done(cid, "ok")
        elif name in ("resume", "reject_plan", "reload_config"):
            # Pausing comes with every heartbeat; the configuration is read on every run. It holds no plans back.
            self.client.command_done(cid, "ok")
        else:
            self.client.command_done(cid, "refused")

    def tick(self) -> None:
        """One heartbeat, its commands, and a scheduled dry run when one is due."""
        lag = int((self.now() - self.last_run).total_seconds()) if self.last_run else None
        answer = self.client.heartbeat(state="degraded" if self.open_errors else "online", lag_seconds=lag,
                                       open_errors=self.open_errors, capabilities=CAPABILITIES)
        self.paused = bool(answer.get("paused"))
        for command in answer.get("commands") or []:
            self.handle(command)
        if self._ticks % CHECK_EVERY_TICKS == 0:
            self.check_sources()
        self._ticks += 1
        if not self.paused and not self.busy and schedule_due(answer.get("schedule") or {}, self.last_run, self.now()):
            self._start("dry_run", None)

    def forever(self, interval: float) -> None:
        while True:
            try:
                self.tick()
            except Refused as exc:
                log.warning("the Platform refused: %s", exc.code)
            except OSError as exc:                    # the network: the next tick tries again
                log.warning("the Platform is not reachable (%s)", type(exc).__name__)
            time.sleep(interval)
