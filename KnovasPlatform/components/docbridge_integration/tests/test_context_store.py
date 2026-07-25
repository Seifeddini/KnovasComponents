"""Tests for context sidecar read/write and search enrichment."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from context_store import (
    build_first_page_payload,
    build_sidecar_payload,
    context_window,
    enrich_result_with_context,
    first_page_text,
    load_context,
    sidecar_path_for_pointer,
    write_context_sidecar,
)


class _FakeSentence:
    def __init__(self, index: int, char_start: int, page_number=None):
        self.index = index
        self.char_start = char_start
        self.page_number = page_number


def test_build_sidecar_payload_first_page_and_sentences():
    text = "Page one intro. Match sentence here. After match."
    sentences = [
        _FakeSentence(0, 0, page_number=1),
        _FakeSentence(1, 16, page_number=1),
        _FakeSentence(2, 38, page_number=2),
    ]
    payload = build_sidecar_payload("corpus/demo.txt", "demo.txt", text, sentences)
    assert payload["pointer"] == "corpus/demo.txt"
    assert len(payload["sentences"]) == 3
    assert payload["sentences"][1]["i"] == 2
    assert payload["first_page"]["text"].startswith("Page one intro.")


def test_context_window_radius():
    sentences = [
        {"i": i, "t": f"S{i}"}
        for i in range(1, 8)
    ]
    window = context_window(sentences, sentence_number=4, radius=2)
    assert window is not None
    assert window["before"] == "S2 S3"
    assert window["match"] == "S4"
    assert window["after"] == "S5 S6"


def test_first_page_fallback_without_page_metadata():
    sentences = [{"i": i, "t": f"Sentence {i}."} for i in range(1, 20)]
    page = build_first_page_payload(sentences)
    assert "Sentence 1." in page["text"]
    assert "Sentence 15." in page["text"]
    assert "Sentence 16." not in page["text"]


def test_write_and_load_sidecar_round_trip(tmp_path: Path):
    pointer = "corpus/foo/report.pdf"
    text = "Alpha. Beta match. Gamma."
    sentences = [
        _FakeSentence(0, 0, page_number=1),
        _FakeSentence(1, 7, page_number=1),
        _FakeSentence(2, 20, page_number=2),
    ]
    assert write_context_sidecar(str(tmp_path), pointer, pointer, text, sentences)
    loaded = load_context(str(tmp_path), [pointer])
    assert loaded is not None
    assert first_page_text(loaded).startswith("Alpha.")
    assert sidecar_path_for_pointer(tmp_path, pointer).is_file()


def test_enrich_result_with_context_attaches_fields(tmp_path: Path):
    pointer = "corpus/demo/doc.txt"
    text = "First page line. Second sentence match. Third."
    sentences = [
        _FakeSentence(0, 0, page_number=1),
        _FakeSentence(1, 18, page_number=1),
        _FakeSentence(2, 41, page_number=2),
    ]
    write_context_sidecar(str(tmp_path), pointer, pointer, text, sentences)
    result = {
        "doc_id": pointer,
        "path": pointer,
        "sentence_number": 2,
        "ingested_summary": {"present": True, "text": "Should be ignored when context exists"},
    }
    assert enrich_result_with_context(result, str(tmp_path), [pointer], context_radius=1)
    assert result.get("first_page_preview")
    assert result.get("context_snippet", {}).get("match")


def test_sidecar_file_is_valid_json(tmp_path: Path):
    pointer = "tenant/a.md"
    write_context_sidecar(str(tmp_path), pointer, pointer, "Hello world.", [_FakeSentence(0, 0)])
    path = sidecar_path_for_pointer(tmp_path, pointer)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["sentences"][0]["t"] == "Hello world."


def test_enhance_search_results_attaches_context(monkeypatch, tmp_path: Path):
    from web_interface import app as web_app

    pointer = "corpus/demo/sample.txt"
    write_context_sidecar(
        str(tmp_path),
        pointer,
        pointer,
        "Opening line. Matched content here. Closing line.",
        [
            _FakeSentence(0, 0, page_number=1),
            _FakeSentence(1, 14, page_number=1),
            _FakeSentence(2, 38, page_number=2),
        ],
    )
    monkeypatch.setenv("SEARCH_CONTEXT_STORE_PATH", str(tmp_path))

    class _Cfg:
        def get_bool(self, key, default=False):
            return default

        def get_int(self, key, default=0):
            if key == "web.search.context_sentences":
                return 10
            return default

        def get(self, key, default=""):
            return default

    results = {
        "results": [
            {
                "doc_id": pointer,
                "path": pointer,
                "sentence_number": 2,
                "ingested_summary": {"present": True, "text": "Summary fallback"},
            }
        ]
    }
    class _Handler:
        autodoc_path = "/tmp/autodoc"

    enhanced = web_app._enhance_search_results(results, _Handler(), _Cfg())
    hit = enhanced["results"][0]
    assert hit.get("first_page_preview")
    assert hit.get("context_snippet", {}).get("match")
