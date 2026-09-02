"""Mapping Knowledge Graph API -> Cortex-Vertrag.

Gegen einen simulierten Client: die echte Instanz steht noch nicht, das
Mapping soll trotzdem beweisbar sein. Die Antwortformen folgen
Knowledge_Graph_API.md; wo die Spezifikation schweigt (Placements), decken
die Tests die tolerante Auswertung ab.
"""
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ontology_graph import GraphOntologySource  # noqa: E402
from ontology_graph_filters import GraphFilterEngine  # noqa: E402


class FakeGraphClient:
    """Minimaler Client mit den Antwortformen der Spezifikation."""

    def __init__(self, **overrides):
        self.node_types = [
            {"id": "t-mandant", "name": "Mandant"},
            {"id": "t-dossier", "name": "Dossier"},
        ]
        self.nodes = [
            {"id": "n-1", "name": "Müller Bau AG", "node_type_id": "t-mandant",
             "assignments": [{"pointer": "kanzlei/mandat.pdf"}]},
            {"id": "n-2", "name": "Dossier 2024-001", "node_type_id": "t-dossier",
             "assignments": [{"pointer": "kanzlei/dossier.pdf"}]},
        ]
        self.edges = [
            {"node_lo": "n-1", "node_hi": "n-2", "relation": "hat_Dossier"},
        ]
        self.filters = {}
        self.placements = {}
        self.rejected = []
        self.__dict__.update(overrides)

    def graph_export(self):
        return {"status": "success", "node_types": self.node_types,
                "nodes": self.nodes, "edges": self.edges}

    def graph_node_types(self):
        return self.node_types

    def graph_nodes(self):
        return self.nodes

    def graph_edges(self):
        return self.edges

    def graph_node(self, node_id):
        for node in self.nodes:
            if node["id"] == node_id:
                return {"status": "success", "node": node}
        return None

    def graph_filters(self, node_id):
        return self.filters.get(node_id, [])

    def graph_create_filter(self, node_id, query_text, child_node_name):
        created = {"id": f"f-{len(self.filters) + 1}", "query_text": query_text,
                   "child_node_id": f"c-{node_id}"}
        self.filters.setdefault(node_id, []).append(created)
        return {"status": "success", "filter": created}

    def graph_placements(self, node_id, status="active"):
        return [p for p in self.placements.get(node_id, [])
                if p.get("_status", "active") == status]

    def graph_reject_placement(self, placement_id):
        for items in self.placements.values():
            for placement in items:
                if placement["id"] == placement_id:
                    placement["_status"] = "rejected"
                    self.rejected.append(placement_id)
                    return {"status": "success"}
        return None


class FakeText:
    """Steht fuer die lokale Textaufloesung (API liefert keinen Wortlaut)."""

    def __init__(self, mentions=None, page_quotes=None):
        self.mentions = mentions or {}
        self.page_quotes = page_quotes or {}

    def find_mention(self, pointer, needle):
        return self.mentions.get((pointer, needle))

    def quote_on_page(self, pointer, page, needle=None):
        return self.page_quotes.get((pointer, page), "")


def test_summary_maps_node_types_and_aggregates_edges():
    source = GraphOntologySource(FakeGraphClient())
    summary = source.summary()
    assert [t["id"] for t in summary["types"]] == ["t-mandant", "t-dossier"]
    assert [t["label"] for t in summary["types"]] == ["Mandant", "Dossier"]
    assert [t["count"] for t in summary["types"]] == [1, 1]
    # Instanz-Kanten werden zu einer Typ-Kante verdichtet
    assert summary["relations"] == [
        {"src": "t-mandant", "predicate": "hat_Dossier", "dst": "t-dossier",
         "count": 1},
    ]


def test_entities_for_type_and_doc_count():
    source = GraphOntologySource(FakeGraphClient())
    entities = source.entities_for_type("t-mandant")["entities"]
    assert entities == [{"id": "n-1", "label": "Müller Bau AG",
                         "type": "t-mandant", "doc_count": 1}]
    assert source.entities_for_type("gibt-es-nicht")["entities"] == []


