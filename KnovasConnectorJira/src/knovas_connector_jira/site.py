"""The Jira connector at a company's site, behind Knovas Link (FEAT-2026-11-10-write-back J-4; BET-2026-11-write-back
WB-07). Everything comes from the environment; the source's site and project come from the Builder, through Link.

    SYNC_LINK_URL           Knovas Link's local API on the site's network, e.g. http://knovas-link:8460 (required)
    SYNC_LINK_TOKEN_FILE    the token Link wrote for this component (default /link/token: its folder, mounted read only)
    SYNC_NAME               the component's name, as Link knows it and wrote its token for (default jira-connector)
    SYNC_INTERVAL_SECONDS   between local heartbeats (default 30)
    SYNC_CREDENTIAL_<KEY>, SYNC_CREDENTIAL_<KEY>_HOST   a source's credential and the one host it may go to
                            (knovas-jira.env; client.credential_from_environment)

It talks to Link only, over its local API (``KnovasLink/contracts/local-api.md``), as the sync component does in link
mode: every call carries ``Authorization: Bearer <token>`` and ``X-Component: <name>``.

- **Registration** as its own component, kind ``jira`` (the declaration's source kind), with its connector protocol 2.0
  declaration and the job kinds ``probe``, ``preview``, ``act``, ``reread``; again every five minutes, so a restarted
  Link knows it again. Link takes the declaration only with ``LINK_PROTOCOL_V2`` on and reports it to the Platform,
  which keeps a component of a kind it has no form for when its declaration is valid (``BUILDER_PROTOCOL_V2``).
- **Heartbeat** every ``SYNC_INTERVAL_SECONDS`` (the first right after registering). It reports, per source Link
  assigned to it, whether it can read it (``source.ok`` or the Jira code, one search of one issue), on the command
  ``check`` and on a heartbeat when the sources or their settings changed (the first one included). Commands:
  ``check``; ``reload_config``, ``pause`` and ``resume`` are acknowledged; ``run`` and every other command are
  refused: this component runs no mappings (a record stream reaches the Brain through ``dump-stream`` and the mapping
  runner).
- **Jobs** from Link's long-poll: a ``probe`` is answered at once; ``preview``, ``act`` and ``reread`` go to the
  connector's ``handle`` (the kit checks the job and the answer against the contract); anything else is
  ``unsupported``. Every job gets exactly one result.

The sources and their settings are those Link holds from the Platform's published configuration for this component's
name, read again at most a minute later; the credential never leaves this process (GI-CONN-07). Logs carry kinds,
statuses, codes, counts and durations only: never an issue key, a value, a site or a token (GI-DATA-02, GI-DATA-04).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Callable, Mapping, Optional

import requests

from knovas_connector_jira import SOURCE_KIND, VERSION
from knovas_connector_jira.client import JiraClient, JiraError, credential_from_environment, settings_of
from knovas_connector_jira.connector import JiraConnector

log = logging.getLogger("knovas_connector_jira.site")

PREFIX = "/local/v1"
DEFAULT_NAME = "jira-connector"
DEFAULT_TOKEN_FILE = "/link/token"
#: The longest wait Link's long-poll takes, in seconds.
WAIT_MAX = 25
#: The job kinds it answers: the probe of protocol 1.x and the write path of 2.0 (no review_outcome, no unstage: no
#: action declares native_review).
JOB_KINDS = ["probe", "preview", "act", "reread"]
V2_JOB_KINDS = ("preview", "act", "reread")
CAPABILITIES = ["source_checks"]
#: How long the sources Link assigned are taken as they were before the configuration is read again.
SOURCES_SECONDS = 60.0
#: Link keeps registrations in memory: registering again this often brings a restarted Link the declaration back.
REGISTER_SECONDS = 300.0
BACKOFF_MAX_SECONDS = 60.0
#: Commands acknowledged without anything to do here.
ACKNOWLEDGED = ("reload_config", "pause", "resume", "reject_plan")
# urllib3 logs every request's path at DEBUG; a path may name an issue (GI-DATA-02).
logging.getLogger("urllib3").setLevel(logging.WARNING)


class LinkError(Exception):
    """Link refused a call: its HTTP status and the code it answered (never a sentence)."""

    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status, self.code = status, code


def read_token(path: Any) -> str:
    """The token Link wrote for this component: one line, nothing else."""
    with open(path, encoding="ascii") as handle:
        token = handle.read().strip()
    if not token or any(c.isspace() for c in token):
        raise ValueError("not a token")
    return token


class LinkClient:
    """Knovas Link's local API, this component's side: plain HTTP on the site's own network, no proxy."""

    def __init__(self, url: str, name: str, token: str, *, session: Any = None, timeout: float = 60):
        self.url = url.rstrip("/") + PREFIX
        self.name = name
        self.timeout = timeout
        self._given = session
        self._local = threading.local()             # the heartbeat and the job loop each have their own session
        self.headers = {"Authorization": f"Bearer {token}", "X-Component": name}

    @property
    def session(self) -> Any:
        if self._given is not None:
            return self._given
        found = getattr(self._local, "session", None)
        if found is None:
            found = self._local.session = requests.Session()
            found.trust_env = False                 # Link is on the site's own network: no proxy sees the token
        return found

    def _call(self, method: str, path: str, body: Optional[dict] = None, *, params: Optional[dict] = None,
              timeout: Optional[float] = None) -> Optional[dict]:
        answer = self.session.request(method, self.url + path, json=body, params=params, headers=dict(self.headers),
                                      timeout=timeout or self.timeout, allow_redirects=False)
        if answer.status_code == 204:
            return None
        try:
            parsed = answer.json()
        except ValueError:
            parsed = None
        if answer.status_code >= 300 or not isinstance(parsed, dict):
            code = parsed.get("error") if isinstance(parsed, dict) else None
            raise LinkError(answer.status_code, str(code or "no_answer"))
        return parsed

    def register(self, declaration: dict) -> list[dict]:
        body = {"component": self.name, "kind": SOURCE_KIND, "version": VERSION, "capabilities": list(CAPABILITIES),
                "job_kinds": list(JOB_KINDS), "declaration": declaration}
        return list((self._call("POST", "/register", body) or {}).get("sources") or [])

    def heartbeat(self, *, state: str, open_errors: int) -> dict:
        body = {"state": state, "version": VERSION, "lag_seconds": None, "open_errors": open_errors,
                "capabilities": list(CAPABILITIES)}
        return self._call("POST", "/heartbeat", body) or {}

    def config(self) -> dict:
        return self._call("GET", "/config") or {}

    def command_done(self, command_id: str, result: str) -> None:
        self._call("POST", f"/commands/{command_id}/done", {"result": result})

    def report_checks(self, checks: list[dict]) -> None:
        self._call("POST", "/report/checks", {"checks": checks})

    def next_job(self, wait: int = WAIT_MAX) -> Optional[dict]:
        wait = max(0, min(int(wait), WAIT_MAX))
        job = (self._call("GET", "/jobs", params={"wait": str(wait)}, timeout=wait + 30) or {}).get("job")
        return job if isinstance(job, dict) else None

    def post_result(self, job_id: str, result: dict) -> None:
        self._call("POST", f"/jobs/{job_id}/result", result)


