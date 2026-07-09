from knovas_extract.result import Sentence

from sync.chunking import iter_text_chunks_with_location


def _make_sentences(text: str, spans: list[tuple[int, int, int | None]]) -> list[Sentence]:
    """Build a fake Sentence list for the chunker.

    spans: list of (char_start, char_end, page_number). One Sentence per span,
    with sequential 0-based indices. line_* are stubbed with 1.
    """
    out = []
    for i, (start, end, page) in enumerate(spans):
        out.append(
            Sentence(
                index=i,
                text=text[start:end],
                char_start=start,
                char_end=end,
                line_start=1,
                line_end=1,
                page_index=(page - 1) if page is not None else None,
                page_number=page,
                section_index=None,
            )
        )
    return out


def test_iter_text_chunks_with_location_carries_page_across_split():
    text = ("Page seven starts here. " + "word " * 200).strip()
    # A single "sentence" covering the whole text, tagged page 7.
    sentences = _make_sentences(text, [(0, len(text), 7)])
    parts = list(iter_text_chunks_with_location(text, 120, sentences=sentences))
    assert len(parts) >= 2
    # Both parts inherit page 7 and sentence 1.
    assert parts[0][1] == 7
    assert parts[0][2] == 1
    assert parts[1][1] == 7
    assert parts[1][2] == 1


def test_iter_text_chunks_with_location_sentence_number_advances():
    text = "Alpha. Beta. Gamma."
    # Three sentences, each ~7 chars; sentence starts at 0, 7, 13.
    sentences = _make_sentences(text, [(0, 6, None), (7, 12, None), (13, 19, None)])
    parts = list(iter_text_chunks_with_location(text, 8, sentences=sentences))
    # First chunk at offset 0 -> sentence 1; second chunk at offset >= 7 -> sentence 2.
    assert parts[0][2] == 1
    assert parts[1][2] >= 2


def test_iter_text_chunks_with_location_without_sentences_yields_none():
    text = "just some text without citation lookup"
    parts = list(iter_text_chunks_with_location(text, 10))
    assert all(p[1] is None and p[2] is None for p in parts)