def test_entity_detail_relations_carry_direction():
    source = GraphOntologySource(FakeGraphClient())
    detail = source.entity_detail("n-2")
    assert detail["entity"]["label"] == "Dossier 2024-001"
    # n-2 ist node_hi -> eingehende Richtung
    assert detail["relations"] == [
        {"predicate": "hat_Dossier", "direction": "in",
         "target": {"id": "n-1", "label": "Müller Bau AG", "type": "t-mandant"}},
    ]
    assert source.entity_detail("n-404") is None


def test_evidence_uses_local_text_and_never_invents_a_quote():
    text = FakeText(mentions={
        ("kanzlei/mandat.pdf", "Müller Bau AG"): (3, "Die Müller Bau AG erteilt das Mandat."),
    })
    source = GraphOntologySource(FakeGraphClient(), text_resolver=text)

    found = source.entity_detail("n-1")["evidence"]
    assert found == [{"document": {"path": "kanzlei/mandat.pdf", "title": "mandat"},
                      "page": 3, "quote": "Die Müller Bau AG erteilt das Mandat."}]

    # Ohne woertliche Fundstelle bleibt das Zitat leer statt erfunden
    ohne = GraphOntologySource(FakeGraphClient(), text_resolver=FakeText())
    assert ohne.entity_detail("n-1")["evidence"][0]["quote"] == ""


def test_corpus_counts_distinct_pointers():
    assert GraphOntologySource(FakeGraphClient()).corpus() == {"documents": 2}
    leer = GraphOntologySource(FakeGraphClient(nodes=[]))
    assert leer.corpus() == {}


def test_export_is_cached_within_ttl():
    client = FakeGraphClient()
    calls = {"n": 0}
    original = client.graph_export

    def counting():
        calls["n"] += 1
        return original()

    client.graph_export = counting
    source = GraphOntologySource(client, ttl_seconds=60, now=lambda: 1000.0)
    source.summary()
    source.summary()
    assert calls["n"] == 1


def test_export_cache_ttl_is_fixed_not_sliding_on_repeated_hits():
    """Repeated hits within TTL must not extend expiry; cache expires at cached_at + ttl."""
    client = FakeGraphClient()
    calls = {"n": 0}
    original = client.graph_export

    def counting():
        calls["n"] += 1
        return original()

    client.graph_export = counting
    clock = {"t": 1000.0}
    source = GraphOntologySource(client, ttl_seconds=60, now=lambda: clock["t"])

    source.summary()                    # cached_at = 1000
    for _ in range(5):
        clock["t"] += 10                # 1010 … 1050 — hits refresh LRU only
        source.summary()
    assert calls["n"] == 1

    clock["t"] = 1060.0                 # cached_at + ttl — must expire despite hits at 1050
    source.summary()
    assert calls["n"] == 2


def test_ttl_zero_disables_export_cache():
    """ttl_seconds=0 must never serve or retain cached exports."""
    client = FakeGraphClient()
    calls = {"n": 0}
    original = client.graph_export

    def counting():
        calls["n"] += 1
        return original()

    client.graph_export = counting
    source = GraphOntologySource(client, ttl_seconds=0, now=lambda: 1000.0)
    source.summary()
    source.summary()
    assert calls["n"] == 2
    assert source._export_by_subject == {}


class _Subject:
    def __init__(self, subject_id):
        self.id = subject_id


class _SwitchableBroker:
    def __init__(self):
        self.user = None

    def current_user(self):
        return self.user


def test_brokered_export_cache_does_not_leak_across_subjects():
    """Two users, one GraphOntologySource: B must not see A's brokered nodes."""
    broker = _SwitchableBroker()

    class SubjectGraphClient(FakeGraphClient):
        def __init__(self):
            super().__init__()
            self._principal_broker = broker

        def graph_export(self):
            user = self._principal_broker.current_user()
            if user is not None and user.id == "user-a":
                nodes = [{"id": "walled-a", "name": "Walled Matter",
                          "node_type_id": "t-mandant"}]
            else:
                nodes = [{"id": "open-b", "name": "Public File",
                          "node_type_id": "t-mandant"}]
            return {"status": "success", "node_types": self.node_types,
                    "nodes": nodes, "edges": []}

    client = SubjectGraphClient()
    source = GraphOntologySource(client, ttl_seconds=60, now=lambda: 1000.0)

    broker.user = _Subject("user-a")
    a_ids = {e["id"] for e in source.entities_for_type("t-mandant")["entities"]}
    assert a_ids == {"walled-a"}

    broker.user = _Subject("user-b")
    b_ids = {e["id"] for e in source.entities_for_type("t-mandant")["entities"]}
    assert "walled-a" not in b_ids
    assert b_ids == {"open-b"}


