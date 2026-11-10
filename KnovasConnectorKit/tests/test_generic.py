"""SPEC-write-back-13 and -16: the kit names no source kind, no source operation and no source field; everything
specific to a source lives in a connector package."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
SOURCE_SPECIFIC = re.compile(r"\b(" + "|".join([
    "git" + "hub", "git" + "lab", "ji" + "ra", "one" + "drive", "share" + "point", "conflu" + "ence", "dro" + "pbox",
    "pull[ -]" + "requests?", "commits?", "branch(es)?", "merged?", "blob", "repositor(y|ies)", "issues?",
]) + r")\b", re.I)


@pytest.mark.parametrize("path", sorted(p for p in SRC.rglob("*") if p.is_file() and p.suffix in (".py", ".json")),
                         ids=lambda p: str(p.relative_to(SRC)))
def test_the_kit_names_no_source_kind(path):
    assert not SOURCE_SPECIFIC.findall(path.read_text(encoding="utf-8")), path.name
