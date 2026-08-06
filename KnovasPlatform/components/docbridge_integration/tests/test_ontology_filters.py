"""Engine-Tests: Matching, Sammel-Zustand, permanente Rejection-Memory."""
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

fitz = pytest.importorskip("fitz")

from ontology_filters import FilterEngine, _split_sentences  # noqa: E402
from ontology_store import OntologyStore  # noqa: E402


def _make_pdf(path: Path, pages) -> None:
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()


def _store() -> OntologyStore:
    data = {
        "types": [{"id": "t", "label": "T", "count": 2}],
        "relations": [],
        "entities": [
            {"id": "a", "label": "Akte A", "type": "t", "doc_count": 1},
            {"id": "b", "label": "Vertrag B", "type": "t", "doc_count": 1},
        ],
        "entity_relations": [{"src": "a", "predicate": "enthält", "dst": "b"}],
        "evidence": [
            {"entity_id": "a",
             "document": {"path": "a.pdf", "title": "A-Dokument"},
             "page": 1, "quote": "x"},
            {"entity_id": "b",
             "document": {"path": "b.pdf", "title": "B-Dokument"},
             "page": 1, "quote": "y"},
        ],
    }
    return OntologyStore(data, [])


@pytest.fixture()
def corpus(tmp_path):
    _make_pdf(tmp_path / "a.pdf", [
        "Fristen\nDie Kündigungsfrist beträgt drei Monate per Monatsende.\n"
        "Baubeginn ist der 1. Mai 2024.",
    ])
    _make_pdf(tmp_path / "b.pdf", [
        "Honorar\nEin Kostenvorschuss von 5000 Franken ist zu leisten.",
    ])
    return tmp_path


def _engine(corpus, state_path=None):
    return FilterEngine(str(state_path) if state_path else None,
                        lambda rel: str(corpus / rel))


def test_matching_finds_passage_with_provenance(corpus):
    eng = _engine(corpus)
    flt = eng.create_filter("a", "Kündigungsklauseln und Fristen")
    detail = eng.filter_detail(_store(), flt["id"])
    assert detail["filter"]["status"] == "active"
    top = detail["proposals"][0]
    assert "Kündigungsfrist" in top["quote"]
    assert top["page"] == 1
    assert top["document"]["path"] == "a.pdf"
    assert 0 < top["score"] <= 1
    assert top["state"] == "pending"


def test_no_match_is_collecting(corpus):
    eng = _engine(corpus)
    flt = eng.create_filter("a", "Quantencomputer und Raumfahrt")
    detail = eng.filter_detail(_store(), flt["id"])
    assert detail["filter"]["status"] == "collecting"
    assert detail["proposals"] == []


def test_one_hop_documents_are_searched(corpus):
    # Der Kostenvorschuss steht nur in b.pdf; a erreicht ihn über die Relation.
    eng = _engine(corpus)
    flt = eng.create_filter("a", "Kostenvorschuss")
    detail = eng.filter_detail(_store(), flt["id"])
    assert any(p["document"]["path"] == "b.pdf" for p in detail["proposals"])


def test_rejection_is_permanent_across_restart(corpus, tmp_path):
    state = tmp_path / "state.json"
    store = _store()
    eng = _engine(corpus, state)
    flt = eng.create_filter("a", "Kündigungsklauseln und Fristen")
    victim = eng.filter_detail(store, flt["id"])["proposals"][0]
    proposal, err = eng.decide(store, flt["id"], victim["id"], "reject")
    assert err == "" and proposal["state"] == "rejected"

    reborn = _engine(corpus, state)          # simulierter Neustart
    states = {p["id"]: p["state"]
              for p in reborn.filter_detail(store, flt["id"])["proposals"]}
    assert states[victim["id"]] == "rejected"

    # Ablehnung ist endgültig: auch ein Accept-Versuch ändert nichts.
    proposal, err = reborn.decide(store, flt["id"], victim["id"], "accept")
    assert err == "" and proposal["state"] == "rejected"


def test_accept_updates_counts(corpus, tmp_path):
    store = _store()
    eng = _engine(corpus, tmp_path / "state.json")
    flt = eng.create_filter("a", "Kündigungsklauseln und Fristen")
    first = eng.filter_detail(store, flt["id"])["proposals"][0]
    eng.decide(store, flt["id"], first["id"], "accept")
    counts = eng.filters_for_entity(store, "a")[0]["counts"]
    assert counts["accepted"] == 1
    assert counts["rejected"] == 0


def test_create_filter_dedupe_and_validation(corpus):
    eng = _engine(corpus)
    a = eng.create_filter("a", "Fristen und Termine")
    b = eng.create_filter("a", "  fristen   und TERMINE ")
    assert a["id"] == b["id"]                # dedupliziert über Normalform
    assert eng.create_filter("a", "") is None
    assert eng.create_filter("", "Fristen") is None


def test_decide_rejects_unknown_ids(corpus):
    eng = _engine(corpus)
    flt = eng.create_filter("a", "Fristen")
    assert eng.decide(_store(), flt["id"], "gibt-es-nicht", "reject")[1] == "not_found"
    assert eng.decide(_store(), "f-unbekannt", "x", "reject")[1] == "not_found"
    assert eng.decide(_store(), flt["id"], "x", "vielleicht")[1] == "bad_action"


def test_sentence_split_keeps_ordinals_and_abbreviations():
    pieces = _split_sentences(
        "Baubeginn ist der 1. Mai 2024. Nachtrag Nr. 1 gilt ab sofort. Ende.")
    assert pieces[0] == "Baubeginn ist der 1. Mai 2024."
    assert pieces[1] == "Nachtrag Nr. 1 gilt ab sofort."
