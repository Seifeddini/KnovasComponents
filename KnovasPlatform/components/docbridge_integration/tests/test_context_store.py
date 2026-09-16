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
    {"i": 1, "t": "Diese Vereinbarung regelt die Zusammenarbeit der Parteien.", "p": 1},
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


def test_locations_that_contain_the_query_words_are_marked_and_come_first():
    """Ohne diese Unterscheidung ist die Liste nur „die acht ähnlichsten
    Absätze" -- was bei einer Namenssuche wie Willkür aussieht."""
    found = build_match_locations(
        SENTENCES,
        [{"sentence_number": 1}, {"sentence_number": 4}],
        terms=["reaktionszeit"],
    )
    assert found[0]["sentence_number"] == 4
    assert found[0]["literal"] is True
    assert found[1]["literal"] is False


def test_without_query_words_nothing_is_marked_or_reordered():
    found = build_match_locations(
        SENTENCES, [{"sentence_number": 1}, {"sentence_number": 4}]
    )
    assert [f["sentence_number"] for f in found] == [1, 4]
    assert "literal" not in found[0]


def test_query_terms_skips_punctuation_and_single_letters():
    from context_store import query_terms

    assert query_terms("Sophie Keller, ./. X") == ["sophie", "keller"]


def test_stopwords_are_not_search_terms():
    """Wer „Die Mandantin Alpenblick Gastro GmbH beauftragt die Kanzlei" sucht,
    meint Alpenblick und Gastro. „die" überall zu markieren färbt das halbe
    Dokument ein und begräbt genau die Wörter, um die es ging."""
    from context_store import query_terms

    assert query_terms("Die Mandantin Alpenblick Gastro GmbH beauftragt die Kanzlei") == [
        "mandantin", "alpenblick", "gastro", "gmbh", "beauftragt", "kanzlei",
    ]


def test_a_location_with_only_stopwords_is_not_a_literal_hit():
    from context_store import query_terms

    found = build_match_locations(
        SENTENCES, [{"sentence_number": 1}], terms=query_terms("die Zusammenarbeit"),
    )
    assert found[0]["literal"] is True  # "zusammenarbeit" trägt den Treffer

    only_stop = build_match_locations(
        SENTENCES, [{"sentence_number": 1}], terms=query_terms("die der das"),
    )
    assert only_stop[0].get("literal") is not True


def test_sentences_by_number_returns_the_passage_text():
    """Die Vorschau kennt die Satznummer einer Fundstelle, nicht ihren Text --
    sonst müssten ganze Sätze durch die Adresszeile."""
    from context_store import sentences_by_number

    entry = {"sentences": SENTENCES}
    assert sentences_by_number(entry, [2]) == [
        "Die Reaktionszeit beträgt vier Stunden.",
    ]
    assert sentences_by_number(entry, [2, 4])[1].startswith("Wird die Reaktionszeit")


def test_sentences_by_number_skips_what_it_cannot_find():
    from context_store import sentences_by_number

    assert sentences_by_number({"sentences": SENTENCES}, [999]) == []
    assert sentences_by_number(None, [1]) == []
    assert sentences_by_number({"sentences": SENTENCES}, []) == []


# --- Welche Fundstellen die Liste weglaesst ----------------------------------
#
# Knovas liefert die bestbewerteten Chunks, und die liegen auf einer
# Vertragsseite oft dicht beieinander. Ungefiltert standen acht Eintraege in der
# Liste, von denen mehrere auf dieselbe Stelle zeigten und einer auf den
# Briefkopf. Das beantwortet "wo steht das" nicht besser als zwei gute.

from context_store import MAX_MATCH_LOCATIONS_PER_PAGE  # noqa: E402

LONG_PAGE = [
    {"i": n, "t": f"Der Auftragnehmer schuldet die Leistung nach Ziffer {n} dieses Vertrags.",
     "p": 3}
    for n in range(1, 21)
]


def test_at_most_two_locations_per_page():
    found = build_match_locations(
        LONG_PAGE, [{"sentence_number": n, "page_number": 3} for n in range(1, 20, 3)]
    )
    assert len(found) == MAX_MATCH_LOCATIONS_PER_PAGE


