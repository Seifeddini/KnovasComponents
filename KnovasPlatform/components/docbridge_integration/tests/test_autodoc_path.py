import os
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from web_interface import app as web_app  # noqa: E402
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


# --- Ein Präfix, von dem diese Installation nichts weiß ----------------------
#
# Die Kennung ist Freitext im Übernahme-Profil ("Mandanten Sync"); die Platform
# streift nur ab, was AUTODOC_IDENTIFIER_PREFIX nennt. Weichen beide ab, sagt
# das nichts an: die Suche läuft, die Snippets stehen da (ihre Sidecars hängen
# am Pointer, nicht an einem Pfad) -- und jede Vorschau antwortet 404.

class TestAPointerWithAnUnknownPrefix:
    def test_it_still_finds_the_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AUTODOC_IDENTIFIER_PREFIX", "tenant")
        (tmp_path / "kanzlei").mkdir()
        target = tmp_path / "kanzlei" / "brief.pdf"
        target.write_bytes(b"%PDF-1.4\n")
        resolved = web_app._confine_to_autodoc(
            str(tmp_path), "Mandanten Sync/kanzlei/brief.pdf"
        )
        assert resolved == str(target)

    def test_the_configured_prefix_still_wins(self, tmp_path, monkeypatch):
        """Wo die Kennung stimmt, wird nichts geraten."""
        monkeypatch.setenv("AUTODOC_IDENTIFIER_PREFIX", "tenant")
        (tmp_path / "kanzlei").mkdir()
        target = tmp_path / "kanzlei" / "brief.pdf"
        target.write_bytes(b"%PDF-1.4\n")
        resolved = web_app._confine_to_autodoc(
            str(tmp_path), "tenant/kanzlei/brief.pdf"
        )
        assert resolved == str(target)

    def test_a_file_that_is_simply_absent_stays_absent(self, tmp_path, monkeypatch):
        """Die Rückfallebene darf kein anderes Dokument unterschieben."""
        monkeypatch.setenv("AUTODOC_IDENTIFIER_PREFIX", "tenant")
        resolved = web_app._confine_to_autodoc(
            str(tmp_path), "Mandanten Sync/kanzlei/fehlt.pdf"
        )
        assert resolved is not None
        assert not os.path.exists(resolved)

    def test_it_never_leaves_the_mount(self, tmp_path, monkeypatch):
        """Verkürzen darf nie zum Ausbruch werden."""
        monkeypatch.setenv("AUTODOC_IDENTIFIER_PREFIX", "tenant")
        outside = tmp_path.parent / "geheim.pdf"
        outside.write_bytes(b"x")
        for pointer in ("Mandanten Sync/../geheim.pdf", "x/../../geheim.pdf"):
            resolved = web_app._confine_to_autodoc(str(tmp_path), pointer)
            assert resolved is None or str(tmp_path) in resolved, pointer

    def test_a_single_segment_pointer_is_left_alone(self, tmp_path, monkeypatch):
        """Ohne führendes Segment gibt es nichts abzustreifen."""
        monkeypatch.setenv("AUTODOC_IDENTIFIER_PREFIX", "tenant")
        resolved = web_app._confine_to_autodoc(str(tmp_path), "brief.pdf")
        assert resolved == str(tmp_path / "brief.pdf")
