"""Tests fuer den Wissensnetz-Ontology-Store (Fixture laden/validieren)."""
import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ontology_store import get_ontology, load_ontology  # noqa: E402


def _fixture(tmp_path, data):
    p = tmp_path / "ontology_fixture.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


VALID = {
    "types": [
        {"id": "mandant", "label": "Mandant", "count": 12},
        {"id": "dossier", "label": "Dossier", "count": 47},
    ],
    "relations": [
        {"src": "mandant", "predicate": "hat_Dossier", "dst": "dossier", "count": 47},
    ],
    "entities": [
        {"id": "e-001", "label": "Müller Bau AG", "type": "mandant", "doc_count": 8},
        {"id": "e-014", "label": "Dossier 2024-001", "type": "dossier", "doc_count": 8},
    ],
    "entity_relations": [
        {"src": "e-001", "predicate": "hat_Dossier", "dst": "e-014"},
    ],
    "evidence": [
        {
            "entity_id": "e-001",
            "document": {"path": "corpus/2024-001/Mustervertrag.pdf", "title": "Mustervertrag"},
            "page": 3,
            "quote": "…zwischen der Müller Bau AG…",
        },
    ],
}


def test_summary_shape(tmp_path):
    store = load_ontology(_fixture(tmp_path, VALID))
    s = store.summary()
    assert [t["id"] for t in s["types"]] == ["mandant", "dossier"]
    assert s["relations"][0]["predicate"] == "hat_Dossier"
    assert s["relations"][0]["count"] == 47


def test_entities_for_type_filters(tmp_path):
    store = load_ontology(_fixture(tmp_path, VALID))
    ents = store.entities_for_type("mandant")["entities"]
    assert [e["id"] for e in ents] == ["e-001"]
    assert store.entities_for_type("unbekannt")["entities"] == []


def test_entity_detail_joins_relations_and_evidence(tmp_path):
    store = load_ontology(_fixture(tmp_path, VALID))
    d = store.entity_detail("e-001")
    assert d["entity"]["label"] == "Müller Bau AG"
    assert d["relations"][0]["predicate"] == "hat_Dossier"
    assert d["relations"][0]["direction"] == "out"
    assert d["relations"][0]["target"]["id"] == "e-014"
    assert d["evidence"][0]["page"] == 3
    assert store.entity_detail("gibt-es-nicht") is None


def test_entity_detail_includes_incoming_relations(tmp_path):
    """e-014 ist nur als dst in entity_relations vertreten (Vertrag v1.1)."""
    store = load_ontology(_fixture(tmp_path, VALID))
    d = store.entity_detail("e-014")
    assert len(d["relations"]) == 1
    rel = d["relations"][0]
    assert rel["direction"] == "in"
    assert rel["predicate"] == "hat_Dossier"
    assert rel["target"]["id"] == "e-001"
    assert rel["target"]["label"] == "Müller Bau AG"


def test_broken_references_filtered_not_500(tmp_path):
    data = json.loads(json.dumps(VALID))
    data["relations"].append({"src": "mandant", "predicate": "kaputt", "dst": "fehlt", "count": 1})
    data["entities"].append({"id": "e-099", "label": "Waise", "type": "fehlt", "doc_count": 1})
    data["entity_relations"].append({"src": "e-001", "predicate": "kaputt", "dst": "e-999"})
    store = load_ontology(_fixture(tmp_path, data))
    assert [r["predicate"] for r in store.summary()["relations"]] == ["hat_Dossier"]
    assert store.entities_for_type("fehlt")["entities"] == []
    assert [r["predicate"] for r in store.entity_detail("e-001")["relations"]] == ["hat_Dossier"]
    assert any("kaputt" in w or "fehlt" in w or "e-999" in w for w in store.warnings)


def test_evidence_with_missing_file_filtered(tmp_path):
    store = load_ontology(_fixture(tmp_path, VALID), path_exists=lambda p: False)
    assert store.entity_detail("e-001")["evidence"] == []
    assert store.warnings  # Warnung statt Fehler


def test_missing_or_invalid_file_yields_empty_store(tmp_path):
    store = load_ontology(str(tmp_path / "fehlt.json"))
    assert store.summary() == {"types": [], "relations": []}
    bad = tmp_path / "kaputt.json"
    bad.write_text("{nicht json", encoding="utf-8")
    assert load_ontology(str(bad)).summary()["types"] == []


def test_get_ontology_uses_env_and_mtime_cache(tmp_path, monkeypatch):
    path = _fixture(tmp_path, VALID)
    monkeypatch.setenv("ONTOLOGY_FIXTURE_PATH", path)
    first = get_ontology()
    assert first is get_ontology()  # gleiche mtime ⇒ gecached
    data = json.loads(json.dumps(VALID))
    data["types"].append({"id": "vertrag", "label": "Vertrag", "count": 5})
    Path(path).write_text(json.dumps(data), encoding="utf-8")
    import os
    os.utime(path, (0, Path(path).stat().st_mtime + 10))
    assert len(get_ontology().summary()["types"]) == 3
