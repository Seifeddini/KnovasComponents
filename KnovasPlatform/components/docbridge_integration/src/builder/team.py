"""Who may do what in the Brain Builder (build plan step B11).

Four builder roles, held like any Platform role for every workspace of the
tenant, or granted for one workspace only (an outside consultant's access,
which ends on its day by itself):

  builder_author     edits drafts, comments, resolves comments
  builder_reviewer   approves a draft (never one they changed), comments, resolves
  builder_publisher  applies an approved draft, comments
  builder_viewer     reads

``admin`` may do everything, including granting access. A route names the
permission it needs (builder_routes.ENDPOINT_PERMISSIONS); the blueprint checks
it on every request against the person's roles and their grant on the
workspace the request is about. The page hiding a control is not the check.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional

ROLES = ("builder_author", "builder_reviewer", "builder_publisher", "builder_viewer")
ROLE_LABELS = {"builder_author": "Autor", "builder_reviewer": "Prüfer", "builder_publisher": "Veröffentlicher",
               "builder_viewer": "Leser", "admin": "Administrator"}
PERMISSIONS = ("view", "edit", "comment", "resolve", "review", "publish", "manage")
PERMISSION_LABELS = {"view": "Lesen", "edit": "Bearbeiten", "comment": "Kommentieren", "resolve": "Kommentare erledigen",
                     "review": "Freigeben", "publish": "Veröffentlichen", "manage": "Zugänge verwalten"}
ROLE_PERMISSIONS: Mapping[str, frozenset[str]] = {
    "admin": frozenset(PERMISSIONS),
    "builder_author": frozenset({"view", "edit", "comment", "resolve"}),
    "builder_reviewer": frozenset({"view", "comment", "resolve", "review"}),
    "builder_publisher": frozenset({"view", "comment", "publish"}),
    "builder_viewer": frozenset({"view"}),
}
#: Roles that open the builder for every workspace.
BUILDER_ROLES = frozenset(ROLE_PERMISSIONS)
#: The longest a consultant's access may run.
MAX_GRANT_DAYS = 366


def permissions_of(roles: Iterable[str]) -> frozenset[str]:
    out: set[str] = set()
    for r in roles or ():
        out |= ROLE_PERMISSIONS.get(str(r), frozenset())
    return frozenset(out)


@dataclass(frozen=True)
class Access:
    """What a person may do on one workspace (or with none chosen), and why."""
    permissions: frozenset[str]
    roles: tuple[str, ...]
    #: "role" when a Platform role gives the access, "grant" when only the workspace grant does, None when nothing does.
    via: Optional[str]
    grant: Optional[Mapping[str, Any]] = None
    #: A grant on the workspace that has run out (its ends_on is past): the page says when.
    expired: Optional[Mapping[str, Any]] = None
    workspaces: frozenset[str] = field(default_factory=frozenset)

    def may(self, permission: str) -> bool:
        return permission in self.permissions


def access(user_roles: Iterable[str], grants: Iterable[Mapping[str, Any]], workspace_id: Optional[str],
           today: dt.date) -> Access:
    """The person's access: their Platform roles everywhere, and their grant on ``workspace_id`` while it runs."""
    own = tuple(r for r in user_roles or () if r in ROLE_PERMISSIONS)
    live = [g for g in grants if dt.date.fromisoformat(str(g["ends_on"])[:10]) >= today]
    granted_workspaces = frozenset(str(g["workspace"]) for g in live)
    grant = next((g for g in live if str(g["workspace"]) == str(workspace_id)), None) if workspace_id else None
    expired = None
    if workspace_id and grant is None:
        expired = next((g for g in grants if str(g["workspace"]) == str(workspace_id)), None)
    roles = own + tuple(r for r in (grant or {}).get("roles") or () if r not in own)
    perms = permissions_of(roles)
    via = "role" if own else ("grant" if grant else None)
    return Access(perms, roles, via, grant, expired, granted_workspaces)


def check_grant(roles: Any, ends_on: Any, today: dt.date) -> tuple[list[str], str]:
    """Validated roles and end date of a grant; ValueError with the reason for the page."""
    if not isinstance(roles, list) or not roles or any(r not in ROLES for r in roles):
        raise ValueError("Rollen: mindestens eine der Builder-Rollen.")
    try:
        day = dt.date.fromisoformat(str(ends_on or ""))
    except ValueError:
        raise ValueError("Ende: ein Datum wie 2026-12-31.") from None
    if day < today:
        raise ValueError("Ende: heute oder später.")
    if day > today + dt.timedelta(days=MAX_GRANT_DAYS):
        raise ValueError(f"Ende: höchstens {MAX_GRANT_DAYS} Tage im Voraus.")
    return sorted(dict.fromkeys(roles), key=ROLES.index), day.isoformat()
