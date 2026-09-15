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


def test_sidecar_shapes_never_break_the_search_response(tmp_path):
    """A sidecar from an older ingest must cost a snippet, never the response.

    context_window read every sentence as ``sent["t"]``. A store written when
    sentences were plain strings, or keyed "text", raised AttributeError or
    KeyError inside the search route, and the user saw "Fehler bei der Suche:
    Interner Serverfehler" for a query whose results were perfectly good.
    """
    import hashlib
    import json as _json

    from context_store import enrich_result_with_context

    shapes = {
        "current": [{"i": 0, "t": "Erster Satz."}, {"i": 1, "t": "Zweiter Satz."}],
        "plain_strings": ["Erster Satz.", "Zweiter Satz."],
        "text_key": [{"i": 0, "text": "Erster Satz."}, {"i": 1, "text": "Zweiter Satz."}],
        "mixed": ["Erster Satz.", {"i": 1, "t": "Zweiter Satz."}, None],
        "nonsense": [1, 2, 3],
        "empty": [],
    }
    for name, sentences in shapes.items():
        pointer = f"ptr-{name}"
        digest = hashlib.sha256(pointer.encode("utf-8")).hexdigest()
        (tmp_path / f"{digest}.json").write_text(
            _json.dumps({"sentences": sentences}), encoding="utf-8"
        )
        result = {"sentence_number": 1}
        # The assertion is that this returns at all.
        enrich_result_with_context(result, str(tmp_path), [pointer])

    # The shapes that carry readable sentences still produce a snippet, so the
    # tolerance is not just swallowing everything.
    for name in ("current", "plain_strings", "text_key", "mixed"):
        pointer = f"ptr-{name}"
        result = {"sentence_number": 1}
        enrich_result_with_context(result, str(tmp_path), [pointer])
        assert result.get("context_snippet"), f"{name} should still yield a snippet"


def test_unreadable_sidecar_is_skipped_not_raised(tmp_path):
    """Corrupt JSON is a reason to show no snippet, not to fail the search."""
    import hashlib

    from context_store import enrich_result_with_context

    pointer = "ptr-corrupt"
    digest = hashlib.sha256(pointer.encode("utf-8")).hexdigest()
    (tmp_path / f"{digest}.json").write_text("{not json", encoding="utf-8")

    result = {"sentence_number": 1}
    assert enrich_result_with_context(result, str(tmp_path), [pointer]) is False
    assert "context_snippet" not in result


# --- Fundstellen ------------------------------------------------------------
#
# Knovas sagt, WO ein Dokument getroffen wurde (top_chunks tragen Seite und
# Satznummer); der Sidecar hat den Text. Beides zusammen beantwortet die Frage,
# mit der eine Anwältin einen 60-seitigen Vertrag öffnet: an welcher Stelle,
# und was steht dort. Bisher wurden die beiden nur für den ersten Treffer
# zusammengeführt, für das Snippet auf der Karte.

from context_store import MAX_MATCH_LOCATIONS, build_match_locations  # noqa: E402

SENTENCES = [
    {"i": 1, "t": "Einleitung.", "p": 1},
    {"i": 2, "t": "Die Reaktionszeit beträgt vier Stunden.", "p": 7},
    {"i": 3, "t": "Sie wird ab Eingang gemessen.", "p": 7},
    {"i": 4, "t": "Wird die Reaktionszeit überschritten, eskaliert der Auftragnehmer.", "p": 7},
    {"i": 5, "t": "Schlussbestimmungen.", "p": 18},
]


def test_every_reported_location_becomes_an_entry():
    found = build_match_locations(
        SENTENCES, [{"sentence_number": 2, "page_number": 7},
                    {"sentence_number": 4, "page_number": 7}]
    )
    assert [f["sentence_number"] for f in found] == [2, 4]
    assert "vier Stunden" in found[0]["match"]


def test_each_entry_carries_its_page():
    """Ohne Seitenzahl ist eine Fundstelle in einem langen Vertrag wertlos."""
    found = build_match_locations(SENTENCES, [{"sentence_number": 2, "page_number": 7}])
    assert found[0]["page"] == 7


def test_a_location_without_a_page_still_lists():
    """TXT und E-Mail haben keine Seiten; die Fundstelle gibt es trotzdem."""
    found = build_match_locations(SENTENCES, [{"sentence_number": 2}])
    assert found and "page" not in found[0]


def test_the_same_sentence_twice_is_one_place_to_look():
    found = build_match_locations(
        SENTENCES, [{"sentence_number": 2}, {"sentence_number": 2}]
    )
    assert len(found) == 1


def test_surrounding_sentences_come_along():
    found = build_match_locations(SENTENCES, [{"sentence_number": 3}])
    assert "Reaktionszeit beträgt" in found[0]["before"]
    assert "eskaliert" in found[0]["after"]


def test_the_list_is_capped():
    chunks = [{"sentence_number": n} for n in range(1, 40)]
    assert len(build_match_locations(SENTENCES * 10, chunks)) <= MAX_MATCH_LOCATIONS


def test_nonsense_locations_are_skipped_not_fatal():
    found = build_match_locations(
        SENTENCES, [None, "x", {"sentence_number": "nicht-zahl"}, {"sentence_number": 2}]
    )
    assert [f["sentence_number"] for f in found] == [2]


def test_no_sidecar_means_no_locations():
    assert build_match_locations([], [{"sentence_number": 2}]) == []
    assert build_match_locations(SENTENCES, None) == []
