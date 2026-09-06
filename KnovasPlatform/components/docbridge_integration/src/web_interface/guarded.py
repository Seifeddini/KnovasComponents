"""One way to perform a four-eyes-guarded action from the console.

Pflichtenheft B5. ``identity/approvals.py`` decides *whether* an actor must
queue an action; this module is the only place a console route asks it. Two
outcomes, and nothing in between:

- queued: a request row exists and the action did NOT run;
- executed: the action ran, and because the actor was allowed to act alone,
  an ``approval.bypassed`` row now says so.

The second half is the part that keeps the record honest. An administrator
acting alone is a decision, not an exemption, and an auditor must be able to
tell the two apart (decided 2026-08-14).

``audit.record`` is best-effort by design -- it never raises -- so the bypass
row is written whenever the audit log is reachable, and a logging outage
never blocks the guarded action itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from identity.approvals import GUARDED_KINDS


@dataclass(frozen=True)
class GuardOutcome:
    queued: bool
    request: Any = None
    result: Mapping[str, Any] | None = None


def run_guarded(
    service,
    actor,
    *,
    kind: str,
    target_ref: str,
    payload: Mapping[str, Any],
    execute: Callable[[], Mapping[str, Any] | None],
) -> GuardOutcome:
    """Queue ``kind`` on ``target_ref``, or run ``execute`` and record the bypass.

    Raises:
        ValueError: ``kind`` is not a guarded kind. Ordinary work does not go
            through here; a guarded-action list that grows by accident is how
            a queue becomes something people route around.
        Whatever ``execute`` raises: nothing ran, so nothing is recorded.
    """
    if kind not in GUARDED_KINDS:
        raise ValueError(
            f"{kind!r} is not a guarded action; call it directly. Guarded: "
            f"{', '.join(sorted(GUARDED_KINDS))}."
        )
    if service.requires_approval(kind, actor):
        request = service.request(
            actor, kind=kind, target_ref=target_ref, payload=dict(payload)
        )
        return GuardOutcome(queued=True, request=request)

    result = dict(execute() or {})
    service.record_bypass(
        actor, kind=kind, target_ref=target_ref, detail={"result": result}
    )
    return GuardOutcome(queued=False, result=result)