def test_brokered_export_cache_evicts_expired_subject_entries():
    """Expired per-subject slots are dropped so the cache cannot grow without bound."""
    broker = _SwitchableBroker()
    client = FakeGraphClient()
    client._principal_broker = broker
    clock = {"t": 1000.0}
    source = GraphOntologySource(client, ttl_seconds=60, now=lambda: clock["t"])

    broker.user = _Subject("user-a")
    source.summary()
    broker.user = _Subject("user-b")
    source.summary()
    assert set(source._export_by_subject) == {"user-a", "user-b"}

    clock["t"] = 1061.0
    broker.user = _Subject("user-c")
    source.summary()

    assert "user-a" not in source._export_by_subject
    assert "user-b" not in source._export_by_subject
    assert set(source._export_by_subject) == {"user-c"}


def test_brokered_export_cache_hits_for_the_same_subject():
    broker = _SwitchableBroker()
    broker.user = _Subject("user-a")
    client = FakeGraphClient()
    client._principal_broker = broker
    calls = {"n": 0}
    original = client.graph_export

    def counting():
        calls["n"] += 1
        return original()

    client.graph_export = counting
    source = GraphOntologySource(client, ttl_seconds=60, now=lambda: 1000.0)
    source.summary()
    source.summary()
    assert calls["n"] == 1


def test_brokered_export_cache_evicts_by_last_access_not_insertion():
    """Re-accessing a subject refreshes LRU recency; eviction drops least-recently used."""
    broker = _SwitchableBroker()
    client = FakeGraphClient()
    client._principal_broker = broker
    clock = {"t": 1000.0}
    source = GraphOntologySource(
        client, ttl_seconds=3600, max_cache_subjects=3, now=lambda: clock["t"],
    )

    broker.user = _Subject("user-a")
    source.summary()                    # insert user-a @ 1000
    clock["t"] = 1001.0
    broker.user = _Subject("user-b")
    source.summary()                    # insert user-b @ 1001
    clock["t"] = 1002.0
    broker.user = _Subject("user-c")
    source.summary()                    # insert user-c @ 1002 — cache full

    clock["t"] = 1003.0
    broker.user = _Subject("user-a")
    source.summary()                    # hit user-a — must refresh to 1003

    clock["t"] = 1004.0
    broker.user = _Subject("user-d")
    source.summary()                    # insert user-d — evict LRU (user-b @ 1001)

    assert set(source._export_by_subject) == {"user-a", "user-c", "user-d"}
    assert "user-b" not in source._export_by_subject
    assert source._export_by_subject["user-a"][1] == 1003.0


def test_tolerates_alternative_field_names():
    """Die Spec zeigt die Listenformen nicht - andere Schreibweisen duerfen
    nicht zum leeren Graphen fuehren."""
    client = FakeGraphClient(
        node_types=[{"node_type_id": "t-x", "label": "Vertrag"}],
        nodes=[{"node_id": "n-9", "title": "Werkvertrag", "type_id": "t-x",
                "pointers": ["a/b.pdf"]}],
        edges=[],
    )
    summary = GraphOntologySource(client).summary()
    assert summary["types"] == [{"id": "t-x", "label": "Vertrag", "count": 1}]
    entities = GraphOntologySource(client).entities_for_type("t-x")["entities"]
    assert entities[0]["label"] == "Werkvertrag"
    assert entities[0]["doc_count"] == 1


# -- Filter ---------------------------------------------------------------


def _engine(client, tmp_path=None, text=None):
    return GraphFilterEngine(client,
                             state_path=str(tmp_path / "review.json") if tmp_path else None,
                             text_resolver=text)


def test_create_filter_calls_api_and_dedupes():
    client = FakeGraphClient()
    engine = _engine(client)
    first = engine.create_filter("n-2", "Kündigungsklauseln und Fristen")
    assert first["id"] == "f-1"
    again = engine.create_filter("n-2", "  kündigungsklauseln und fristen ")
    assert again["id"] == "f-1"          # kein zweiter Filter
    assert len(client.filters["n-2"]) == 1
    assert engine.create_filter("n-2", "") is None


