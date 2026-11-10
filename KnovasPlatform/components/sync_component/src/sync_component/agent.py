"""The component's loop: heartbeat, the Platform's commands, scheduled dry runs, diff polling and automatic apply.

The Brain is written on a command from the Platform (an apply a person asked for after a dry run, or a held plan a
person approved), or -- only for a source whose automatic apply a person switched on in the Builder -- right after a
scheduled plan that holds nothing a person must decide (``engine.attention``: no guard hold, drift, held retire entry,
release, stopped or reported document, nothing unresolved). Anything else waits for a person exactly as before. A plan
a person asked for (``run dry_run``) is never applied on its own.

Schedules: manual, every n minutes, every n minutes inside a window, or «bei neuen Commits» (``commits``): every
``poll_seconds`` the component asks each GitHub source for the commit its ref names now (one request; nothing else
when it is the last synced or planned one); when it is new, brain-runner's ``affected`` names the mappings the commits
since the synced one concern and only those are planned (each whole). A new package plans everything. The synced
commit is written only after an apply without errors (``engine``). Everything reported is codes, counts, commits and
times (GI-DATA-02/04).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import time
from typing import Any, Callable, Iterable, Mapping, Optional

from sync_component.client import PlatformClient, Refused
from sync_component.engine import EngineError, Result, RunnerEngine, package_hash
from sync_component.fetchers import CHECKS, FETCHERS, HEADS, FetchError, check, credential_for

log = logging.getLogger("sync_component")
#: ``poll_commits``: it follows the schedule «bei neuen Commits»; ``auto_apply``: it applies a source's clean plans
#: on its own where a person switched that on; ``sync_status``: its heartbeat names each source's synced commit (KG-2).
CAPABILITIES = ["dry_run", "apply", "held_plans", "package", "source_checks", "poll_commits", "auto_apply", "sync_status"]
#: Heartbeats between looks at whether the sources changed in the Platform.
CHECK_EVERY_TICKS = 10
DAYS = ("mo", "di", "mi", "do", "fr", "sa", "so")
#: «bei neuen Commits»: how often each source is asked for its head, in seconds (the Builder allows 15 to 3600).
POLL_SECONDS, POLL_MIN, POLL_MAX = 30, 15, 3600
#: The configuration the poll reads is read again this often (and after a ``reload_config``); a plan reads it fresh.
POLL_CONFIG_SECONDS = 300
#: A head whose plan failed is planned again after this long, not on every poll.
RETRY_FAILED_SECONDS = 300


def poll_seconds(schedule: Mapping[str, Any]) -> int:
    try:
        n = int((schedule or {}).get("poll_seconds") or POLL_SECONDS)
    except (TypeError, ValueError):
        n = POLL_SECONDS
    return max(POLL_MIN, min(POLL_MAX, n))


def _utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def schedule_due(schedule: Mapping[str, Any], last: Optional[dt.datetime], now: dt.datetime) -> bool:
    """Whether a scheduled dry run of every source is due: the Platform's schedule, manual, interval or window.
    «bei neuen Commits» (``commits``) is never due as a whole: ``Agent.poll`` plans a source when its head moved."""
    mode = (schedule or {}).get("mode", "manual")
    if mode in ("manual", "commits"):
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
    """``client`` is the Platform's protocol (direct mode) or Knovas Link's local API (link mode, ``LinkClient``).
    ``writable_kinds`` are the kinds of source this component writes to through jobs (link mode): such a source may be
    switched to ``read_write`` and is still read; any other source that is not ``read`` is refused as before."""

    def __init__(self, client: PlatformClient, engine: RunnerEngine, *, fetchers: Optional[Mapping[str, Callable]] = None,
                 checks: Optional[Mapping[str, Callable]] = None, environ: Optional[Mapping[str, str]] = None,
                 now: Callable[[], dt.datetime] = _utc, writable_kinds: Iterable[str] = (),
                 capabilities: Optional[list[str]] = None, heads: Optional[Mapping[str, Callable]] = None):
        self.client = client
        self.writable_kinds = frozenset(writable_kinds)
        self.capabilities = list(CAPABILITIES if capabilities is None else capabilities)
        self.engine = engine
        self.fetchers = dict(FETCHERS if fetchers is None else fetchers)
        #: The kinds whose head commit the poll asks for (fetchers.HEADS; a stand-in in tests).
        self.heads = dict(HEADS if heads is None else heads)
        # A stand-in fetcher is checked by fetching, unless it brings its own check.
        self.checks = dict(checks if checks is not None else ({} if fetchers is not None else CHECKS))
        self._checked: Optional[str] = None
        self._ticks = 0
        self.environ = environ
        self.now = now
        self.last_run: Optional[dt.datetime] = None
        self.open_errors = 0
        self.paused = False
        self.schedule: dict = {"mode": "manual"}
        self._poll_config: Optional[dict] = None
        self._poll_config_at: Optional[dt.datetime] = None
        self._polled_at: Optional[dt.datetime] = None
        self._etags: dict[tuple, str] = {}

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

    def _readable(self, source: Mapping[str, Any]) -> bool:
        access = source.get("access", "read")
        return access == "read" or (access == "read_write" and source.get("kind") in self.writable_kinds)

    def _dry_run(self, source: Mapping[str, Any], *, poll: bool = False) -> Optional[Result]:
        """A plan of the source's mappings. ``poll`` (a schedule «bei neuen Commits» found a new head): with a commit
        synced under the same package, only the mappings the commits since then concern, as brain-runner's
        ``affected`` names them; None when they concern none (nothing planned, nothing to report)."""
        if not self._readable(source):
            return Result(status="failed", errors=1, codes=["source.write_unsupported"])
        fetcher = self.fetchers.get(source.get("kind"))
        if fetcher is None:
            return Result(status="failed", errors=1, codes=["source.kind_unknown"])
        try:
            root = self.engine.source_root(source["workspace"], source["key"])
            self.engine.write_package(root, source["files"])
            reading = fetcher(source.get("settings") or {}, self._credential(source), root)
        except (FetchError, EngineError) as exc:
            return Result(status="failed", errors=1, codes=[str(exc)])
        mappings, commit, codes = list(source["mapping_ids"]), None, []
        package = package_hash(source["files"])
        state = self.engine.sync_state(root)
        if poll and state.get("synced_commit") and state.get("package") == package and source.get("kind") == "github":
            settings = source.get("settings") or {}
            found = self.engine.affected(root, state["synced_commit"], str(settings.get("repository") or ""),
                                         str(settings.get("ref") or "HEAD"), getattr(reading, "env", None))
            if isinstance(found, dict):
                commit = found["head"]
                if found["mappings"] is not None:
                    mappings = [m for m in mappings if m in found["mappings"]]
                if not mappings:
                    self.engine.update_sync_state(root, planned_commit=commit, planned_at=self.now().isoformat(),
                                                  held_back=[])
                    log.info("poll: new commits concern no mapping of a source")
                    return None
            else:
                codes.append(found)               # cannot say: every mapping is planned
        one = self.engine.dry_run(root, mappings, reading, commit=commit, package=package)
        one.codes = codes + one.codes
        return one

    def _credential(self, source: Mapping[str, Any]) -> Optional[str]:
        kwargs = {} if self.environ is None else {"environ": self.environ}
        return credential_for(str(source.get("key") or ""), str(source.get("kind") or ""), **kwargs)

    # -------------------------------------------------------- can it read its sources?

    def check_sources(self, *, force: bool = False) -> None:
        """Tell the Platform, per source, whether this component can read it: right after it starts, when a source
        changed in the Platform, and when a person asks. What is wrong shows on the component's page, before a run."""
        config = self.client.config()
        sources = config.get("sources") or []
        seen = json.dumps([[s.get("workspace"), s.get("key"), s.get("kind"), s.get("access"), s.get("settings")] for s in sources], sort_keys=True)
        if seen == self._checked and not force:
            return
        items = []
        for s in sources:
            code, counts = ("source.write_unsupported", {}) if not self._readable(s) else check(
                str(s.get("kind")), s.get("settings") or {}, self._credential(s), fetchers=self.fetchers, checks=self.checks)
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

    @staticmethod
    def _add(total: Result, one: Result) -> None:
        total.added += one.added
        total.changed += one.changed
        total.retired += one.retired
        total.errors += one.errors
        total.held += one.held
        total.codes += one.codes
        if one.status == "failed" or (one.status == "held" and total.status == "ok"):
            total.status = one.status

    def run(self, kind: str, *, command: Optional[str] = None, mapping: Optional[str] = None,
            reason: Optional[str] = None, scheduled: bool = False, only: Optional[Iterable[tuple]] = None,
            poll: bool = False) -> Result:
        """Every source (or the one that holds ``mapping``, or those in ``only``), reported as one run. A dry run the
        schedule made (``scheduled``) is applied right after, as a second run, for each source whose automatic apply
        is on and whose plan holds nothing that needs a person."""
        started = self.now()
        total = Result()
        try:
            sources = self._sources()
        except Refused as exc:
            total = Result(status="failed", errors=1, codes=[f"config.{exc.code}"])
            sources = []
        if mapping:
            sources = [s for s in sources if mapping in s["mapping_ids"]]
        if only is not None:
            wanted = set(only)
            sources = [s for s in sources if (s.get("workspace"), s.get("key")) in wanted]
        if not sources and total.status == "ok" and not poll:
            total.codes.append("config.nothing_to_run")
        automatic, planned = [], 0
        for source in sources:
            if kind == "dry_run":
                one = self._dry_run(source, poll=poll)
                if one is None:
                    continue
                planned += 1
                if poll:
                    self._planned(source, one)
                if scheduled and source.get("auto_apply") is True:
                    if self._may_apply_on_its_own(source, one):
                        automatic.append(source)
            else:
                one = self._apply(source, reason)
            self._add(total, one)
        if poll and not planned and total.status == "ok":
            return total                              # the new commits concern no mapping: nothing to report
        self._report(kind, started, total, command)
        if automatic:
            self._apply_on_its_own(automatic)
        return total

    def _report(self, kind: str, started: dt.datetime, total: Result, command: Optional[str]) -> None:
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

    # -------------------------------------------------------- automatic apply (KG-2)

    def _may_apply_on_its_own(self, source: Mapping[str, Any], one: Result) -> bool:
        """Only a clean plan: status ok, no error, nothing held, and nothing in it a person must decide."""
        try:
            root = self.engine.source_root(source["workspace"], source["key"])
        except EngineError:
            return False
        reasons = [] if (one.status == "ok" and not one.errors and not one.held) else ["plan.not_clean"]
        reasons += self.engine.attention(root) if not reasons else []
        self.engine.update_sync_state(root, held_back=reasons[:10])
        if reasons:
            self.client.events([{"code": "auto_apply.withheld", "counts": {}}]
                               + [{"code": code, "counts": {}} for code in reasons[:10]])
            log.info("automatic apply withheld: %s", ", ".join(reasons))
            return False
        return True

    def _apply_on_its_own(self, sources: list[Mapping[str, Any]]) -> None:
        """Apply the plans just made, as one run without a command; then a person sees it as an apply that ran on its
        own (event ``auto_apply.applied``)."""
        started, total = self.now(), Result()
        for source in sources:
            try:
                root = self.engine.source_root(source["workspace"], source["key"])
            except EngineError as exc:
                self._add(total, Result(status="failed", errors=1, codes=[str(exc)]))
                continue
            self._add(total, self.engine.apply(root, auto=True))
        self._report("apply", started, total, None)
        self.client.events([{"code": "auto_apply.applied", "counts": {
            "sources": len(sources), "added": total.added, "changed": total.changed, "retired": total.retired,
            "errors": total.errors}}])

    # -------------------------------------------------------- «bei neuen Commits» (KG-2)

    def _planned(self, source: Mapping[str, Any], one: Result) -> None:
        """The commit a poll planned, so the next poll does not plan it again while it waits; a failed plan is tried
        again after RETRY_FAILED_SECONDS."""
        try:
            root = self.engine.source_root(source["workspace"], source["key"])
        except EngineError:
            return
        pending = self.engine.pending(root)
        now = self.now().isoformat()
        if one.status == "failed":
            self.engine.update_sync_state(root, failed_commit=self.engine.sync_state(root).get("head_commit"),
                                          failed_at=now)
        else:
            self.engine.update_sync_state(root, planned_commit=pending.get("commit") or
                                          self.engine.sync_state(root).get("head_commit"), planned_at=now,
                                          failed_commit=None, held_back=[])

    def _config_for_poll(self) -> dict:
        now = self.now()
        if (self._poll_config is None or self._poll_config_at is None
                or (now - self._poll_config_at).total_seconds() >= POLL_CONFIG_SECONDS):
            self._poll_config, self._poll_config_at = self.client.config(), now
        return self._poll_config

    def poll(self, schedule: Mapping[str, Any]) -> list[tuple]:
        """«bei neuen Commits»: every ``poll_seconds``, ask each source that has commits for its head. Nothing more
        when it is the commit last synced or planned (no plan, no Brain call); else plan that source. Returns the
        sources planned, as (workspace, key)."""
        now = self.now()
        if self._polled_at is not None and (now - self._polled_at).total_seconds() < poll_seconds(schedule):
            return []
        self._polled_at = now
        config = self._config_for_poll()
        mapped = {(m.get("workspace"), m.get("source")) for m in config.get("mappings") or [] if m.get("id")}
        due = []
        for s in config.get("sources") or []:
            key = (s.get("workspace"), s.get("key"))
            head_of = self.heads.get(str(s.get("kind")))
            if head_of is None or key not in mapped or not self._readable(s):
                continue
            try:
                root = self.engine.source_root(*key)
                head = head_of(s.get("settings") or {}, self._credential(s), etag=self._etags.get(key))
            except (FetchError, EngineError) as exc:
                log.info("poll: a source's head not read (%s)", exc)
                continue
            if head.etag:
                self._etags[key] = head.etag
            state = self.engine.sync_state(root)
            commit = head.commit or state.get("head_commit")
            state = self.engine.update_sync_state(root, head_commit=commit, checked_at=now.isoformat())
            if not commit or commit in (state.get("synced_commit"), state.get("planned_commit")):
                continue
            failed_at = _moment(state.get("failed_at"))
            if (state.get("failed_commit") == commit and failed_at is not None
                    and (now - failed_at).total_seconds() < RETRY_FAILED_SECONDS):
                continue
            due.append(key)
        if due:
            self.run("dry_run", scheduled=True, only=due, poll=True)
        return due

    def sync_report(self) -> list[dict]:
        """Per source: the synced commit and when, the last apply's counts, the last check and the next one, and why
        the last plan was not applied on its own. Commits, counts, codes and times only."""
        out = []
        every = poll_seconds(self.schedule)
        mode = (self.schedule or {}).get("mode")
        for state in self.engine.sync_states()[:200]:
            checked = _moment(state.get("checked_at"))
            if self.paused or mode in (None, "manual"):
                upcoming = None
            elif mode == "commits":                   # only a source the poll asks (one with commits) has a next check
                upcoming = checked + dt.timedelta(seconds=every) if checked else None
            elif mode == "interval" and self.last_run is not None:
                upcoming = self.last_run + dt.timedelta(minutes=int(self.schedule.get("every_minutes") or 60))
            else:
                upcoming = None
            entry = {"workspace": state["workspace"], "key": state["key"], "synced_commit": state.get("synced_commit"),
                     "synced_at": state.get("synced_at"), "checked_at": state.get("checked_at"),
                     "next_check_at": upcoming.isoformat() if upcoming else None,
                     "planned_commit": state.get("planned_commit"),
                     "held_back": list(state.get("held_back") or [])[:10]}
            if isinstance(state.get("last_apply"), dict):
                entry["last_apply"] = state["last_apply"]
            out.append(entry)
        return out

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
            # Pausing comes with every heartbeat; a rejected plan stays unapplied; the configuration is read on every
            # run, and the poll's copy again now.
            if name == "reload_config":
                self._poll_config = None
            self.client.command_done(cid, "ok")
        else:
            self.client.command_done(cid, "refused")

    def tick(self) -> None:
        """One heartbeat, its commands, and a scheduled dry run when one is due."""
        lag = int((self.now() - self.last_run).total_seconds()) if self.last_run else None
        answer = self.client.heartbeat(state="degraded" if self.open_errors else "online", lag_seconds=lag,
                                       open_errors=self.open_errors, capabilities=self.capabilities,
                                       sources=self.sync_report())
        self.paused = bool(answer.get("paused"))
        self.schedule = answer.get("schedule") if isinstance(answer.get("schedule"), dict) else {"mode": "manual"}
        for command in answer.get("commands") or []:
            self.handle(command)
        if self._ticks % CHECK_EVERY_TICKS == 0:
            self.check_sources()
        self._ticks += 1
        if self.paused:
            return
        if self.schedule.get("mode") == "commits":
            self.poll(self.schedule)
        elif schedule_due(self.schedule, self.last_run, self.now()):
            self.run("dry_run", scheduled=True)

    def forever(self, interval: float) -> None:
        """One tick every ``interval`` seconds; under «bei neuen Commits» at least as often as the poll asks."""
        while True:
            try:
                self.tick()
            except Refused as exc:
                log.warning("the Platform refused: %s", exc.code)
            except OSError as exc:                    # the network: the next tick tries again
                log.warning("the Platform is not reachable (%s)", type(exc).__name__)
            if self.schedule.get("mode") == "commits":
                time.sleep(min(interval, poll_seconds(self.schedule)))
            else:
                time.sleep(interval)


def _moment(value: Any) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        t = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
