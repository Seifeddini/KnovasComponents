from knovas_extract.result import Section

from sync.section_pages import section_prefix_at_offset
from sync.chunking import build_transmission_parts
from knovas_extract.result import Sentence


def test_section_prefix_injected_at_section_start():
    text = "# Intro\n\nBody one.\n\n## Details\n\nBody two."
    sections = [
        Section(heading="Intro", level=1, text="Body one.", line_start=1, line_end=3),
        Section(heading="Details", level=2, text="Body two.", line_start=5, line_end=7),
    ]
    assert section_prefix_at_offset(sections, text, 0) == "# Intro\n\n"
    assert section_prefix_at_offset(sections, text, text.index("##")) == "## Details\n\n"


def test_build_parts_includes_page_and_sentence():
    text = "Alpha. Beta."
    sentences = [
        Sentence(
            index=0,
            text="Alpha.",
            char_start=0,
            char_end=6,
            line_start=1,
            line_end=1,
            page_index=0,
            page_number=1,
            section_index=None,
        ),
        Sentence(
            index=1,
            text="Beta.",
            char_start=7,
            char_end=12,
            line_start=1,
            line_end=1,
            page_index=0,
            page_number=1,
            section_index=None,
        ),
    ]
    parts = build_transmission_parts(text, 20, sentences=sentences)
    assert parts[0]["page_number"] == 1
    assert parts[0]["sentence_number"] == 1


def test_heading_is_replaced_not_duplicated_when_the_text_carries_it():
    """DOCX text carries the heading as its first line; the chunker used to
    yield "# Intro\\n\\nIntro" (prepended AND repeated)."""
    from sync.section_pages import apply_section_heading

    assert apply_section_heading("Intro\n\nErster Absatz.", "# Intro\n\n") == ("# Intro\n\nErster Absatz.", 2)
    # already marked up at another level: canonical line, same text
    assert apply_section_heading("## Intro\nBody", "# Intro\n\n") == ("# Intro\nBody", -1)
    # already carries the full prefix: untouched
    assert apply_section_heading("# Intro\n\nBody", "# Intro\n\n") == ("# Intro\n\nBody", 0)
    # a different first line: prepended, everything shifts by the prefix
    assert apply_section_heading("Body only.", "# Intro\n\n") == ("# Intro\n\nBody only.", len("# Intro\n\n"))
    assert apply_section_heading("Body", "") == ("Body", 0)


def test_build_parts_replaces_the_docx_heading_line():
    text = "Intro\n\nErster Absatz."
    sections = [Section(heading="Intro", level=1, text="Erster Absatz.", line_start=1, line_end=3)]
    parts = build_transmission_parts(text, 10_000, sections=sections)
    assert parts[0]["snippet"] == "# Intro\n\nErster Absatz."


def test_heading_level_is_capped_at_four():
    """The server parses ^(#{1,4})\\s+ only; a deeper level would reach the
    embedder as a literal '##### ' prefix."""
    from sync.section_pages import heading_line

    text = "Deep\n\nBody."
    sections = [Section(heading="Deep", level=6, text="Body.", line_start=1, line_end=3)]
    assert section_prefix_at_offset(sections, text, 0) == "#### Deep\n\n"
    assert heading_line("H", 0) == "# H"
    assert heading_line("H", 4) == "#### H"
    assert heading_line("H", 9) == "#### H"
