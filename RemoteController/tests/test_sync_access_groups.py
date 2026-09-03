"""Per-source access groups reach /secured/init_document_transmission.

`contracts/sync_request.schema.json` has documented `sources[].access_groups`
since July with the rationale "documents from a walled folder are born walled
rather than repaired afterwards". Until this task, no code read it: every
re-sync of a walled folder re-ingested its documents unrestricted.
"""

from __future__ import annotations

import json
import pathlib

import pytest

CONTRACT = (
    pathlib.Path(__file__).resolve().parents[1]
    / "contracts" / "sync_request.schema.json"
)


class TestContract:
    def test_schema_still_declares_per_source_access_groups(self):
        schema = json.loads(CONTRACT.read_text(encoding="utf-8"))
        source_props = schema["properties"]["sources"]["items"]["properties"]
        assert "access_groups" in source_props
        assert source_props["access_groups"]["type"] == "array"


@pytest.fixture
def watch_root(tmp_path, monkeypatch):
    """resolve_root only accepts paths under RC_WATCH_ROOTS; point it at tmp."""
    from config import reset_config

    monkeypatch.setenv("RC_WATCH_ROOTS", str(tmp_path))
    reset_config()
    try:
        yield tmp_path
    finally:
        reset_config()


class TestWalkTargetCarriesGroups:
    def test_build_walk_targets_reads_access_groups(self, watch_root):
        from sync.sync_executor import build_walk_targets

        root = watch_root / "matters"
        root.mkdir()
        body = {
            "mode": "incremental",
            "sources": [{"path": str(root), "access_groups": ["litigation"]}],
            "filters": {},
            "ingestion": {"identifier_prefix": "rc-sync"},
        }
        targets, _ = build_walk_targets(body, None, None)
        assert len(targets) == 1
        assert targets[0].access_groups == ("litigation",)

    def test_absent_access_groups_is_empty_not_none(self, watch_root):
        from sync.sync_executor import build_walk_targets

        root = watch_root / "general"
        root.mkdir()
        body = {
            "mode": "incremental",
            "sources": [{"path": str(root)}],
            "filters": {},
            "ingestion": {"identifier_prefix": "rc-sync"},
        }
        targets, _ = build_walk_targets(body, None, None)
        assert targets[0].access_groups == ()


def _capturing_uploader(monkeypatch):
    from sync.knovas_uploader import SemantixUploader

    captured = {}

    class _Resp:
        status_code = 200
        content = b'{"transmission_key_id": "k1"}'

        @staticmethod
        def json():
            return {"transmission_key_id": "k1"}

    def _fake_request(self, method, endpoint, json_body=None, **kw):
        if endpoint.endswith("init_document_transmission"):
            captured.update(json_body or {})
        return _Resp()

    monkeypatch.setattr(SemantixUploader, "_request", _fake_request)
    monkeypatch.setattr(
        "sync.knovas_uploader.write_context_sidecar", lambda *a, **k: None
    )
    return SemantixUploader.__new__(SemantixUploader), captured


class TestUploaderSendsGroups:
    def test_init_body_carries_access_groups(self, tmp_path, monkeypatch):
        doc = tmp_path / "note.txt"
        doc.write_text("hello world", encoding="utf-8")
        uploader, captured = _capturing_uploader(monkeypatch)
        uploader.upload_file(
            doc,
            "note.txt",
            {"ingestion": {"identifier_prefix": "rc-sync"}},
            access_groups=("litigation",),
        )
        assert captured.get("access_groups") == ["litigation"]

    def test_no_groups_means_no_key_so_folder_rules_can_apply(
        self, tmp_path, monkeypatch
    ):
        """An absent key lets the Secure API fall back to its folder rule.

        Sending an explicit empty list would mean "deliberately unrestricted"
        and would override the folder rule -- the opposite of what an
        unconfigured source should do.
        """
        doc = tmp_path / "note.txt"
        doc.write_text("hello world", encoding="utf-8")
        uploader, captured = _capturing_uploader(monkeypatch)
        uploader.upload_file(
            doc, "note.txt", {"ingestion": {"identifier_prefix": "rc-sync"}}
        )
        assert "identifier" in captured, "init must have been called"
        assert "access_groups" not in captured
