"""The customer image must not carry pymupdf-layout (PolyForm-NC).

KNOVAS_EXTRACT_REF is pinned to knovas-extract 0.4.0a1, whose [pdf] extra
is clean, so the CI step that asserts the package is absent gates the build:
a step with ``continue-on-error`` would let a later bump ship the
non-redistributable package with a green job.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
CI = REPO / ".github" / "workflows" / "ci.yml"
DOCKERFILE = REPO / "RemoteController" / "Dockerfile"
STEP = "- name: No pymupdf-layout (PolyForm-NC) in the customer image"


def _step(text: str) -> str:
    start = text.index(STEP)
    indent = text[:start].rsplit("\n", 1)[1]
    rest = text[start + len(STEP):]
    end = rest.find("\n" + indent + "- ")
    return rest if end < 0 else rest[:end]


@pytest.mark.skipif(not CI.is_file(), reason="the workflow is not in this checkout")
def test_the_license_step_blocks_the_build():
    step = _step(CI.read_text(encoding="utf-8"))
    assert "pip show pymupdf-layout" in step
    assert "continue-on-error" not in step


@pytest.mark.skipif(not CI.is_file(), reason="the workflow is not in this checkout")
def test_the_gate_runs_on_the_pinned_library():
    ci = CI.read_text(encoding="utf-8")
    pinned = re.search(r"^ARG KNOVAS_EXTRACT_REF=(\S+)$", DOCKERFILE.read_text(encoding="utf-8"),
                       re.MULTILINE)
    assert pinned and re.fullmatch(r"[0-9a-f]{40}", pinned.group(1)), "a sha, not a branch"
    assert pinned.group(1) in ci