def test_placements_map_to_proposals_with_local_quote():
    client = FakeGraphClient()
    engine = _engine(client, text=FakeText(page_quotes={
        ("kanzlei/dossier.pdf", 3): "Die Kündigungsfrist beträgt drei Monate.",
    }))
    engine.create_filter("n-2", "Fristen")
    client.placements["c-n-2"] = [
        {"id": "p-1", "pointer": "kanzlei/dossier.pdf", "page_number": 3,
         "score": 0.81},
    ]
    detail = engine.filter_detail(None, "f-1")
    proposal = detail["proposals"][0]
    assert proposal["state"] == "pending"
    assert proposal["page"] == 3 and proposal["score"] == 0.81
    assert proposal["quote"] == "Die Kündigungsfrist beträgt drei Monate."
    assert detail["filter"]["counts"] == {"pending": 1, "accepted": 0, "rejected": 0}


def test_reject_goes_to_api_and_is_final():
    client = FakeGraphClient()
    engine = _engine(client)
    engine.create_filter("n-2", "Fristen")
    client.placements["c-n-2"] = [{"id": "p-1", "pointer": "a.pdf", "page_number": 1,
                                   "score": 0.5}]
    proposal, err = engine.decide(None, "f-1", "p-1", "reject")
    assert err == "" and proposal["state"] == "rejected"
    assert client.rejected == ["p-1"]        # Ablehnung liegt im Backend

    # danach unveraenderbar, auch per accept
    proposal, err = engine.decide(None, "f-1", "p-1", "accept")
    assert err == "" and proposal["state"] == "rejected"


def test_accept_is_local_review_state_and_survives_restart(tmp_path):
    client = FakeGraphClient()
    engine = _engine(client, tmp_path)
    engine.create_filter("n-2", "Fristen")
    client.placements["c-n-2"] = [{"id": "p-1", "pointer": "a.pdf", "page_number": 1,
                                   "score": 0.5}]
    proposal, err = engine.decide(None, "f-1", "p-1", "accept")
    assert err == "" and proposal["state"] == "accepted"
    assert client.rejected == []             # accept fasst die API nicht an

    wieder = _engine(client, tmp_path)
    assert wieder.filter_detail(None, "f-1")["proposals"][0]["state"] == "accepted"


def test_decide_rejects_unknown_ids():
    engine = _engine(FakeGraphClient())
    assert engine.decide(None, "f-unbekannt", "p-1", "reject")[1] == "not_found"
    assert engine.decide(None, "f-1", "p-1", "bla")[1] == "bad_action"


def test_create_entity_calls_api_and_refreshes_topology():
    """Kuratieren im Graph-Modus: Anlegen geht an die API, der Cache faellt."""
    client = FakeGraphClient()
    created_calls = []

    def create_node(name, node_type_id=None):
        created_calls.append((name, node_type_id))
        node = {"id": "n-3", "name": name, "node_type_id": node_type_id,
                "assignments": []}
        client.nodes.append(node)
        return {"status": "success", "node": node}

    client.graph_create_node = create_node
    source = GraphOntologySource(client, ttl_seconds=3600)
    source.summary()                                   # Topologie im Cache
    created = source.create_entity("Meier Immobilien AG", "t-mandant")
    assert created == {"id": "n-3", "label": "Meier Immobilien AG",
                       "type": "t-mandant", "doc_count": 0}
    assert created_calls == [("Meier Immobilien AG", "t-mandant")]
    # Cache wurde verworfen, der neue Knoten erscheint sofort
    assert any(e["id"] == "n-3"
               for e in source.entities_for_type("t-mandant")["entities"])
    assert source.create_entity("", "t-mandant") is None


def test_create_relation_maps_to_edge_endpoints():
    client = FakeGraphClient()
    calls = []
    client.graph_create_edge = lambda node_lo, node_hi, relation: (
        calls.append((node_lo, node_hi, relation)) or {"status": "success"})
    source = GraphOntologySource(client)
    assert source.create_relation("n-1", "hat Dossier", "n-2") == {
        "src": "n-1", "predicate": "hat Dossier", "dst": "n-2"}
    assert calls == [("n-1", "n-2", "hat Dossier")]
    assert source.create_relation("n-1", "x", "n-1") is None      # Selbstbezug
    assert source.create_relation("n-1", "", "n-2") is None


