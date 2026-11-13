"""The real app with custom views on, for the gates and routes tests (Task 9; SS-626, SS-627).

A views folder under ``tmp_path``, a fake Brain client (``FakeViewsClient`` over a
graph, plus the attributes create_app needs from ``DummyKnovasClient``) and the
``custom_views:`` section of config.yaml.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from conftest import DummyFileHandler, DummyKnovasClient, _identity_app
from views_fixture import FakeViewsClient, FixtureGraph, client_zero_graph

NAV_LABEL = "Eigene Ansichten"


def client_class(graph: Optional[FixtureGraph] = None, base: type = FakeViewsClient) -> type:
    """A Knovas client class for create_app: the views' fake over ``graph``
    (client_zero_graph by default) with what DummyKnovasClient provides."""

    class Client(base, DummyKnovasClient):
        instances: list = []

        def __init__(self, config):
            DummyKnovasClient.__init__(self, config)
            base.__init__(self, graph if graph is not None else client_zero_graph())
            Client.instances.append(self)

    return Client


def write_view(root: Path, view_id: str, files: Optional[Dict[str, Any]] = None, **manifest: Any) -> Path:
    """A view folder with a valid manifest; ``files`` maps names to text (default: one entry file)."""
    data = {
        "format": 1,
        "title": {"de": f"Ansicht {view_id}", "en": f"View {view_id}"},
        "roles": ["*"],
        "entry": "index.html",
        "reads": {"Invariant": ["key"]},
    }
    data.update(manifest)
    folder = root / view_id
    folder.mkdir(parents=True)
    (folder / "view.json").write_text(json.dumps(data), encoding="utf-8")
    for name, content in ({"index.html": "<p>Hallo</p>"} if files is None else files).items():
        (folder / name).write_text(content, encoding="utf-8")
    return folder


def views_folder(tmp_path: Path, monkeypatch) -> Path:
    """The views folder, not writable for the Platform (as a read-only mount on a server),
    and the AutoDoc documents folder beside it, not around it."""
    folder = tmp_path / "custom-views"
    folder.mkdir()
    documents = tmp_path / "documents"
    documents.mkdir()
    monkeypatch.setattr(DummyFileHandler, "autodoc_path", str(documents))
    real_access = os.access

    def access(path, mode, *args, **kwargs):
        same = os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(folder))
        if mode & os.W_OK and same:
            return False
        return real_access(path, mode, *args, **kwargs)

    monkeypatch.setattr(os, "access", access)
    return folder


def views_yaml(folder: Path, *, enabled: bool = True, **extra: Any) -> str:
    lines = ["custom_views:", f'  enabled: "{str(enabled).lower()}"', f'  dir: "{folder.as_posix()}"']
    lines += [f'  {key}: "{value}"' for key, value in extra.items()]
    return "\n".join(lines) + "\n"


def identity_app(platform_db, tmp_path, monkeypatch, folder: Path, *, enabled: bool = True,
                 graph_mode: bool = True, client_cls: Optional[type] = None, **extra: Any):
    """The app with accounts on."""
    _graph_mode(monkeypatch, graph_mode)
    return _identity_app(platform_db, tmp_path, monkeypatch, client_cls=client_cls or client_class(),
                         extra_yaml=views_yaml(folder, enabled=enabled, **extra))


def accounts_off_app(tmp_path, monkeypatch, folder: Path, *, enabled: bool = True, graph_mode: bool = True):
    """The app with accounts off and no login (the Cortex switch's app)."""
    _graph_mode(monkeypatch, graph_mode)
    monkeypatch.setenv("WEB_SECRET_KEY", "test-secret-key-for-custom-views")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        'web:\n'
        '  secret_key: "${WEB_SECRET_KEY}"\n'
        '  login:\n'
        '    enabled: false\n'
        'identity:\n'
        '  enabled: false\n'
        'api:\n'
        '  base_url: "http://example.test"\n'
        + views_yaml(folder, enabled=enabled),
        encoding="utf-8",
    )
    from web_interface import app as web_app

    monkeypatch.setattr(web_app, "KnovasAPIClient", client_class())
    monkeypatch.setattr(web_app, "AutoDocFileHandler", DummyFileHandler)
    flask_app = web_app.create_app(str(config_path))
    flask_app.config.update(TESTING=True)
    return flask_app


def _graph_mode(monkeypatch, on: bool) -> None:
    if on:
        monkeypatch.setenv("ONTOLOGY_SOURCE", "graph")
    else:
        monkeypatch.delenv("ONTOLOGY_SOURCE", raising=False)
