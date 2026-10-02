"""The versioned ingestion profile -- the only artifact a person edits.

Every save is a new row; the previous current row is superseded, never
updated. Restore copies an old version forward as a new one, so "what was
running on Tuesday" is always a row and never a diff.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Mapping
from uuid import UUID

from identity.ingestion_compiler import IngestionProfile, SourceFolder

_COLUMNS = ("id", "name", "version", "profile", "is_current", "created_at",
            "created_by", "approved_by", "pushed_at")


def _source_to_json(source: SourceFolder) -> dict[str, Any]:
    """One ``sources[]`` entry. The document-field keys are written only when
    set, so a profile without them stores (and compares, see apply_profile)
    exactly as it did before they existed."""
    entry: dict[str, Any] = {"path": source.path, "recursive": bool(source.recursive),
                             "access_groups": list(source.access_groups)}
    if source.fields:
        entry["fields"] = source.fields_json()
    if source.field_templates:
        entry["field_templates"] = list(source.field_templates)
    if source.metadata_fields:
        entry["metadata_fields"] = list(source.metadata_fields)
    return entry


def _source_from_json(s: Mapping[str, Any]) -> SourceFolder:
    """Field by field, as before: an unknown key is ignored, and the
    document-field keys default to empty for a row saved before them. A
    shape this code does not understand reads as "not set" rather than
    failing the whole profile."""
    raw_fields = s.get("fields")
    raw_templates = s.get("field_templates")
    raw_items = s.get("metadata_fields")
    return SourceFolder(
        path=str(s["path"]), recursive=bool(s.get("recursive", True)),
        access_groups=tuple(str(g) for g in (s.get("access_groups") or ())),
        fields=raw_fields if isinstance(raw_fields, Mapping) else (),
        field_templates=(tuple(str(t) for t in raw_templates)
                         if isinstance(raw_templates, (list, tuple)) else ()),
        metadata_fields=(tuple(str(m) for m in raw_items)
                         if isinstance(raw_items, (list, tuple)) else ()),
    )


def profile_to_json(profile: IngestionProfile) -> dict[str, Any]:
    data = asdict(profile)
    # Document-field settings live inside sources[] only. A new top-level
    # key would crash an older Platform reading this row back through
    # IngestionProfile(**fields) (spec 2.5, "Platform downgrade").
    data["sources"] = [_source_to_json(s) for s in profile.sources]
    return data


def profile_from_json(data: Mapping[str, Any]) -> IngestionProfile:
    fields = dict(data)
    fields["sources"] = [_source_from_json(s) for s in fields.get("sources") or []]
    return IngestionProfile(**fields)


@dataclass(frozen=True)
class ProfileVersion:
    id: UUID
    name: str
    version: int
    profile: IngestionProfile
    is_current: bool
    created_at: datetime
    created_by: UUID | None
    approved_by: UUID | None
    pushed_at: datetime | None


class IngestionProfileRepository:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def _row(self, row) -> ProfileVersion:
        d = dict(zip(_COLUMNS, row))
        raw = d["profile"] if isinstance(d["profile"], dict) else json.loads(d["profile"])
        d["profile"] = profile_from_json(raw)
        return ProfileVersion(**d)

    def current(self, name: str = "default") -> ProfileVersion | None:
        row = self._conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM ingestion_profiles "
            "WHERE name = %s AND is_current", (name,)
        ).fetchone()
        return None if row is None else self._row(row)

    def versions(self, name: str = "default") -> list[ProfileVersion]:
        rows = self._conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM ingestion_profiles "
            "WHERE name = %s ORDER BY version DESC", (name,)
        ).fetchall()
        return [self._row(r) for r in rows]

    def save_new_version(self, profile: IngestionProfile, *, name: str = "default",
                         by: Any, approved_by: Any | None = None) -> ProfileVersion:
        with self._conn.transaction():
            self._conn.execute(
                "UPDATE ingestion_profiles SET is_current = FALSE WHERE name = %s AND is_current",
                (name,),
            )
            (next_version,) = self._conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM ingestion_profiles WHERE name = %s",
                (name,),
            ).fetchone()
            row = self._conn.execute(
                "INSERT INTO ingestion_profiles (name, version, profile, is_current, "
                "created_by, approved_by) VALUES (%s, %s, %s, TRUE, %s, %s) "
                f"RETURNING {', '.join(_COLUMNS)}",
                (name, next_version, json.dumps(profile_to_json(profile)),
                 str(by.id), None if approved_by is None else str(approved_by.id)),
            ).fetchone()
        return self._row(row)

    def mark_pushed(self, version_id: UUID | str) -> None:
        self._conn.execute(
            "UPDATE ingestion_profiles SET pushed_at = now() WHERE id = %s", (str(version_id),)
        )

    def restore(self, name: str, version: int, *, by: Any) -> ProfileVersion:
        row = self._conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM ingestion_profiles WHERE name = %s AND version = %s",
            (name, int(version)),
        ).fetchone()
        if row is None:
            raise LookupError(f"Profil {name!r} hat keine Version {version}.")
        return self.save_new_version(self._row(row).profile, name=name, by=by)
