"""The component's loop: heartbeat, the Platform's commands, scheduled dry runs.

The Brain is only written on a command from the Platform (an apply a person asked for after a dry run, or a held plan a
person approved). A schedule runs dry runs: it keeps the plan current and shows what would change, and it writes
nothing. Everything reported is codes and counts (GI-DATA-02/04).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import time
from typing import Any, Callable, Mapping, Optional

from sync_component.client import PlatformClient, Refused
from sync_component.engine import EngineError, Result, RunnerEngine
from sync_component.fetchers import CHECKS, FETCHERS, FetchError, check, credential_for

log = logging.getLogger("sync_component")
CAPABILITIES = ["dry_run", "apply", "held_plans", "package", "source_checks"]
#: Heartbeats between looks at whether the sources changed in the Platform.
CHECK_EVERY_TICKS = 10
DAYS = ("mo", "di", "mi", "do", "fr", "sa", "so")


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
    def __init__(self, client: PlatformClient, engine: RunnerEngine, *, fetchers: Optional[Mapping[str, Callable]] = None,
                 checks: Optional[Mapping[str, Callable]] = None, environ: Optional[Mapping[str, str]] = None,
                 now: Callable[[], dt.datetime] = _utc):
        self.client = client
        self.engine = engine
        self.fetchers = dict(FETCHERS if fetchers is None else fetchers)
        # A stand-in fetcher is checked by fetching, unless it brings its own check.
        self.checks = dict(checks if checks is not None else ({} if fetchers is not None else CHECKS))
        self._checked: Optional[str] = None
        self._ticks = 0
        self.environ = environ
        self.now = now
        self.last_run: Optional[dt.datetime] = None
        self.open_errors = 0
        self.paused = False

    # -------------------------------------------------------- one source

    def _sources(self) -> list[dict]:
        """The sources this component serves with their mappings and package, from the Platform on every run."""
        config = self.client.config()
        package = {w["workspace"]: w for w in self.client.package().get("workspaces") or []}
        out = []
        for s in config.get("sources") or []:
            mappings = [m["id"] for m in config.get("mappings") or []
                        if m.get("workspace") == s.get("workspace") and m.get("source") == s.get("key") and m.get("id")]
            if mappings and s.get("workspace") in package:
                out.append({**s, "mapping_ids": mappings, "files": package[s["workspace"]].get("files") or {}})
        return out

    def _dry_run(self, source: Mapping[str, Any]) -> Result:
        fetcher = self.fetchers.get(source.get("kind"))
        if fetcher is None:
            return Result(status="failed", errors=1, codes=["source.kind_unknown"])
        try:
            root = self.engine.source_root(source["workspace"], source["key"])
            self.engine.write_package(root, source["files"])
            reading = fetcher(source.get("settings") or {}, self._credential(source), root)
        except (FetchError, EngineError) as exc:
            return Result(status="failed", errors=1, codes=[str(exc)])
        return self.engine.dry_run(root, source["mapping_ids"], reading)

    def _credential(self, source: Mapping[str, Any]) -> Optional[str]:
        kwargs = {} if self.environ is None else {"environ": self.environ}
        return credential_for(str(source.get("key") or ""), str(source.get("kind") or ""), **kwargs)

    # -------------------------------------------------------- can it read its sources?

    def check_sources(self, *, force: bool = False) -> None:
        """Tell the Platform, per source, whether this component can read it: right after it starts, when a source
        changed in the Platform, and when a person asks. What is wrong shows on the component's page, before a run."""
        config = self.client.config()
        sources = config.get("sources") or []
        seen = json.dumps([[s.get("workspace"), s.get("key"), s.get("kind"), s.get("settings")] for s in sources], sort_keys=True)
        if seen == self._checked and not force:
            return
        items = []
        for s in sources:
            code, counts = check(str(s.get("kind")), s.get("settings") or {}, self._credential(s), fetchers=self.fetchers,
                                 checks=self.checks)
            items.append({"workspace": s.get("workspace"), "source": s.get("key"), "code": code, "counts": counts})
        self.client.report_checks(items)
        self._checked = seen

    def _apply(self, source: Mapping[str, Any], reason: Optional[str]) -> Result:
        try:
            root = self.engine.source_root(source["workspace"], source["key"])
        except EngineError as exc:
            return Result(status="failed", errors=1, codes=[str(exc)])
        return self.engine.apply(root, approve_reason=reason)

    # -------------------------------------------------------- one run

    def run(self, kind: str, *, command: Optional[str] = None, mapping: Optional[str] = None,
            reason: Optional[str] = None) -> Result:
        """Every source (or the one that holds ``mapping``), reported as one run."""
        started = self.now()
        total = Result()
        try:
            sources = self._sources()
        except Refused as exc:
            total = Result(status="failed", errors=1, codes=[f"config.{exc.code}"])
            sources = []
        if mapping:
            sources = [s for s in sources if mapping in s["mapping_ids"]]
        if not sources and total.status == "ok":
            total.codes.append("config.nothing_to_run")
        for source in sources:
            one = self._dry_run(source) if kind == "dry_run" else self._apply(source, reason)
            total.added += one.added
            total.changed += one.changed
            total.retired += one.retired
            total.errors += one.errors
            total.held += one.held
            total.codes += one.codes
            if one.status == "failed" or (one.status == "held" and total.status == "ok"):
                total.status = one.status
        finished = self.now()
        self.last_run = finished
        self.open_errors = total.errors
        self.client.report_run(kind=kind, status=total.status, started_at=started.isoformat(), finished_at=finished.isoformat(),
                               duration_ms=int((finished - started).total_seconds() * 1000), added=total.added,
                               changed=total.changed, retired=total.retired, errors=total.errors, held=total.held,
                               command=command)
        if total.codes:
            self.client.events([{"code": code, "counts": {}} for code in dict.fromkeys(total.codes)][:100])
        log.info("%s: %s, added %d, changed %d, retired %d, errors %d", kind, total.status, total.added, total.changed,
                 total.retired, total.errors)
        return total

    # -------------------------------------------------------- commands

    def handle(self, command: Mapping[str, Any]) -> None:
        name, cid, args = command.get("command"), str(command.get("id")), command.get("args") or {}
        if name == "run":
            kind = args.get("kind")
            if self.paused or kind not in ("dry_run", "apply"):
                self.client.command_done(cid, "refused")
                return
            self.run(kind, command=cid)
        elif name == "approve_plan":
            if self.paused:
                self.client.command_done(cid, "refused")
                return
            self.run("apply", command=cid, mapping=str(args.get("mapping") or ""), reason=str(args.get("reason") or "approved"))
        elif name == "check":
            self.check_sources(force=True)
            self.client.command_done(cid, "ok")
        elif name in ("pause", "resume", "reject_plan", "reload_config"):
            # Pausing comes with every heartbeat; a rejected plan stays unapplied; the configuration is read on every run.
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
        if not self.paused and schedule_due(answer.get("schedule") or {}, self.last_run, self.now()):
            self.run("dry_run")

    def forever(self, interval: float) -> None:
        while True:
            try:
                self.tick()
            except Refused as exc:
                log.warning("the Platform refused: %s", exc.code)
            except OSError as exc:                    # the network: the next tick tries again
                log.warning("the Platform is not reachable (%s)", type(exc).__name__)
            time.sleep(interval)