class Site:
    """The component's two loops: the control plane (registration, heartbeat, commands, checks) and the jobs."""

    def __init__(self, client: Any, *, environ: Mapping[str, str] = os.environ, jira_session: Any = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Optional[Callable[[float], None]] = None):
        self.client, self.environ, self.clock = client, environ, clock
        self.jira_session = jira_session              # the Jira session (tests); None: a new one per client
        self._sources: dict[str, dict] = {}
        self._sources_at: Optional[float] = None
        self._registered_at: Optional[float] = None
        self._checked: Optional[str] = None
        self.open_errors = 0
        kwargs = {"sleep": sleep} if sleep is not None else {}
        self.connector = JiraConnector(source=self.source, credential=self.credential, session=jira_session, **kwargs)

    # -------------------------------------------------------- sources and credentials

    def source(self, key: Any) -> Optional[dict]:
        """The source Link assigned under this key, as the configuration said at most a minute ago; read again when the
        key is unknown."""
        now = self.clock()
        if key not in self._sources or self._sources_at is None or now - self._sources_at > SOURCES_SECONDS:
            self._load()
        return self._sources.get(key)

    def _load(self) -> list[dict]:
        config = self.client.config()
        found = [s for s in config.get("sources") or [] if isinstance(s, dict) and s.get("key")]
        self._sources = {s["key"]: s for s in found}
        self._sources_at = self.clock()
        return found

    def credential(self, source: Mapping[str, Any]):
        return credential_from_environment(source, self.environ)

    # -------------------------------------------------------- the control plane

    def register(self) -> list[dict]:
        sources = self.client.register(self.connector.declaration())
        self._registered_at = self.clock()
        log.info("site.registered, sources %d", len(sources))
        return sources

    def check_one(self, source: Mapping[str, Any]) -> str:
        """``source.ok`` when one search of one issue of the source's project answers, else the code of what failed."""
        if source.get("kind") != SOURCE_KIND:
            return "source.kind_unknown"
        try:
            settings = settings_of(source)
            kwargs = {"session": self.jira_session} if self.jira_session is not None else {}
            client = JiraClient(settings.site, self.credential(source), **kwargs)
            client.call("POST", "/search/jql", body={"jql": f'project = "{settings.project}" ORDER BY key ASC',
                                                      "fields": ["summary"], "maxResults": 1})
        except JiraError as err:
            return err.code
        return "source.ok"

    @staticmethod
    def _seen(sources: list[dict]) -> str:
        return json.dumps([[s.get("workspace"), s.get("key"), s.get("kind"), s.get("access"), s.get("settings")]
                           for s in sources], sort_keys=True, default=str)

    def check_sources(self, *, force: bool = True) -> int:
        """Report per assigned source whether it can be read: on ``check`` (``force``), and on a heartbeat when the
        sources or their settings changed since the last report (the first heartbeat included)."""
        sources = self._load()
        seen = self._seen(sources)
        if not force and seen == self._checked:
            return 0
        items = [{"workspace": s.get("workspace"), "source": s.get("key"), "code": self.check_one(s), "counts": {}}
                 for s in sources]
        self.client.report_checks(items)
        self._checked = seen
        failing = sum(1 for i in items if i["code"] != "source.ok")
        self.open_errors = failing
        log.info("site.checked, sources %d, failing %d", len(items), failing)
        return len(items)

    def command(self, command: Mapping[str, Any]) -> str:
        name, cid = command.get("command"), str(command.get("id") or "")
        if name == "check":
            self.check_sources()
            result = "ok"
        elif name in ACKNOWLEDGED:
            if name == "reload_config":
                self._sources_at = None
            result = "ok"
        else:
            result = "refused"                       # run, approve_plan, ...: this component runs no mappings
        self.client.command_done(cid, result)
        log.info("site.command %s %s", name if isinstance(name, str) and name.isidentifier() else "unknown", result)
        return result

    def tick(self) -> dict:
        """One local heartbeat and its commands; registers again when it is time."""
        if self._registered_at is None or self.clock() - self._registered_at > REGISTER_SECONDS:
            self.register()
        answer = self.client.heartbeat(state="degraded" if self.open_errors else "online", open_errors=self.open_errors)
        commands = [c for c in answer.get("commands") or [] if isinstance(c, Mapping)]
        for command in commands:
            self.command(command)
        if not any(c.get("command") == "check" for c in commands):
            self.check_sources(force=False)
        return answer

    # -------------------------------------------------------- the jobs

    def answer(self, job: Mapping[str, Any]) -> dict:
        kind = job.get("kind")
        if kind == "probe":
            return {"status": "ok"}
        if kind in V2_JOB_KINDS:
            return self.connector.handle(dict(job))
        return {"status": "unsupported", "code": "unsupported"}

    def handle(self, job: Mapping[str, Any]) -> dict:
        """Answer one job and post its result to Link; the result as posted."""
        started = self.clock()
        kind = job.get("kind") if job.get("kind") in JOB_KINDS else "unknown"
        try:
            out = self.answer(job)
        except Exception as exc:                      # noqa: BLE001 - one job's failure; the type is logged
            log.error("job.failed %s (%s)", kind, type(exc).__name__)
            out = {"status": "failed", "code": "internal"}
        out = dict(out)
        out["duration_ms"] = max(0, int((self.clock() - started) * 1000))
        if out.get("code") is None:
            out.pop("code", None)
        try:
            self.client.post_result(str(job.get("id") or ""), out)
        except LinkError as exc:
            log.warning("job.result_refused %s %d %s", kind, exc.status, exc.code)
            return out
        except OSError as exc:
            log.warning("job.result_not_sent %s (%s)", kind, type(exc).__name__)
            return out
        log.info("job.done %s %s %s %dms", kind, out["status"], out.get("code") or "-", out["duration_ms"])
        return out

    def poll_once(self, wait: int = WAIT_MAX) -> bool:
        job = self.client.next_job(wait)
        if job is None:
            return False
        self.handle(job)
        return True

    # -------------------------------------------------------- running

    def _loop(self, step: Callable[[], Any], stop: threading.Event, pause: Callable[[], float], what: str) -> None:
        backoff = 1.0
        while not stop.is_set():
            try:
                step()
                backoff = 1.0
                wait = pause()
            except LinkError as exc:
                log.warning("%s.refused %d %s, retry in %.0fs", what, exc.status, exc.code, backoff)
                wait, backoff = backoff, min(backoff * 2, BACKOFF_MAX_SECONDS)
            except OSError as exc:                    # Link not reachable: try again
                log.warning("%s.link_unreachable (%s), retry in %.0fs", what, type(exc).__name__, backoff)
                wait, backoff = backoff, min(backoff * 2, BACKOFF_MAX_SECONDS)
            except Exception as exc:                  # noqa: BLE001 - the loop goes on; the type is logged
                log.error("%s.failed (%s), retry in %.0fs", what, type(exc).__name__, backoff)
                wait, backoff = backoff, min(backoff * 2, BACKOFF_MAX_SECONDS)
            if wait:
                stop.wait(wait)

    def forever(self, interval: float, stop: Optional[threading.Event] = None) -> None:
        stop = stop or threading.Event()
        beat = threading.Thread(target=self._loop, args=(self.tick, stop, lambda: interval, "heartbeat"),
                                name="heartbeat", daemon=True)
        beat.start()
        self._loop(self.poll_once, stop, lambda: 0.0, "jobs")


