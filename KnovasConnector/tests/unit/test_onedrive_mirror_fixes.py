"""Regression tests for CONFIRMED data-loss / security bugs in the OneDrive mirror.

Each test targets one specific bug (A5, A6, A7, A9, C8, C9) and is written
test-first: it fails against the unfixed code for the documented reason, then
passes once the minimal fix lands.

Network-free: Graph interactions are injected via small fakes (the code takes a
``client=`` / ``_client``), never real HTTP.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from onedrive_mirror.graph import DeltaTokenInvalid, GraphClient, GraphRequestError
from onedrive_mirror.mirror import MirrorStats, OneDriveMirror


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class WalkGraph:
    """Fake Graph client for the full-walk path (``use_delta=False``).

    ``fail_list_ids`` lets a sub-folder enumeration blow up mid-walk (lazily,
    on iteration — matching the real generator-based client).
    """

    def __init__(
        self,
        root_children,
        children_by_id=None,
        file_bytes=None,
        fail_list_ids=frozenset(),
    ):
        self._root_children = root_children
        self._children_by_id = children_by_id or {}
        self._file_bytes = file_bytes or {}
        self._fail_list_ids = set(fail_list_ids)
        self.download_calls = 0
        self.downloaded_ids: list[str] = []

    def test_drive(self, drive_id):
        return None

    def list_root_children(self, drive_id, root_path):
        yield from self._root_children

    def list_children_by_id(self, drive_id, item_id):
        if item_id in self._fail_list_ids:
            raise GraphRequestError(f"boom listing {item_id}")
        yield from self._children_by_id.get(item_id, [])

    def download_to(self, drive_id, item_id, dest_path):
        self.download_calls += 1
        self.downloaded_ids.append(item_id)
        data = self._file_bytes.get(item_id, b"")
        with open(dest_path, "wb") as fh:
            fh.write(data)
        return len(data)

    def delta_pages(self, drive_id, delta_url=None):
        raise GraphRequestError("delta not configured")


class DeltaGraph:
    """Fake Graph client for the delta path.

    ``fail_download_ids`` makes ``download_to`` raise for those item ids.
    """

    def __init__(self, pages_by_url, file_bytes=None, fail_download_ids=frozenset()):
        self._pages_by_url = pages_by_url  # {"__initial__": [(items, delta_link)], url: [...]}
        self._file_bytes = file_bytes or {}
        self._fail_download_ids = set(fail_download_ids)
        self.download_calls = 0
        self.downloaded_ids: list[str] = []
        self.delta_calls: list = []

    def test_drive(self, drive_id):
        return None

    def download_to(self, drive_id, item_id, dest_path):
        self.download_calls += 1
        if item_id in self._fail_download_ids:
            raise GraphRequestError(f"throttled {item_id}")
        self.downloaded_ids.append(item_id)
        data = self._file_bytes.get(item_id, b"")
        with open(dest_path, "wb") as fh:
            fh.write(data)
        return len(data)

    def delta_pages(self, drive_id, delta_url=None):
        self.delta_calls.append(delta_url)
        key = delta_url or "__initial__"
        for items, delta_link in self._pages_by_url.get(key, []):
            yield items, delta_link

    def list_root_children(self, drive_id, root_path):
        return iter(())

    def list_children_by_id(self, drive_id, item_id):
        return iter(())


class FakeResponse:
    """Minimal stand-in for ``requests.Response`` used by graph.py tests."""

    def __init__(self, status_code=200, headers=None, chunks=(), json_data=None, text=""):
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = list(chunks)
        self._json = json_data if json_data is not None else {}
        self.text = text
        self.closed = False

    def iter_content(self, chunk_size=65536):
        for c in self._chunks:
            yield c

    def json(self):
        return self._json

    def close(self):
        self.closed = True


class FakeSession:
    """Records every request so tests can assert a token was / was not sent."""

    def __init__(self, response):
        self._response = response
        self.requests_made: list = []

    def request(self, method, url, headers=None, stream=False, timeout=None):
        self.requests_made.append((method, url, headers, stream))
        return self._response


class SequenceSession:
    """Returns a queued sequence of responses (one per call), recording each."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests_made: list = []

    def request(self, method, url, headers=None, stream=False, timeout=None):
        self.requests_made.append((method, url, headers, stream))
        return self._responses.pop(0)


def _client_no_network(session) -> GraphClient:
    client = GraphClient(tenant_id="t", client_id="c", client_secret="s")
    client._token = "faketoken"
    client._token_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    client._session = session
    return client


