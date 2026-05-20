import os

import pytest

from discover.filesystem import discover_filesystem, resolve_root


def test_resolve_root_escape_rejected(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("RC_WATCH_ROOTS", str(allowed))
    from config import load_config

    load_config(validate=False)
    root, err = resolve_root(str(outside))
    assert root is None
    assert err is not None


def test_discover_returns_files(tmp_watch_root):
    from config import reset_config, load_config

    reset_config()
    load_config(validate=False, force_reload=True)
    body = discover_filesystem()
    names = [e["name"] for e in body["entries"]]
    assert "sample.md" in names


def test_max_depth_cap(tmp_watch_root):
    root = tmp_watch_root
    deep = root / "a" / "b" / "c" / "d" / "e"
    deep.mkdir(parents=True)
    (deep / "deep.md").write_text("x", encoding="utf-8")
    body = discover_filesystem(max_depth=2)
    paths = [e["path"] for e in body["entries"] if e["type"] == "file"]
    assert not any("deep.md" in p for p in paths)
