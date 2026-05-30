"""Incremental sync state persisted locally (per-document path)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

from config import get_config
from sync.sync_state_db import SyncStateDatabase, json_state_path_to_db

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


def status_from_fingerprint(
    stored: Optional[tuple[str, int]], mtime_iso: str, size_bytes: int
) -> DocumentSyncStatus:
    if stored is None:
        return "pending"
    if stored[0] == mtime_iso and stored[1] == size_bytes:
        return "synced"
    return "modified"


class SyncStateStore:
    """Tracks upload state per document path using mtime + size as content fingerprint."""

    def __init__(self, path: Optional[str] = None):
        json_path = Path(path or get_config().rc_sync_state_path)
        self._json_path = json_path
        self._db = SyncStateDatabase(json_state_path_to_db(json_path), json_path=json_path)
        self._fingerprints: Optional[dict[str, tuple[str, int]]] = None

    def load_fingerprints(self) -> dict[str, tuple[str, int]]:
        """Load all tracked fingerprints once per scan cycle."""
        if self._fingerprints is None:
            self._fingerprints = self._db.load_fingerprints()
        return self._fingerprints

    def lookup_stored(
        self, relative_path: str, fingerprints: dict[str, tuple[str, int]]
    ) -> Optional[tuple[str, int]]:
        return self._db.lookup_fingerprint(fingerprints, relative_path)

    def document_status(
        self,
        relative_path: str,
        mtime_iso: str,
        size_bytes: int,
        *,
        fingerprints: Optional[dict[str, tuple[str, int]]] = None,
    ) -> DocumentSyncStatus:
        """Return sync status for a file at its current fingerprint."""
        fp_map = fingerprints if fingerprints is not None else self.load_fingerprints()
        stored = self._db.lookup_fingerprint(fp_map, relative_path)
        return status_from_fingerprint(stored, mtime_iso, size_bytes)

    def file_key(self, relative_path: str, mtime_iso: str, size_bytes: int) -> str:
        """Legacy composite key (tests / backward compatibility)."""
        return f"{relative_path}|{mtime_iso}|{size_bytes}"

    def should_skip(self, relative_path: str, mtime_iso: str, size_bytes: int) -> bool:
        return self.document_status(relative_path, mtime_iso, size_bytes) == "synced"

    def record_upload(
        self,
        relative_path: str,
        mtime_iso: str,
        size_bytes: int,
        transmission_key_id: str,
    ) -> None:
        fp_map = self.load_fingerprints()
        self._db.record_upload(
            relative_path,
            mtime_iso,
            size_bytes,
            transmission_key_id,
            fingerprints=fp_map,
        )

    def record_skip(
        self,
        relative_path: str,
        mtime_iso: str,
        size_bytes: int,
        *,
        reason: str,
    ) -> None:
        """Mark a path handled so incremental sync does not retry it forever."""
        self.record_upload(relative_path, mtime_iso, size_bytes, f"skip:{reason}")

    def summarize(
        self,
        scanned: list[tuple[str, str, int]],
        *,
        include_documents: bool = False,
    ) -> DocumentSyncSummary:
        """Classify scanned files against persisted sync state."""
        fp_map = self.load_fingerprints()
        summary = DocumentSyncSummary()
        for rel, mtime_iso, size_bytes in scanned:
            status = self.document_status(
                rel, mtime_iso, size_bytes, fingerprints=fp_map
            )
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

    def count_tracked_paths(self) -> int:
        return self._db.count_tracked()

    def list_tracked_paths(self) -> list[str]:
        return self._db.list_tracked_paths()

    def close(self) -> None:
        self._db.close()
