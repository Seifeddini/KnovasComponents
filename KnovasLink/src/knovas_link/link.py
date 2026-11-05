"""Knovas Link's state and loops: enrolment, the certificate, the control plane with the Platform, and the site's
telemetry (MVP-15, MVP-19). The local API (``local_api.py``) and the work channel (``jobs.py``) work on this object.

**The certificate (DEC-0056, open).** If the Platform's enrolment answer carries a certificate, Link uses it for
secure-api. Otherwise Link runs its control plane (heartbeat, configuration, package, commands, reports) but is in
the state ``waiting_for_certificate``: it claims no job, relays no Brain call, and says so in its heartbeat
(``site.certificate: "waiting"``) and in ``GET /local/v1/ping``. No other signing path is built here.

**Only to test on one's own machine, never for a company.** With ``LINK_TEST_ONLY_SHARED_CERTIFICATE=true``, and
only while the Platform issued no certificate, Link uses the tenant's own certificate from ``LINK_BRAIN_CERT`` and
``LINK_BRAIN_KEY`` for secure-api, as the sync component's documented ``SYNC_BRAIN_CERT``/``SYNC_BRAIN_KEY`` do. It
warns at start and in every heartbeat (``warnings: ["certificate.test_only_shared"]``): a leak exposes the whole
tenant, and the site cannot be revoked on its own (DEC-0040, DEC-0056).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

import httpx

from knovas_link import CAPABILITIES, VERSION, contracts, tls
from knovas_link.brain_client import BrainClient
from knovas_link.errors import LocalError, StartupError
from knovas_link.identity import Credentials, Store
from knovas_link.jobs import MAX_SOURCES, WorkChannel
from knovas_link.platform_client import PlatformClient, Refused, Unreachable
from knovas_link.registry import Registry
from knovas_link.settings import Settings
from knovas_link.telemetry import Telemetry

log = logging.getLogger("knovas_link")

#: Heartbeats between refreshes of the Platform's configuration.
CONFIG_EVERY_BEATS = 10
#: The first probe after start, in seconds: the long-poll is up by then.
FIRST_PROBE_SECONDS = 5.0
SHARED_CERTIFICATE_WARNING = "certificate.test_only_shared"
LOOPS = ("heartbeat", "channel", "probe")


def _private_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    os.replace(tmp, path)


def _read_json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _source(raw: Any) -> Optional[dict]:
    """One source of the Platform's configuration, as Link keeps it."""
    if not isinstance(raw, dict) or not isinstance(raw.get("key"), str) or not raw.get("key"):
        return None
    component = raw.get("component")
    return {"workspace": raw.get("workspace"), "key": raw["key"], "kind": raw.get("kind"),
            "access": raw.get("access") if raw.get("access") in ("read", "read_write") else "read",
            "settings": raw.get("settings") if isinstance(raw.get("settings"), dict) else {},
            "fetch": raw.get("fetch") is True,
            "component": component if isinstance(component, str) and component else None}


class Commands:
    """The Platform's commands for the site's components: delivered once, on the component's next local heartbeat,
    and open until the component reports them done."""

    def __init__(self):
        self.pending: dict[str, list] = {}
        self.open: dict[str, tuple[str, dict]] = {}

    def add(self, component: str, command: dict) -> None:
        cid = str(command.get("id") or "")
        if not cid:
            return
        self.pending.setdefault(component, []).append(
            {"id": cid, "command": command.get("command"), "args": command.get("args") or {}})

    def deliver(self, component: str) -> list:
        out = self.pending.pop(component, [])
        for c in out:
            self.open[c["id"]] = (component, c["args"] if isinstance(c["args"], dict) else {})
        return out

    def owner(self, cid: str) -> Optional[str]:
        held = self.open.get(cid)
        return held[0] if held else None


