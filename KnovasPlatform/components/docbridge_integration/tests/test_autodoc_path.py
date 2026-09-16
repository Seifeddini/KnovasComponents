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


# --- Ein Profil, das auf einen Unterordner zeigt -----------------------------
#
# Der Pfad im Pointer ist relativ zum QUELLORDNER des Übernahme-Profils
# (sync_executor setzt rel_root auf ihn), gemountet wird hier aber
# KNOVAS_DOCUMENTS_PATH im Ganzen. Zeigt das Profil auf
# /mnt/documents/kanzlei/Mandanten, fehlt jedem Pointer vorne "kanzlei/
# Mandanten" -- und nichts sagt das: Suche und Snippets laufen weiter, nur
# Download und Öffnen finden die Datei nicht mehr.

class TestAProfileRootedBelowTheMount:
    def _reset(self):
        web_app._autodoc_offset.clear()

    def test_the_document_is_found_anyway(self, tmp_path, monkeypatch):
        self._reset()
        monkeypatch.setenv("AUTODOC_IDENTIFIER_PREFIX", "Mandanten Sync")
        deep = tmp_path / "kanzlei" / "Mandanten" / "2023-041"
        deep.mkdir(parents=True)
        target = deep / "klage.txt"
        target.write_text("x", encoding="utf-8")
        resolved = web_app._confine_to_autodoc(
            str(tmp_path), "Mandanten Sync/2023-041/klage.txt"
        )
        assert resolved == str(target)

    def test_the_offset_is_learned_once(self, tmp_path, monkeypatch):
        """Sonst liefe der Suchlauf bei jedem Vorschaubild erneut."""
        self._reset()
        monkeypatch.setenv("AUTODOC_IDENTIFIER_PREFIX", "Mandanten Sync")
        deep = tmp_path / "kanzlei" / "Mandanten" / "2023-041"
        deep.mkdir(parents=True)
        (deep / "a.txt").write_text("x", encoding="utf-8")
        (deep / "b.txt").write_text("x", encoding="utf-8")
        web_app._confine_to_autodoc(str(tmp_path), "Mandanten Sync/2023-041/a.txt")
        assert web_app._autodoc_offset.get(str(tmp_path)) == "kanzlei/Mandanten"

        calls = []
        original = web_app._discover_autodoc_offset
        monkeypatch.setattr(
            web_app, "_discover_autodoc_offset",
            lambda *a, **kw: (calls.append(a), original(*a, **kw))[1],
        )
        resolved = web_app._confine_to_autodoc(
            str(tmp_path), "Mandanten Sync/2023-041/b.txt"
        )
        assert resolved == str(deep / "b.txt")
        assert calls == [], "the offset was already known; it must not walk again"

    def test_a_document_that_is_simply_absent_stays_absent(self, tmp_path, monkeypatch):
        self._reset()
        monkeypatch.setenv("AUTODOC_IDENTIFIER_PREFIX", "Mandanten Sync")
        (tmp_path / "kanzlei" / "Mandanten").mkdir(parents=True)
        resolved = web_app._confine_to_autodoc(
            str(tmp_path), "Mandanten Sync/2023-041/fehlt.txt"
        )
        assert resolved is not None
        assert not os.path.exists(resolved)

    def test_the_direct_path_still_wins(self, tmp_path, monkeypatch):
        """Wo der Pointer stimmt, wird nichts gesucht."""
        self._reset()
        monkeypatch.setenv("AUTODOC_IDENTIFIER_PREFIX", "Mandanten Sync")
        flat = tmp_path / "2023-041"
        flat.mkdir(parents=True)
        target = flat / "klage.txt"
        target.write_text("x", encoding="utf-8")
        deep = tmp_path / "kanzlei" / "Mandanten" / "2023-041"
        deep.mkdir(parents=True)
        (deep / "klage.txt").write_text("anderes dokument", encoding="utf-8")
        resolved = web_app._confine_to_autodoc(
            str(tmp_path), "Mandanten Sync/2023-041/klage.txt"
        )
        assert resolved == str(target)

    def test_the_search_stays_inside_the_mount(self, tmp_path, monkeypatch):
        self._reset()
        monkeypatch.setenv("AUTODOC_IDENTIFIER_PREFIX", "Mandanten Sync")
        outside = tmp_path.parent / "ausserhalb"
        outside.mkdir(exist_ok=True)
        (outside / "geheim.txt").write_text("x", encoding="utf-8")
        mount = tmp_path / "mount"
        mount.mkdir()
        resolved = web_app._confine_to_autodoc(
            str(mount), "Mandanten Sync/../ausserhalb/geheim.txt"
        )
        assert resolved is None or str(mount) in resolved
