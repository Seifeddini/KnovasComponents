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


def profile_to_json(profile: IngestionProfile) -> dict[str, Any]:
    data = asdict(profile)
    data["sources"] = [
        {"path": s.path, "recursive": bool(s.recursive),
         "access_groups": list(s.access_groups)}
        for s in profile.sources
    ]
    return data


def profile_from_json(data: Mapping[str, Any]) -> IngestionProfile:
    fields = dict(data)
    fields["sources"] = [
        SourceFolder(path=str(s["path"]), recursive=bool(s.get("recursive", True)),
                     access_groups=tuple(str(g) for g in (s.get("access_groups") or ())))
        for s in fields.get("sources") or []
    ]
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
