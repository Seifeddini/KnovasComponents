"""Obligation tests: page-break markers on the wire (GI-INGEST-17).

Alloy: KnowledgeBase ``models/alloy/mechanisms/client_pipeline.als`` preds
``DeclaredPageMechanism`` and ``MarkerInsertionMechanism`` (model
``data_plane/page_break_provenance.als``; mutants ``pagebreak__marker_at_
part_start``, ``pagebreak__absolute_offsets``, ``pagebreak__empty_pages_
uncounted``). The server half is ``ingest_pipeline.als::PageAdvanceMechanism``
(KnowledgeBase ``tests/test_information_object_processing.py::
TestPageBreakProvenance``).

Contract (one coordinate system — the UNMARKED content.text):
  * a part's page_number is the page of its first character; the join
    whitespace between two pages belongs to the preceding page;
  * before the first character of every TEXT page whose start lies
    STRICTLY inside the part, the RC writes ``page.index - last_text_page.
    index`` form feeds (one for the page, one more per EMPTY page in
    between — empty pages have no text and no line_start);
  * never a marker at the part's first character; offsets are relative to
    the part, not the document;
  * the sidecar is built from the unmarked text (test_knovas_uploader).

Planned API: ``sync.page_markers.insert_page_markers(snippet, pages,
part_start, text) -> str`` and ``iter_text_chunks_with_location(...,
page_markers=True)``. RED until the feature lands.
"""

from __future__ import annotations

import pytest
from knovas_extract.result import Page, Sentence

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
        # one sentence per page is enough for location lookups
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
        """A part that begins exactly on page 2's first character carries
        no marker for page 2 — its page_number already says 2."""
        from sync.page_markers import insert_page_markers

        text, pages, _ = _pages_and_text(["Seite eins.", "Seite zwei.", "Seite drei."])
        p2 = pages[1]
        assert p2.line_start is not None
        start = text.index("Seite zwei.")
        snippet = text[start:]
        out = insert_page_markers(snippet, pages, start, text)
        assert not out.startswith(FF)
        assert out.count(FF) == 1, "only page 3's start lies strictly inside the part"
        assert out == "Seite zwei.\n\n" + FF + "Seite drei."

    def test_offsets_are_relative_to_the_part(self):
        """Pages that start BEFORE the part never produce a marker inside it."""
        from sync.page_markers import insert_page_markers

        text, pages, _ = _pages_and_text(["Seite eins.", "Seite zwei.", "Seite drei."])
        start = text.index("Seite drei.")
        out = insert_page_markers(text[start:], pages, start, text)
        assert out == "Seite drei." and FF not in out

    def test_marker_sits_immediately_before_the_page_first_character(self):
        from sync.page_markers import insert_page_markers

        text, pages, _ = _pages_and_text(["Seite eins.", "Seite zwei."])
        out = insert_page_markers(text, pages, 0, text)
        assert out == "Seite eins.\n\n" + FF + "Seite zwei."

    def test_empty_page_between_text_pages_yields_two_markers(self):
        """Blank duplex back side (or an OCR-skipped page) between page 1
        and page 3: two form feeds before page 3's first character."""
        from sync.page_markers import insert_page_markers

        text, pages, _ = _pages_and_text(["Seite eins.", None, "Seite drei."])
        out = insert_page_markers(text, pages, 0, text)
        assert out == "Seite eins.\n\n" + FF + FF + "Seite drei."

    def test_two_empty_pages_yield_three_markers(self):
        from sync.page_markers import insert_page_markers

        text, pages, _ = _pages_and_text(["A.", None, None, "D."])
        out = insert_page_markers(text, pages, 0, text)
        assert out == "A.\n\n" + FF * 3 + "D."

    def test_leading_empty_pages_do_not_produce_markers(self):
        """The first text page's number is carried by page_number, not by
        markers (there is no earlier text page to count from)."""
        from sync.page_markers import insert_page_markers

        text, pages, _ = _pages_and_text([None, None, "Seite drei.", "Seite vier."])
        out = insert_page_markers(text, pages, 0, text)
        assert out == "Seite drei.\n\n" + FF + "Seite vier."

    def test_marker_is_inserted_after_the_heading_prefix(self):
        """The heading prefix the chunker prepends shifts wire offsets; the
        marker must still land before the page's first character."""
        from sync.chunking import iter_text_chunks_with_location
        from knovas_extract.result import Section

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
        """The marked snippet never exceeds part_max_chars: the chunker
        reserves one char per text page that can fall inside the part."""
        from sync.chunking import iter_text_chunks_with_location

        text, pages, sentences = _pages_and_text([f"Seite {i}." * 3 for i in range(1, 30)])
        part_max = 100
        for snippet, _page, _sn, _start in iter_text_chunks_with_location(
            text, part_max, sentences=sentences, pages=pages, page_markers=True,
        ):
            assert len(snippet) <= part_max


