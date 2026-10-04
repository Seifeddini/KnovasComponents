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


def _hand_set_schedule():
    doc = seed_from_env()
    doc["window"] = {"start_local": "00:00", "end_local": "23:59"}
    doc["max_files_per_cycle"] = 500
    return doc


def test_old_config_file_name_is_adopted(tmp_path, caplog):
    """An installation from before the rename keeps its schedule: the old
    file is renamed, not replaced by one seeded from the environment."""
    old = tmp_path / "remote_controller_sync.json"
    old.write_text(json.dumps(_hand_set_schedule()), encoding="utf-8")
    new = tmp_path / "knovas_connector_sync.json"

    with caplog.at_level("INFO"):
        doc = load_sync_config(str(new))

    assert doc["window"] == {"start_local": "00:00", "end_local": "23:59"}
    assert new.is_file() and not old.exists()
    assert "Sync config renamed" in caplog.text


def test_new_config_file_wins_over_an_old_one(tmp_path):
    old = tmp_path / "remote_controller_sync.json"
    old.write_text(json.dumps(_hand_set_schedule()), encoding="utf-8")
    new = tmp_path / "knovas_connector_sync.json"
    current = seed_from_env()
    current["window"] = {"start_local": "20:00", "end_local": "08:00"}
    new.write_text(json.dumps(current), encoding="utf-8")

    assert load_sync_config(str(new))["window"]["start_local"] == "20:00"
    assert old.exists()


def test_save_adopts_the_old_file_before_writing(tmp_path):
    old = tmp_path / "remote_controller_sync.json"
    old.write_text(json.dumps(_hand_set_schedule()), encoding="utf-8")
    new = tmp_path / "knovas_connector_sync.json"

    save_sync_config(seed_from_env(), path=str(new))

    assert new.is_file() and not old.exists()


def test_an_explicit_config_path_is_never_renamed(tmp_path):
    """RC_SYNC_CONFIG_PATH naming another file: nothing beside it moves."""
    old = tmp_path / "remote_controller_sync.json"
    old.write_text(json.dumps(_hand_set_schedule()), encoding="utf-8")

    load_sync_config(str(tmp_path / "custom.json"))

    assert old.exists()
