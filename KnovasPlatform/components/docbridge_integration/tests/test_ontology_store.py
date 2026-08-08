"""Tests fuer den Cortex-Ontology-Store (Fixture laden/validieren)."""
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
    # Die Zahl kommt aus den echten Verbindungen, nicht aus der Fixture:
    # eine Entitaetsverbindung dieser Art existiert.
    assert s["relations"][0]["count"] == 1


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


def test_optional_icon_name_passes_through_sanitized(tmp_path):
    """types[].icon ist optional; nur ein enger Zeichensatz kommt durch."""
    data = json.loads(json.dumps(VALID))
    data["types"][0]["icon"] = "Shield"          # wird kleingeschrieben
    data["types"][1]["icon"] = "../../etc/passwd"  # verworfen
    store = load_ontology(_fixture(tmp_path, data))
    types = {t["id"]: t for t in store.summary()["types"]}
    assert types[data["types"][0]["id"]]["icon"] == "shield"
    assert "icon" not in types[data["types"][1]["id"]]


def test_create_entity_and_relation_persist_to_fixture(tmp_path):
    """Der Graph ist kuratiert: Anlegen muss die Fixture fortschreiben."""
    path = _fixture(tmp_path, VALID)
    store = load_ontology(path)
    type_id = VALID["types"][0]["id"]

    created = store.create_entity("  Meier   Immobilien AG ", type_id)
    assert created["label"] == "Meier Immobilien AG"      # Leerraum normalisiert
    assert created["type"] == type_id
    # gleicher Name im selben Typ legt nicht doppelt an
    assert store.create_entity("meier immobilien ag", type_id)["id"] == created["id"]
    assert store.create_entity("", type_id) is None
    assert store.create_entity("X", "gibt-es-nicht") is None

    other = VALID["entities"][0]["id"]
    relation = store.create_relation(created["id"], "hat  Dossier", other)
    assert relation == {"src": created["id"], "predicate": "hat Dossier", "dst": other}
    assert store.create_relation(created["id"], "x", created["id"]) is None  # Selbstbezug
    assert store.create_relation("e-404", "x", other) is None

    # Neu eingelesen ist beides da, und das Detail zeigt die Verbindung
    frisch = load_ontology(path)
    labels = [e["label"] for e in frisch.entities_for_type(type_id)["entities"]]
    assert "Meier Immobilien AG" in labels
    detail = frisch.entity_detail(created["id"])
    assert [(r["predicate"], r["direction"]) for r in detail["relations"]] == [
        ("hat Dossier", "out")]


def test_entities_without_type_returns_all(tmp_path):
    store = load_ontology(_fixture(tmp_path, VALID))
    assert len(store.entities_for_type("")["entities"]) == len(VALID["entities"])


def test_create_and_delete_type_cascades(tmp_path):
    """Typ anlegen und wieder loeschen - samt seiner Entitaeten und Verweise."""
    path = _fixture(tmp_path, VALID)
    store = load_ontology(path)

    created = store.create_type("  Sachverständiger  ")
    assert created["label"] == "Sachverständiger"
    assert created["id"] == "sachverstaendiger"        # Umlaute im Bezeichner aufgeloest
    assert store.create_type("sachverständiger")["id"] == created["id"]   # kein Duplikat
    assert store.create_type("") is None

    entity = store.create_entity("Dr. Bauer", created["id"])
    other = VALID["entities"][0]["id"]
    store.create_relation(entity["id"], "begutachtet", other)

    assert store.delete_type(created["id"]) is True
    assert store.delete_type(created["id"]) is False    # zweimal loeschen ist kein Fehlerfall

    frisch = load_ontology(path)
    assert created["id"] not in [t["id"] for t in frisch.summary()["types"]]
    assert frisch.entity_detail(entity["id"]) is None   # Entitaet mitgeloescht
    # kein Verweis ins Leere beim Gegenueber (eigene Relationen bleiben)
    assert "begutachtet" not in [
        r["predicate"] for r in frisch.entity_detail(other)["relations"]]


