"""Custom views: settings and the switch (SS-625)."""

import logging

import pytest

import config_loader
from custom_views.settings import CustomViewsSettings, load_settings

_BLOCK = """
custom_views:
  enabled: "${CUSTOM_VIEWS_ENABLED:-false}"
  dir: "${CUSTOM_VIEWS_DIR:-/mnt/custom-views}"
  facts_per_minute: "${CUSTOM_VIEWS_FACTS_PER_MINUTE:-60}"
"""


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("ENABLED", "DIR", "FACTS_PER_MINUTE"):
        monkeypatch.delenv(f"CUSTOM_VIEWS_{name}", raising=False)


def _config(tmp_path, text=_BLOCK):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return config_loader.ConfigLoader(str(path))


def test_the_switch_is_off_by_default(tmp_path):
    settings = load_settings(_config(tmp_path, "other: 1\n"))
    assert settings == CustomViewsSettings()
    assert settings.enabled is False
    assert settings.dir == "/mnt/custom-views"


def test_the_block_defaults_match_the_dataclass(tmp_path):
    assert load_settings(_config(tmp_path)) == CustomViewsSettings()


def test_values_come_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("CUSTOM_VIEWS_ENABLED", "true")
    monkeypatch.setenv("CUSTOM_VIEWS_DIR", str(tmp_path))
    monkeypatch.setenv("CUSTOM_VIEWS_FACTS_PER_MINUTE", "12")
    settings = load_settings(_config(tmp_path))
    assert settings.enabled is True
    assert settings.dir == str(tmp_path)
    assert settings.facts_per_minute == 12


def test_a_bad_number_keeps_the_default(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("CUSTOM_VIEWS_FACTS_PER_MINUTE", "abc")
    with caplog.at_level(logging.WARNING):
        settings = load_settings(_config(tmp_path))
    assert settings.facts_per_minute == 60
    assert "facts_per_minute" in caplog.text