def test_type_relation_is_not_promised_in_graph_mode():
    """Der Schema-Endpunkt kann eine Vorgabe nicht zurueckliefern, also wird
    im Graph-Modus auch keine versprochen: None, die Route antwortet 400."""
    client = FakeGraphClient()
    aufrufe = []
    client.graph_create_schema_attribute = lambda type_id, name, datatype="entity_ref": (
        aufrufe.append((type_id, name, datatype))
        or {"status": "success", "attribute": {"id": "a-1", "name": name}})
    source = GraphOntologySource(client)

    assert source.create_type_relation("t-mandant", "hat Dossier", "t-dossier") is None
    assert aufrufe == []            # es wird nichts geschrieben
    assert source.create_type_relation("t-mandant", "", "t-dossier") is None
    assert source.create_type_relation("t-x", "y", "t-x") is None


def test_delete_relation_removes_matching_edge():
    client = FakeGraphClient()
    geloescht = []
    client.graph_delete_edge = lambda edge_id: (geloescht.append(edge_id)
                                                or {"status": "success"})
    client.edges = [{"id": "e-1", "node_lo": "n-1", "node_hi": "n-2",
                     "relation": "hat_Dossier"}]
    source = GraphOntologySource(client)

    assert source.delete_relation("n-1", "hat_Dossier", "n-2") is True
    assert geloescht == ["e-1"]
    assert source.delete_relation("n-1", "gibt_es_nicht", "n-2") is False


def test_delete_relation_is_directed():
    """Gezeichnet wird gerichtet, also gilt gerichtet: die Gegenrichtung
    trifft die Kante nicht."""
    client = FakeGraphClient()
    geloescht = []
    client.graph_delete_edge = lambda edge_id: (geloescht.append(edge_id)
                                                or {"status": "success"})
    client.edges = [{"id": "e-1", "node_lo": "n-1", "node_hi": "n-2",
                     "relation": "hat_Dossier"}]
    source = GraphOntologySource(client)

    assert source.delete_relation("n-2", "hat_Dossier", "n-1") is False
    assert geloescht == []
    assert source.delete_relation("n-1", "hat_Dossier", "n-2") is True
    assert geloescht == ["e-1"]


def test_delete_type_relation_needs_an_attribute_id():
    """Ohne Attribut-Kennung geht keine Anfrage an die API."""
    client = FakeGraphClient()
    aufrufe = []
    client.graph_delete_schema_attribute = lambda type_id, attribute_id: (
        aufrufe.append((type_id, attribute_id)) or {"status": "success"})
    client.node_types = [
        {"id": "t-mandant", "name": "Mandant",
         "schema": [{"name": "hat Dossier"}]},      # Eintrag ohne Kennung
    ]
    source = GraphOntologySource(client)

    assert source.delete_type_relation("t-mandant", "hat Dossier", "t-dossier") is False
    assert aufrufe == []


def test_delete_type_relation_robust_against_corrupt_schema():
    """delete_type_relation filtert nicht-Dict-Elemente in der schema-Liste."""
    client = FakeGraphClient()
    geloescht = []
    client.graph_delete_schema_attribute = lambda type_id, attribute_id: (
        geloescht.append((type_id, attribute_id)) or {"status": "success"})

    # Knotentyp mit schema-Liste, die gueltige Attribute und kaputte Eintraege enthaelt
    client.node_types = [
        {"id": "t-mandant", "name": "Mandant",
         "schema": [
             None,                                          # kaputt
             42,                                            # kaputt
             {"id": "a-1", "name": "hat Dossier"},         # gueltig
         ]}
    ]
    source = GraphOntologySource(client)

    # Erfolgsfall: Attribut wird gefunden und geloescht trotz kaputter Eintraege
    assert source.delete_type_relation("t-mandant", "hat Dossier", "t-dossier") is True
    assert geloescht == [("t-mandant", "a-1")]

    # fehlendes Attribut
    geloescht.clear()
    assert source.delete_type_relation("t-mandant", "gibt_es_nicht", "t-dossier") is False
    assert geloescht == []

    # fehlender Typ
    assert source.delete_type_relation("t-unbekannt", "hat Dossier", "t-dossier") is False
