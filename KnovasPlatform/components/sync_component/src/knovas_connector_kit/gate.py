"""A reference of Knovas Link's checks on the write path (connector protocol 2.0, "What Knovas Link checks"), for the
conformance suite and for tests. Knovas Link implements the same checks itself (it imports no other package of
Knovas); this reference follows the Alloy model's ``LinkVerifyMechanism`` order
(``network/write_back_permits.als``): permit missing, bad signature, wrong site, mismatch, used (the first result,
``repeat: true``), expired; then the source's switch; then the write log, before the connector sees the job.

As Link does (verifier, Phase A, M2, L2): a permit is reserved before the connector runs and its first result is kept
and never replaced; every delivery that carried a permit is a line of the write log, a refusal before the permit
verified under the id it names, unverified, and a delivery that carried none as one line without an id. The reference
is synchronous, so no second delivery arrives while the connector runs (Link makes one wait for the first's result).
"""
from __future__ import annotations

import hashlib
from typing import Callable, Optional

from knovas_connector_kit import contracts
from knovas_connector_kit.canonical import canonical, params_hash
from knovas_connector_kit.connector import Connector
from knovas_connector_kit.permits import KeyRing, PermitError, permit_id_unverified, verify


class LinkGate:
    """One site's Link in front of one connector: the used-permit store and the write log live here."""

    def __init__(self, connector: Connector, ring: KeyRing, *, tenant: str, serial: str,
                 clock: Callable[[], int], writable: bool = True):
        self.connector, self.ring, self.tenant, self.serial = connector, ring, tenant, serial
        self.clock, self.writable = clock, writable
        #: permit id -> (job content digest, first result or None while it is reserved, job id)
        self.used: dict[str, tuple] = {}
        self.log: list[dict] = []                           # {permit_id, job_id, outcome, code, at}

    def _record(self, job: dict, permit_ids: list, outcome: str, code: Optional[str]) -> None:
        for pid in permit_ids or [None]:
            self.log.append({"permit_id": pid, "job_id": job.get("id") if isinstance(job, dict) else None,
                             "outcome": outcome, "code": code, "at": self.clock()})

    def _refuse(self, job: dict, permit_ids: list, code: str, status: str = "refused") -> dict:
        self._record(job, permit_ids, "refused", code)
        return {"status": status, "code": code, "duration_ms": 0}

    @staticmethod
    def _carried(job: dict) -> list:
        tokens = job.get("permits") if isinstance(job, dict) else None
        return [permit_id_unverified(t) for t in tokens] if isinstance(tokens, list) else []

    def deliver(self, job: dict) -> dict:
        if not contracts.valid("job.schema.json", job):
            if isinstance(job, dict) and job.get("kind") == "act":
                return self._refuse(job, self._carried(job), "invalid_job", "failed")
            return {"status": "failed", "code": "invalid_job", "duration_ms": 0}
        if job["kind"] != "act":
            return self.connector.handle(job)
        steps, tokens = job["args"]["steps"], job.get("permits") or []
        carried = self._carried(job)
        now = self.clock()
        if len(tokens) < len(steps):
            return self._refuse(job, carried, "permit_missing")
        payloads = []
        for step, token in zip(steps, tokens):
            try:
                payload = verify(token, self.ring).payload
            except PermitError as refused:
                return self._refuse(job, carried, refused.code)
            payloads.append((step, payload))
        ids = [p["permit_id"] for _s, p in payloads]
        for step, p in payloads:
            if p["site"] != self.serial:
                return self._refuse(job, carried, "permit_wrong_site")
        for step, p in payloads:
            if (p["tenant"] != self.tenant or p["source"] != job["source"] or p["action"] != step["action"]
                    or p["params_hash"] != params_hash(step["target"], step["params"])
                    or p["expected_version"] != step["expected_version"] or p["staging"] != job["args"]["staging"]
                    or len(tokens) != len(steps)):
                return self._refuse(job, carried, "permit_mismatch")
        content = hashlib.sha256(canonical({"source": job["source"], "args": job["args"]})).hexdigest()
        if any(pid in self.used for pid in ids):
            entries = [self.used.get(pid) for pid in ids]
            same = all(entries) and len({e[2] for e in entries}) == 1 and all(e[0] == content for e in entries)
            if same and all(e[1] is not None for e in entries):
                self._record(job, ids, "repeated", "permit_used")
                return dict(entries[0][1], repeat=True, code="permit_used")
            if not (same and all(e[1] is None for e in entries)):
                return self._refuse(job, ids, "permit_mismatch")
            # Reserved by a delivery that ended without a result: it goes on again before the expiry (the
            # connector's marker answers the earlier write), and is refused permit_expired after it.
        if any(now >= p["exp"] for _s, p in payloads):
            return self._refuse(job, ids, "permit_expired")
        if not self.writable:
            return self._refuse(job, ids, "write_disabled")
        for pid in ids:                                     # reserved before the connector runs
            self.used[pid] = (content, None, job["id"])
        self._record(job, ids, "accepted", None)
        result = self.connector.handle(job)
        for pid in ids:                                     # the first result, never replaced
            if self.used[pid][1] is None:
                self.used[pid] = (content, dict(result), job["id"])
        self._record(job, ids, "executed" if result["status"] == "ok" else "refused", result.get("code"))
        return result
