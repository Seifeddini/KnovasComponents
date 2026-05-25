import json
from pathlib import Path

import pytest

from sync.sync_config import load_sync_config, save_sync_config, seed_from_env, validate_sync_config


def test_seed_from_env():
    doc = seed_from_env()
    assert doc["schema_version"] == 1
    assert validate_sync_config(doc) == []


def test_invalid_schema_rejected():
    errors = validate_sync_config({"schema_version": 1, "enabled": True})
    assert errors


def test_max_document_age_seconds_accepted():
    doc = seed_from_env()
    doc["max_document_age_seconds"] = 2592000
    assert validate_sync_config(doc) == []


def test_atomic_save(tmp_path, monkeypatch):
    path = tmp_path / "sync.json"
    monkeypatch.setenv("RC_SYNC_CONFIG_PATH", str(path))
    from config import load_config

    load_config(validate=False)
    doc = seed_from_env()
    save_sync_config(doc, path=str(path))
    assert path.exists()
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["enabled"] is True