def test_the_per_page_cap_counts_each_page_separately():
    sentences = LONG_PAGE + [
        {"i": n + 100, "p": 9,
         "t": f"Die Verguetung fuer Ziffer {n} ist in Anlage {n} abschliessend geregelt."}
        for n in range(1, 21)
    ]
    chunks = (
        [{"sentence_number": n, "page_number": 3} for n in (1, 4, 7)]
        + [{"sentence_number": n + 100, "page_number": 9} for n in (1, 4, 7)]
    )
    found = build_match_locations(sentences, chunks)
    assert [f["page"] for f in found] == [3, 3, 9, 9]


def test_neighbouring_sentences_are_one_location():
    """Bei Radius 1 teilen sich zwei Nachbarn ihr Fenster: zwei Eintraege,
    die fast denselben Text zeigen, und ein Klick, der kaum woandershin
    springt."""
    found = build_match_locations(
        LONG_PAGE, [{"sentence_number": 5}, {"sentence_number": 6}]
    )
    assert [f["sentence_number"] for f in found] == [5]


def test_the_same_wording_on_two_pages_is_listed_once():
    """Kopf- und Fusszeilen stehen auf jeder Seite -- andere Satznummer,
    gleicher Wortlaut."""
    sentences = [
        {"i": 1, "t": "Die Parteien vereinbaren die folgenden Bedingungen.", "p": 1},
        {"i": 2, "t": "Ein trennender Satz ohne Bedeutung fuer diesen Test.", "p": 1},
        {"i": 3, "t": "Die Parteien vereinbaren die folgenden Bedingungen.", "p": 2},
    ]
    found = build_match_locations(
        sentences,
        [{"sentence_number": 1, "page_number": 1}, {"sentence_number": 3, "page_number": 2}],
    )
    assert len(found) == 1


def test_a_letterhead_line_is_not_a_location():
    sentences = [
        {"i": 1, "t": "Muster Rechtsanwaelte AG, Raemistrasse 14, 8001 Zuerich", "p": 1},
        {"i": 2, "t": "Aktenzeichen: 2019-021", "p": 1},
        {"i": 3, "t": "Die Mandantin wird nach dem vereinbarten Zeitaufwand abgerechnet.", "p": 1},
    ]
    found = build_match_locations(
        sentences, [{"sentence_number": n, "page_number": 1} for n in (1, 2, 3)]
    )
    assert [f["sentence_number"] for f in found] == [3]


def test_a_short_line_stays_when_it_holds_a_searched_word():
    """Wer nach der Adresse sucht, meint die Adresszeile."""
    sentences = [
        {"i": 1, "t": "Muster Rechtsanwaelte AG, Raemistrasse 14, 8001 Zuerich", "p": 1},
        {"i": 2, "t": "Ein Satz, der mit der Suche nichts zu tun hat.", "p": 1},
    ]
    found = build_match_locations(
        sentences, [{"sentence_number": 1, "page_number": 1}], terms=["raemistrasse"]
    )
    assert [f["sentence_number"] for f in found] == [1]


def test_a_document_of_only_short_lines_still_has_locations():
    """Lieber duenne Fundstellen als eine leere Liste unter einem Treffer."""
    sentences = [
        {"i": 1, "t": "Pos. 1: 4000.00", "p": 1},
        {"i": 2, "t": "Pos. 2: 250.00", "p": 1},
    ]
    found = build_match_locations(
        sentences, [{"sentence_number": 1, "page_number": 1}]
    )
    assert len(found) == 1


def test_the_best_hit_keeps_its_place_on_a_crowded_page():
    """Die zwei Plaetze einer Seite gehoeren dem literalen Treffer, auch wenn
    er erst an vierter Stelle gemeldet wird."""
    chunks = [{"sentence_number": n, "page_number": 3} for n in (1, 4, 7, 10)]
    sentences = list(LONG_PAGE)
    sentences[9] = dict(sentences[9], t="Die Verguetung richtet sich nach dem Stundenansatz.")
    found = build_match_locations(sentences, chunks, terms=["stundenansatz"])
    assert found[0]["sentence_number"] == 10
    assert found[0]["literal"] is True


