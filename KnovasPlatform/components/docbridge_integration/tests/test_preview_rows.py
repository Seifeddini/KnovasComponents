"""Fundstellen in Tabellenzeilen: wohin ein Klick auf eine Bilanzzeile fuehrt.

Der Sidecar traegt eine Zeile der markdown-lite-Extraktion als
``Fluessige Mittel | 2023: 1'234'567.80 | 2022: 987'654.30``. Auf der
digitalen Seite stehen die Zellen durch Leerraum getrennt, ohne Trenner und
ohne Fold-Schluessel -- ``page.search_for`` der ganzen Zeile fand nichts,
und der Viewer blieb auf Seite 1 stehen (Diagnose P6, Plan §6).
"""
from __future__ import annotations

import pytest

pymupdf = pytest.importorskip("pymupdf")

from web_interface.preview import (  # noqa: E402
    MIN_CELL_CHARS,
    highlight_pdf,
    passage_anchors,
    search_spans,
)

ROW = "Flüssige Mittel | 2023: 1'234'567.80 | 2022: 987'654.30"
ROW_TOTAL = "Total Aktiven | 2023: 2'345'678.00 | 2022: 2'100'000.50"
PROSE = "Die Reaktionszeit bei Stoerungen betraegt vier Stunden."


class TestSearchSpans:
    def test_a_prose_passage_is_searched_as_it_is(self):
        assert search_spans(PROSE) == [PROSE]

    def test_a_row_is_searched_without_separators_and_fold_keys(self):
        spans = search_spans(ROW)
        assert spans[0] == ROW
        assert "Flüssige Mittel 1'234'567.80 987'654.30" in spans
        assert " | " not in spans[1] and "2023:" not in spans[1]

    def test_the_longest_cell_is_the_last_resort(self):
        assert search_spans(ROW)[-1] == "Flüssige Mittel"
        assert len(search_spans(ROW)[-1]) >= MIN_CELL_CHARS

    def test_short_cells_are_never_searched_alone(self):
        """Jahreszahlen und Betraege stehen auf jeder Bilanzseite."""
        spans = search_spans("Anhang | 2023: 2.1 | 2022: 1.9")
        assert all(len(s) >= MIN_CELL_CHARS for s in spans)
        assert "2.1" not in spans and "1.9" not in spans

    def test_a_short_passage_yields_nothing(self):
        assert search_spans("Kurz | 1.00") == []

    def test_a_negative_amount_in_brackets_loses_its_key_too(self):
        spans = search_spans("Jahresverlust | 2023: (12'345.00) | 2022: -1'000.00")
        assert "Jahresverlust (12'345.00) -1'000.00" in spans


@pytest.fixture()
def bilanz(tmp_path):
    """Seite 1: Briefkopf und zwei Bilanzzeilen, die Zellen als eigene
    Textbloecke weit auseinander (so setzt ein Buchhaltungsprogramm sie).
    Seite 2: eine Zeile in einem Stueck."""
    path = tmp_path / "bilanz.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 80), "Müller AG, Bahnhofstrasse 1, 8001 Zürich", fontsize=10)
    page.insert_text((72, 300), "Flüssige Mittel", fontsize=9)
    page.insert_text((320, 300), "1'234'567.80", fontsize=9)
    page.insert_text((440, 300), "987'654.30", fontsize=9)
    page.insert_text((72, 316), "Total Aktiven", fontsize=9)
    page.insert_text((320, 316), "2'345'678.00", fontsize=9)
    page.insert_text((440, 316), "2'100'000.50", fontsize=9)
    zwei = doc.new_page()
    zwei.insert_text((72, 120), "Flüssige Mittel 1'234'567.80 987'654.30", fontsize=9)
    doc.save(str(path))
    doc.close()
    return str(path)


def test_a_bilanz_row_is_anchored_on_its_page(bilanz):
    anchors = passage_anchors(bilanz, {7: ROW})
    assert anchors[7]["page"] == 1
    # Die Hoehe zaehlt von unten: die Zeile (y=300 von oben) liegt unter dem
    # Briefkopf (y=80), bekommt also den KLEINEREN Wert.
    kopf = passage_anchors(bilanz, {1: "Müller AG, Bahnhofstrasse 1, 8001 Zürich"})
    assert anchors[7]["top"] < kopf[1]["top"]


def test_two_rows_on_one_page_get_different_heights(bilanz):
    anchors = passage_anchors(bilanz, {1: ROW, 2: ROW_TOTAL})
    assert anchors[1]["page"] == anchors[2]["page"] == 1
    assert anchors[1]["top"] != anchors[2]["top"]


def test_a_row_set_in_one_piece_matches_the_flattened_line(bilanz, monkeypatch):
    """Seite 2 traegt die Zeile ohne Luecken: die Fassung ohne Trenner und
    Schluessel trifft sie ganz, nicht nur die laengste Zelle."""
    with pymupdf.open(bilanz) as doc:
        flat = search_spans(ROW)[1]
        assert doc[1].search_for(flat), "die normalisierte Zeile steht so auf Seite 2"
        assert not doc[1].search_for(ROW), "die Sidecar-Zeile selbst steht nirgends"


def test_the_row_is_highlighted_in_the_served_pdf(bilanz):
    marked = highlight_pdf(bilanz, [], passages=[ROW], active=ROW)
    assert marked
    with pymupdf.open(stream=marked, filetype="pdf") as doc:
        assert len(list(doc[0].annots())) >= 1


def test_a_row_that_is_not_in_the_document_yields_nothing(bilanz):
    assert passage_anchors(bilanz, {1: "Passiven total | 2023: 9'999.99 | 2022: 8'888.88"}) == {}
