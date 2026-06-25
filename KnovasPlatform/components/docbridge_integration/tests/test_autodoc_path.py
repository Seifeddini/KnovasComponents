import os
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from web_interface.app import _rel_path_for_autodoc  # noqa: E402


@pytest.mark.parametrize(
    ("env_prefix", "pointer", "expected"),
    [
        ("corpus", "corpus/010001-010500/a.docx", "010001-010500/a.docx"),
        ("winjur", "winjur/010001-010500/a.docx", "010001-010500/a.docx"),
        (
            "corpus,winjur",
            "corpus/010001-010500/a.docx",
            "010001-010500/a.docx",
        ),
        (
            "corpus,winjur",
            "winjur/010001-010500/a.docx",
            "010001-010500/a.docx",
        ),
        ("corpus", "010001-010500/a.docx", "010001-010500/a.docx"),
        ("winjur", "corpus/010001-010500/a.docx", "corpus/010001-010500/a.docx"),
    ],
)
def test_rel_path_for_autodoc(monkeypatch, env_prefix, pointer, expected):
    monkeypatch.setenv("AUTODOC_IDENTIFIER_PREFIX", env_prefix)
    assert _rel_path_for_autodoc(pointer) == expected
