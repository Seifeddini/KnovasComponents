"""Unit tests for context sidecar writer."""
from __future__ import annotations

from pathlib import Path

from sync.context_sidecar import (
    build_first_page_payload,
    build_sidecar_payload,
    sidecar_path_for_pointer,
    write_context_sidecar,
)


class _Sent:
    def __init__(self, index: int, char_start: int, page_number=None):
        self.index = index
        self.char_start = char_start
        self.page_number = page_number


def test_write_context_sidecar(tmp_path: Path):
    text = "Hello world. Second sentence."
    sentences = [_Sent(0, 0, 1), _Sent(1, 13, 1)]
    pointer = "rc-sync/demo.txt"
    assert write_context_sidecar(tmp_path, pointer, "demo.txt", text, sentences)
    path = sidecar_path_for_pointer(tmp_path, pointer)
    assert path.is_file()
    payload = build_sidecar_payload(pointer, "demo.txt", text, sentences)
    assert payload["first_page"]["text"]


def test_build_first_page_uses_page_one_only():
    sentences = [
        {"i": 1, "p": 1, "t": "Page one."},
        {"i": 2, "p": 2, "t": "Page two."},
    ]
    assert build_first_page_payload(sentences)["text"] == "Page one."
