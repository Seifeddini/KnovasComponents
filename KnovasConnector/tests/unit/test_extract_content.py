"""Description from file properties (spec L2): the library's keys, in order."""
from __future__ import annotations

from knovas_extract.result import Metadata

from sync.extract_content import description_from_metadata


def test_description_prefers_word_then_pdf_subject_then_xmp_description():
    every = Metadata(extra={"docx:subject": "Mandatsvertrag", "pdf:subject": "Ignoriert",
                            "pdf:xmp_description": "Auch ignoriert"})
    assert description_from_metadata(every) == "Mandatsvertrag"
    pdf = Metadata(extra={"pdf:subject": "Jahresrechnung 2023", "pdf:xmp_description": "Beschreibung"})
    assert description_from_metadata(pdf) == "Jahresrechnung 2023"
    xmp = Metadata(extra={"pdf:subject": "  ", "pdf:xmp_description": "Revisionsbericht"})
    assert description_from_metadata(xmp) == "Revisionsbericht", "a blank subject falls through"


def test_keys_the_library_never_produces_are_not_read():
    assert description_from_metadata(Metadata(extra={"subject": "x", "description": "y"})) is None
    assert description_from_metadata(Metadata(extra={})) is None
    assert description_from_metadata(None) is None


def test_description_is_capped_at_2000_characters():
    assert len(description_from_metadata(Metadata(extra={"pdf:subject": "a" * 5000}))) == 2000
