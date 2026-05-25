"""Incremental sync state persisted locally (per-document path)."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from config import get_config

logger = logging.getLogger(__name__)

DocumentSyncStatus = Literal["synced", "pending", "modified", "excluded_max_age"]


@dataclass(frozen=True)
class DocumentSyncRecord:
    relative_path: str
    mtime_iso: str
    size_bytes: int
    status: DocumentSyncStatus


@dataclass
class DocumentSyncSummary:
    total: int = 0
    synced: int = 0
    pending: int = 0
    modified: int = 0
    excluded_max_age: int = 0
    documents: list[DocumentSyncRecord] = field(default_factory=list)

    def as_dict(self, *, include_documents: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "total": self.total,
            "synced": self.synced,
            "pending": self.pending,
            "modified": self.modified,
            "excluded_max_age": self.excluded_max_age,
        }
        if include_documents:
            out["documents"] = [
                {
                    "path": d.relative_path,
                    "status": d.status,
                    "mtime_iso": d.mtime_iso,
                    "size_bytes": d.size_bytes,
                }
                for d in self.documents
            ]
        return out


class SyncStateStore:
    """Tracks upload state per document path using mtime + size as content fingerprint."""

    def __init__(self, path: Optional[str] = None):
        self._path = Path(path or get_config().rc_sync_state_path)

    def _load_raw(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"documents": {}}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return self._normalize(data)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Corrupt sync state file, resetting: %s", type(exc).__name__)
        return {"documents": {}}

    def _normalize(self, data: dict[str, Any]) -> dict[str, Any]:
        documents = data.get("documents")
        if not isinstance(documents, dict):
            documents = {}
        legacy = data.get("files")
        if isinstance(legacy, dict) and legacy:
            for key, meta in legacy.items():
                if not isinstance(meta, dict):
                    continue
                parts = key.split("|", 2)
                if len(parts) != 3:
                    continue
                rel_path, mtime_iso, size_str = parts
                try:
                    size_bytes = int(size_str)
                except ValueError:
                    continue
                documents[rel_path] = {
                    "mtime_iso": mtime_iso,
                    "size_bytes": size_bytes,
                    "last_uploaded_at": meta.get("last_uploaded_at"),
                    "transmission_key_id": meta.get("transmission_key_id"),
                }
        return {"documents": documents}

    def _documents(self, data: dict[str, Any]) -> dict[str, Any]:
        docs = data.get("documents")
        return docs if isinstance(docs, dict) else {}

    def document_status(
        self, relative_path: str, mtime_iso: str, size_bytes: int
    ) -> DocumentSyncStatus:
        """Return sync status for a file at its current fingerprint."""
        entry = self._documents(self._load_raw()).get(relative_path)
        if not entry:
            return "pending"
        if (
            entry.get("mtime_iso") == mtime_iso
            and entry.get("size_bytes") == size_bytes
        ):
            return "synced"
        return "modified"

    def file_key(self, relative_path: str, mtime_iso: str, size_bytes: int) -> str:
        """Legacy composite key (tests / backward compatibility)."""
        return f"{relative_path}|{mtime_iso}|{size_bytes}"

    def should_skip(self, relative_path: str, mtime_iso: str, size_bytes: int) -> bool:
        return self.document_status(relative_path, mtime_iso, size_bytes) == "synced"

    def record_upload(
        self, relative_path: str, mtime_iso: str, size_bytes: int, transmission_key_id: str
    ) -> None:
        data = self._load_raw()
        docs = self._documents(data)
        docs[relative_path] = {
            "mtime_iso": mtime_iso,
            "size_bytes": size_bytes,
            "last_uploaded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "transmission_key_id": transmission_key_id,
        }
        data["documents"] = docs
        if "files" in data:
            del data["files"]
        self._save(data)

    def summarize(
        self,
        scanned: list[tuple[str, str, int]],
        *,
        include_documents: bool = False,
    ) -> DocumentSyncSummary:
        """Classify scanned files against persisted sync state."""
        summary = DocumentSyncSummary()
        for rel, mtime_iso, size_bytes in scanned:
            status = self.document_status(rel, mtime_iso, size_bytes)
            summary.total += 1
            if status == "synced":
                summary.synced += 1
            elif status == "pending":
                summary.pending += 1
            else:
                summary.modified += 1
            if include_documents:
                summary.documents.append(
                    DocumentSyncRecord(
                        relative_path=rel,
                        mtime_iso=mtime_iso,
                        size_bytes=size_bytes,
                        status=status,
                    )
                )
        return summary

    def list_tracked_paths(self) -> list[str]:
        return sorted(self._documents(self._load_raw()).keys())

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
