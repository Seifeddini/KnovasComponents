"""The write path of connector protocol 2.0 at the site (draft; LINK_PROTOCOL_V2, off by default): the permit checks
before a connector sees an ``act`` job, the used-permit store and the site's write log (SPEC-write-back -10, -21,
-22, -23, -26; GI-CONN-05).

The checks run in the order of the Alloy model's ``LinkVerifyMechanism`` (``network/write_back_permits.als``), before
anything else Link checks about an act (its deadline, the source's switch, the connector's declaration: step 7 of
"What Knovas Link checks", in ``jobs.WorkChannel``), so a repeat of a written permit gets its first result whatever
changed at the site since (verifier, Phase A, M1):

1. every step has its permit: else ``permit_missing``;
2. each permit verifies (``permits.verify``): else ``permit_bad_signature``;
3. each names this Link's certificate serial: else ``permit_wrong_site``;
4. each names this site's tenant and the job's source, action, target and parameters (hash), expected version and
   staging, and there are no more permits than steps: else ``permit_mismatch``;
5. a permit carried out before: the stored first result, ``repeat: true`` and ``code: permit_used``, also after its
   expiry; nothing goes to the connector again. A permit reserved by a delivery that has no result yet is
   ``reserved``: the caller waits while that delivery is still with the connector, and otherwise hands it on again
   before the expiry (the connector's marker of the write answers the earlier receipt) or refuses it
   ``permit_expired`` after it;
6. now is before every ``exp``: else ``permit_expired``.

The used-permit store and the write log live on Link's volume (``<data>/state``) and are written, and flushed to disk,
before a job goes on: a permit is **reserved** in the store before its job reaches the connector (the Brain's
reference, ``used.begin``), so a second delivery while the first is in flight never runs (M2); the first result is
kept under every permit id before it leaves Link and is never replaced. Every delivery that carried a permit is a log
line ``{permit_id, job_id, outcome, code, at}`` per permit it carried (outcomes ``accepted``, ``executed``,
``refused``, ``repeated``), refusals included: a permit refused before it verified is logged under the id it names,
unverified, and a delivery that carried none as one line without an id (SPEC-write-back-26, L2). They hold ids,
codes, a hash of the job's content and the first result; never a credential. The first result carries the values of
the change, so the store is private to Link's user like the key (0600) and never logged.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Callable, Optional

from knovas_link.permits import KeyRing, PermitRefused, canonical, params_hash, permit_id_unverified, verify

log = logging.getLogger("knovas_link.write_path")

#: How long a used permit is kept after its expiry (the protocol's "Idempotency and exactly once").
KEEP_SECONDS = 30 * 24 * 3600


def content_digest(job: dict) -> str:
    """What a repeat must equal: the job's source, target serial and arguments, canonical."""
    return hashlib.sha256(canonical({"source": job.get("source"), "target_serial": job.get("target_serial"),
                                     "args": job.get("args")})).hexdigest()


class WriteLogUnavailable(Exception):
    pass


