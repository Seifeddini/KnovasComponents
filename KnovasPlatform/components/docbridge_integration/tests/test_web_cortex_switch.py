"""Switching Cortex off, for a firm that only wants the search.

Hiding the navigation link is not switching a feature off: the page is still
there for anyone who kept the URL, and its API still answers. So both are
asserted here, and both go through one gate rather than eleven route decorators.
"""

from __future__ import annotations

import pytest

pytest.importorskip("flask")


def _app(tmp_path, monkeypatch, *, cortex: str):
    monkeypatch.setenv("WEB_SECRET_KEY", "test-secret-key-for-cortex-switch")
    monkeypatch.setenv("CORTEX_ENABLED", cortex)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        'web:\n'
        '  secret_key: "${WEB_SECRET_KEY}"\n'
        '  cortex_enabled: "${CORTEX_ENABLED:-true}"\n'
        '  login:\n'
        '    enabled: false\n'
        'identity:\n'
        '  enabled: false\n'
        'api:\n'
        '  base_url: "http://example.test"\n',
        encoding="utf-8",
    )
    from conftest import DummyFileHandler, DummyKnovasClient
    from web_interface import app as web_app

    monkeypatch.setattr(web_app, "KnovasAPIClient", DummyKnovasClient)
    monkeypatch.setattr(web_app, "AutoDocFileHandler", DummyFileHandler)
    flask_app = web_app.create_app(str(config_path))
    flask_app.config.update(TESTING=True)
    return flask_app.test_client()


class TestCortexOff:
    def test_the_navigation_does_not_offer_it(self, tmp_path, monkeypatch):
        client = _app(tmp_path, monkeypatch, cortex="false")
        assert "Cortex" not in client.get("/").data.decode("utf-8")

    def test_the_page_does_not_answer_either(self, tmp_path, monkeypatch):
        """A link nobody can see is not a feature that is off."""
        client = _app(tmp_path, monkeypatch, cortex="false")
        response = client.get("/ontology")
        assert response.status_code in (301, 302)

    def test_its_api_is_refused(self, tmp_path, monkeypatch):
        client = _app(tmp_path, monkeypatch, cortex="false")
        assert client.get("/api/ontology/summary").status_code == 404

    def test_the_search_is_untouched(self, tmp_path, monkeypatch):
        client = _app(tmp_path, monkeypatch, cortex="false")
        assert client.get("/").status_code == 200


class TestCortexOn:
    def test_it_is_in_the_navigation_by_default(self, tmp_path, monkeypatch):
        client = _app(tmp_path, monkeypatch, cortex="true")
        assert "Cortex" in client.get("/").data.decode("utf-8")

    def test_the_page_answers(self, tmp_path, monkeypatch):
        client = _app(tmp_path, monkeypatch, cortex="true")
        assert client.get("/ontology").status_code == 200