# --------------------------------------------------------------------------- #
# Item builders
# --------------------------------------------------------------------------- #
def _file(name, item_id, size, iso, mime="text/plain", web_url=None):
    item = {
        "name": name,
        "id": item_id,
        "size": size,
        "lastModifiedDateTime": iso,
        "file": {"mimeType": mime},
    }
    if web_url is not None:
        item["webUrl"] = web_url
    return item


def _folder(name, item_id):
    return {"name": name, "id": item_id, "folder": {"childCount": 0}}


def _delta_file(name, item_id, rel_dir, size, iso, mime="application/pdf", web_url="https://o/x"):
    parent_path = f"/drive/root:/{rel_dir}".rstrip("/")
    return {
        "name": name,
        "id": item_id,
        "size": size,
        "lastModifiedDateTime": iso,
        "file": {"mimeType": mime},
        "webUrl": web_url,
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


ISO = "2026-06-24T10:00:00Z"


# --------------------------------------------------------------------------- #
# A5 — Prune must not run after a partial / failed enumeration
# --------------------------------------------------------------------------- #
def test_a5_prune_skipped_when_enumeration_incomplete(tmp_path: Path):
    mirror_root = tmp_path / "mirror"
    mirror_root.mkdir()
    # A local-only file that a *clean* prune would legitimately delete.
    stale = mirror_root / "stale.txt"
    stale.write_text("keep me — enumeration never completed", encoding="utf-8")

    fake = WalkGraph(
        root_children=[_folder("sub", "folder1"), _file("top.txt", "t1", 3, ISO)],
        children_by_id={"folder1": [_file("inner.txt", "i1", 5, ISO)]},
        file_bytes={"t1": b"top", "i1": b"inner"},
        fail_list_ids={"folder1"},  # enumeration blows up part-way through
    )
    mirror = OneDriveMirror(
        client=fake, drive_id="d", root_path="", local_root=mirror_root, use_delta=False
    )

    pruned = {"called": False}
    orig_prune = mirror._prune_local_only

    def spy(paths, stats):
        pruned["called"] = True
        return orig_prune(paths, stats)

    mirror._prune_local_only = spy

    stats = mirror.run_once()

    assert pruned["called"] is False, "prune must be skipped when enumeration is incomplete"
    assert stale.exists(), "local-only file must survive an incomplete enumeration"
    assert stats.deleted_locally == 0


def test_a5_clean_enumeration_still_prunes(tmp_path: Path):
    mirror_root = tmp_path / "mirror"
    mirror_root.mkdir()
    stale = mirror_root / "stale.txt"
    stale.write_text("no longer remote", encoding="utf-8")

    fake = WalkGraph(
        root_children=[_file("top.txt", "t1", 3, ISO)],
        file_bytes={"t1": b"top"},
    )
    mirror = OneDriveMirror(
        client=fake, drive_id="d", root_path="", local_root=mirror_root, use_delta=False
    )
    stats = mirror.run_once()

    assert not stale.exists(), "clean enumeration must still prune local-only files"
    assert stats.deleted_locally == 1


# --------------------------------------------------------------------------- #
# A6 — Delta cursor must not advance past a failed download
# --------------------------------------------------------------------------- #
def test_a6_delta_cursor_not_advanced_on_download_failure(tmp_path: Path):
    pages = {
        "__initial__": [
            (
                [
                    _delta_file("ok.pdf", "id-ok", "", 5, ISO),
                    _delta_file("bad.pdf", "id-bad", "", 5, ISO),
                ],
                "https://graph.microsoft.com/delta?token=v2",
            ),
        ],
    }
    fake = DeltaGraph(
        pages_by_url=pages,
        file_bytes={"id-ok": b"okok!", "id-bad": b"nope!"},
        fail_download_ids={"id-bad"},  # this file cannot be fetched this pass
    )
    mirror = OneDriveMirror(
        client=fake,
        drive_id="d",
        root_path="",
        local_root=tmp_path / "mirror",
        use_delta=True,
        allowed_extensions=["pdf"],
    )

    saved = {"called": False}
    orig_save = mirror._save_delta_token

    def spy(link):
        saved["called"] = True
        return orig_save(link)

    mirror._save_delta_token = spy

    stats = mirror.run_once()

    assert saved["called"] is False, "delta cursor must NOT advance when a download failed"
    assert not (tmp_path / "mirror" / ".onedrive_delta.json").exists()
    assert stats.download_failures == 1


# --------------------------------------------------------------------------- #
# A7 — Default mirror root must never be a bare watch root
# --------------------------------------------------------------------------- #
def test_a7_default_mirror_path_is_dedicated_subdir(tmp_path: Path):
    from onedrive_mirror import runner

    watch_root = tmp_path / "watched"
    # ONEDRIVE_MIRROR_PATH unset (empty) + a configured watch root.
    resolved = runner._resolve_mirror_path("", [str(watch_root)])

    assert resolved != watch_root.resolve(), (
        "mirror root must not default to the bare watch root (prune would wipe it)"
    )
    assert resolved == watch_root.resolve() / "onedrive_mirror"
    # Still inside the watch root so RC discover/sync picks up mirrored files.
    assert resolved.is_relative_to(watch_root.resolve())


def test_a7_explicit_mirror_path_is_respected(tmp_path: Path):
    from onedrive_mirror import runner

    explicit = tmp_path / "custom" / "loc"
    resolved = runner._resolve_mirror_path(str(explicit), [str(tmp_path / "watched")])
    assert resolved == explicit.resolve()


# --------------------------------------------------------------------------- #
# A9 — download_to must be atomic and size-verified
# --------------------------------------------------------------------------- #
def test_a9_download_size_mismatch_raises_and_leaves_no_partial(tmp_path: Path):
    dest = tmp_path / "sub" / "file.pdf"
    # Server declares 10 bytes but streams only 5 (truncated download).
    resp = FakeResponse(status_code=200, headers={"Content-Length": "10"}, chunks=[b"short"])
    client = _client_no_network(FakeSession(resp))

    with pytest.raises(GraphRequestError):
        client.download_to("drive", "item", dest)

    assert not dest.exists(), "a truncated download must not leave a partial file at the final path"
    # No leftover temp/part files either.
    assert list((tmp_path / "sub").glob("*")) == []


def test_a9_download_success_replaces_atomically(tmp_path: Path):
    dest = tmp_path / "file.pdf"
    dest.write_bytes(b"OLD-GOOD-COPY")  # existing good copy must survive until replace
    resp = FakeResponse(status_code=200, headers={"Content-Length": "5"}, chunks=[b"new", b"!!"])
    client = _client_no_network(FakeSession(resp))

    written = client.download_to("drive", "item", dest, expected_size=5)

    assert written == 5
    assert dest.read_bytes() == b"new!!"


# --------------------------------------------------------------------------- #
# C8 — Bearer token must not be sent to a non-Graph host; control files protected
# --------------------------------------------------------------------------- #
def test_c8_delta_link_to_foreign_host_is_refused(tmp_path: Path):
    # A persisted deltaLink pointing at an attacker-controlled host.
    session = FakeSession(FakeResponse(status_code=200, json_data={"value": []}))
    client = _client_no_network(session)

    with pytest.raises(GraphRequestError):
        list(client.delta_pages("drive", "https://attacker.example/evil?token=x"))

    # The Authorization: Bearer header must NEVER reach the foreign host.
    assert session.requests_made == [], "no request (and no token) may be sent to a non-Graph host"


def test_c8_graph_host_allowed(tmp_path: Path):
    # Sanity: a legitimate national-cloud Graph host is still allowed.
    session = FakeSession(FakeResponse(status_code=200, json_data={"value": []}))
    client = _client_no_network(session)
    pages = list(
        client.delta_pages("drive", "https://graph.microsoft.us/v1.0/drives/x/root/delta")
    )
    assert pages == [([], None)]
    assert len(session.requests_made) == 1


def test_c8_mirrored_item_colliding_with_control_file_is_skipped(tmp_path: Path):
    mirror_root = tmp_path / "mirror"
    fake = WalkGraph(
        root_children=[
            # A remote file named exactly like our delta-token control file.
            _file(".onedrive_delta.json", "evil", 4, ISO, mime="application/json"),
            _file("good.txt", "g1", 4, ISO),
        ],
        file_bytes={"evil": b"EVIL", "g1": b"good"},
    )
    mirror = OneDriveMirror(
        client=fake,
        drive_id="d",
        root_path="",
        local_root=mirror_root,
        use_delta=False,
        allowed_extensions=["txt", "json"],  # .json allowed → extension filter won't mask it
    )
    stats = mirror.run_once()

    token_path = mirror_root / ".onedrive_delta.json"
    assert not token_path.exists(), "a mirrored item must not overwrite the delta-token control file"
    assert "evil" not in fake.downloaded_ids
    assert stats.skipped_control == 1
    assert (mirror_root / "good.txt").read_bytes() == b"good"


# --------------------------------------------------------------------------- #
# C9 — a ".." delta path must never delete the mirror root
# --------------------------------------------------------------------------- #
def test_c9_delta_delete_dotdot_does_not_wipe_root(tmp_path: Path):
    mirror_root = tmp_path / "mirror"
    mirror_root.mkdir()
    keep = mirror_root / "keep.txt"
    keep.write_text("precious data", encoding="utf-8")
    (mirror_root / "foo").mkdir()

    # A delete item named ".." whose parent is <root>/foo → rel "foo/.." which
    # resolves back to the mirror root itself.
    pages = {
        "__initial__": [
            (
                [_delta_deletion("..", "d1", "SharePointRoot/foo")],
                "https://graph.microsoft.com/delta?token=v1",
            ),
        ],
    }
    fake = DeltaGraph(pages_by_url=pages)
    mirror = OneDriveMirror(
        client=fake,
        drive_id="d",
        root_path="SharePointRoot",
        local_root=mirror_root,
        use_delta=True,
    )
    stats = mirror.run_once()

    assert keep.exists(), "a '..' delta delete must not wipe the mirror root"
    assert mirror_root.exists()
    assert stats.deleted_locally == 0


def test_c9_apply_delete_refuses_dest_equal_to_root(tmp_path: Path):
    mirror_root = tmp_path / "mirror"
    mirror_root.mkdir()
    keep = mirror_root / "keep.txt"
    keep.write_text("precious data", encoding="utf-8")

    fake = DeltaGraph(pages_by_url={})
    mirror = OneDriveMirror(
        client=fake, drive_id="d", root_path="", local_root=mirror_root, use_delta=True
    )

    stats = MirrorStats()
    # A rel that resolves to the mirror root itself must be refused outright.
    mirror._apply_delta_delete(".", stats)

    assert keep.exists()
    assert mirror_root.exists()
    assert stats.deleted_locally == 0


# --------------------------------------------------------------------------- #
# G1 — Pagination must not loop forever on a self-referential / cyclic link
# --------------------------------------------------------------------------- #
def test_g1_paginate_aborts_on_self_referential_nextlink():
    # A page whose ``@odata.nextLink`` points straight back at itself would
    # loop forever. The fake returns this SAME response on every call (returns
    # immediately, no network), so the guard — not a timeout — must stop it.
    loop_url = "https://graph.microsoft.com/v1.0/drives/d/root/children"
    resp = FakeResponse(
        status_code=200,
        json_data={"value": [], "@odata.nextLink": loop_url},
    )
    session = FakeSession(resp)
    client = _client_no_network(session)

    with pytest.raises(GraphRequestError):
        list(client._paginate(loop_url))

    # Proof it did not loop: only a bounded number of fetches happened.
    assert len(session.requests_made) <= 2


def test_g1_delta_pages_aborts_on_self_referential_nextlink():
    loop_url = "https://graph.microsoft.com/v1.0/drives/d/root/delta?token=loop"
    resp = FakeResponse(
        status_code=200,
        json_data={"value": [], "@odata.nextLink": loop_url},
    )
    session = FakeSession(resp)
    client = _client_no_network(session)

    with pytest.raises(GraphRequestError):
        list(client.delta_pages("d", loop_url))

    assert len(session.requests_made) <= 2


# --------------------------------------------------------------------------- #
# G2 — A retried stream=True response must be closed before re-issuing
# --------------------------------------------------------------------------- #
def test_g2_streamed_response_closed_before_retry(monkeypatch):
    # Don't actually sleep during the backoff.
    monkeypatch.setattr("onedrive_mirror.graph.time.sleep", lambda _s: None)

    first = FakeResponse(status_code=503)  # transient -> triggers a retry
    second = FakeResponse(status_code=200, json_data={"value": []})
    session = SequenceSession([first, second])
    client = _client_no_network(session)

    resp = client._request(
        "GET", "https://graph.microsoft.com/v1.0/drives/d/items/i/content", stream=True
    )

    # The unconsumed streamed 503 response must be closed before the retry so
    # its pooled connection is released rather than leaked until GC.
    assert first.closed is True, "the retried streamed response must be closed"
    assert resp is second
    assert resp.status_code == 200
    assert len(session.requests_made) == 2