def test_delete_entity_removes_relations_and_evidence(tmp_path):
    path = _fixture(tmp_path, VALID)
    store = load_ontology(path)
    victim = VALID["entities"][0]["id"]
    partner = store.create_entity("Partner AG", VALID["types"][0]["id"])
    store.create_relation(partner["id"], "arbeitet mit", victim)

    assert store.delete_entity(victim) is True
    assert store.delete_entity(victim) is False

    frisch = load_ontology(path)
    assert frisch.entity_detail(victim) is None
    assert frisch.entity_detail(partner["id"])["relations"] == []   # nur die eine, jetzt weg
    assert all(ev["entity_id"] != victim for ev in frisch._evidence)


def test_create_type_relation_is_a_declaration(tmp_path):
    """Vorgaben haben count 0 und bleiben ohne Instanzen bestehen."""
    path = _fixture(tmp_path, VALID)
    store = load_ontology(path)
    a, b = VALID["types"][0]["id"], VALID["types"][1]["id"]

    created = store.create_type_relation(a, "  hat   Dossier ", b)
    assert created == {"src": a, "predicate": "hat Dossier", "dst": b, "count": 0}
    # zweimal anlegen ergibt keinen zweiten Eintrag
    assert store.create_type_relation(a, "hat Dossier", b) == created
    assert store.create_type_relation(a, "", b) is None
    assert store.create_type_relation(a, "x", "gibt-es-nicht") is None
    assert store.create_type_relation(a, "x", a) is None       # Selbstbezug

    frisch = load_ontology(path)
    vorgaben = [r for r in frisch.summary()["relations"] if r["count"] == 0]
    assert {"src": a, "predicate": "hat Dossier", "dst": b, "count": 0} in vorgaben


def test_summary_counts_entity_relations_into_type_lines(tmp_path):
    """Vorgabe wird Beobachtung: eine deklarierte Relation mit count 0 zaehlt
    hoch, sobald eine passende Entitaetsverbindung entsteht."""
    daten = json.loads(json.dumps(VALID))
    daten["relations"] = [
        {"src": "mandant", "predicate": "kennt", "dst": "mandant", "count": 0},
    ]
    daten["entity_relations"] = []
    path = _fixture(tmp_path, daten)
    store = load_ontology(path)

    vorgabe = [r for r in store.summary()["relations"] if r["predicate"] == "kennt"]
    assert vorgabe == [{"src": "mandant", "predicate": "kennt",
                        "dst": "mandant", "count": 0}]

    zweiter = store.create_entity("Meier Immobilien AG", "mandant")["id"]
    store.create_relation("e-001", "kennt", zweiter)
    relations = load_ontology(path).summary()["relations"]
    assert [r for r in relations if r["predicate"] == "kennt"] == [
        {"src": "mandant", "predicate": "kennt", "dst": "mandant", "count": 1}]


def test_summary_adds_undeclared_triples(tmp_path):
    """Ein ungeplantes Tripel erscheint zusaetzlich, statt unsichtbar zu
    bleiben - die Fixture selbst bleibt dabei unveraendert."""
    path = _fixture(tmp_path, VALID)
    store = load_ontology(path)
    store.create_relation("e-014", "gehoert zu", "e-001")   # Dossier -> Mandant

    relations = load_ontology(path).summary()["relations"]
    assert {"src": "dossier", "predicate": "gehoert zu",
            "dst": "mandant", "count": 1} in relations
    # In der Datei steht weiterhin nur die deklarierte Relation.
    roh = json.loads(Path(path).read_text(encoding="utf-8"))
    assert [r["predicate"] for r in roh["relations"]] == ["hat_Dossier"]


def test_delete_type_relation_and_relation(tmp_path):
    path = _fixture(tmp_path, VALID)
    store = load_ontology(path)
    a, b = VALID["types"][0]["id"], VALID["types"][1]["id"]
    store.create_type_relation(a, "hat Dossier", b)

    assert store.delete_type_relation(a, "hat Dossier", b) is True
    assert store.delete_type_relation(a, "hat Dossier", b) is False
    assert all(r["predicate"] != "hat Dossier"
               for r in load_ontology(path).summary()["relations"])

    # Entitaetsverbindung loeschen
    e1 = VALID["entities"][0]["id"]
    e2 = store.create_entity("Partner AG", a)["id"]
    store.create_relation(e2, "arbeitet mit", e1)
    assert store.delete_relation(e2, "arbeitet mit", e1) is True
    assert store.delete_relation(e2, "arbeitet mit", e1) is False
    assert load_ontology(path).entity_detail(e2)["relations"] == []