class PermitStore:
    """The used-permit store and the write log, on Link's volume."""

    def __init__(self, folder: Path, *, clock: Callable[[], float]):
        self.folder = Path(folder)
        self.clock = clock
        self.used_path = self.folder / "used-permits.json"
        self.log_path = self.folder / "write-log.jsonl"
        self.used: dict[str, dict] = {}
        try:
            self.used = json.loads(self.used_path.read_text(encoding="utf-8")).get("used") or {}
        except (OSError, ValueError, AttributeError):
            self.used = {}

    def _write_private(self, path: Path, data: bytes, *, append: bool = False) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        if append:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, data)
                os.fsync(fd)
            finally:
                os.close(fd)
            return
        tmp = path.with_name(path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)

    def log(self, permit_ids: list[Optional[str]], job_id: Optional[str], outcome: str, code: Optional[str]) -> None:
        """One line per permit (a refusal of a delivery that carried none: one line without an id); raises
        WriteLogUnavailable when the line cannot be written."""
        at = int(self.clock())
        lines = "".join(json.dumps({"permit_id": pid, "job_id": job_id, "outcome": outcome, "code": code, "at": at},
                                   separators=(",", ":")) + "\n" for pid in (permit_ids or [None]))
        try:
            self._write_private(self.log_path, lines.encode("utf-8"), append=True)
        except OSError as exc:
            log.error("write_log.unwritable (%s)", type(exc).__name__)
            raise WriteLogUnavailable() from None

    def entries(self) -> list[dict]:
        try:
            return [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines() if line]
        except OSError:
            return []

    def state(self, permit_ids: list[str], digest: str) -> tuple[str, Optional[bytes], Optional[str]]:
        """(state, first result, job id) of the permits: ``new`` when none was handed on; ``used`` with the first result
        when all were carried out by one job of the same content; ``reserved`` with that job's id when all were handed
        on by one job of the same content that has no result yet; else ``mismatch``."""
        found = [self.used.get(pid) for pid in permit_ids]
        if not any(found):
            return "new", None, None
        if all(found) and len({f["job_id"] for f in found}) == 1 and all(f["content"] == digest for f in found):
            results = {f.get("result") for f in found}
            if None not in results and len(results) == 1:
                return "used", found[0]["result"].encode("utf-8"), found[0]["job_id"]
            if results == {None}:
                return "reserved", None, found[0]["job_id"]
        return "mismatch", None, None

    def _save(self) -> None:
        try:
            self._write_private(self.used_path, json.dumps({"used": self.used}, separators=(",", ":")).encode())
        except OSError as exc:
            log.error("used_permits.unwritable (%s)", type(exc).__name__)
            raise WriteLogUnavailable() from None

    def begin(self, permit_ids: list[str], job_id: str, digest: str, exp: int) -> bool:
        """Reserve the permits for ``job_id`` before the connector sees the job, kept until 30 days after the expiry.
        False (nothing changed) when one of them has a result already; a reservation without a result is taken over
        (its delivery ended without one). WriteLogUnavailable when the store cannot be written."""
        if any((self.used.get(pid) or {}).get("result") is not None for pid in permit_ids):
            return False
        now = self.clock()
        before = dict(self.used)
        self.used = {k: v for k, v in self.used.items() if v["keep_until"] > now}
        for pid in permit_ids:
            self.used[pid] = {"job_id": job_id, "content": digest, "result": None, "keep_until": exp + KEEP_SECONDS}
        try:
            self._save()
        except WriteLogUnavailable:
            self.used = before
            raise
        return True

    def complete(self, permit_ids: list[str], job_id: str, digest: str, raw_result: bytes, exp: int) -> bytes:
        """Keep the connector's result under every permit id before it leaves Link, and return the result kept: the
        first one ever stored for these permits, which is never replaced (a later answer cannot overwrite it)."""
        kept = next((self.used[pid]["result"] for pid in permit_ids
                     if (self.used.get(pid) or {}).get("result") is not None), None)
        if kept is not None:
            return kept.encode("utf-8")
        before = dict(self.used)
        for pid in permit_ids:
            entry = self.used.get(pid) or {"job_id": job_id, "content": digest, "keep_until": exp + KEEP_SECONDS}
            self.used[pid] = dict(entry, result=raw_result.decode("utf-8"))
        try:
            self._save()
        except WriteLogUnavailable:
            self.used = before
            raise
        return raw_result


class Verdict:
    """What Link does with an act job: refuse it (``code``), answer it with a stored first result (``repeat``), wait for
    or take over an earlier delivery's reservation (``reserved``), or hand it on (``permit_ids``, ``exp``). For a
    refusal before every permit verified, ``permit_ids`` are the ids the tokens name, unverified (the write log's)."""

    def __init__(self, *, code: Optional[str] = None, repeat: Optional[bytes] = None, reserved: bool = False,
                 permit_ids: Optional[list] = None, exp: int = 0):
        self.code, self.repeat, self.reserved, self.permit_ids, self.exp = code, repeat, reserved, permit_ids or [], exp


def carried_ids(job: dict) -> list[Optional[str]]:
    """The permit ids the job's tokens name, unverified, one per token it carried: for the write log."""
    tokens = job.get("permits") if isinstance(job, dict) else None
    return [permit_id_unverified(t) for t in tokens] if isinstance(tokens, list) else []


def check_act(job: dict, *, ring: KeyRing, tenant: Optional[str], serial: Optional[str], now: int,
              store: PermitStore) -> Verdict:
    """Link's checks of an act job, in the model's order (module docstring). Writes nothing."""
    steps = job["args"]["steps"]
    tokens = job.get("permits") or []
    if len(tokens) < len(steps):
        return Verdict(code="permit_missing", permit_ids=carried_ids(job))
    payloads = []
    for token in tokens[:len(steps)]:
        try:
            payloads.append(verify(token, ring).payload)
        except PermitRefused as refused:
            return Verdict(code=refused.code, permit_ids=carried_ids(job))
    ids = [p["permit_id"] for p in payloads]
    if any(p["site"] != serial for p in payloads) or not serial:
        return Verdict(code="permit_wrong_site", permit_ids=carried_ids(job))
    for step, p in zip(steps, payloads):
        if (p["tenant"] != tenant or p["source"] != job["source"] or p["action"] != step["action"]
                or p["params_hash"] != params_hash(step["target"], step["params"])
                or p["expected_version"] != step["expected_version"] or p["staging"] != job["args"]["staging"]):
            return Verdict(code="permit_mismatch", permit_ids=carried_ids(job))
    if len(tokens) != len(steps) or len(set(ids)) != len(ids):
        return Verdict(code="permit_mismatch", permit_ids=carried_ids(job))
    exp = max(p["exp"] for p in payloads)
    state, first, _job = store.state(ids, content_digest(job))
    if state == "used":
        return Verdict(repeat=first, permit_ids=ids, exp=exp)
    if state == "reserved":
        return Verdict(reserved=True, permit_ids=ids, exp=exp)
    if state == "mismatch":
        return Verdict(code="permit_mismatch", permit_ids=ids)
    if any(now >= p["exp"] for p in payloads):
        return Verdict(code="permit_expired", permit_ids=ids)
    return Verdict(permit_ids=ids, exp=exp)


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)
