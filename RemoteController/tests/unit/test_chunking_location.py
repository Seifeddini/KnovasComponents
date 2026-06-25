from sync.chunking import iter_text_chunks_with_location


def test_iter_text_chunks_with_location_carries_page_across_split():
    text = ("## Page 7\n\n" + ("word " * 200)).strip()
    parts = list(iter_text_chunks_with_location(text, 120))
    assert len(parts) >= 2
    assert parts[0][1] == 7
    assert parts[0][2] == 1
    assert parts[1][1] == 7