class TestDeclaredPageMechanism:
    """Alloy obligation: mechanisms/client_pipeline.als::DeclaredPageMechanism."""

    def test_part_starting_on_join_line_is_declared_on_preceding_page(self):
        """When the chunker cuts at the blank join line after page 1, the next
        part's first character is the join whitespace: it is declared on page
        1 and carries the marker for page 2 right after the whitespace — the
        server's empty first segment yields no chunk, the second gets page 2.
        Declaring page 2 AND writing the marker would serve page 3."""
        from sync.chunking import iter_text_chunks_with_location

        text, pages, sentences = _pages_and_text(["Seite eins. " * 20, "Seite zwei. " * 20])
        join = text.index("\n\n")
        parts = list(iter_text_chunks_with_location(
            text, join + 2, sentences=sentences, pages=pages, page_markers=True,
        ))
        assert len(parts) >= 2
        second = parts[1]
        snippet, page_number, _sn, start = second
        assert start <= join + 2
        assert page_number == 1
        assert snippet.lstrip("\n").startswith(FF + "Seite zwei.")
        assert snippet.count(FF) == 1

    def test_part_starting_on_page_first_char_is_declared_on_that_page(self):
        from sync.chunking import iter_text_chunks_with_location

        text, pages, sentences = _pages_and_text(["Seite eins.", "Seite zwei."])
        start = text.index("Seite zwei.")
        parts = list(iter_text_chunks_with_location(
            text, start, sentences=sentences, pages=pages, page_markers=True,
        ))
        tail = [p for p in parts if p[3] == start]
        assert tail and tail[0][1] == 2 and FF not in tail[0][0]

    def test_page_numbers_and_sentence_numbers_are_unchanged_by_markers(self):
        from sync.chunking import iter_text_chunks_with_location

        text, pages, sentences = _pages_and_text(["Seite eins.", "Seite zwei.", "Seite drei."])
        without = list(iter_text_chunks_with_location(text, 10_000, sentences=sentences, pages=pages))
        with_markers = list(iter_text_chunks_with_location(
            text, 10_000, sentences=sentences, pages=pages, page_markers=True,
        ))
        assert [(p, s, o) for _, p, s, o in without] == [(p, s, o) for _, p, s, o in with_markers]
        assert [t.replace(FF, "") for t, *_ in with_markers] == [t for t, *_ in without]


class TestDeclaredPageWithoutSentences:
    """Spec E4: without sentences (the size gate, a sentence cap) a part's
    page_number comes from content.pages under the same contract -- the
    page of its first character, join whitespace to the preceding page."""

    @pytest.mark.parametrize("page_texts", [
        ["Seite eins.", "Seite zwei.", "Seite drei."],
        ["Seite eins. " * 20, None, "Seite drei. " * 20, "Seite vier. " * 20],
        [None, None, "Seite drei. " * 10, "Seite vier. " * 10],
        ["Zeile eins\nZeile zwei\nZeile drei", "Seite zwei.\nNoch eine Zeile."],
    ])
    @pytest.mark.parametrize("part_max", [7, 13, 40, 120, 10_000])
    def test_pages_give_the_same_page_numbers_as_sentences(self, page_texts, part_max):
        from sync.chunking import iter_text_chunks_with_location

        text, pages, sentences = _pages_and_text(page_texts)
        with_sentences = list(iter_text_chunks_with_location(text, part_max, sentences=sentences, pages=pages))
        from_pages = list(iter_text_chunks_with_location(text, part_max, pages=pages))
        assert [(t, o) for t, _p, _s, o in from_pages] == [(t, o) for t, _p, _s, o in with_sentences]
        assert [p for _t, p, _s, _o in from_pages] == [p for _t, p, _s, _o in with_sentences]
        assert all(s is None for _t, _p, s, _o in from_pages)

    def test_three_pages_get_page_numbers_one_to_three(self):
        from sync.chunking import iter_text_chunks_with_location

        text, pages, _ = _pages_and_text(["Seite eins.", "Seite zwei.", "Seite drei."])
        parts = list(iter_text_chunks_with_location(text, text.index("Seite zwei."), pages=pages))
        assert [p for _t, p, _s, _o in parts] == [1, 2, 3]

    def test_an_empty_page_keeps_the_numbering(self):
        from sync.chunking import iter_text_chunks_with_location

        text, pages, _ = _pages_and_text(["Seite eins.", None, "Seite drei."])
        parts = list(iter_text_chunks_with_location(text, text.index("Seite drei."), pages=pages))
        assert [p for _t, p, _s, _o in parts] == [1, 3]

    def test_no_pages_no_page_number(self):
        from sync.chunking import iter_text_chunks_with_location

        assert [p for _t, p, _s, _o in iter_text_chunks_with_location("Ohne Seiten.", 5)] == [None, None, None]
