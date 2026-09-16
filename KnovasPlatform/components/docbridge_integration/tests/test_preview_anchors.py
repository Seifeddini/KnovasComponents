"""Wohin ein Klick auf eine Fundstelle im PDF fuehrt.

Der browsereigene Viewer laesst sich nichts hervorheben und nirgendwohin
scrollen -- er nimmt Seite und Hoehe aus dem Adressfragment
``#page=N&view=FitH,<top>``, und Anmerkungen zeigt er an, wenn sie im Dokument
stehen. Ohne die Hoehe bleibt nur die Seite: bei mehreren Fundstellen auf
derselben Seite -- dem Normalfall -- ergibt jeder Klick dieselbe Adresse, und
sichtbar passiert gar nichts.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from web_interface.preview import (  # noqa: E402
    ACTIVE_PASSAGE_COLOUR,
    highlight_pdf,
    passage_anchors,
)

pymupdf = pytest.importorskip("pymupdf")

OBEN = "Die Reaktionszeit bei Stoerungen betraegt vier Stunden."
UNTEN = "Die Messung erfolgt ueber das Ticketsystem des Auftragnehmers."
SEITE_ZWEI = "Abweichende Fristen sind in Anlage 4 abschliessend geregelt."


@pytest.fixture()
def dokument(tmp_path):
    """Zwei Saetze weit auseinander auf Seite 1, einer auf Seite 2."""
    path = tmp_path / "vertrag.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 90), OBEN, fontsize=11)
    page.insert_text((72, 700), UNTEN, fontsize=11)
    zwei = doc.new_page()
    zwei.insert_text((72, 120), SEITE_ZWEI, fontsize=11)
    doc.save(str(path))
    doc.close()
    return str(path)


def test_two_locations_on_one_page_get_different_heights(dokument):
    """Der Fall, in dem vorher nichts passierte."""
    anchors = passage_anchors(dokument, {1: OBEN, 2: UNTEN})
    assert anchors[1]["page"] == anchors[2]["page"] == 1
    assert anchors[1]["top"] != anchors[2]["top"]


def test_the_height_counts_from_the_bottom_like_the_viewer_expects(dokument):
    """PyMuPDF misst von oben, das Fragment erwartet es von unten. Der obere
    Satz muss also den GROESSEREN Wert bekommen."""
    anchors = passage_anchors(dokument, {1: OBEN, 2: UNTEN})
    assert anchors[1]["top"] > anchors[2]["top"]


def test_a_location_on_the_second_page_says_so(dokument):
    anchors = passage_anchors(dokument, {3: SEITE_ZWEI})
    assert anchors[3]["page"] == 2


def test_the_anchors_are_keyed_by_sentence_number_not_by_position(dokument):
    """Ein Satz, den das PDF nicht enthaelt, darf die uebrigen nicht
    verschieben -- sonst zeigt ein Klick auf die dritte Fundstelle auf die
    zweite."""
    anchors = passage_anchors(dokument, {1: OBEN, 2: "Steht so nicht im PDF.", 3: SEITE_ZWEI})
    assert set(anchors) == {1, 3}
    assert anchors[3]["page"] == 2


def test_a_text_that_is_not_in_the_document_yields_nothing(dokument):
    assert passage_anchors(dokument, {1: "Dieser Satz kommt nirgends vor."}) == {}


def test_anchors_never_raise_on_a_file_that_is_not_a_pdf(tmp_path):
    other = tmp_path / "notiz.txt"
    other.write_text("Kein PDF.", encoding="utf-8")
    assert passage_anchors(str(other), {1: OBEN}) == {}


def test_the_active_location_is_coloured_differently(dokument):
    """Sonst sind alle Stellen gleich bunt, und ein Klick auf eine andere
    Fundstelle aendert im PDF sichtbar nichts."""
    marked = highlight_pdf(dokument, [], passages=[OBEN, UNTEN], active=UNTEN)
    assert marked
    with pymupdf.open(stream=marked, filetype="pdf") as doc:
        farben = []
        for annot in doc[0].annots():
            stroke = (annot.colors or {}).get("stroke")
            if stroke:
                farben.append(tuple(round(c, 3) for c in stroke))
    aktiv = tuple(round(c, 3) for c in ACTIVE_PASSAGE_COLOUR)
    assert aktiv in farben
    assert len(set(farben)) > 1, "aktive und uebrige Stelle sehen gleich aus"


def test_without_an_active_location_nothing_is_singled_out(dokument):
    marked = highlight_pdf(dokument, [], passages=[OBEN, UNTEN])
    assert marked
    with pymupdf.open(stream=marked, filetype="pdf") as doc:
        farben = {
            tuple(round(c, 3) for c in (annot.colors or {}).get("stroke") or ())
            for annot in doc[0].annots()
        }
    assert tuple(round(c, 3) for c in ACTIVE_PASSAGE_COLOUR) not in farben
