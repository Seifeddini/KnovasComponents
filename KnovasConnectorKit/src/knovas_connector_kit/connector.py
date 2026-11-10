"""The base of every connector (connector protocol 2.0, "Jobs").

A connector subclasses ``Connector``, returns its declaration and implements the operations its declaration promises.
``handle(job)`` is what its job loop calls for every job Knovas Link hands it: it checks the job against the contract,
decodes the permits of an ``act`` (Link has verified them; the connector re-checks only that each permit binds its own
step), dispatches, and checks the answer against the contract before it leaves, including that every change,
difference and conflict of a ``preview`` or ``act`` names a step of the job on that step's target (D-14). An exception
becomes ``failed internal`` and is logged by its type only: an error text may quote a credential or a value
(GI-DATA-04, GI-CONN-07).
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Optional

from knovas_connector_kit import contracts
from knovas_connector_kit.canonical import params_hash
from knovas_connector_kit.permits import PermitError, decode

log = logging.getLogger("knovas_connector_kit")

WRITE_PATH = ("preview", "act", "reread", "review_outcome", "unstage")


def _steps_named(job: Any, result: dict) -> bool:
    """A ``preview`` or ``act`` answer's changes, values, differences and conflict each name a step of the job, on that
    step's target (D-14). A ``reread`` or ``review_outcome`` receipt names the step of its permit in an earlier
    ``act`` job, which this job does not hold."""
    if not isinstance(job, dict) or job.get("kind") not in ("preview", "act"):
        return True
    steps = (job.get("args") or {}).get("steps") or []
    entries = list((result.get("preview") or {}).get("changes") or [])
    for receipt in result.get("receipts") or []:
        entries += receipt.get("values") or []
        entries += receipt.get("differences") or []
    if result.get("conflict"):
        entries += [result["conflict"]] + list(result["conflict"].get("changes") or [])
    return all(e["step"] < len(steps) and e["target"] == steps[e["step"]]["target"] for e in entries)


def outcome(status: str, code: Optional[str] = None, **fields: Any) -> dict:
    """A result without its duration (``handle`` sets ``duration_ms``)."""
    out: dict = {"status": status, "code": code}
    out.update(fields)
    return out


class Connector(ABC):
    """One connector: one source kind, the streams it reads, the actions it carries out."""

    @abstractmethod
    def declaration(self) -> dict:
        """The connector's declaration (``declaration.schema.json``)."""

    # ---- reading
    def read(self, source: str, state: Optional[dict]) -> list[dict]:
        """The stream of the source from ``state`` on (``stream-message.schema.json`` messages)."""
        return []

    def fetch(self, source: str, args: dict) -> dict:
        return outcome("unsupported", "unsupported")

    # ---- the write path; a connector without actions keeps these defaults
    def preview(self, source: str, steps: list[dict], staging: str, at_version: Optional[str]) -> dict:
        return outcome("unsupported", "unsupported")

    def act(self, source: str, steps: list[dict], permits: list[dict], staging: str) -> dict:
        return outcome("unsupported", "unsupported")

    def reread(self, source: str, targets: list[str], fields: Optional[list[str]], permit_ids: list[str]) -> dict:
        return outcome("unsupported", "unsupported")

    def review_outcome(self, source: str, stage_ref: str, permit_ids: list[str]) -> dict:
        return outcome("unsupported", "unsupported")

    def unstage(self, source: str, stage_ref: str, permit_ids: list[str]) -> dict:
        return outcome("unsupported", "unsupported")

    # ---- what the declaration says
    def declared_action(self, name: str) -> Optional[dict]:
        return next((a for a in self.declaration()["actions"] if a["name"] == name), None)

    # ---- the job loop's entry
    def handle(self, job: dict) -> dict:
        started = time.monotonic()
        result = self._handle(job)
        result["duration_ms"] = int((time.monotonic() - started) * 1000)
        if not contracts.valid("result.schema.json", result) or not _steps_named(job, result):
            log.warning("connector.result_invalid", extra={"kind": job.get("kind") if isinstance(job, dict) else None})
            result = {"status": "failed", "code": "internal", "duration_ms": result["duration_ms"]}
        return result

    def _handle(self, job: dict) -> dict:
        if not contracts.valid("job.schema.json", job):
            return outcome("failed", "invalid_job")
        kind, args, source = job["kind"], job["args"], job.get("source")
        try:
            if kind == "probe":
                return outcome("ok")
            if kind == "fetch":
                return self.fetch(source, args)
            if kind in ("preview", "act"):
                for step in args["steps"]:
                    if self.declared_action(step["action"]) is None:
                        return outcome("refused", "action_not_declared")
                    if args["staging"] not in self.declared_action(step["action"])["staging"]:
                        return outcome("refused", "staging_unsupported")
            if kind == "preview":
                return self.preview(source, args["steps"], args["staging"], args.get("at_version"))
            if kind == "act":
                permits = self._bound_permits(job)
                return self.act(source, args["steps"], permits, args["staging"])
            if kind == "reread":
                return self.reread(source, args["targets"], args.get("fields"), args.get("permit_ids") or [])
            if kind == "review_outcome":
                return self.review_outcome(source, args["stage_ref"], args["permit_ids"])
            if kind == "unstage":
                return self.unstage(source, args["stage_ref"], args["permit_ids"])
        except PermitError as refused:
            return outcome("refused", refused.code)
        except Exception as exc:  # noqa: BLE001 - one result per job, whatever happened
            log.warning("connector.failed", extra={"kind": kind, "error_type": type(exc).__name__})
            return outcome("failed", "internal")
        return outcome("unsupported", "unsupported")

    def _bound_permits(self, job: dict) -> list[dict]:
        """Each step's permit payload, re-checked against its own step (defence in depth: Link checked them first)."""
        steps, tokens = job["args"]["steps"], job.get("permits") or []
        if len(tokens) < len(steps):
            raise PermitError("permit_missing")
        if len(tokens) > len(steps):
            raise PermitError("permit_mismatch")
        out = []
        for step, token in zip(steps, tokens):
            payload = decode(token).payload
            if (payload["source"] != job["source"] or payload["action"] != step["action"]
                    or payload["params_hash"] != params_hash(step["target"], step["params"])
                    or payload["expected_version"] != step["expected_version"]
                    or payload["staging"] != job["args"]["staging"] or payload["site"] != job["target_serial"]):
                raise PermitError("permit_mismatch")
            out.append(payload)
        if len(steps) > 1 and any(self.declared_action(s["action"])["grouping"] != "atomic" for s in steps):
            raise PermitError("grouping_unsupported")
        return out
