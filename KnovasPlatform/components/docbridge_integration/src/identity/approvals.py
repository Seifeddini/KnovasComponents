"""Four-eyes on destructive actions, and the administrator's recorded bypass.

Pflichtenheft B5 asks that matter deletion, wall changes and bulk exports
require a second confirmer. This module implements that, with one deliberate
exception decided on 14 August 2026: **an administrator acts alone.**

Why the exception is written as a *bypass* and not an exemption
---------------------------------------------------------------
The operational case for it is real. A firm with one administrator, or an
administrator locked out of their own system by a queue nobody can drain, is a
failure mode as expensive as the one four-eyes prevents.

The security cost is equally real and should be stated to a buyer rather than
discovered by them: a compromised administrator account is precisely the single
actor two-person control exists to stop, so with the bypass on, B5 is met for
ordinary users and not met against a privileged attacker.

What makes that defensible is the record. An administrator's action does not
skip the audit — it writes ``approval.bypassed``, naming who acted and on what.
An auditor reading the log sees that a two-person control was available and was
not used, which is a fact they can weigh. An exemption that logged nothing would
leave the same log looking like the control had simply never applied.

``set_admin_bypass(False)`` turns it off for a firm that wants strict
enforcement; the setting is per-installation and stored in ``settings``.

Plan: docs/superpowers/plans/2026-08-14-section-b-buildout.md (KC-B5-1..4)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence
from uuid import UUID

from identity import audit

logger = logging.getLogger(__name__)

#: Actions that need a second confirmer. Anything not named here is ordinary
#: work and is never queued — a guarded-action list that grows by accident is
#: how an approval queue becomes something people route around.
GUARDED_KINDS: frozenset[str] = frozenset(
    {
        "matter_delete",
        "acl_change",
        "bulk_export",
        "purge_all_documents",
        "ingestion_profile_change",
    }
)

#: Roles that may confirm someone else's request.
APPROVER_ROLES: frozenset[str] = frozenset({"approver", "admin"})

#: The role that may act without one.
BYPASS_ROLE = "admin"

SETTING_ADMIN_BYPASS = "approvals.admin_bypass"
DEFAULT_TTL = timedelta(hours=24)


class ApprovalError(Exception):
    """Base for refusals a person should see verbatim."""


class UnknownKindError(ApprovalError):
    """That action is not one of the guarded kinds."""


class UnknownRequestError(ApprovalError):
    """No such approval request."""


class SelfApprovalError(ApprovalError):
    """The requester may not confirm their own request."""


class NotAnApproverError(ApprovalError):
    """This account may not confirm other people's requests."""


class RequestExpiredError(ApprovalError):
    """The request sat too long and must be raised again."""


