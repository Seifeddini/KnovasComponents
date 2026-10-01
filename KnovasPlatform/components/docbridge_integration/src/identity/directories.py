"""How this installation presents its knowledge-graph node types.

Three things, all presentation and all local to the installation:

* **Directories** (``node_type_views``): a page in the navigation listing every
  entry of one type, with chosen fields as columns.
* **Card layouts** (``node_type_cards``): which fields of a type stand in the
  card's header, which in its rail, and how the rest is grouped into sections.
* **Dismissed suggestions** (``graph_suggestion_dismissals``): proposals an
  administrator turned down, so they never come back.

Types, fields and values are the Knowledge Graph's and are never copied here.
Rows are psycopg 3 tuples, mapped by position like ``identity/users.py``.

Design: the "Wissenstypen & Verzeichnisse" artifact (SS-315 follow-up).
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any, Iterable, Optional

import psycopg
from psycopg.types.json import Jsonb

SLUG_MAX = 64
TITLE_MAX = 200

_UMLAUTS = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}
_SLUG_SHAPE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_VIEW_COLUMNS = ("node_type_id", "slug", "title", "subtitle", "active", "position",
                 "columns", "updated_at")


class DirectoryError(ValueError):
    """A directory setting cannot be saved. The message is for a person."""


class SlugTakenError(DirectoryError):
    """Another directory already answers at this address."""


class ViewExistsError(DirectoryError):
    """The type already has a directory; a type carries at most one."""


def slugify(text: Any, *, fallback: str = "liste") -> str:
    """An address segment from a title: lower case, ASCII, words joined by '-'.

    Umlauts are spelled out (Mandatsübersicht -> mandatsuebersicht) rather than
    stripped, because "mandatsbersicht" is not a word anyone would type.
    """
    lowered = "".join(_UMLAUTS.get(ch, ch) for ch in str(text or "").lower())
    ascii_only = unicodedata.normalize("NFKD", lowered).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_only).strip("-")[:SLUG_MAX].strip("-")
    return slug or fallback


def _clean_title(value: Any, what: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise DirectoryError(f"{what} fehlt.")
    if len(text) > TITLE_MAX:
        raise DirectoryError(f"{what} ist länger als {TITLE_MAX} Zeichen.")
    return text


def _clean_columns(columns: Any) -> list[str]:
    if columns is None:
        return []
    if not isinstance(columns, (list, tuple)):
        raise DirectoryError("Spalten erwarten eine Liste von Feldern.")
    seen: list[str] = []
    for column in columns:
        key = str(column or "").strip()
        if key and key not in seen:
            seen.append(key)
    return seen


def suggestion_key(suggestion_id: str) -> str:
    return hashlib.sha256(str(suggestion_id).encode("utf-8")).hexdigest()


class DirectoryStore:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    # ── directories ───────────────────────────────────────────────────────

    @staticmethod
    def _view(row: Optional[tuple]) -> Optional[dict]:
        if row is None:
            return None
        view = dict(zip(_VIEW_COLUMNS, row))
        view["columns"] = [str(c) for c in (view["columns"] or [])]
        view["updated_at"] = view["updated_at"].isoformat() if view["updated_at"] else None
        return view

    def views(self) -> list[dict]:
        """Every directory, in navigation order."""
        rows = self._conn.execute(
            f"SELECT {', '.join(_VIEW_COLUMNS)} FROM node_type_views "
            "ORDER BY position, title").fetchall()
        return [self._view(row) for row in rows]

    def active_views(self) -> list[dict]:
        return [view for view in self.views() if view["active"]]

    def view_for_type(self, node_type_id: str) -> Optional[dict]:
        return self._view(self._conn.execute(
            f"SELECT {', '.join(_VIEW_COLUMNS)} FROM node_type_views "
            "WHERE node_type_id = %s", (str(node_type_id),)).fetchone())

    def view_by_slug(self, slug: str) -> Optional[dict]:
        return self._view(self._conn.execute(
            f"SELECT {', '.join(_VIEW_COLUMNS)} FROM node_type_views WHERE slug = %s",
            (str(slug),)).fetchone())

    def _checked_slug(self, slug: Any, node_type_id: str) -> str:
        wanted = slugify(slug)
        if not _SLUG_SHAPE.match(wanted):
            raise DirectoryError("Die Adresse darf nur Kleinbuchstaben, Ziffern und '-' enthalten.")
        holder = self.view_by_slug(wanted)
        if holder is not None and holder["node_type_id"] != str(node_type_id):
            # One address, one page. A link that points at two different lists
            # depending on when it was saved is not a link.
            raise SlugTakenError(
                f"Die Adresse „{wanted}“ gehört bereits zu „{holder['title']}“. "
                "Bitte eine andere wählen.")
        return wanted

    def create_view(self, node_type_id: str, *, title: str, subtitle: str = "",
                    slug: str = "", columns: Iterable[str] = (), by: Any = None) -> dict:
        type_id = str(node_type_id or "").strip()
        if not type_id:
            raise DirectoryError("Wissenstyp fehlt.")
        if self.view_for_type(type_id) is not None:
            raise ViewExistsError(
                "Dieser Wissenstyp hat bereits ein Verzeichnis. Ein Typ trägt höchstens eines.")
        clean_title = _clean_title(title, "Titel")
        clean_slug = self._checked_slug(slug or clean_title, type_id)
        position = self._conn.execute(
            "SELECT COALESCE(MAX(position), 0) + 1 FROM node_type_views").fetchone()[0]
        try:
            self._conn.execute(
                "INSERT INTO node_type_views (node_type_id, slug, title, subtitle, active, "
                "position, columns, updated_by) VALUES (%s, %s, %s, %s, TRUE, %s, %s, %s)",
                (type_id, clean_slug, clean_title, " ".join(str(subtitle or "").split())[:TITLE_MAX],
                 position, Jsonb(_clean_columns(list(columns))), _user_id(by)))
        except psycopg.errors.UniqueViolation as exc:
            # A concurrent save won the race for the type or the address.
            raise SlugTakenError("Adresse oder Wissenstyp ist bereits vergeben.") from exc
        return self.view_for_type(type_id)

    def update_view(self, node_type_id: str, *, by: Any = None, **fields: Any) -> Optional[dict]:
        """Change title, subtitle, slug, columns or active. None if there is no
        directory for this type. The type of a directory never changes: its
        columns and its card belong to that type's fields."""
        current = self.view_for_type(node_type_id)
        if current is None:
            return None
        values = dict(current)
        if "title" in fields:
            values["title"] = _clean_title(fields["title"], "Titel")
        if "subtitle" in fields:
            values["subtitle"] = " ".join(str(fields["subtitle"] or "").split())[:TITLE_MAX]
        if "slug" in fields and str(fields["slug"] or "").strip():
            values["slug"] = self._checked_slug(fields["slug"], current["node_type_id"])
        if "columns" in fields:
            values["columns"] = _clean_columns(fields["columns"])
        if "active" in fields:
            values["active"] = bool(fields["active"])
        try:
            self._conn.execute(
                "UPDATE node_type_views SET slug = %s, title = %s, subtitle = %s, "
                "active = %s, columns = %s, updated_by = %s, updated_at = now() "
                "WHERE node_type_id = %s",
                (values["slug"], values["title"], values["subtitle"], values["active"],
                 Jsonb(values["columns"]), _user_id(by), current["node_type_id"]))
        except psycopg.errors.UniqueViolation as exc:
            raise SlugTakenError("Diese Adresse ist bereits vergeben.") from exc
        return self.view_for_type(node_type_id)

    def delete_view(self, node_type_id: str) -> bool:
        """Remove the directory. The card layout stays: it describes the entries,
        and they are untouched by a directory going away."""
        removed = self._conn.execute(
            "DELETE FROM node_type_views WHERE node_type_id = %s RETURNING node_type_id",
            (str(node_type_id),)).fetchall()
        return bool(removed)

    def move_view(self, node_type_id: str, delta: int) -> bool:
        """Swap with the neighbour above (-1) or below (+1).

        Swapping two positions rather than renumbering leaves every other
        directory exactly where it was.
        """
        ordered = [view["node_type_id"] for view in self.views()]
        if str(node_type_id) not in ordered or delta not in (-1, 1):
            return False
        index = ordered.index(str(node_type_id))
        other = index + delta
        if other < 0 or other >= len(ordered):
            return False
        # Positions may tie (two rows written before this code existed); the
        # rewrite gives every row its visible rank so a swap always moves.
        ordered[index], ordered[other] = ordered[other], ordered[index]
        for rank, type_id in enumerate(ordered, start=1):
            self._conn.execute(
                "UPDATE node_type_views SET position = %s WHERE node_type_id = %s",
                (rank, type_id))
        return True

    # ── card layouts ──────────────────────────────────────────────────────

    def card(self, node_type_id: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT layout FROM node_type_cards WHERE node_type_id = %s",
            (str(node_type_id),)).fetchone()
        return None if row is None else dict(row[0] or {})

    def save_card(self, node_type_id: str, layout: dict, *, by: Any = None) -> dict:
        if not isinstance(layout, dict):
            raise DirectoryError("Der Kartenaufbau erwartet ein Objekt.")
        self._conn.execute(
            "INSERT INTO node_type_cards (node_type_id, layout, updated_by) "
            "VALUES (%s, %s, %s) ON CONFLICT (node_type_id) DO UPDATE SET "
            "layout = EXCLUDED.layout, updated_by = EXCLUDED.updated_by, updated_at = now()",
            (str(node_type_id), Jsonb(layout), _user_id(by)))
        return self.card(node_type_id)

    # ── dismissed suggestions ─────────────────────────────────────────────

    def dismissed(self, suggestion_ids: Iterable[str]) -> set[str]:
        """Which of these suggestion ids an administrator already turned down."""
        by_key = {suggestion_key(s): s for s in suggestion_ids}
        if not by_key:
            return set()
        rows = self._conn.execute(
            "SELECT suggestion_key FROM graph_suggestion_dismissals "
            "WHERE suggestion_key = ANY(%s)", (list(by_key),)).fetchall()
        return {by_key[row[0]] for row in rows if row[0] in by_key}

    def dismiss(self, suggestion_id: str, *, by: Any = None) -> None:
        self._conn.execute(
            "INSERT INTO graph_suggestion_dismissals (suggestion_key, dismissed_by) "
            "VALUES (%s, %s) ON CONFLICT (suggestion_key) DO NOTHING",
            (suggestion_key(suggestion_id), _user_id(by)))


def _user_id(user: Any) -> Optional[str]:
    """A user, a user id, or nothing -- as the text of a UUID column."""
    if user is None:
        return None
    return str(getattr(user, "id", user))
