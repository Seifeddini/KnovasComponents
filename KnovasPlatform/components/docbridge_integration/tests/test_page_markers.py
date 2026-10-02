"""Page-break markers on the wire (GI-INGEST-17), Platform mirror.

Ported from ``RemoteController/tests/unit/test_page_markers.py``: the admin
upload builds the same wire format as the RC's sync pipeline, through its own
copy of the chunker (``knovas_transmit``). Alloy: KnowledgeBase
``models/alloy/mechanisms/client_pipeline.als`` preds
``DeclaredPageMechanism`` and ``MarkerInsertionMechanism`` (model
``data_plane/page_break_provenance.als``).

Contract (one coordinate system — the UNMARKED content.text):
  * a part's page_number is the page of its first character; the join
    whitespace between two pages belongs to the preceding page;
  * before the first character of every TEXT page whose start lies
    STRICTLY inside the part, the chunker writes ``page.index -
    last_text_page.index`` form feeds (one for the page, one more per EMPTY
    page in between — empty pages have no text and no line_start);
  * never a marker at the part's first character; offsets are relative to
    the part, not the document;
  * the sidecar is built from the unmarked text (test_knovas_extract_upload).
"""

from __future__ import annotations

import pytest

pytest.importorskip("knovas_extract")

from knovas_extract.result import Page, Section, Sentence  # noqa: E402

from knovas_transmit.chunking import iter_text_chunks_with_location  # noqa: E402
from knovas_transmit.page_markers import insert_page_markers  # noqa: E402
from knovas_transmit.section_pages import apply_section_heading, section_prefix_at_offset  # noqa: E402

FF = "\f"


def _pages_and_text(page_texts: list[str | None]) -> tuple[str, list[Page], list[Sentence]]:
    """Join page texts with "\\n\\n" the way the PDF extractor does; an entry
    of None is an EMPTY page (no text, line_start/line_end None)."""
    text_parts: list[str] = []
    pages: list[Page] = []
    sentences: list[Sentence] = []
    line = 1
    offset = 0
    first = True
    for idx, pt in enumerate(page_texts):
        if not pt:
            pages.append(Page(index=idx, text="", line_start=None, line_end=None))
            continue
        if not first:
            text_parts.append("\n\n")
            offset += 2
            line += 2
        first = False
        n_lines = pt.count("\n") + 1
        pages.append(Page(index=idx, text=pt, line_start=line, line_end=line + n_lines - 1))
        sentences.append(Sentence(
            index=len(sentences), text=pt, char_start=offset, char_end=offset + len(pt),
            line_start=line, line_end=line + n_lines - 1,
            page_index=idx, page_number=idx + 1, section_index=None,
        ))
        text_parts.append(pt)
        offset += len(pt)
        line += n_lines - 1
    return "".join(text_parts), pages, sentences


class TestMarkerInsertionMechanism:
    """Alloy obligation: mechanisms/client_pipeline.als::MarkerInsertionMechanism."""

    def test_no_marker_at_part_start(self):
        text, pages, _ = _pages_and_text(["Seite eins.", "Seite zwei.", "Seite drei."])
        start = text.index("Seite zwei.")
        out = insert_page_markers(text[start:], pages, start, text)
        assert not out.startswith(FF)
        assert out.count(FF) == 1, "only page 3's start lies strictly inside the part"
        assert out == "Seite zwei.\n\n" + FF + "Seite drei."

    def test_offsets_are_relative_to_the_part(self):
        text, pages, _ = _pages_and_text(["Seite eins.", "Seite zwei.", "Seite drei."])
        start = text.index("Seite drei.")
        out = insert_page_markers(text[start:], pages, start, text)
        assert out == "Seite drei." and FF not in out

    def test_marker_sits_immediately_before_the_page_first_character(self):
        text, pages, _ = _pages_and_text(["Seite eins.", "Seite zwei."])
        assert insert_page_markers(text, pages, 0, text) == "Seite eins.\n\n" + FF + "Seite zwei."

    def test_empty_page_between_text_pages_yields_two_markers(self):
        text, pages, _ = _pages_and_text(["Seite eins.", None, "Seite drei."])
        assert insert_page_markers(text, pages, 0, text) == "Seite eins.\n\n" + FF + FF + "Seite drei."

    def test_two_empty_pages_yield_three_markers(self):
        text, pages, _ = _pages_and_text(["A.", None, None, "D."])
        assert insert_page_markers(text, pages, 0, text) == "A.\n\n" + FF * 3 + "D."

    def test_leading_empty_pages_do_not_produce_markers(self):
        text, pages, _ = _pages_and_text([None, None, "Seite drei.", "Seite vier."])
        assert insert_page_markers(text, pages, 0, text) == "Seite drei.\n\n" + FF + "Seite vier."

    def test_marker_is_inserted_after_the_heading_prefix(self):
        text, pages, sentences = _pages_and_text(["Intro\n\nErster Absatz.", "Zweite Seite."])
        sections = [Section(heading="Intro", level=1, text="Intro", line_start=1, line_end=1)]
        parts = list(iter_text_chunks_with_location(
            text, 10_000, sentences=sentences, sections=sections, pages=pages, page_markers=True,
        ))
        assert len(parts) == 1
        snippet = parts[0][0]
        assert snippet.startswith("# Intro\n\n")
        assert snippet.endswith(FF + "Zweite Seite.")
        assert snippet.count(FF) == 1

    def test_markers_fit_inside_the_part_budget(self):
        text, pages, sentences = _pages_and_text([f"Seite {i}." * 3 for i in range(1, 30)])
        part_max = 100
        for snippet, _page, _sn, _start in iter_text_chunks_with_location(
            text, part_max, sentences=sentences, pages=pages, page_markers=True,
        ):
            assert len(snippet) <= part_max


