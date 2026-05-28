from pathlib import Path

from sync.chunking import chunk_text, count_file_text_parts, iter_file_text_chunks


def test_iter_file_matches_chunk_text(tmp_path):
    text = "alpha" * 120 + "beta" * 80
    path = tmp_path / "doc.txt"
    path.write_text(text, encoding="utf-8")
    streamed = list(iter_file_text_chunks(path, 50))
    buffered = chunk_text(text, 50)
    assert streamed == buffered


def test_count_parts_matches_list(tmp_path):
    path = tmp_path / "small.md"
    path.write_text("hello world", encoding="utf-8")
    assert count_file_text_parts(path, 100) == len(chunk_text("hello world", 100))
