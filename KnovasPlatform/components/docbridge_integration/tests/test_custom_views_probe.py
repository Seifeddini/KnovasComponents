"""The probe view (tests/fixtures/custom_views/probe) is a valid view and builds into a document (SS-628).

It is the page that browser evidence runs (reads one type, then probes what a view may not do), so it must pass the
registry and the document builder like any real view, and it must follow the rules it checks.
"""

import json
import os
import re
import shutil
from pathlib import Path

import pytest

from custom_views.document import build_document, new_nonce
from custom_views.registry import Registry
from custom_views.settings import CustomViewsSettings
from views_fixture import client_zero_graph

PROBE = Path(__file__).resolve().parent / "fixtures" / "custom_views" / "probe"


@pytest.fixture
def probe_registry(tmp_path, monkeypatch):
    folder = tmp_path / "views"
    folder.mkdir()
    shutil.copytree(PROBE, folder / "probe")
    real_access = os.access

    def access(path, mode, *args, **kwargs):  # the folder is read-only for the Platform, as on a server
        if mode & os.W_OK and os.path.normcase(os.path.abspath(path)) == os.path.normcase(str(folder)):
            return False
        return real_access(path, mode, *args, **kwargs)

    monkeypatch.setattr(os, "access", access)
    return Registry(CustomViewsSettings(enabled=True, dir=str(folder)))


def test_registry_loads_the_probe_view_as_valid(probe_registry):
    assert probe_registry.invalid() == []
    view = probe_registry.get("probe")
    assert view is not None
    assert view.manifest.allows(frozenset({"member"}))
    assert view.manifest.link_hosts == ("example.com",)


def test_build_document_accepts_the_probe_view(probe_registry):
    nonce = new_nonce()
    doc = build_document(probe_registry.get("probe"), nonce)
    assert "PROBE DONE" in doc and doc.count(f'nonce="{nonce}"') >= 3


def test_the_probe_reads_one_type_and_one_fact_that_the_fixture_graph_has():
    manifest = json.loads((PROBE / "view.json").read_text(encoding="utf-8"))
    graph = client_zero_graph()
    (type_name, facts), = manifest["reads"].items()
    assert type_name in graph.types and len(facts) == 1
    assert facts[0] in {a["name"] for a in graph.schemas[graph.types[type_name]]}


def test_the_probe_draws_with_textcontent():
    script = (PROBE / "probe.js").read_text(encoding="utf-8")
    assert len(re.findall(r"\.innerHTML\s*=", script)) == 1, "the one deliberate attack probe only"
    assert "textContent" in script and "outerHTML" not in script and "insertAdjacentHTML" not in script
