from sync.chunking import chunk_text


def test_single_chunk():
    assert chunk_text("hello", 100) == ["hello"]


def test_exact_boundary():
    text = "a" * 100
    chunks = chunk_text(text, 50)
    assert len(chunks) == 2
    assert "".join(chunks) == text


def test_empty_file():
    assert chunk_text("", 50) == [""]


def test_unicode_no_split():
    text = "é" * 10
    chunks = chunk_text(text, 5)
    assert "".join(chunks) == text