# --- Der Satz mit der Antwort ------------------------------------------------
#
# "Womit hat die Alpenblick Gastro beauftragt?" auf eine Mandatsvereinbarung:
# gemeldet wurden die Kopfzeile "In Sachen: Alpenblick Gastro GmbH" und ein
# Nebensatz ueber den Lieferstopp. Der Satz, der die Frage beantwortet --
# "Die Mandantin Alpenblick Gastro GmbH beauftragt die Kanzlei" -- stand nicht
# in der Liste, obwohl er im Dokument steht und seine Woerter markiert waren.

MANDAT = [
    {"i": 7, "p": 1, "t": "Aktenzeichen: 2019-031"},
    {"i": 8, "p": 1, "t": "In Sachen: Alpenblick Gastro GmbH ./."},
    {"i": 9, "p": 1, "t": "Seebräu AG"},
    {"i": 10, "p": 1, "t": "2019-04-15"},
    {"i": 11, "p": 1, "t": "1. Gegenstand."},
    {"i": 12, "p": 1, "t": "Die Mandantin Alpenblick Gastro GmbH beauftragt die "
                           "Kanzlei in Sachen Alpenblick Gastro GmbH ./."},
    {"i": 13, "p": 1, "t": "Seebräu AG."},
    {"i": 14, "p": 1, "t": "Die Brauerei stoppte die Lieferung nach einem Streit "
                           "um Pfandgebinde."},
    {"i": 15, "p": 1, "t": "Streitort ist Seestrasse 40, Wädenswil."},
]


def test_the_sentence_that_answers_the_question_is_listed():
    from context_store import query_terms

    found = build_match_locations(
        MANDAT,
        [{"sentence_number": 8, "page_number": 1}, {"sentence_number": 14, "page_number": 1}],
        terms=query_terms("Womit hat die Alpenblick Gastro beauftragt?"),
    )
    assert found[0]["sentence_number"] == 12
    assert "beauftragt die Kanzlei" in found[0]["match"]


def test_more_of_the_searched_words_beats_being_reported_first():
    """Die Kopfzeile traegt zwei gesuchte Woerter, der Antwortsatz drei. Bei
    zwei Plaetzen je Seite darf nicht die Meldereihenfolge entscheiden."""
    from context_store import query_terms

    found = build_match_locations(
        MANDAT,
        [{"sentence_number": 8, "page_number": 1}, {"sentence_number": 12, "page_number": 1}],
        terms=query_terms("Alpenblick Gastro beauftragt"),
    )
    assert [f["sentence_number"] for f in found] == [12, 8]


def test_nothing_is_added_when_the_report_already_holds_the_best_sentence():
    from context_store import query_terms

    found = build_match_locations(
        MANDAT,
        [{"sentence_number": 12, "page_number": 1}],
        terms=query_terms("Alpenblick Gastro beauftragt"),
    )
    assert [f["sentence_number"] for f in found] == [12]


def test_the_internal_coverage_key_does_not_reach_the_interface():
    from context_store import query_terms

    found = build_match_locations(
        MANDAT, [{"sentence_number": 12}], terms=query_terms("Alpenblick"),
    )
    assert all(not k.startswith("_") for k in found[0])


def test_question_words_are_not_searched_for():
    from context_store import query_terms

    assert query_terms("Womit hat die Alpenblick Gastro beauftragt?") == [
        "alpenblick", "gastro", "beauftragt",
    ]


def test_the_local_search_does_not_read_a_whole_huge_sidecar():
    """Sie laeuft je Treffer einmal; ein Sidecar darf 50'000 Saetze halten."""
    from context_store import LOCAL_SCAN_MAX_SENTENCES, query_terms

    sentences = [
        {"i": n, "p": 1, "t": "Ein Satz ohne jede Bedeutung fuer diese Suche."}
        for n in range(1, LOCAL_SCAN_MAX_SENTENCES + 200)
    ]
    sentences[-1] = {"i": len(sentences), "p": 1,
                     "t": "Hier steht Alpenblick Gastro und wird beauftragt."}
    found = build_match_locations(
        sentences, [{"sentence_number": 5, "page_number": 1}],
        terms=query_terms("Alpenblick Gastro beauftragt"),
    )
    # Jenseits der Lesegrenze, also nicht gefunden -- und vor allem: kein Fehler.
    assert all(f["sentence_number"] != len(sentences) for f in found)
