"""Folder on the RemoteController host -> pointer prefix for a folder rule.

The vectors follow how RemoteController builds pointers
(``identifier_prefix + "/" + relative path``) and the server's raw
``startswith`` match, which is why every prefix ends in ``/``.
"""

from __future__ import annotations

import pathlib

import pytest

from identity.rc_pointers import (
    FolderOutsideSources,
    normalize_pointer_prefix,
    prefix_depth,
    prefix_for_folder,
    rc_pointer_prefix,
    relative_folder,
)


@pytest.mark.parametrize("ident, source, folder, expected", [
    # The source root itself.
    ("kanzlei", "/data/corpus", "/data/corpus", "kanzlei/"),
    ("kanzlei", "/data/corpus/", "/data/corpus", "kanzlei/"),
    # Sub-folders, trailing slashes on either side.
    ("kanzlei", "/data/corpus", "/data/corpus/Mandate", "kanzlei/Mandate/"),
    ("kanzlei", "/data/corpus/", "/data/corpus/Mandate/2024/", "kanzlei/Mandate/2024/"),
    # Surrounding blanks in the identifier prefix go; inner spelling stays.
    ("  kanzlei ", "/data/corpus", "/data/corpus/Muster AG", "kanzlei/Muster AG/"),
    # Windows paths: backslashes, a drive letter compared without case.
    ("kanzlei", "C:\\Daten\\Mandate", "C:\\Daten\\Mandate\\2024\\Muster",
     "kanzlei/2024/Muster/"),
    ("kanzlei", "c:\\daten\\mandate", "C:\\Daten\\Mandate\\Beispiel GmbH\\",
     "kanzlei/Beispiel GmbH/"),
    # UNC share.
    ("kanzlei", "\\\\server\\akten", "\\\\server\\akten\\Mandate", "kanzlei/Mandate/"),
    # Dot segments are resolved before the comparison.
    ("kanzlei", "/data/corpus", "/data/corpus/./Mandate/../Mandate", "kanzlei/Mandate/"),
])
def test_rc_pointer_prefix_vectors(ident, source, folder, expected):
    assert rc_pointer_prefix(ident, source, folder) == expected


@pytest.mark.parametrize("source, folder", [
    ("/data/corpus", "/data/other"),
    # A sibling whose name merely starts with the source name.
    ("/data/corpus", "/data/corpus2/Mandate"),
    # A dot-dot escape out of the source.
    ("/data/corpus", "/data/corpus/../etc"),
    ("/data/corpus", ""),
    ("", "/data/corpus"),
    # POSIX paths are case-sensitive, as the file system is.
    ("/data/Corpus", "/data/corpus/Mandate"),
])
def test_a_folder_outside_the_source_is_refused(source, folder):
    assert relative_folder(source, folder) is None
    with pytest.raises(FolderOutsideSources):
        rc_pointer_prefix("kanzlei", source, folder)


def test_an_empty_identifier_prefix_is_refused():
    with pytest.raises(ValueError):
        rc_pointer_prefix("  ", "/data/corpus", "/data/corpus/Mandate")


def test_prefix_for_folder_picks_the_containing_source():
    prefix, matches = prefix_for_folder(
        "kanzlei", ["/data/a", "/data/b"], "/data/b/Mandate/2024")
    assert prefix == "kanzlei/Mandate/2024/"
    assert matches == 1


def test_prefix_for_folder_uses_the_most_specific_of_nested_sources():
    prefix, matches = prefix_for_folder(
        "kanzlei", ["/data", "/data/mandate"], "/data/mandate/2024")
    assert prefix == "kanzlei/2024/"
    assert matches == 2


def test_prefix_for_folder_outside_every_source_is_refused():
    with pytest.raises(FolderOutsideSources):
        prefix_for_folder("kanzlei", ["/data/a", "/data/b"], "/srv/elsewhere")
    with pytest.raises(FolderOutsideSources):
        prefix_for_folder("kanzlei", [], "/data/a")


@pytest.mark.parametrize("typed, expected", [
    ("kanzlei/Mandate", "kanzlei/Mandate/"),
    ("kanzlei/Mandate/", "kanzlei/Mandate/"),
    ("  kanzlei\\Mandate\\2024 ", "kanzlei/Mandate/2024/"),
    ("kanzlei", "kanzlei/"),
])
def test_a_typed_prefix_gets_the_same_validation(typed, expected):
    assert normalize_pointer_prefix(typed) == expected


@pytest.mark.parametrize("typed", ["", "   ", "/", "//", "kanzlei/../x", "kanzlei/./x",
                                   "kanzlei\x00/x", "k/" + "a" * 2000])
def test_a_bad_typed_prefix_is_refused(typed):
    with pytest.raises(ValueError):
        normalize_pointer_prefix(typed)


def test_prefix_depth_counts_segments_only():
    assert prefix_depth("kanzlei/") == 1
    assert prefix_depth("kanzlei/Mandate/2024/") == 3
    assert prefix_depth("") == 0


def test_module_is_ascii_only():
    source = pathlib.Path(__file__).resolve().parents[1] / "src" / "identity" / "rc_pointers.py"
    assert source.read_bytes().isascii()


def test_a_folder_name_ending_in_a_space_keeps_it():
    """platform-admin-ingestion-7: RemoteController keeps the space in every
    pointer (``relative_to(...).as_posix()``), so the rule prefix must too --
    stripped, it would miss the folder and hit a sibling without the space."""
    prefix, _ = prefix_for_folder("kanzlei", ["/data/corpus"], "/data/corpus/Muster AG ")
    assert prefix == "kanzlei/Muster AG /"
    assert "kanzlei/Muster AG /x.pdf".startswith(prefix)
    assert not "kanzlei/Muster AG/y.pdf".startswith(prefix)
    assert relative_folder("/data/corpus", "/data/corpus/ Beispiel GmbH") == " Beispiel GmbH"
    assert relative_folder("   ", "/data/corpus") is None