class Link:
    def __init__(self, settings: Settings, *, platform_transport: Optional[httpx.AsyncBaseTransport] = None,
                 brain_transport: Optional[httpx.AsyncBaseTransport] = None, loops: tuple = LOOPS,
                 key_size: int = 3072):
        self.settings = settings
        self.store = Store(settings.data_dir / "identity", key_size=key_size)
        self.state_dir = settings.data_dir / "state"
        self.registry = Registry(settings.tokens_dir)
        self.telemetry = Telemetry()
        self.channel = WorkChannel(self)
        self.commands = Commands()
        self.platform = PlatformClient(settings.platform_url, ca_file=settings.platform_ca, transport=platform_transport)
        self.brain_transport = brain_transport
        self.brain: Optional[BrainClient] = None
        self.brain_url: Optional[str] = None
        self.credentials: Optional[Credentials] = None
        self.certificate_mode: Optional[str] = None          # "issued", "test_only_shared", or None: waiting
        self.config: Optional[dict] = _read_json(self.state_dir / "config.json")
        self.package: Optional[dict] = _read_json(self.state_dir / "package.json")
        self.paused = False
        self.schedule: dict = {"mode": "manual"}
        self.loops = loops
        self._tasks: list = []
        self._brain_ready: Optional[asyncio.Event] = None
        self._prepared = False
        if self.config:
            self.brain_url = self.config.get("brain_url") or None
            self.paused = bool(self.config.get("paused"))
            self.schedule = self.config.get("schedule") or {"mode": "manual"}

    # ------------------------------------------------------------------ the certificate

    @property
    def certificate_present(self) -> bool:
        return self.certificate_mode is not None

    @property
    def certificate(self) -> str:
        return "present" if self.certificate_present else "waiting"

    def _certificate_files(self) -> tuple[Optional[str], Optional[str]]:
        c = self.credentials
        if c is not None and c.certificate and c.key:
            self.certificate_mode = "issued"
            if self.settings.test_only_shared_certificate:
                log.warning("certificate.issued: LINK_TEST_ONLY_SHARED_CERTIFICATE is ignored")
            return str(c.certificate), str(c.key)
        s = self.settings
        if not s.test_only_shared_certificate:
            if s.brain_cert or s.brain_key:
                log.warning("certificate.shared_ignored: LINK_BRAIN_CERT/LINK_BRAIN_KEY need "
                            "LINK_TEST_ONLY_SHARED_CERTIFICATE=true and are only to test, never for a company")
            self.certificate_mode = None
            return None, None
        if not (s.brain_cert and s.brain_key and os.access(s.brain_cert, os.R_OK) and os.access(s.brain_key, os.R_OK)):
            log.error("certificate.shared_unreadable: LINK_BRAIN_CERT and LINK_BRAIN_KEY must be readable files")
            self.certificate_mode = None
            return None, None
        self.certificate_mode = "test_only_shared"
        log.warning("!" * 72)
        log.warning("%s: Knovas Link is using the TENANT'S OWN CERTIFICATE (LINK_BRAIN_CERT/LINK_BRAIN_KEY).",
                    SHARED_CERTIFICATE_WARNING)
        log.warning("This is only to test on your own machine, NEVER FOR A COMPANY: a leak exposes the whole tenant,")
        log.warning("and this site cannot be revoked on its own (DEC-0040, DEC-0056).")
        log.warning("!" * 72)
        return s.brain_cert, s.brain_key

    async def _setup_brain(self) -> None:
        """The client for secure-api, once Link has a certificate and the Brain's address."""
        cert, key = self._certificate_files()
        if self.brain is not None:
            await self.brain.aclose()
            self.brain = None
        if cert is None or not self.brain_url:
            if cert is None:
                log.warning("certificate.waiting: the Platform issued no certificate (DEC-0056); Link claims no job "
                            "and relays nothing to the Brain until it has one")
            return
        if self.brain_transport is not None:
            self.brain = BrainClient(self.brain_url, transport=self.brain_transport)
        else:
            chain = self.credentials.chain if self.credentials else None
            ca = self.settings.brain_ca or (str(chain) if chain else None)
            self.brain = BrainClient(self.brain_url, ssl_context=tls.context(ca, cert, key))
        if self._brain_ready is not None:
            self._brain_ready.set()

    async def wait_for_brain(self, seconds: float) -> None:
        if self._brain_ready is None:
            self._brain_ready = asyncio.Event()
        try:
            await asyncio.wait_for(self._brain_ready.wait(), seconds)
        except asyncio.TimeoutError:
            pass

    # ------------------------------------------------------------------ enrolment (MVP-15)

    async def enrol(self) -> Credentials:
        """The credentials of an earlier start, or a new enrolment with the one-time token; a token these credentials
        did not come from (the Platform's «Token erneuern») enrols again."""
        token = self.settings.enrol_token
        found = self.store.load()
        if found is not None and (not token or self.store.used(token)):
            return found
        if not token:
            raise StartupError("enrol.no_token")
        csr, key = self.store.request(self.settings.name)
        try:
            answer = await self.platform.enrol(token, csr)
        except Refused as exc:
            log.error("enrol.refused %s (HTTP %d): a used or expired token needs a new one from the Platform",
                      exc.code, exc.status)
            raise StartupError(f"enrol.{exc.code}") from None
        except Unreachable as exc:
            log.error("enrol.unreachable (%s): check LINK_PLATFORM_URL and the network", exc)
            raise StartupError("enrol.unreachable") from None
        try:
            credentials = self.store.save(answer, token, key)
        except (KeyError, TypeError):
            raise StartupError("enrol.bad_answer") from None
        if isinstance(answer.get("brain_url"), str) and answer["brain_url"]:
            self.brain_url = answer["brain_url"]
        log.info("enrol.done, certificate %s", "issued" if credentials.certificate else "none")
        return credentials

    async def prepare(self) -> None:
        contracts.verify()
        self._brain_ready = asyncio.Event()
        self.credentials = await self.enrol()
        await self.platform.use(self.credentials)
        self.registry.ensure(self.settings.default_components)
        await self.refresh_config()
        await self._setup_brain()
        self._prepared = True

    async def start(self) -> None:
        await self.prepare()
        if "heartbeat" in self.loops:
            self._tasks.append(asyncio.ensure_future(self._control_loop()))
        if "channel" in self.loops:
            self._tasks.append(asyncio.ensure_future(self.channel.run()))
        if "probe" in self.loops:
            self._tasks.append(asyncio.ensure_future(self._probe_loop()))
        log.info("link.started %s, certificate %s", VERSION, self.certificate)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        await self.channel.close()
        if self.brain is not None:
            await self.brain.aclose()
        await self.platform.aclose()

    # ------------------------------------------------------------------ configuration and package

    async def refresh_config(self) -> bool:
        try:
            raw = await self.platform.config()
        except (Refused, Unreachable) as exc:
            log.warning("config.not_read (%s)", getattr(exc, "code", None) or exc)
            return False
        sources = [s for s in (_source(x) for x in raw.get("sources") or []) if s]
        mappings = [m for m in raw.get("mappings") or [] if isinstance(m, dict)]
        self.config = {"component": raw.get("component"), "paused": bool(raw.get("paused")),
                       "schedule": raw.get("schedule") or {"mode": "manual"}, "brain_url": raw.get("brain_url"),
                       "sources": sources, "mappings": mappings}
        _private_json(self.state_dir / "config.json", self.config)
        new_url = raw.get("brain_url") if isinstance(raw.get("brain_url"), str) and raw.get("brain_url") else None
        if new_url and new_url != self.brain_url:
            self.brain_url = new_url
            if self._prepared:
                await self._setup_brain()
        self.registry.ensure(list(self.settings.default_components) + [s["component"] for s in sources if s["component"]])
        log.info("config.read, sources %d", len(sources))
        return True

    def sources(self) -> list[dict]:
        return list((self.config or {}).get("sources") or [])

    def source(self, key: Any) -> Optional[dict]:
        for s in self.sources():
            if s["key"] == key:
                return s
        return None

    def channel_sources(self) -> list[str]:
        keys = sorted({s["key"] for s in self.sources() if "," not in s["key"]})
        if len(keys) > MAX_SOURCES:
            log.warning("channel.too_many_sources %d", len(keys))
        return keys[:MAX_SOURCES]

    def assigned(self, name: str) -> list[dict]:
        return [s for s in self.sources() if s["component"] == name]

    def source_read_ok(self, key: str) -> None:
        s = self.source(key)
        if s is not None:
            self.telemetry.read_ok(s["workspace"], s["key"])

    def _require_config(self) -> dict:
        if self.config is None:
            raise LocalError(503, "unreachable", "Link has no configuration from the Platform yet")
        return self.config

    @staticmethod
    def _public(source: dict) -> dict:
        return {k: source[k] for k in ("workspace", "key", "kind", "access", "settings", "fetch")}

    async def sources_for(self, name: str) -> list[dict]:
        """The sources assigned to this component's name (``POST /local/v1/register``)."""
        if self.config is None:
            await self.refresh_config()
        self._require_config()
        return [self._public(s) for s in self.assigned(name)]

    async def config_for(self, name: str, brain_url: str) -> dict:
        """Protocol v1's ``GET /config`` body, limited to this component's sources and their workspaces. Its
        ``brain_url`` is Link's relay, the only way a component in link mode reaches the Brain."""
        await self.refresh_config()
        config = self._require_config()
        mine = self.assigned(name)
        keys = {(s["workspace"], s["key"]) for s in mine}
        return {"component": name, "paused": self.paused, "schedule": self.schedule, "brain_url": brain_url,
                "sources": [self._public(s) for s in mine],
                "mappings": [m for m in config.get("mappings") or [] if (m.get("workspace"), m.get("source")) in keys]}

    async def package_for(self, name: str) -> dict:
        """Protocol v1's ``GET /package`` body (from the Platform, cached), limited to this component's workspaces."""
        try:
            package = await self.platform.package()
            self.package = package
            _private_json(self.state_dir / "package.json", package)
        except (Refused, Unreachable) as exc:
            log.warning("package.not_read (%s)", getattr(exc, "code", None) or exc)
        if self.package is None:
            raise LocalError(503, "unreachable", "Link has no package from the Platform yet")
        workspaces = {s["workspace"] for s in self.assigned(name)}
        return {"workspaces": [w for w in self.package.get("workspaces") or []
                               if isinstance(w, dict) and w.get("workspace") in workspaces]}

    def note_report(self, name: str, kind: str, body: dict) -> None:
        """What a component's checked report says about its sources, for the heartbeat: a check ``source.ok`` is a
        good read; a run's status is each of its sources' last run (only the mapping's source, for a run a command
        named with a mapping). A run that names a command delivered to another component is refused."""
        mine = self.assigned(name)
        if kind == "checks":
            keys = {(s["workspace"], s["key"]) for s in mine}
            for c in body.get("checks") or []:
                if c.get("code") == "source.ok" and (c.get("workspace"), c.get("source")) in keys:
                    self.telemetry.read_ok(c["workspace"], c["source"])
        elif kind == "runs":
            cid = body.get("command")
            if cid:
                owner = self.commands.owner(cid)
                if owner is not None and owner != name:
                    raise LocalError(400, "bad_field", "command: not one delivered to this component")
                if owner == name:
                    mapping = self.commands.open.pop(cid)[1].get("mapping")
                    if mapping:
                        held = {(m.get("workspace"), m.get("source")) for m in (self.config or {}).get("mappings") or []
                                if m.get("id") == mapping}
                        mine = [s for s in mine if (s["workspace"], s["key"]) in held]
            for s in mine:
                self.telemetry.run_status(s["workspace"], s["key"], body["status"])
                if body["status"] in ("ok", "held"):
                    self.telemetry.read_ok(s["workspace"], s["key"])

    # ------------------------------------------------------------------ the control plane (MVP-19)

    def site(self) -> dict:
        """The site's telemetry for the heartbeat: codes, counts, versions and times only."""
        return {"certificate": self.certificate, "work_channel": self.channel.state, "probe": dict(self.channel.probe),
                "components": self.registry.telemetry(), "sources": self.telemetry.sources_list(self.sources()),
                "jobs": self.telemetry.jobs_snapshot()}

    def heartbeat_body(self) -> dict:
        site = self.site()
        state = "online" if site["certificate"] == "present" and site["work_channel"] == "connected" else "degraded"
        body = {"state": state, "version": VERSION, "lag_seconds": None, "open_errors": 0,
                "capabilities": list(CAPABILITIES), "site": site}
        if self.certificate_mode == "test_only_shared":
            body["warnings"] = [SHARED_CERTIFICATE_WARNING]
        return body

    async def beat(self) -> bool:
        body = self.heartbeat_body()
        try:
            answer = await self.platform.heartbeat(body)
        except (Refused, Unreachable) as exc:
            log.warning("heartbeat.not_taken (%s)", getattr(exc, "code", None) or exc)
            return False
        self.telemetry.sent(body["site"]["jobs"])
        self.paused = bool(answer.get("paused"))
        if isinstance(answer.get("schedule"), dict):
            self.schedule = answer["schedule"]
        for command in answer.get("commands") or []:
            if not isinstance(command, dict):
                continue
            component = command.get("component")
            if component:
                self.commands.add(str(component), command)
            else:
                self.channel.spawn(self.own_command(command))
        return True

    async def own_command(self, command: dict) -> None:
        """A command for Link itself: ``check`` probes the channel, ``reload_config`` reads the configuration again;
        pausing is passed on to the components with every local heartbeat; Link runs no mappings."""
        name, cid = command.get("command"), str(command.get("id") or "")
        if name == "check":
            result = "ok" if await self.channel.probe_now() else "failed"
        elif name == "reload_config":
            result = "ok" if await self.refresh_config() else "failed"
        elif name in ("pause", "resume"):
            result = "ok"
        else:
            result = "refused"
        try:
            await self.platform.command_done(cid, result)
        except (Refused, Unreachable) as exc:
            log.warning("command.done_not_taken (%s)", getattr(exc, "code", None) or exc)
        log.info("command.%s %s", name if isinstance(name, str) and name.isidentifier() else "unknown", result)

    async def _control_loop(self) -> None:
        beats = 0
        while True:
            try:
                if beats and beats % CONFIG_EVERY_BEATS == 0:
                    await self.refresh_config()
                await self.beat()
            except asyncio.CancelledError:
                raise
            except Exception as exc:                          # noqa: BLE001 - the loop goes on; the type is logged
                log.error("heartbeat.failed (%s)", type(exc).__name__)
            beats += 1
            await asyncio.sleep(self.settings.heartbeat_seconds)

    async def _probe_loop(self) -> None:
        await asyncio.sleep(FIRST_PROBE_SECONDS)
        while True:
            try:
                await self.channel.probe_now()
            except asyncio.CancelledError:
                raise
            except Exception as exc:                          # noqa: BLE001
                log.error("probe.failed (%s)", type(exc).__name__)
            await asyncio.sleep(self.settings.probe_seconds)