class SetupError(Exception):
    """The settings cannot reach Link: a code for the log, never a value."""


def link_client(environ: Mapping[str, str]) -> LinkClient:
    """The client of Link's local API from SYNC_LINK_URL, SYNC_NAME and the token file; ``SetupError`` naming what is
    missing."""
    url = str(environ.get("SYNC_LINK_URL") or "").rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise SetupError("SYNC_LINK_URL must be Knovas Link's local address, e.g. http://knovas-link:8460")
    name = environ.get("SYNC_NAME") or DEFAULT_NAME
    try:
        token = read_token(environ.get("SYNC_LINK_TOKEN_FILE") or DEFAULT_TOKEN_FILE)
    except (OSError, ValueError) as exc:
        raise SetupError(f"no token from Knovas Link ({type(exc).__name__}): mount Link's folder for this component "
                         "at /link, read only, and have Link write it (LINK_COMPONENTS)") from None
    return LinkClient(url, name, token)


def serve(environ: Mapping[str, str] = os.environ, *, once: bool = False) -> int:
    """Run behind Knovas Link until stopped (``once``: register, one heartbeat with its commands, one job poll)."""
    try:
        client = link_client(environ)
    except SetupError as exc:
        log.error("%s", exc)
        return 2
    try:
        interval = float(environ.get("SYNC_INTERVAL_SECONDS") or 30)
    except ValueError:
        interval = 0.0
    if not 1 <= interval <= 3600:
        log.error("SYNC_INTERVAL_SECONDS: 1 to 3600 seconds.")
        return 2
    site = Site(client, environ=environ)
    while True:
        try:
            site.register()
            break
        except LinkError as exc:
            if exc.status == 401:
                log.error("Knovas Link refused this component's token (%s): check SYNC_NAME, LINK_COMPONENTS and the "
                          "mounted token folder.", exc.code)
                return 2
            log.warning("Knovas Link cannot register this component yet (%s)", exc.code)
        except OSError as exc:
            log.warning("Knovas Link is not reachable at SYNC_LINK_URL (%s)", type(exc).__name__)
        if once:
            return 2
        time.sleep(15)
    log.info("jira connector %s running behind Knovas Link", VERSION)
    if once:
        site.tick()
        site.poll_once(wait=0)
        return 0
    site.forever(interval)
    return 0
