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
