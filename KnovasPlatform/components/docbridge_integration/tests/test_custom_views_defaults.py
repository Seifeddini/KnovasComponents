"""Custom views: the shipped defaults -- the switch off, the folder empty (spec § 8).

Off by default is what keeps an installation that never chose the feature safe, so
the files that ship it are read as they are: the component's real config.yaml with
no ``CUSTOM_VIEWS_*`` variable set, and the repository-root docker-compose.yml that
starts docbridge-web (found as test_compose_extraction_settings.py finds it).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

import config_loader
from custom_views.settings import CustomViewsSettings, load_settings

COMPONENT = Path(__file__).resolve().parents[1]
REPO = COMPONENT.parents[2]
CONFIG = COMPONENT / "config" / "config.yaml"
COMPOSE = REPO / "docker-compose.yml"


@pytest.fixture(autouse=True)
def _no_custom_views_environment(monkeypatch):
    for name in list(os.environ):
        if name.upper().startswith("CUSTOM_VIEWS_"):
            monkeypatch.delenv(name)


def _docbridge_web() -> dict:
    if not COMPOSE.is_file():
        pytest.skip("repository-root docker-compose.yml is not in this checkout")
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["docbridge-web"]


def test_the_shipped_config_yaml_loads_the_defaults_with_no_environment():
    settings = load_settings(config_loader.ConfigLoader(str(CONFIG)))

    assert settings == CustomViewsSettings()
    assert settings.enabled is False
    assert settings.dir == "/mnt/custom-views"
    assert settings.facts_per_minute == 60


def test_compose_starts_docbridge_web_with_the_switch_off_unless_knovas_env_sets_it():
    environment = _docbridge_web()["environment"]

    assert environment["CUSTOM_VIEWS_ENABLED"] == "${CUSTOM_VIEWS_ENABLED:-false}"
    assert environment["CUSTOM_VIEWS_DIR"] == "/mnt/custom-views"


def test_compose_mounts_the_views_folder_read_only_and_an_empty_stand_in_by_default():
    volumes = [str(v) for v in _docbridge_web()["volumes"]]

    assert "${CUSTOM_VIEWS_PATH:-./data/no-custom-views}:/mnt/custom-views:ro" in volumes
    # the one mount at the views folder: nothing else puts files there or makes it writable
    assert [v for v in volumes if ":/mnt/custom-views" in v] == [
        "${CUSTOM_VIEWS_PATH:-./data/no-custom-views}:/mnt/custom-views:ro"]