class InvalidTransitionError(ApprovalError):
    """The request is not in a state where that is possible."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ApprovalRequest:
    id: UUID
    kind: str
    target_ref: str
    payload: Mapping[str, Any]
    requested_by: UUID
    requested_at: datetime
    expires_at: datetime
    status: str
    approved_by: UUID | None
    approved_at: datetime | None
    decision_reason: str | None

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= _now()


_COLUMNS = (
    "id", "kind", "target_ref", "payload", "requested_by", "requested_at",
    "expires_at", "status", "approved_by", "approved_at", "decision_reason",
)


class ApprovalService:
    """The four-eyes workflow over ``approval_requests``."""

    def __init__(self, conn: Any, user_repo: Any) -> None:
        self._conn = conn
        self._users = user_repo

    # ── the bypass ─────────────────────────────────────────────────────────

    def admin_bypass_enabled(self) -> bool:
        row = self._conn.execute(
            "SELECT value FROM settings WHERE key = %s", (SETTING_ADMIN_BYPASS,)
        ).fetchone()
        if row is None:
            return True  # decided default; see the module docstring
        value = row[0]
        return bool(value if isinstance(value, bool) else value.get("enabled", True))

    def set_admin_bypass(self, enabled: bool, *, by: Any | None = None) -> None:
        self._conn.execute(
            "INSERT INTO settings (key, value, updated_by) VALUES (%s, %s, %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
            "updated_by = EXCLUDED.updated_by, updated_at = now()",
            (
                SETTING_ADMIN_BYPASS,
                json.dumps({"enabled": bool(enabled)}),
                str(by.id) if by is not None else None,
            ),
        )
        audit.record(
            self._conn,
            action="approvals.bypass_setting_changed",
            actor=by,
            detail={"enabled": bool(enabled)},
        )

    def requires_approval(self, kind: str, actor: Any) -> bool:
        """Whether ``actor`` must queue ``kind`` rather than doing it.

        False for an unguarded action, and false for an administrator while the
        bypass is on. Callers that get False and go on to act must still call
        :meth:`record_bypass` when the reason was the bypass — that call is what
        keeps the audit honest.
        """
        if kind not in GUARDED_KINDS:
            return False
        if BYPASS_ROLE in getattr(actor, "roles", frozenset()) and self.admin_bypass_enabled():
            return False
        return True

    def record_bypass(
        self,
        actor: Any,
        *,
        kind: str,
        target_ref: str,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        """Note that a guarded action ran without a second confirmer.

        Call this at the moment of the action, not before it. The row is what
        distinguishes "an administrator decided alone, knowingly" from "no
        control applied here", and those must not look the same in a log a firm
        produces in a supervisory proceeding.
        """
        audit.record(
            self._conn,
            action="approval.bypassed",
            actor=actor,
            target_type=kind,
            target_id=target_ref,
            detail={
                "kind": kind,
                "target_ref": target_ref,
                "reason": "administrator bypass",
                **dict(detail or {}),
            },
        )
        logger.info(
            "Four-eyes bypassed by administrator %s for %s on %s",
            getattr(actor, "email", "?"), kind, target_ref,
        )

    # ── the workflow ───────────────────────────────────────────────────────

    def request(
        self,
        actor: Any,
        *,
        kind: str,
        target_ref: str,
        payload: Mapping[str, Any] | None = None,
        ttl: timedelta = DEFAULT_TTL,
    ) -> ApprovalRequest:
        """Queue a guarded action for a second person to confirm."""
        if kind not in GUARDED_KINDS:
            raise UnknownKindError(
                f"{kind!r} is not a guarded action. Guarded: "
                f"{', '.join(sorted(GUARDED_KINDS))}."
            )
        row = self._conn.execute(
            "INSERT INTO approval_requests (kind, target_ref, payload, requested_by, "
            "expires_at, status) VALUES (%s, %s, %s, %s, %s, 'pending') "
            f"RETURNING {', '.join(_COLUMNS)}",
            (kind, target_ref, json.dumps(dict(payload or {})), str(actor.id), _now() + ttl),
        ).fetchone()
        created = self._to_request(row)
        audit.record(
            self._conn,
            action="approval.requested",
            actor=actor,
            target_type=kind,
            target_id=target_ref,
            detail={"request_id": str(created.id), "kind": kind},
        )
        return created

    def approve(self, request_id: UUID | str, approver: Any) -> ApprovalRequest:
        """Confirm someone else's request.

        Raises:
            SelfApprovalError: the requester and approver are the same person.
                The database enforces this too (``approval_requests.four_eyes``);
                both exist so a bug here cannot permit self-approval.
            NotAnApproverError: this account may not confirm.
            RequestExpiredError / InvalidTransitionError: wrong state.
        """
        existing = self._load(request_id)
        self._check_decidable(existing, approver)
        row = self._conn.execute(
            "UPDATE approval_requests SET status = 'approved', approved_by = %s, "
            f"approved_at = now() WHERE id = %s RETURNING {', '.join(_COLUMNS)}",
            (str(approver.id), str(existing.id)),
        ).fetchone()
        requester = self._users.get(existing.requested_by)
        audit.record(
            self._conn,
            action="approval.approved",
            actor=approver,
            target_type=existing.kind,
            target_id=existing.target_ref,
            detail={
                "request_id": str(existing.id),
                "requested_by_email": getattr(requester, "email", None),
            },
        )
        return self._to_request(row)

    def reject(
        self, request_id: UUID | str, approver: Any, *, reason: str
    ) -> ApprovalRequest:
        existing = self._load(request_id)
        self._check_decidable(existing, approver)
        row = self._conn.execute(
            "UPDATE approval_requests SET status = 'rejected', approved_by = %s, "
            "approved_at = now(), decision_reason = %s WHERE id = %s "
            f"RETURNING {', '.join(_COLUMNS)}",
            (str(approver.id), reason, str(existing.id)),
        ).fetchone()
        audit.record(
            self._conn,
            action="approval.rejected",
            actor=approver,
            target_type=existing.kind,
            target_id=existing.target_ref,
            detail={"request_id": str(existing.id), "reason": reason},
        )
        return self._to_request(row)

    def mark_executed(
        self, request_id: UUID | str, result: Mapping[str, Any]
    ) -> ApprovalRequest:
        """Record that an approved request has now been carried out, once."""
        existing = self._load(request_id)
        if existing.status != "approved":
            raise InvalidTransitionError(
                f"This request is {existing.status}; only an approved request can be "
                "carried out."
            )
        row = self._conn.execute(
            "UPDATE approval_requests SET status = 'executed', executed_at = now(), "
            f"execution_result = %s WHERE id = %s RETURNING {', '.join(_COLUMNS)}",
            (json.dumps(dict(result)), str(existing.id)),
        ).fetchone()
        return self._to_request(row)

    def pending(self) -> list[ApprovalRequest]:
        rows = self._conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM approval_requests "
            "WHERE status = 'pending' AND expires_at > now() ORDER BY requested_at"
        ).fetchall()
        return [self._to_request(r) for r in rows]

    def approved(self) -> list[ApprovalRequest]:
        """Confirmed but not yet carried out — newest decision first.

        This is what lets an approved request that a failed execution left
        stranded be found again and retried, instead of vanishing from every
        page once ``approve()`` has run.
        """
        rows = self._conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM approval_requests "
            "WHERE status = 'approved' ORDER BY approved_at DESC"
        ).fetchall()
        return [self._to_request(r) for r in rows]

    def expire_stale(self) -> int:
        """Mark timed-out requests expired. Returns how many."""
        rows = self._conn.execute(
            "UPDATE approval_requests SET status = 'expired' "
            "WHERE status = 'pending' AND expires_at <= now() RETURNING id"
        ).fetchall()
        return len(rows)

    # ── internals ──────────────────────────────────────────────────────────

    def _check_decidable(self, existing: ApprovalRequest, approver: Any) -> None:
        if existing.status != "pending":
            raise InvalidTransitionError(
                f"This request is already {existing.status}."
            )
        if existing.is_expired:
            raise RequestExpiredError(
                "This request has expired and must be raised again."
            )
        if str(existing.requested_by) == str(approver.id):
            raise SelfApprovalError(
                "You raised this request, so someone else has to confirm it."
            )
        if not (APPROVER_ROLES & getattr(approver, "roles", frozenset())):
            raise NotAnApproverError(
                "Confirming a colleague's request needs the approver or "
                "administrator role."
            )

    def _load(self, request_id: UUID | str) -> ApprovalRequest:
        row = self._conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM approval_requests WHERE id = %s",
            (str(request_id),),
        ).fetchone()
        if row is None:
            raise UnknownRequestError(f"No approval request {request_id}.")
        return self._to_request(row)

    @staticmethod
    def _to_request(row: Sequence[Any]) -> ApprovalRequest:
        values = dict(zip(_COLUMNS, row))
        return ApprovalRequest(**values)
