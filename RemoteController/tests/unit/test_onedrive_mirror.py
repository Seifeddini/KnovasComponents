"""Unit tests for the OneDrive mirror.

Network-free: GraphClient calls are stubbed via a fake passed into
OneDriveMirror. Covers: download-on-new, skip-on-unchanged, prune-deleted,
extension filter, oversize filter, mtime preservation, path-escape guard,
enrichment JSONL writer, and the delta fast path with fallback.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import pytest

from onedrive_mirror.graph import DeltaTokenInvalid, GraphRequestError
from onedrive_mirror.mirror import OneDriveMirror


class FakeGraph:
    """Minimal in-memory Graph stand-in for OneDriveMirror.

    Default behaviour matches the original (no delta) — delta_pages() raises
    GraphRequestError so the mirror falls back to the full walk. Tests that
    exercise the delta path pass an explicit ``delta_responses`` parameter.
    """

    def __init__(
        self,
        root_children,
        children_by_id,
        file_bytes,
        *,
        delta_responses=None,
        delta_invalid_on=None,
    ):
        self._root_children = root_children
        self._children_by_id = children_by_id
        self._file_bytes = file_bytes
        self._delta_responses = delta_responses
        self._delta_invalid_on = delta_invalid_on  # raise on N-th call (1-indexed) if matched URL
        self._delta_calls_made = []  # records (url,) for each delta_pages call
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

    def delta_pages(self, drive_id, delta_url=None):
        self._delta_calls_made.append(delta_url)
        if self._delta_invalid_on and delta_url in self._delta_invalid_on:
            raise DeltaTokenInvalid(f"simulated 410 for {delta_url}")
        if self._delta_responses is None:
            raise GraphRequestError("delta not configured")
        key = delta_url or "__initial__"
        pages = self._delta_responses.get(key, [])
        for items, delta_link in pages:
            yield items, delta_link


def _file(name, item_id, size, last_modified_iso, mime="text/plain", web_url=None):
    item = {
        "name": name,
        "id": item_id,
        "size": size,
        "lastModifiedDateTime": last_modified_iso,
        "file": {"mimeType": mime},
    }
    if web_url is not None:
        item["webUrl"] = web_url
    return item


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


def test_enrichment_file_written_with_identifier_prefix(tmp_path: Path):
    import json as _json
    iso = "2026-06-24T10:00:00Z"
    fake = FakeGraph(
        root_children=[
            _folder("sub", "f1"),
            _file("top.pdf", "t1", 4, iso, web_url="https://example/onedrive/top"),
        ],
        children_by_id={
            "f1": [_file("inner.pdf", "i1", 5, iso, web_url="https://example/onedrive/inner")],
        },
        file_bytes={"t1": b"topd", "i1": b"inner"},
    )
    enrichment = tmp_path / ".search_enrichment.jsonl"
    mirror = OneDriveMirror(
        client=fake,
        drive_id="drive",
        root_path="",
        local_root=tmp_path / "mirror",
        allowed_extensions=["pdf"],
        identifier_prefix="adiuvat",
        enrichment_path=enrichment,
    )
    stats = mirror.run_once()
    assert stats.enrichment_entries == 2
    rows = [_json.loads(line) for line in enrichment.read_text().splitlines() if line.strip()]
    by_id = {r["doc_id"]: r for r in rows}
    assert by_id["adiuvat/top.pdf"]["web_url"] == "https://example/onedrive/top"
    assert by_id["adiuvat/sub/inner.pdf"]["web_url"] == "https://example/onedrive/inner"
    assert by_id["adiuvat/top.pdf"]["title"] == "top.pdf"


def test_enrichment_skipped_when_no_path_configured(tmp_path: Path):
    iso = "2026-06-24T10:00:00Z"
    fake = FakeGraph(
        root_children=[_file("doc.txt", "d1", 4, iso, web_url="https://x/y")],
        children_by_id={},
        file_bytes={"d1": b"data"},
    )
    mirror = OneDriveMirror(
        client=fake,
        drive_id="drive",
        root_path="",
        local_root=tmp_path / "mirror",
    )
    stats = mirror.run_once()
    assert stats.enrichment_entries == 0
    assert not any(p.suffix == ".jsonl" for p in (tmp_path / "mirror").iterdir())


def _delta_file(name, item_id, rel_dir, size, iso, web_url="https://example.com/f"):
    parent_path = f"/drive/root:/{rel_dir}".rstrip("/")
    return {
        "name": name,
        "id": item_id,
        "size": size,
        "lastModifiedDateTime": iso,
        "file": {"mimeType": "application/pdf"},
        "webUrl": web_url,
        "parentReference": {"path": parent_path},
    }


def _delta_folder(name, item_id, rel_dir):
    parent_path = f"/drive/root:/{rel_dir}".rstrip("/")
    return {
        "name": name,
        "id": item_id,
        "folder": {"childCount": 0},
        "parentReference": {"path": parent_path},
    }


def _delta_deletion(name, item_id, rel_dir):
    parent_path = f"/drive/root:/{rel_dir}".rstrip("/")
    return {
        "name": name,
        "id": item_id,
        "deleted": {"state": "deleted"},
        "parentReference": {"path": parent_path},
    }


def test_delta_initial_pass_downloads_and_saves_token(tmp_path: Path):
    import json as _json
    iso = "2026-06-24T10:00:00Z"
    delta_responses = {
        "__initial__": [
            (
                [
                    _delta_folder("Adiuvat", "rootfolder", ""),
                    _delta_file("a.pdf", "id-a", "Adiuvat", 4, iso, web_url="https://o/a"),
                ],
                "https://graph/delta?token=v1",
            ),
        ]
    }
    fake = FakeGraph(
        root_children=[],
        children_by_id={},
        file_bytes={"id-a": b"alpha"},
        delta_responses=delta_responses,
    )
    enrichment = tmp_path / ".search_enrichment.jsonl"
    mirror = OneDriveMirror(
        client=fake,
        drive_id="drive",
        root_path="/Adiuvat",
        local_root=tmp_path / "mirror",
        identifier_prefix="adiuvat",
        enrichment_path=enrichment,
        use_delta=True,
    )
    stats = mirror.run_once()

    assert stats.mode == "delta-initial"
    assert stats.downloaded == 1
    assert (tmp_path / "mirror" / "a.pdf").read_bytes() == b"alpha"
    token_path = tmp_path / "mirror" / ".onedrive_delta.json"
    assert token_path.exists()
    saved = _json.loads(token_path.read_text())
    assert saved["delta_link"] == "https://graph/delta?token=v1"
    rows = [_json.loads(l) for l in enrichment.read_text().splitlines() if l.strip()]
    assert rows[0]["doc_id"] == "adiuvat/a.pdf"


def test_delta_incremental_uses_saved_token(tmp_path: Path):
    import json as _json
    iso_a = "2026-06-24T10:00:00Z"
    iso_b = "2026-06-25T10:00:00Z"
    delta_responses = {
        "__initial__": [
            (
                [_delta_file("a.pdf", "id-a", "Adiuvat", 4, iso_a, web_url="https://o/a")],
                "https://graph/delta?token=v1",
            )
        ],
        "https://graph/delta?token=v1": [
            (
                [_delta_file("b.pdf", "id-b", "Adiuvat", 4, iso_b, web_url="https://o/b")],
                "https://graph/delta?token=v2",
            )
        ],
    }
    fake = FakeGraph(
        root_children=[],
        children_by_id={},
        file_bytes={"id-a": b"alpha", "id-b": b"bravo"},
        delta_responses=delta_responses,
    )
    enrichment = tmp_path / ".search_enrichment.jsonl"
    mirror = OneDriveMirror(
        client=fake,
        drive_id="drive",
        root_path="/Adiuvat",
        local_root=tmp_path / "mirror",
        identifier_prefix="adiuvat",
        enrichment_path=enrichment,
        use_delta=True,
    )
    # First pass — uses __initial__
    mirror.run_once()
    assert fake._delta_calls_made[-1] is None

    # Second pass — must use the saved token, NOT walk again
    stats = mirror.run_once()
    assert fake._delta_calls_made[-1] == "https://graph/delta?token=v1"
    assert stats.mode == "delta"
    assert stats.downloaded == 1
    # Saved token rotated to v2
    saved = _json.loads((tmp_path / "mirror" / ".onedrive_delta.json").read_text())
    assert saved["delta_link"] == "https://graph/delta?token=v2"
    # Enrichment carries BOTH files (state persisted across passes)
    rows = [_json.loads(l) for l in enrichment.read_text().splitlines() if l.strip()]
    by_id = {r["doc_id"]: r for r in rows}
    assert "adiuvat/a.pdf" in by_id
    assert "adiuvat/b.pdf" in by_id


def test_delta_deletion_removes_local_file_and_enrichment(tmp_path: Path):
    import json as _json
    iso = "2026-06-24T10:00:00Z"
    delta_responses = {
        "__initial__": [
            (
                [_delta_file("a.pdf", "id-a", "Adiuvat", 4, iso)],
                "https://graph/delta?token=v1",
            )
        ],
        "https://graph/delta?token=v1": [
            (
                [_delta_deletion("a.pdf", "id-a", "Adiuvat")],
                "https://graph/delta?token=v2",
            )
        ],
    }
    fake = FakeGraph(
        root_children=[],
        children_by_id={},
        file_bytes={"id-a": b"alpha"},
        delta_responses=delta_responses,
    )
    enrichment = tmp_path / ".search_enrichment.jsonl"
    mirror = OneDriveMirror(
        client=fake,
        drive_id="drive",
        root_path="/Adiuvat",
        local_root=tmp_path / "mirror",
        identifier_prefix="adiuvat",
        enrichment_path=enrichment,
        use_delta=True,
    )
    mirror.run_once()
    assert (tmp_path / "mirror" / "a.pdf").exists()

    stats = mirror.run_once()
    assert stats.deleted_locally == 1
    assert not (tmp_path / "mirror" / "a.pdf").exists()
    rows = [_json.loads(l) for l in enrichment.read_text().splitlines() if l.strip()]
    assert all("adiuvat/a.pdf" != r["doc_id"] for r in rows)


def test_delta_token_invalid_triggers_full_walk_fallback(tmp_path: Path):
    iso = "2026-06-24T10:00:00Z"
    fake = FakeGraph(
        # Full-walk path: list_root_children returns a single file
        root_children=[_file("fallback.pdf", "fb1", 5, iso, web_url="https://o/fb")],
        children_by_id={},
        file_bytes={"fb1": b"fallb"},
        delta_invalid_on={None},  # initial delta call raises DeltaTokenInvalid
    )
    mirror = OneDriveMirror(
        client=fake,
        drive_id="drive",
        root_path="",
        local_root=tmp_path / "mirror",
        identifier_prefix="adiuvat",
        use_delta=True,
    )
    stats = mirror.run_once()
    assert stats.mode == "delta-then-walk"
    assert stats.downloaded == 1
    assert (tmp_path / "mirror" / "fallback.pdf").exists()


def test_delta_disabled_uses_full_walk(tmp_path: Path):
    iso = "2026-06-24T10:00:00Z"
    fake = FakeGraph(
        root_children=[_file("x.pdf", "x1", 5, iso, web_url="https://o/x")],
        children_by_id={},
        file_bytes={"x1": b"xdata"},
    )
    mirror = OneDriveMirror(
        client=fake,
        drive_id="drive",
        root_path="",
        local_root=tmp_path / "mirror",
        use_delta=False,
    )
    stats = mirror.run_once()
    assert stats.mode == "walk"
    assert stats.downloaded == 1
    # delta_pages must NOT have been called when delta is disabled
    assert fake._delta_calls_made == []


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
