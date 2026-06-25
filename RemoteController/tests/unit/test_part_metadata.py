from sync.part_metadata import (
    location_for_snippet,
    page_number_at_offset,
    sentence_number_at_offset,
)


def test_page_number_from_pdf_markdown_heading():
    text = "Preamble.\n\n## Page 2\n\nFirst paragraph on page two.\n\n## Page 3\n\nText on three."
    assert page_number_at_offset(text, 0) is None
    assert page_number_at_offset(text, text.index("First")) == 2
    assert page_number_at_offset(text, text.index("Text on three")) == 3


def test_sentence_number_at_offset():
    text = "Alpha one. Beta two! Gamma three?"
    assert sentence_number_at_offset(text, 0) == 1
    assert sentence_number_at_offset(text, text.index("Beta")) == 2
    assert sentence_number_at_offset(text, text.index("Gamma")) == 3


def test_location_for_snippet():
    text = "## Page 4\n\nHello world. Second sentence."
    page, sentence = location_for_snippet(text, text.index("Second"))
    assert page == 4
    assert sentence == 2
