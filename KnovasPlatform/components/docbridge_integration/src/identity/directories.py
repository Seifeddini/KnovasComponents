"""Persisted Cortex views and card layouts.

Only presentation is stored locally. Node types, nodes, facts and their read
ACL remain owned by the Knowledge Graph.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable, Optional

SLUG_MAX = 64
TITLE_MAX = 200
_SLUG_SHAPE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_UMLAUTS = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}
_VIEW_COLUMNS = (
    "node_type_id", "slug", "title", "subtitle", "active", "position",
    "columns", "updated_at",
)


class DirectoryError(ValueError):
    """A directory setting cannot be saved."""


class SlugTakenError(DirectoryError):
    """A second view already uses the slug."""


class ViewExistsError(DirectoryError):
    """A node type already has a view."""


def slugify(value: Any, *, fallback: str = "verzeichnis") -> str:
    lowered = "".join(_UMLAUTS.get(ch, ch) for ch in str(value or "").lower())
    ascii_value = unicodedata.normalize("NFKD", lowered).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")[:SLUG_MAX].strip("-")
    return slug or fallback


def _clean_title(value: Any) -> str:
    title = " ".join(str(value or "").split())
    if not title:
        raise DirectoryError("Titel fehlt.")
    if len(title) > TITLE_MAX:
        raise DirectoryError(f"Titel ist länger als {TITLE_MAX} Zeichen.")
    return title


def _clean_columns(values: Any) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, (list, tuple)):
        raise DirectoryError("Spalten erwarten eine Liste.")
    result: list[str] = []
    for value in values:
        key = str(value or "").strip()
        if key and key not in result:
            result.append(key)
    return result


def _user_id(user: Any) -> Optional[str]:
    if user is None:
        return None
    return str(getattr(user, "id", user))


def _jsonb(value: Any) -> Any:
    """Import psycopg only for database writes.

    Pure graph projections and fixture-mode tests deliberately work without
    the optional identity dependency installed.
    """
    from psycopg.types.json import Jsonb

    return Jsonb(value)


class DirectoryStore:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    @staticmethod
    def _view(row: Optional[tuple]) -> Optional[dict]:
        if row is None:
            return None
        result = dict(zip(_VIEW_COLUMNS, row))
        result["columns"] = [str(value) for value in (result["columns"] or [])]
        result["updated_at"] = (
            result["updated_at"].isoformat() if result["updated_at"] else None
        )
        return result

    def views(self) -> list[dict]:
        rows = self._conn.execute(
            f"SELECT {', '.join(_VIEW_COLUMNS)} FROM node_type_views "
            "ORDER BY position, title"
        ).fetchall()
        return [self._view(row) for row in rows]

    def active_views(self) -> list[dict]:
        return [view for view in self.views() if view["active"]]

    def view_for_type(self, node_type_id: str) -> Optional[dict]:
        row = self._conn.execute(
            f"SELECT {', '.join(_VIEW_COLUMNS)} FROM node_type_views "
            "WHERE node_type_id = %s",
            (str(node_type_id),),
        ).fetchone()
        return self._view(row)

    def view_by_slug(self, slug: str) -> Optional[dict]:
        row = self._conn.execute(
            f"SELECT {', '.join(_VIEW_COLUMNS)} FROM node_type_views WHERE slug = %s",
            (str(slug),),
        ).fetchone()
        return self._view(row)

    def _checked_slug(self, value: Any, node_type_id: str) -> str:
        slug = slugify(value)
        if not _SLUG_SHAPE.fullmatch(slug):
            raise DirectoryError("Ungültige Verzeichnisadresse.")
        existing = self.view_by_slug(slug)
        if existing and existing["node_type_id"] != str(node_type_id):
            raise SlugTakenError(f"Die Adresse „{slug}“ wird bereits verwendet.")
        return slug

    def create_view(
        self,
        node_type_id: str,
        *,
        title: str,
        subtitle: str = "",
        slug: str = "",
        columns: Iterable[str] = (),
        by: Any = None,
    ) -> dict:
        type_id = str(node_type_id or "").strip()
        if not type_id:
            raise DirectoryError("Wissenstyp fehlt.")
        if self.view_for_type(type_id):
            raise ViewExistsError("Dieser Wissenstyp hat bereits ein Verzeichnis.")
        clean_title = _clean_title(title)
        clean_slug = self._checked_slug(slug or clean_title, type_id)
        position = self._conn.execute(
            "SELECT COALESCE(MAX(position), 0) + 1 FROM node_type_views"
        ).fetchone()[0]
        try:
            self._conn.execute(
                "INSERT INTO node_type_views "
                "(node_type_id, slug, title, subtitle, active, position, columns, updated_by) "
                "VALUES (%s, %s, %s, %s, TRUE, %s, %s, %s)",
                (
                    type_id,
                    clean_slug,
                    clean_title,
                    " ".join(str(subtitle or "").split())[:TITLE_MAX],
                    position,
                    _jsonb(_clean_columns(list(columns))),
                    _user_id(by),
                ),
            )
        except Exception as exc:
            try:
                from psycopg.errors import UniqueViolation
            except ImportError:
                raise
            if not isinstance(exc, UniqueViolation):
                raise
            raise SlugTakenError("Adresse oder Wissenstyp ist bereits vergeben.") from exc
        return self.view_for_type(type_id)

    def update_view(self, node_type_id: str, *, by: Any = None, **fields: Any) -> Optional[dict]:
        current = self.view_for_type(node_type_id)
        if current is None:
            return None
        values = dict(current)
        if "title" in fields:
            values["title"] = _clean_title(fields["title"])
        if "subtitle" in fields:
            values["subtitle"] = " ".join(str(fields["subtitle"] or "").split())[:TITLE_MAX]
        if str(fields.get("slug") or "").strip():
            values["slug"] = self._checked_slug(fields["slug"], node_type_id)
        if "columns" in fields:
            values["columns"] = _clean_columns(fields["columns"])
        if "active" in fields:
            values["active"] = bool(fields["active"])
        self._conn.execute(
            "UPDATE node_type_views SET slug=%s, title=%s, subtitle=%s, active=%s, "
            "columns=%s, updated_by=%s, updated_at=now() WHERE node_type_id=%s",
            (
                values["slug"],
                values["title"],
                values["subtitle"],
                values["active"],
                _jsonb(values["columns"]),
                _user_id(by),
                str(node_type_id),
            ),
        )
        return self.view_for_type(node_type_id)

    def card(self, node_type_id: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT layout FROM node_type_cards WHERE node_type_id=%s",
            (str(node_type_id),),
        ).fetchone()
        return None if row is None else dict(row[0] or {})

    def save_card(self, node_type_id: str, layout: dict, *, by: Any = None) -> dict:
        if not isinstance(layout, dict):
            raise DirectoryError("Der Kartenaufbau erwartet ein Objekt.")
        self._conn.execute(
            "INSERT INTO node_type_cards (node_type_id, layout, updated_by) VALUES (%s,%s,%s) "
            "ON CONFLICT (node_type_id) DO UPDATE SET layout=EXCLUDED.layout, "
            "updated_by=EXCLUDED.updated_by, updated_at=now()",
            (str(node_type_id), _jsonb(layout), _user_id(by)),
        )
        return self.card(node_type_id)
