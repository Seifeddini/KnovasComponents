"""The work channel and probes (MVP-18).

Link holds one long-poll against secure-api, ``GET /secured/connector/jobs?source=<the site's sources>&wait=25``, with
the site's certificate. Each job it claims goes to the component that serves its source and named its kind, through
that component's queue and its own long-poll ``GET /local/v1/jobs?wait=25``. The component's parts and result are
passed on unchanged and in order: each part is streamed through as it arrives, never held whole in memory or on disk.

Link answers for the site where no component does (local-api.md):

- no registered component named the job's kind for its source: ``unsupported``;
- the component stayed silent past the job's deadline: ``unreachable``, and its late part or result gets 409
  ``lease_lost``;
- fetching is off for the source: ``refused`` ``fetch_disabled``; the source's ``access`` is not ``read_write``:
  ``refused`` ``write_disabled``.

While Link waits for its certificate it claims nothing. Every 5 minutes, and on the Platform's ``check`` command,
Link probes: ``POST /secured/connector/probe``, answers the probe when it comes round the channel, and hands each
component that answers probes a local probe of its own to measure ``local_ping_ms``.

Logs carry job ids, kinds, codes, counts and durations; never a source's content, a path, a token or a certificate.
"""
from __future__ import annotations

import asyncio
import collections
import datetime as dt
import json
import logging
import random
import re
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncIterator, Optional

from knovas_link import contracts
from knovas_link.brain_client import LEASE, BrainBusy, BrainRefused, BrainUnreachable
from knovas_link.errors import LocalError

if TYPE_CHECKING:                                     # pragma: no cover
    from knovas_link.link import Link

log = logging.getLogger("knovas_link.jobs")

MAX_PART_BYTES = 4 * 1024 * 1024
WAIT_SECONDS = 25
#: How long a probe may take to come round the channel before it counts as failed.
PROBE_WAIT_SECONDS = 60
#: The deadline of a local probe.
LOCAL_PROBE_SECONDS = 30
#: secure-api takes at most this many sources in one long-poll.
MAX_SOURCES = 32
BACKOFF_MAX_SECONDS = 60.0
#: The actor of the jobs Link makes itself (local probes).
LINK_ACTOR = {"kind": "component", "id": "knovas-link"}
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_PART_NUMBER = re.compile(r"^[0-9]{1,9}$")
_REMEMBERED = 500


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(moment: dt.datetime) -> str:
    return moment.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_deadline(value: Any) -> Optional[dt.datetime]:
    try:
        t = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


class PartTooLarge(Exception):
    pass


@dataclass
class Held:
    """A job Link holds for one component, from the claim to its outcome."""
    job: dict                        # as the component gets it: without the lease
    kind: str
    component: str
    source: Optional[str]
    local: bool
    claimed_at: float
    lease: Optional[str] = None      # the claim's lease, sent back with every part and the result
    handed_at: Optional[float] = None
    next_part: int = 0
    part_in_flight: bool = False
    finishing: bool = False
    ended: bool = False
    timer: Optional[asyncio.TimerHandle] = None
    expired: bool = False


