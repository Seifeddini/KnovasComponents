"""Sequential top-level subfolder cursor for large archive sync."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from sync.sync_state_db import SyncStateDatabase, json_state_path_to_db
from config import get_config

logger = logging.getLogger(__name__)

_FOLDER_QUEUE_SCHEMA = """
CREATE TABLE IF NOT EXISTS folder_queue (
    source_root TEXT PRIMARY KEY NOT NULL,
    subfolder_names TEXT NOT NULL,
    current_index INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT
);
"""


@dataclass(frozen=True)
class SubfolderProgress:
    source_root: str
    current_subfolder: Optional[str]
    current_index: int
    total_subfolders: int
    completed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_root": self.source_root,
            "current_subfolder": self.current_subfolder,
            "current_index": self.current_index,
            "total_subfolders": self.total_subfolders,
            "completed": self.completed,
        }


def _list_immediate_subdirs(root: Path) -> list[str]:
    names: list[str] = []
    try:
        with os.scandir(root) as it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        names.append(entry.name)
                except OSError:
                    continue
    except OSError as exc:
        logger.warning("Cannot list subfolders under %s: %s", root, exc)
        return []
    return sorted(names)


class SubfolderQueue:
    def __init__(self, db: SyncStateDatabase):
        self._db = db
        conn = db._connect()
        conn.executescript(_FOLDER_QUEUE_SCHEMA)

    @classmethod
    def from_config(cls) -> SubfolderQueue:
        json_path = Path(get_config().rc_sync_state_path)
        db = SyncStateDatabase(json_state_path_to_db(json_path), json_path=json_path)
        return cls(db)

    def close(self) -> None:
        self._db.close()

    def _load_row(self, source_root: str) -> Optional[tuple[list[str], int]]:
        conn = self._db._connect()
        row = conn.execute(
            "SELECT subfolder_names, current_index FROM folder_queue WHERE source_root = ?",
            (source_root,),
        ).fetchone()
        if not row:
            return None
        try:
            names = json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            names = []
        if not isinstance(names, list):
            names = []
        return [str(n) for n in names], int(row[1])

    def _save_row(self, source_root: str, names: list[str], index: int) -> None:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        conn = self._db._connect()
        conn.execute(
            """
            INSERT OR REPLACE INTO folder_queue (source_root, subfolder_names, current_index, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (source_root, json.dumps(names), index, now),
        )
        conn.commit()

    def refresh(self, source_root: Path) -> list[str]:
        key = str(source_root)
        names = _list_immediate_subdirs(source_root)
        row = self._load_row(key)
        index = row[1] if row else 0
        if index >= len(names):
            index = max(0, len(names) - 1) if names else 0
        self._save_row(key, names, index)
        return names

    def progress(self, source_root: Path) -> SubfolderProgress:
        key = str(source_root)
        row = self._load_row(key)
        if row is None:
            names = self.refresh(source_root)
            row = (names, 0)
        names, index = row
        if not names:
            return SubfolderProgress(key, None, 0, 0, True)
        if index >= len(names):
            return SubfolderProgress(key, None, index, len(names), True)
        return SubfolderProgress(key, names[index], index, len(names), False)

    def current_path(self, source_root: Path) -> Optional[Path]:
        prog = self.progress(source_root)
        if prog.completed or prog.current_subfolder is None:
            return None
        return source_root / prog.current_subfolder

    def maybe_advance(
        self,
        source_root: Path,
        *,
        pending: int,
        modified: int,
        scan_truncated: bool,
        paused_reason: Optional[str],
    ) -> bool:
        """Advance to next subfolder when current one has no remaining work."""
        if scan_truncated:
            return False
        if paused_reason in ("stop_requested", "outside_window", "rate_limited", "scan_limit_reached"):
            return False
        if pending > 0 or modified > 0:
            return False

        key = str(source_root)
        row = self._load_row(key)
        if row is None:
            return False
        names, index = row
        if index >= len(names) - 1:
            self._save_row(key, names, len(names))
            logger.info("Sequential subfolder sync complete for %s (%d folders)", key, len(names))
            return True
        new_index = index + 1
        self._save_row(key, names, new_index)
        logger.info(
            "Advanced sequential subfolder sync for %s -> %s (%d/%d)",
            key,
            names[new_index],
            new_index + 1,
            len(names),
        )
        return True