class TestDeclaredPageMechanism:
    """Alloy obligation: mechanisms/client_pipeline.als::DeclaredPageMechanism."""

    def test_part_starting_on_join_line_is_declared_on_preceding_page(self):
        text, pages, sentences = _pages_and_text(["Seite eins. " * 20, "Seite zwei. " * 20])
        join = text.index("\n\n")
        parts = list(iter_text_chunks_with_location(
            text, join + 2, sentences=sentences, pages=pages, page_markers=True,
        ))
        assert len(parts) >= 2
        snippet, page_number, _sn, start = parts[1]
        assert start <= join + 2
        assert page_number == 1
        assert snippet.lstrip("\n").startswith(FF + "Seite zwei.")
        assert snippet.count(FF) == 1

    def test_part_starting_on_page_first_char_is_declared_on_that_page(self):
        text, pages, sentences = _pages_and_text(["Seite eins.", "Seite zwei."])
        start = text.index("Seite zwei.")
        parts = list(iter_text_chunks_with_location(
            text, start, sentences=sentences, pages=pages, page_markers=True,
        ))
        tail = [p for p in parts if p[3] == start]
        assert tail and tail[0][1] == 2 and FF not in tail[0][0]

    def test_page_numbers_and_sentence_numbers_are_unchanged_by_markers(self):
        text, pages, sentences = _pages_and_text(["Seite eins.", "Seite zwei.", "Seite drei."])
        without = list(iter_text_chunks_with_location(text, 10_000, sentences=sentences, pages=pages))
        with_markers = list(iter_text_chunks_with_location(
            text, 10_000, sentences=sentences, pages=pages, page_markers=True,
        ))
        assert [(p, s, o) for _, p, s, o in without] == [(p, s, o) for _, p, s, o in with_markers]
        assert [t.replace(FF, "") for t, *_ in with_markers] == [t for t, *_ in without]


def test_page_markers_default_off_keeps_the_wire_unchanged():
    text = "Seite eins.\n\nSeite zwei."
    pages = [
        Page(index=0, text="Seite eins.", line_start=1, line_end=1),
        Page(index=1, text="Seite zwei.", line_start=3, line_end=3),
    ]
    plain = list(iter_text_chunks_with_location(text, 10_000, pages=pages))
    marked = list(iter_text_chunks_with_location(text, 10_000, pages=pages, page_markers=True))
    assert plain[0][0] == text and FF not in plain[0][0]
    assert marked[0][0] == "Seite eins.\n\n" + FF + "Seite zwei."


def test_heading_is_replaced_not_duplicated_when_the_text_carries_it():
    """DOCX text carries the heading as its first line; the chunker used to
    yield "# Intro\\n\\nIntro" (prepended AND repeated)."""
    assert apply_section_heading("Intro\n\nErster Absatz.", "# Intro\n\n") == ("# Intro\n\nErster Absatz.", 2)
    assert apply_section_heading("## Intro\nBody", "# Intro\n\n") == ("# Intro\nBody", -1)
    assert apply_section_heading("# Intro\n\nBody", "# Intro\n\n") == ("# Intro\n\nBody", 0)
    assert apply_section_heading("Body only.", "# Intro\n\n") == ("# Intro\n\nBody only.", len("# Intro\n\n"))


def test_heading_level_is_capped_at_the_server_regex():
    """``^(#{1,4})\\s+`` on the server: a level-6 section must not reach the
    embedder as a literal "###### " prefix."""
    text = "Tief\n\nBody."
    sections = [Section(heading="Tief", level=6, text="Body.", line_start=1, line_end=3)]
    assert section_prefix_at_offset(sections, text, 0) == "#### Tief\n\n"