class WorkChannel:
    def __init__(self, link: "Link"):
        self.link = link
        self.state = "down"
        self.probe = {"last_at": None, "ok": None, "round_trip_ms": None}
        self.backoff_base = 1.0
        self.held: dict[str, Held] = {}
        self._queues: dict[str, collections.deque] = {}
        self._events: dict[str, asyncio.Event] = {}
        self._finished: collections.OrderedDict = collections.OrderedDict()
        self._probes: dict[str, tuple[float, asyncio.Future]] = {}
        self._answered_probes: collections.OrderedDict = collections.OrderedDict()
        self._tasks: set = set()

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _loop() -> asyncio.AbstractEventLoop:
        return asyncio.get_running_loop()

    def spawn(self, coro) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        for h in self.held.values():
            if h.timer:
                h.timer.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def _remember(self, store: collections.OrderedDict, key: str, value: Any) -> None:
        store[key] = value
        while len(store) > _REMEMBERED:
            store.popitem(last=False)

    def _ms(self, since: float) -> int:
        return max(0, int((self._loop().time() - since) * 1000))

    # ------------------------------------------------------------------ the long-poll against secure-api

    async def run(self) -> None:
        """Claim jobs as long as Link runs; reconnect with backoff."""
        backoff = self.backoff_base
        while True:
            brain = self.link.brain
            if brain is None or not self.link.certificate_present:
                self.state = "down"
                await self.link.wait_for_brain(5.0)
                continue
            try:
                job = await brain.claim(self.link.channel_sources(), WAIT_SECONDS)
            except BrainBusy as exc:
                self.state = "down"
                log.warning("channel.busy, retry after %ds", exc.retry_after)
                await asyncio.sleep(max(float(exc.retry_after), backoff))
                backoff = min(backoff * 2, BACKOFF_MAX_SECONDS)
                continue
            except (BrainUnreachable, BrainRefused) as exc:
                self.state = "down"
                code = getattr(exc, "status", None) or type(exc).__name__
                log.warning("channel.down (%s), retry in %.1fs", code, backoff)
                await asyncio.sleep(backoff * (0.5 + random.random() / 2))
                backoff = min(backoff * 2, BACKOFF_MAX_SECONDS)
                continue
            if self.state != "connected":
                log.info("channel.connected")
            self.state = "connected"
            backoff = self.backoff_base
            if job is not None:
                self.spawn(self.accept(job))

    async def accept(self, job: Any) -> None:
        """A job claimed from secure-api: to its component, or answered by Link."""
        loop = self._loop()
        claimed = loop.time()
        jid = job.get("id") if isinstance(job, dict) else None
        if not isinstance(jid, str) or not _UUID.match(jid):
            log.warning("job.no_id")
            return
        lease = job.get("lease")
        if lease is not None and (not isinstance(lease, str) or not LEASE.match(lease)):
            log.warning("job.bad_lease")
            return
        job = {k: v for k, v in job.items() if k != "lease"}          # the lease stays with Link
        held = self.held.get(jid)
        if held is not None and not held.ended:
            # Claimed again after its lease ran out: the first claim is gone. Its hand-out ends (a late part or
            # result of it is 409 lease_lost), and the job is handed out afresh under the new lease.
            self._end(held)
            log.info("job.claimed_again %s", held.kind)
        if jid in self._finished:
            await self._post_own(jid, self._finished[jid][0], self._finished[jid][1], lease)
            return
        if not contracts.job_valid(job):
            await self.answer(jid, str(job.get("kind") or "unknown"), "failed", "bad_field", claimed, lease)
            return
        kind = job["kind"]
        if kind == "probe":
            await self._answer_probe(jid, claimed, lease)
            return
        deadline = parse_deadline(job["deadline"])
        remaining = (deadline - utcnow()).total_seconds() if deadline else 0
        if remaining <= 0:
            await self.answer(jid, kind, "unreachable", "unreachable", claimed, lease)
            return
        source = self.link.source(job["source"])
        if source is None:
            await self.answer(jid, kind, "unsupported", "unsupported", claimed, lease)
            return
        if kind == "fetch" and source.get("fetch") is not True:
            await self.answer(jid, kind, "refused", "fetch_disabled", claimed, lease)
            return
        if kind == "write" and source.get("access") != "read_write":
            await self.answer(jid, kind, "refused", "write_disabled", claimed, lease)
            return
        name = source.get("component")
        component = self.link.registry.get(name)
        if component is None or not component.registered or kind not in component.job_kinds:
            await self.answer(jid, kind, "unsupported", "unsupported", claimed, lease)
            return
        self._hold(jid, job, kind, name, job["source"], remaining, local=False, claimed=claimed, lease=lease)

    # ------------------------------------------------------------------ Link's own answers

    async def answer(self, jid: str, kind: str, status: str, code: Optional[str], since: float,
                     lease: Optional[str] = None) -> bool:
        result = {"status": status, "code": code, "duration_ms": self._ms(since)}
        if status == "ok":
            result.pop("code")
        assert contracts.result_valid(result), result
        raw = json.dumps(result, separators=(",", ":")).encode("utf-8")
        self.link.telemetry.job(kind, status)
        log.info("job.answered %s %s %s", kind, status, code or "-")
        self._remember(self._finished, jid, (kind, raw))
        return await self._post_own(jid, kind, raw, lease)

    async def _post_own(self, jid: str, kind: str, raw: bytes, lease: Optional[str]) -> bool:
        brain = self.link.brain
        if brain is None:
            return False
        try:
            status, code = await brain.post_result(jid, raw, lease)
        except BrainUnreachable as exc:
            log.warning("job.answer_not_taken %s (%s)", kind, exc)
            return False
        if status >= 300:
            log.warning("job.answer_refused %s %d %s", kind, status, code or "-")
            return False
        return True

    async def _answer_probe(self, jid: str, claimed: float, lease: Optional[str]) -> None:
        if await self.answer(jid, "probe", "ok", None, claimed, lease):
            now = self._loop().time()
            waiting = self._probes.get(jid)
            if waiting is not None:
                if not waiting[1].done():
                    waiting[1].set_result(now - waiting[0])
            else:
                self._remember(self._answered_probes, jid, now)

    # ------------------------------------------------------------------ held jobs and the components' queues

    def _hold(self, jid: str, job: dict, kind: str, component: str, source: Optional[str], seconds: float, *,
              local: bool, claimed: float, lease: Optional[str] = None) -> Held:
        h = Held(job=job, kind=kind, component=component, source=source, local=local, claimed_at=claimed, lease=lease)
        h.timer = self._loop().call_later(max(0.0, seconds), self._deadline, jid)
        self.held[jid] = h
        self._queues.setdefault(component, collections.deque()).append(jid)
        self._events.setdefault(component, asyncio.Event()).set()
        return h

    def _deadline(self, jid: str) -> None:
        self.spawn(self._expire(jid))

    def _end(self, h: Held) -> None:
        h.ended = True
        if h.timer:
            h.timer.cancel()
            h.timer = None

    async def _expire(self, jid: str) -> None:
        h = self.held.get(jid)
        if h is None or h.ended:
            return
        h.expired = True
        if h.finishing:
            return                   # a result is on its way; if it is not taken, it ends the job (finish below)
        self._end(h)
        if h.local:
            component = self.link.registry.get(h.component)
            if component is not None:
                component.local_ping_ms = None
            log.info("probe.local_silent")
            return
        await self.answer(jid, h.kind, "unreachable", "unreachable", h.claimed_at, h.lease)

    def _pop(self, name: str) -> Optional[dict]:
        queue = self._queues.get(name)
        while queue:
            jid = queue.popleft()
            h = self.held.get(jid)
            if h is not None and not h.ended and h.handed_at is None:
                h.handed_at = self._loop().time()
                return h.job
        return None

    async def next_job(self, name: str, wait: int) -> Optional[dict]:
        """The component's next job, waiting up to ``wait`` seconds; None for a 204."""
        loop = self._loop()
        until = loop.time() + wait
        while True:
            job = self._pop(name)
            if job is not None:
                return job
            remaining = until - loop.time()
            if remaining <= 0:
                return None
            event = self._events.setdefault(name, asyncio.Event())
            event.clear()
            try:
                await asyncio.wait_for(event.wait(), remaining)
            except asyncio.TimeoutError:
                pass

    def mine(self, name: str, jid: str) -> Held:
        h = self.held.get(jid)
        if h is None or h.ended or h.component != name or h.handed_at is None:
            raise LocalError(409, "lease_lost", "this job is not the caller's, or it has an outcome already")
        return h

    def _fail_fetch(self, jid: str, h: Held, code: str) -> None:
        """A refused part ends the fetch: Link answers it failed, and every later part or result is 409."""
        self._end(h)
        self.spawn(self.answer(jid, h.kind, "failed", code, h.claimed_at, h.lease))

    # ------------------------------------------------------------------ parts and results from a component

    async def part(self, name: str, jid: str, n_raw: Optional[str], length_raw: Optional[str],
                   stream: AsyncIterator[bytes]) -> None:
        h = self.mine(name, jid)
        if h.kind != "fetch":
            raise LocalError(400, "bad_field", "only a fetch job takes parts")

        def refuse(status: int, what: str) -> LocalError:
            self._fail_fetch(jid, h, "bad_field")
            log.info("job.part_refused fetch %d", status)
            return LocalError(status, "bad_field", what)

        if h.part_in_flight or h.finishing:
            raise refuse(400, "parts go one after the other")
        if n_raw is None or not _PART_NUMBER.match(n_raw) or int(n_raw) != h.next_part:
            raise refuse(400, "n counts from 0 and goes up by one")
        length = None
        if length_raw is not None:
            if not _PART_NUMBER.match(length_raw.strip()):
                raise refuse(400, "a part has a length")
            length = int(length_raw)
            if length > MAX_PART_BYTES:
                raise refuse(413, f"a part is at most {MAX_PART_BYTES} bytes")
            if length == 0:
                raise refuse(400, "an empty part")
        h.part_in_flight = True
        try:
            first = b""
            try:
                while not first:
                    first = await stream.__anext__()
            except StopAsyncIteration:
                raise refuse(400, "an empty part") from None
            if len(first) > MAX_PART_BYTES:
                raise refuse(413, f"a part is at most {MAX_PART_BYTES} bytes")

            async def through() -> AsyncIterator[bytes]:
                total = len(first)
                yield first
                async for chunk in stream:
                    total += len(chunk)
                    if total > MAX_PART_BYTES:
                        raise PartTooLarge()
                    yield chunk

            brain = self.link.brain
            if brain is None:
                self._fail_fetch(jid, h, "unreachable")
                raise LocalError(503, "unreachable", "Knovas did not take the part")
            try:
                status, code = await brain.post_part(jid, h.next_part, through(), length, h.lease)
            except PartTooLarge:
                raise refuse(413, f"a part is at most {MAX_PART_BYTES} bytes") from None
            except BrainUnreachable:
                self._fail_fetch(jid, h, "unreachable")
                raise LocalError(503, "unreachable", "Knovas did not take the part") from None
            except LocalError:
                raise
            except Exception as exc:          # noqa: BLE001 - the component's stream broke off
                log.info("job.part_broken (%s)", type(exc).__name__)
                raise refuse(400, "the part broke off") from None
        finally:
            h.part_in_flight = False
        if 200 <= status < 300:
            h.next_part += 1
            return
        if status == 404 or (status == 409 and code == "lease_lost"):
            self._end(h)
            raise LocalError(409, "lease_lost", "the job's lease ended")
        if status == 413:
            raise refuse(413, f"a part is at most {MAX_PART_BYTES} bytes")
        if status in (400, 409):
            raise refuse(400, "secure-api refused the part")
        self._fail_fetch(jid, h, "unreachable")
        raise LocalError(503, "unreachable", "Knovas did not take the part")

    async def finish(self, name: str, jid: str, raw: bytes) -> None:
        """The component's result for a job it holds: checked against the contract, then passed on unchanged."""
        h = self.mine(name, jid)
        try:
            result = json.loads(raw)
        except ValueError:
            raise LocalError(400, "bad_field", "a job result") from None
        if not contracts.result_valid(result):
            raise LocalError(400, "bad_field", "a job result as job-result.schema.json has it")
        if h.local:
            self._end(h)
            component = self.link.registry.get(h.component)
            if component is not None:
                component.local_ping_ms = self._ms(h.handed_at or h.claimed_at)
            return
        if h.part_in_flight or h.finishing:
            raise LocalError(400, "bad_field", "a part or result is still on its way")
        brain = self.link.brain
        if brain is None:
            raise LocalError(503, "unreachable", "secure-api did not answer")
        h.finishing = True
        try:
            status, code = await brain.post_result(jid, raw, h.lease)
        except BrainUnreachable:
            status, code = 503, None
        finally:
            h.finishing = False
        if 200 <= status < 300:
            self._end(h)
            self.link.telemetry.job(h.kind, result["status"])
            if h.kind == "fetch" and result["status"] == "ok" and h.source:
                self.link.source_read_ok(h.source)
            log.info("job.done %s %s %s", h.kind, result["status"], result.get("code") or "-")
            return
        if status == 404 or (status == 409 and code == "lease_lost"):
            self._end(h)
            raise LocalError(409, "lease_lost", "the job's lease ended or it has an outcome already")
        if h.expired:                         # its deadline passed while the result was on its way
            await self._expire(jid)
        if status == 400:
            raise LocalError(400, "bad_field", "secure-api refused the result")
        raise LocalError(503, "unreachable", "secure-api did not answer")

    # ------------------------------------------------------------------ probes

    def _record_probe(self, ok: bool, seconds: Optional[float]) -> None:
        self.probe = {"last_at": iso(utcnow()), "ok": ok,
                      "round_trip_ms": int(seconds * 1000) if ok and seconds is not None else None}
        log.info("probe.%s %s", "ok" if ok else "failed", self.probe["round_trip_ms"])

    def local_probes(self) -> int:
        """A local probe for every registered component that answers probes."""
        sent = 0
        for c in self.link.registry.components():
            if c.registered and "probe" in c.job_kinds:
                jid = str(uuid.uuid4())
                job = {"id": jid, "kind": "probe", "deadline": iso(utcnow() + dt.timedelta(seconds=LOCAL_PROBE_SECONDS)),
                       "actor": dict(LINK_ACTOR), "args": {}}
                self._hold(jid, job, "probe", c.name, None, LOCAL_PROBE_SECONDS, local=True, claimed=self._loop().time())
                sent += 1
        return sent

    async def probe_now(self) -> bool:
        """One probe round the whole channel, and a local probe of every component. Nothing while Link waits for its
        certificate: a probe that cannot be sent is not reported as failed, the certificate says why."""
        brain = self.link.brain
        if brain is None or not self.link.certificate_present:
            return False
        loop = self._loop()
        started = loop.time()
        self.local_probes()
        try:
            jid = await brain.probe()
        except (BrainUnreachable, BrainRefused, BrainBusy) as exc:
            log.warning("probe.not_sent (%s)", getattr(exc, "status", None) or type(exc).__name__)
            self._record_probe(False, None)
            return False
        answered = self._answered_probes.pop(jid, None)
        if answered is not None:
            self._record_probe(True, answered - started)
            return True
        future = loop.create_future()
        self._probes[jid] = (started, future)
        try:
            seconds = await asyncio.wait_for(future, PROBE_WAIT_SECONDS)
        except asyncio.TimeoutError:
            self._record_probe(False, None)
            return False
        finally:
            self._probes.pop(jid, None)
        self._record_probe(True, seconds)
        return True
