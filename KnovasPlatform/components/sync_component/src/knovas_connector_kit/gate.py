"""A reference of Knovas Link's checks on the write path (connector protocol 2.0, "What Knovas Link checks"), for the
conformance suite and for tests. Knovas Link implements the same checks itself (it imports no other package of
Knovas); this reference follows the Alloy model's ``LinkVerifyMechanism`` order
(``network/write_back_permits.als``): permit missing, bad signature, wrong site, mismatch, used (the first result,
``repeat: true``), expired; then the declaration; then the write log, before the connector sees the job.
"""
from __future__ import annotations

import hashlib
from typing import Callable, Optional

from knovas_connector_kit import contracts
from knovas_connector_kit.canonical import canonical, params_hash
from knovas_connector_kit.connector import Connector
from knovas_connector_kit.permits import KeyRing, PermitError, verify


class LinkGate:
    """One site's Link in front of one connector: the used-permit store and the write log live here."""

    def __init__(self, connector: Connector, ring: KeyRing, *, tenant: str, serial: str,
                 clock: Callable[[], int], writable: bool = True):
        self.connector, self.ring, self.tenant, self.serial = connector, ring, tenant, serial
        self.clock, self.writable = clock, writable
        self.used: dict[str, tuple[str, dict, str]] = {}  # permit id -> (job content digest, first result, job id)
        self.log: list[dict] = []                           # {permit_id, job_id, outcome, code, at}

    def _record(self, job: dict, permit_ids: list[str], outcome: str, code: Optional[str]) -> None:
        for pid in permit_ids:
            self.log.append({"permit_id": pid, "job_id": job.get("id"), "outcome": outcome, "code": code,
                             "at": self.clock()})

    def _refuse(self, job: dict, permit_ids: list[str], code: str, status: str = "refused") -> dict:
        self._record(job, permit_ids, "refused", code)
        return {"status": status, "code": code, "duration_ms": 0}

    def deliver(self, job: dict) -> dict:
        if not contracts.valid("job.schema.json", job):
            return {"status": "failed", "code": "invalid_job", "duration_ms": 0}
        if job["kind"] != "act":
            return self.connector.handle(job)
        steps, tokens = job["args"]["steps"], job.get("permits") or []
        now = self.clock()
        if len(tokens) < len(steps):
            return self._refuse(job, [], "permit_missing")
        payloads = []
        for step, token in zip(steps, tokens):
            try:
                payload = verify(token, self.ring).payload
            except PermitError as refused:
                return self._refuse(job, [], refused.code)
            payloads.append((step, payload))
        ids = [p["permit_id"] for _s, p in payloads]
        for step, p in payloads:
            if p["site"] != self.serial:
                return self._refuse(job, ids, "permit_wrong_site")
        for step, p in payloads:
            if (p["tenant"] != self.tenant or p["source"] != job["source"] or p["action"] != step["action"]
                    or p["params_hash"] != params_hash(step["target"], step["params"])
                    or p["expected_version"] != step["expected_version"] or p["staging"] != job["args"]["staging"]
                    or len(tokens) != len(steps)):
                return self._refuse(job, ids, "permit_mismatch")
        content = hashlib.sha256(canonical({"source": job["source"], "args": job["args"]})).hexdigest()
        if any(pid in self.used for pid in ids):
            entries = [self.used.get(pid) for pid in ids]
            if all(entries) and len({e[2] for e in entries}) == 1 and entries[0][0] == content:
                self._record(job, ids, "repeated", "permit_used")
                return dict(entries[0][1], repeat=True, code="permit_used")
            return self._refuse(job, ids, "permit_mismatch")
        if any(now >= p["exp"] for _s, p in payloads):
            return self._refuse(job, ids, "permit_expired")
        if not self.writable:
            return self._refuse(job, ids, "write_disabled")
        self._record(job, ids, "accepted", None)
        result = self.connector.handle(job)
        for pid in ids:
            self.used[pid] = (content, dict(result), job["id"])
        self._record(job, ids, "executed" if result["status"] == "ok" else "refused", result.get("code"))
        return result
