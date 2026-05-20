"""Incremental sync state persisted locally."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from config import get_config

logger = logging.getLogger(__name__)


class SyncStateStore:
    def __init__(self, path: Optional[str] = None):
        self._path = Path(path or get_config().rc_sync_state_path)

    def _load_raw(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"files": {}}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("files"), dict):
                return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Corrupt sync state file, resetting: %s", type(exc).__name__)
        return {"files": {}}

    def file_key(self, relative_path: str, mtime_iso: str, size_bytes: int) -> str:
        return f"{relative_path}|{mtime_iso}|{size_bytes}"

    def should_skip(self, relative_path: str, mtime_iso: str, size_bytes: int) -> bool:
        data = self._load_raw()
        key = self.file_key(relative_path, mtime_iso, size_bytes)
        return key in data["files"]

    def record_upload(
        self, relative_path: str, mtime_iso: str, size_bytes: int, transmission_key_id: str
    ) -> None:
        data = self._load_raw()
        key = self.file_key(relative_path, mtime_iso, size_bytes)
        data["files"][key] = {
            "last_uploaded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "transmission_key_id": transmission_key_id,
        }
        self._save(data)

    def _save(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self._path)
            try:
                os.chmod(self._path, 0o600)
            except OSError:
                pass
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
