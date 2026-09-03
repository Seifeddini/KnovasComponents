"""Append-only record of who did what.

Small on purpose. B4 — per-user attributable audit — is out of this plan's
scope; this module writes only the events B1, B2, B3 and B5 generate, and is
the substrate B4 will build on rather than a first attempt at it.

One decision worth reading: the actor's e-mail is written into the row, not
only their id. An audit entry has to stay legible after the account is deleted,
and a dangling uuid is not an answer to "who did this?". The id is kept too, so
a live account can still be joined.

Plan: docs/superpowers/plans/2026-08-14-section-b-buildout.md (KC-F2, KC-B5-1)
"""
from __future__ import annotations

import json
import logging
from typing import Any, Mapping

logger = logging.getLogger(__name__)


def record(
    conn: Any,
    *,
    action: str,
    actor: Any | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    outcome: str = "ok",
    detail: Mapping[str, Any] | None = None,
    request_id: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Append one entry. Never raises into the caller's path.

    An audit write that fails must not take the action with it — a deletion
    that succeeded and then 500'd on its own logging would leave the operator
    with neither the outcome nor the record. The failure is logged loudly
    instead, where monitoring can see it.
    """
    try:
        conn.execute(
            "INSERT INTO audit_log (actor_user_id, actor_email_snapshot, action, "
            "target_type, target_id, outcome, request_id, ip, user_agent, detail) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                str(actor.id) if actor is not None else None,
                actor.email if actor is not None else None,
                action,
                target_type,
                target_id,
                outcome,
                request_id,
                ip,
                user_agent,
                json.dumps(dict(detail or {})),
            ),
        )
    except Exception:  # noqa: BLE001
        logger.exception("AUDIT WRITE FAILED action=%s target=%s", action, target_id)


def recent(
    conn: Any, *, action: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """The newest rows, newest first. A read over an append-only table.

    Keys: id, occurred_at, actor_user_id, actor_email, action, target_type,
    target_id, outcome, detail. ``detail`` is the JSONB column as a dict.
    """
    sql = (
        "SELECT id, occurred_at, actor_user_id, actor_email_snapshot, action, "
        "target_type, target_id, outcome, detail FROM audit_log"
    )
    params: tuple[Any, ...] = ()
    if action:
        sql += " WHERE action = %s"
        params = (action,)
    sql += " ORDER BY occurred_at DESC, id DESC LIMIT %s"
    rows = conn.execute(sql, params + (int(limit),)).fetchall()
    keys = ("id", "occurred_at", "actor_user_id", "actor_email", "action",
            "target_type", "target_id", "outcome", "detail")
    return [dict(zip(keys, row)) for row in rows]
