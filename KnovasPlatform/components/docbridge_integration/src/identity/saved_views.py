"""Saved views of the Ansichten page: personal and shared presentation settings.

A view stores only how to draw (plan 2026-10-27, Step 5): name, description,
audience, source (a process or a spine key), render kind, and settings from a
fixed allowlist — default root, grouping threshold, hidden types, filters,
sorting, colours. Anything else is refused, so a view can never become a copy
of brain content.

Personal views: the owner reads and changes them; nobody else sees them.
Shared views: everyone signed in reads them; admins create, change, delete and
share — and every one of those changes is written to audit_log.

Rows are tuples, mapped by position, like the rest of identity/.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional
from uuid import UUID

import psycopg

from identity import audit

AUDIENCES = ("personal", "shared")
RENDER_KINDS = {"process": ("trace", "board"), "spine": ("tree", "matrix", "doc")}
SORTINGS = ("key", "name", "days", "missing", "risks")
FILTER_KEYS = ("owner", "capability", "status", "branch", "rows", "cols", "meta")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_KEY = re.compile(r"^(PROC|SPINE)-[a-z0-9-]{1,72}$")

_COLUMNS = ("id, audience, owner_user_id, name, description, source_kind, source_key, "
            "render_kind, settings, created_at, created_by, updated_at, updated_by")


class SavedViewError(Exception):
    status = 400


class InvalidView(SavedViewError):
    status = 400


class ViewNotFound(SavedViewError):
    status = 404


class ViewForbidden(SavedViewError):
    status = 403


class ViewNameTaken(SavedViewError):
    status = 409


def _uuid(value: Any) -> Optional[str]:
    try:
        return str(UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return None


def _text(value: Any, field: str, limit: int, *, required: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if required and not text:
        raise InvalidView(f"{field} fehlt.")
    if len(text) > limit:
        raise InvalidView(f"{field} ist länger als {limit} Zeichen.")
    return text


def clean_settings(raw: Any) -> Dict[str, Any]:
    """The presentation settings, checked against the allowlist."""
    if raw in (None, ""):
        return {}
    if not isinstance(raw, dict):
        raise InvalidView("settings muss ein Objekt sein.")
    unknown = set(raw) - {"default_root", "threshold", "hidden_types", "filters", "sorting", "colours", "sections"}
    if unknown:
        raise InvalidView(f"Unbekannte Einstellungen: {', '.join(sorted(unknown))}. "
                          "Eine Ansicht speichert nur Darstellung.")
    out: Dict[str, Any] = {}
    if raw.get("default_root"):
        ids = [p for p in str(raw["default_root"]).split(",") if p]
        if len(ids) > 8 or any(_uuid(p) is None for p in ids):
            raise InvalidView("default_root ist eine Knoten-Id oder ein Pfad aus höchstens 8 Ids.")
        out["default_root"] = ",".join(_uuid(p) for p in ids)
    if raw.get("threshold") not in (None, ""):
        try:
            n = int(raw["threshold"])
        except (TypeError, ValueError):
            raise InvalidView("threshold ist eine Zahl.") from None
        if not 1 <= n <= 50:
            raise InvalidView("threshold liegt zwischen 1 und 50.")
        out["threshold"] = n
    if raw.get("hidden_types"):
        types = raw["hidden_types"]
        if not isinstance(types, list) or len(types) > 60 or \
                any(not isinstance(t, str) or not 0 < len(t) <= 80 for t in types):
            raise InvalidView("hidden_types ist eine Liste von Typnamen.")
        out["hidden_types"] = sorted(set(types))
    if raw.get("filters"):
        filters = raw["filters"]
        if not isinstance(filters, dict) or set(filters) - set(FILTER_KEYS):
            raise InvalidView(f"filters kennt nur {', '.join(FILTER_KEYS)}.")
        cleaned = {}
        for k, v in filters.items():
            if k == "meta":
                cleaned[k] = bool(v)
            elif v not in (None, ""):
                cleaned[k] = _text(v, f"filters.{k}", 120)
        out["filters"] = cleaned
    if raw.get("sorting"):
        if raw["sorting"] not in SORTINGS:
            raise InvalidView(f"sorting ist eines von {', '.join(SORTINGS)}.")
        out["sorting"] = raw["sorting"]
    if raw.get("sections"):
        if raw["sections"] not in (["context"], ["context", "lanes"]):
            raise InvalidView("sections ist [\"context\"] (nur der Kontext) oder [\"context\", \"lanes\"].")
        out["sections"] = list(raw["sections"])
    if raw.get("colours"):
        colours = raw["colours"]
        if not isinstance(colours, dict) or len(colours) > 60 or \
                any(not isinstance(k, str) or not isinstance(v, str) or not _HEX.match(v) for k, v in colours.items()):
            raise InvalidView("colours ordnet Typnamen Farben wie #1a45c7 zu.")
        out["colours"] = {k: v.lower() for k, v in colours.items()}
    return out


def clean_view(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise InvalidView("Eine Ansicht ist ein Objekt.")
    source = data.get("source") or {}
    if not isinstance(source, dict) or len(source) != 1 or next(iter(source)) not in RENDER_KINDS:
        raise InvalidView("source ist {process: <Schlüssel>} oder {spine: <Schlüssel>}.")
    source_kind, source_key = next(iter(source.items()))
    source_key = str(source_key or "")
    if not _KEY.match(source_key):
        raise InvalidView("source nennt einen Prozess- (PROC-…) oder Achsenschlüssel (SPINE-…).")
    render_kind = data.get("render_kind")
    if render_kind not in RENDER_KINDS[source_kind]:
        raise InvalidView(f"Ein {'Prozess' if source_kind == 'process' else 'Achse'} wird als "
                          f"{' oder '.join(RENDER_KINDS[source_kind])} gezeigt.")
    return {"name": _text(data.get("name"), "name", 80, required=True),
            "description": _text(data.get("description"), "description", 500),
            "source_kind": source_kind, "source_key": source_key, "render_kind": render_kind,
            "settings": clean_settings(data.get("settings"))}


def _row(r) -> Dict[str, Any]:
    settings = r[8] if isinstance(r[8], dict) else json.loads(r[8] or "{}")
    return {"id": str(r[0]), "audience": r[1], "owner_user_id": str(r[2]) if r[2] else None,
            "name": r[3], "description": r[4], "source": {r[5]: r[6]}, "render_kind": r[7],
            "settings": settings, "created_at": r[9].isoformat() if r[9] else None,
            "created_by": str(r[10]) if r[10] else None,
            "updated_at": r[11].isoformat() if r[11] else None,
            "updated_by": str(r[12]) if r[12] else None}


def _is_admin(user: Any) -> bool:
    return "admin" in (getattr(user, "roles", ()) or ())


class SavedViewStore:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    # -- reading -------------------------------------------------------------

    def list_for(self, user: Any) -> List[Dict[str, Any]]:
        """The user's personal views and every shared view, shared first."""
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM saved_views "
            "WHERE audience = 'shared' OR owner_user_id = %s "
            "ORDER BY audience DESC, lower(name)", (str(user.id),)).fetchall()
        return [_row(r) for r in rows]

    def get(self, view_id: Any, user: Any) -> Dict[str, Any]:
        vid = _uuid(view_id)
        if vid is None:
            raise ViewNotFound("Ansicht nicht gefunden.")
        r = self._conn.execute(f"SELECT {_COLUMNS} FROM saved_views WHERE id = %s", (vid,)).fetchone()
        # Someone else's personal view answers like a missing one: its name
        # is nobody else's business.
        if r is None or (r[1] == "personal" and str(r[2]) != str(user.id)):
            raise ViewNotFound("Ansicht nicht gefunden.")
        return _row(r)

    # -- writing -------------------------------------------------------------

    def _may_change(self, view: Dict[str, Any], user: Any) -> None:
        if view["audience"] == "shared" and not _is_admin(user):
            raise ViewForbidden("Geteilte Ansichten ändern nur Administratoren.")

    def _audit(self, action: str, user: Any, view_id: str, **detail: Any) -> None:
        audit.record(self._conn, action=f"view.shared.{action}", actor=user,
                     target_type="saved_view", target_id=view_id, detail=detail)

    def create(self, user: Any, data: Any, *, audience: str = "personal") -> Dict[str, Any]:
        if audience not in AUDIENCES:
            raise InvalidView("audience ist personal oder shared.")
        if audience == "shared" and not _is_admin(user):
            raise ViewForbidden("Geteilte Ansichten legen nur Administratoren an.")
        v = clean_view(data)
        try:
            r = self._conn.execute(
                "INSERT INTO saved_views (audience, owner_user_id, name, description, source_kind, "
                "source_key, render_kind, settings, created_by, updated_by) "
                f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING {_COLUMNS}",
                (audience, str(user.id) if audience == "personal" else None, v["name"], v["description"],
                 v["source_kind"], v["source_key"], v["render_kind"], json.dumps(v["settings"]),
                 str(user.id), str(user.id))).fetchone()
        except psycopg.errors.UniqueViolation:
            raise ViewNameTaken(f"Es gibt schon eine Ansicht namens „{v['name']}“.") from None
        view = _row(r)
        if audience == "shared":
            self._audit("created", user, view["id"], name=view["name"], source=view["source"],
                        render_kind=view["render_kind"], settings=view["settings"])
        return view

    def update(self, view_id: Any, user: Any, data: Any) -> Dict[str, Any]:
        before = self.get(view_id, user)
        self._may_change(before, user)
        merged = {"name": before["name"], "description": before["description"],
                  "source": before["source"], "render_kind": before["render_kind"],
                  "settings": before["settings"]}
        if not isinstance(data, dict):
            raise InvalidView("Eine Änderung ist ein Objekt.")
        merged.update({k: v for k, v in data.items() if k in merged})
        v = clean_view(merged)
        try:
            r = self._conn.execute(
                "UPDATE saved_views SET name = %s, description = %s, source_kind = %s, source_key = %s, "
                "render_kind = %s, settings = %s, updated_at = now(), updated_by = %s "
                f"WHERE id = %s RETURNING {_COLUMNS}",
                (v["name"], v["description"], v["source_kind"], v["source_key"], v["render_kind"],
                 json.dumps(v["settings"]), str(user.id), before["id"])).fetchone()
        except psycopg.errors.UniqueViolation:
            raise ViewNameTaken(f"Es gibt schon eine Ansicht namens „{v['name']}“.") from None
        after = _row(r)
        if after["audience"] == "shared":
            changed = sorted(k for k in ("name", "description", "source", "render_kind", "settings")
                             if before[k] != after[k])
            self._audit("updated", user, after["id"], name=after["name"], changed=changed,
                        before={k: before[k] for k in changed}, after={k: after[k] for k in changed})
        return after

    def delete(self, view_id: Any, user: Any) -> None:
        view = self.get(view_id, user)
        self._may_change(view, user)
        self._conn.execute("DELETE FROM saved_views WHERE id = %s", (view["id"],))
        if view["audience"] == "shared":
            self._audit("deleted", user, view["id"], name=view["name"], source=view["source"],
                        render_kind=view["render_kind"])

    def share(self, view_id: Any, user: Any, *, name: Optional[str] = None) -> Dict[str, Any]:
        """A shared copy of one of the user's personal views (admins only)."""
        if not _is_admin(user):
            raise ViewForbidden("Mit dem Mandanten teilen nur Administratoren.")
        personal = self.get(view_id, user)
        if personal["audience"] != "personal":
            raise InvalidView("Diese Ansicht ist schon geteilt.")
        data = {k: personal[k] for k in ("name", "description", "source", "render_kind", "settings")}
        if name:
            data["name"] = name
        shared = self.create(user, data, audience="shared")
        self._audit("shared", user, shared["id"], name=shared["name"], from_personal=personal["id"])
        return shared
