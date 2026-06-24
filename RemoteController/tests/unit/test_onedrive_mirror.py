"""Unit tests for the OneDrive mirror.

Network-free: GraphClient calls are stubbed via a fake passed into
OneDriveMirror. Covers: download-on-new, skip-on-unchanged, prune-deleted,
extension filter, oversize filter, mtime preservation, and path-escape guard.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import pytest

from onedrive_mirror.mirror import OneDriveMirror


class FakeGraph:
    """Minimal in-memory Graph stand-in for OneDriveMirror."""

    def __init__(self, root_children, children_by_id, file_bytes):
        self._root_children = root_children
        self._children_by_id = children_by_id
        self._file_bytes = file_bytes
        self.download_calls = 0

    def test_drive(self, drive_id):
        return None

    def list_root_children(self, drive_id, root_path) -> Iterator[dict]:
        yield from self._root_children

    def list_children_by_id(self, drive_id, item_id) -> Iterator[dict]:
        yield from self._children_by_id.get(item_id, [])

    def download_to(self, drive_id, item_id, dest_path) -> int:
        self.download_calls += 1
        data = self._file_bytes[item_id]
        with open(dest_path, "wb") as fh:
            fh.write(data)
        return len(data)


def _file(name, item_id, size, last_modified_iso, mime="text/plain"):
    return {
        "name": name,
        "id": item_id,
        "size": size,
        "lastModifiedDateTime": last_modified_iso,
        "file": {"mimeType": mime},
    }


def _folder(name, item_id):
    return {"name": name, "id": item_id, "folder": {"childCount": 0}}


def test_downloads_new_file_and_sets_mtime(tmp_path: Path):
    iso = "2026-06-24T10:00:00Z"
    fake = FakeGraph(
        root_children=[_file("doc.txt", "id1", 5, iso)],
        children_by_id={},
        file_bytes={"id1": b"hello"},
    )
    mirror = OneDriveMirror(
        client=fake,
        drive_id="drive",
        root_path="/Adiuvat",
        local_root=tmp_path / "mirror",
    )
    stats = mirror.run_once()

    dest = tmp_path / "mirror" / "doc.txt"
    assert dest.exists()
    assert dest.read_bytes() == b"hello"
    assert stats.downloaded == 1
    assert stats.skipped_unchanged == 0
    assert fake.download_calls == 1

    expected = datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    assert abs(dest.stat().st_mtime - expected) < 1


def test_second_pass_skips_unchanged(tmp_path: Path):
    iso = "2026-06-24T10:00:00Z"
    fake = FakeGraph(
        root_children=[_file("doc.txt", "id1", 5, iso)],
        children_by_id={},
        file_bytes={"id1": b"hello"},
    )
    mirror = OneDriveMirror(
        client=fake,
        drive_id="drive",
        root_path="",
        local_root=tmp_path / "mirror",
    )
    mirror.run_once()
    stats = mirror.run_once()
    assert stats.downloaded == 0
    assert stats.skipped_unchanged == 1
    assert fake.download_calls == 1  # not re-downloaded


def test_redownload_when_remote_newer(tmp_path: Path):
    older = "2026-06-24T10:00:00Z"
    fake = FakeGraph(
        root_children=[_file("doc.txt", "id1", 5, older)],
        children_by_id={},
        file_bytes={"id1": b"hello"},
    )
    mirror = OneDriveMirror(
        client=fake,
        drive_id="drive",
        root_path="",
        local_root=tmp_path / "mirror",
    )
    mirror.run_once()

    # Remote bumped — RC should re-download
    newer = "2026-06-25T10:00:00Z"
    fake._root_children = [_file("doc.txt", "id1", 6, newer)]
    fake._file_bytes["id1"] = b"hello!"
    stats = mirror.run_once()
    assert stats.downloaded == 1
    assert (tmp_path / "mirror" / "doc.txt").read_bytes() == b"hello!"


def test_prunes_files_no_longer_remote(tmp_path: Path):
    iso = "2026-06-24T10:00:00Z"
    fake = FakeGraph(
        root_children=[
            _file("keep.txt", "k1", 4, iso),
            _file("drop.txt", "k2", 4, iso),
        ],
        children_by_id={},
        file_bytes={"k1": b"keep", "k2": b"drop"},
    )
    mirror = OneDriveMirror(
        client=fake,
        drive_id="drive",
        root_path="",
        local_root=tmp_path / "mirror",
    )
    mirror.run_once()
    assert (tmp_path / "mirror" / "drop.txt").exists()

    # Second pass — drop.txt no longer in remote
    fake._root_children = [_file("keep.txt", "k1", 4, iso)]
    stats = mirror.run_once()
    assert stats.deleted_locally == 1
    assert not (tmp_path / "mirror" / "drop.txt").exists()
    assert (tmp_path / "mirror" / "keep.txt").exists()


def test_extension_filter_skips_unsupported(tmp_path: Path):
    iso = "2026-06-24T10:00:00Z"
    fake = FakeGraph(
        root_children=[
            _file("doc.txt", "ok", 4, iso),
            _file("photo.png", "no", 4, iso, mime="image/png"),
        ],
        children_by_id={},
        file_bytes={"ok": b"text"},
    )
    mirror = OneDriveMirror(
        client=fake,
        drive_id="drive",
        root_path="",
        local_root=tmp_path / "mirror",
        allowed_extensions=["txt", ".pdf"],
    )
    stats = mirror.run_once()
    assert stats.downloaded == 1
    assert stats.skipped_extension == 1
    assert (tmp_path / "mirror" / "doc.txt").exists()
    assert not (tmp_path / "mirror" / "photo.png").exists()


def test_oversize_filter(tmp_path: Path):
    iso = "2026-06-24T10:00:00Z"
    fake = FakeGraph(
        root_children=[_file("big.txt", "b1", 200, iso)],
        children_by_id={},
        file_bytes={"b1": b"x" * 200},
    )
    mirror = OneDriveMirror(
        client=fake,
        drive_id="drive",
        root_path="",
        local_root=tmp_path / "mirror",
        max_file_size_bytes=100,
    )
    stats = mirror.run_once()
    assert stats.downloaded == 0
    assert stats.skipped_oversize == 1
    assert not (tmp_path / "mirror" / "big.txt").exists()


def test_recurses_into_folders(tmp_path: Path):
    iso = "2026-06-24T10:00:00Z"
    fake = FakeGraph(
        root_children=[_folder("sub", "folder1"), _file("top.txt", "t1", 3, iso)],
        children_by_id={
            "folder1": [_file("inner.txt", "i1", 5, iso)],
        },
        file_bytes={"t1": b"top", "i1": b"inner"},
    )
    mirror = OneDriveMirror(
        client=fake,
        drive_id="drive",
        root_path="",
        local_root=tmp_path / "mirror",
    )
    stats = mirror.run_once()
    assert stats.downloaded == 2
    assert stats.folders_seen == 1
    assert (tmp_path / "mirror" / "top.txt").read_bytes() == b"top"
    assert (tmp_path / "mirror" / "sub" / "inner.txt").read_bytes() == b"inner"


def test_rejects_path_escape_names(tmp_path: Path):
    iso = "2026-06-24T10:00:00Z"
    fake = FakeGraph(
        root_children=[
            {"name": "../escape.txt", "id": "x1", "size": 4, "lastModifiedDateTime": iso, "file": {}},
            {"name": "sub/escape.txt", "id": "x2", "size": 4, "lastModifiedDateTime": iso, "file": {}},
            _file("good.txt", "g1", 4, iso),
        ],
        children_by_id={},
        file_bytes={"g1": b"good"},
    )
    mirror = OneDriveMirror(
        client=fake,
        drive_id="drive",
        root_path="",
        local_root=tmp_path / "mirror",
    )
    stats = mirror.run_once()
    assert stats.downloaded == 1
    assert (tmp_path / "mirror" / "good.txt").exists()
    # Escape attempts must produce nothing outside the mirror root
    assert not (tmp_path / "escape.txt").exists()
    assert not (tmp_path / "mirror" / "sub" / "escape.txt").exists()
