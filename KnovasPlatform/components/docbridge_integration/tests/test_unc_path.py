import os
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from unc_path import filesystem_path_to_unc, map_path_with_roots, normalize_unc_root


def test_normalize_unc_root():
    assert normalize_unc_root(" //server/share/ ") == "\\\\server\\share"


def test_filesystem_path_to_unc_basic(tmp_path):
    base = tmp_path / "AutoDoc"
    (base / "Briefe").mkdir(parents=True)
    target = base / "Briefe" / "a.docx"
    target.write_text("x", encoding="utf-8")
    local = str(base)
    unc = "\\\\fs\\Doc\\AutoDoc"
    got = filesystem_path_to_unc(str(target), local, unc)
    assert got == "\\\\fs\\Doc\\AutoDoc\\Briefe\\a.docx"


def test_filesystem_path_to_unc_rejects_escape(tmp_path):
    base = tmp_path / "AutoDoc"
    base.mkdir()
    other = tmp_path / "Other"
    other.mkdir()
    target = other / "a.docx"
    target.write_text("x", encoding="utf-8")
    local = str(base)
    unc = "\\\\fs\\Doc\\AutoDoc"
    assert filesystem_path_to_unc(str(target), local, unc) is None


def test_map_path_with_roots_first_match(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    xf = b / "x.pdf"
    xf.write_text("x", encoding="utf-8")
    roots = [(str(a), "\\\\s\\A"), (str(b), "\\\\s\\B")]
    assert map_path_with_roots(str(xf), roots) == "\\\\s\\B\\x.pdf"
