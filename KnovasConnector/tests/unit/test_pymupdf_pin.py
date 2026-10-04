"""One PyMuPDF under both components' extraction (spec L7).

The Connector was unpinned (it got whatever the library's ``pymupdf>=1.24``
resolved to) while the Platform pins one version; the two images and the
two CI jobs then ran different PDF parsers under the same knovas-extract.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
RC_PYPROJECT = REPO / "KnovasConnector" / "pyproject.toml"
PLATFORM_REQUIREMENTS = REPO / "KnovasPlatform" / "components" / "docbridge_integration" / "requirements.txt"


@pytest.mark.skipif(not PLATFORM_REQUIREMENTS.is_file(), reason="the Platform is not in this checkout")
def test_the_connector_pins_the_platforms_pymupdf():
    deps = tomllib.loads(RC_PYPROJECT.read_text(encoding="utf-8"))["project"]["dependencies"]
    connector = [d.replace(" ", "") for d in deps if d.replace(" ", "").lower().startswith("pymupdf==")]
    platform = re.findall(r"^pymupdf==(\S+)\s*$", PLATFORM_REQUIREMENTS.read_text(encoding="utf-8"), re.MULTILINE)
    assert len(connector) == 1, deps
    assert len(platform) == 1
    assert connector[0].split("==", 1)[1] == platform[0]
