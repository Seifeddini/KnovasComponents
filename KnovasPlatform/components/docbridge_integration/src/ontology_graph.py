"""Cortex-Quelle auf Basis der Knovas Knowledge Graph API.

Erfuellt denselben Vertrag wie ontology_store.OntologyStore (summary,
entities_for_type, entity_detail, corpus), holt die Daten aber aus
/secured/graph statt aus einer Fixture. Damit bleibt alles darueber -
Routen, JSON-Vertrag, Oberflaeche - unveraendert.

Abbildung (Knowledge_Graph_API.md):
    Typ                  -> node-type
    Entitaet             -> node
    Verbundene Entitaet  -> edge (node_lo/node_hi + relation)
    Beleg                -> Zuordnung (pointer) + lokal aufgeloester Wortlaut

Feldnamen werden tolerant gelesen: die Spezifikation zeigt die
Listen-Antworten nicht im Detail, deshalb akzeptiert jeder Zugriff mehrere
plausible Schreibweisen, statt bei der ersten Abweichung zu brechen.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 60
DEFAULT_MAX_CACHE_SUBJECTS = 64


def _first(mapping: Dict[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return default


def _node_id(node: Dict[str, Any]) -> str:
    return str(_first(node, "id", "node_id", "uuid"))


def _node_label(node: Dict[str, Any]) -> str:
    return str(_first(node, "name", "label", "title"))


def _node_type_id(node: Dict[str, Any]) -> str:
    value = _first(node, "node_type_id", "type_id", "node_type", "type")
    if isinstance(value, dict):                 # eingebettetes Typ-Objekt
        return str(_first(value, "id", "node_type_id", "uuid"))
    return str(value)


def _type_id(node_type: Dict[str, Any]) -> str:
    return str(_first(node_type, "id", "node_type_id", "uuid"))


def _type_label(node_type: Dict[str, Any]) -> str:
    return str(_first(node_type, "name", "label", "title"))


def _edge_ends(edge: Dict[str, Any]) -> Optional[tuple]:
    """(src, dst, predicate) einer Kante; None wenn unbrauchbar."""
    src = str(_first(edge, "node_lo", "source", "src", "from_node_id"))
    dst = str(_first(edge, "node_hi", "target", "dst", "to_node_id"))
    predicate = str(_first(edge, "relation", "predicate", "label", "type"))
    if not src or not dst or not predicate:
        return None
    return src, dst, predicate


def _pointers_of(node_detail: Dict[str, Any]) -> List[str]:
    """Dokument-Pointer eines Knotens aus seinen Zuordnungen."""
    out: List[str] = []
    for key in ("assignments", "knowledge", "documents", "pointers"):
        value = node_detail.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, str):
                pointer = item
            elif isinstance(item, dict):
                pointer = str(_first(item, "pointer", "identifier", "document_id"))
            else:
                continue
            if pointer and pointer not in out:
                out.append(pointer)
        if out:
            break
    return out


class GraphOntologySource:
    """Lesende Cortex-Quelle gegen /secured/graph.

    Der Topologie-Export traegt Typen, Knoten und Kanten in einem Aufruf und
    wird kurz zwischengespeichert - das haelt die Zahl der Anfragen klein
    (die Secure-API begrenzt sie deutlich).
    """

    def __init__(self, client: Any,
                 text_resolver: Any = None,
                 ttl_seconds: int = DEFAULT_TTL_SECONDS,
                 max_cache_subjects: int = DEFAULT_MAX_CACHE_SUBJECTS,
                 now: Optional[Callable[[], float]] = None):
        self._client = client
        self._text = text_resolver
        self._ttl = max(0, int(ttl_seconds))
        self._max_cache_subjects = max(1, int(max_cache_subjects))
        self._now = now or time.time
        # Keyed by subject id when a principal broker is present; "" is the
        # identity-off / no-broker slot (one cache for the worker, as before).
        # (cached_at, last_access, payload) — expiry uses cached_at; LRU uses last_access
        self._export_by_subject: Dict[str, tuple[float, float, Dict[str, Any]]] = {}
        self.warnings: List[str] = []

    # -- Topologie ------------------------------------------------------

    def _export_cache_key(self) -> Optional[str]:
        """Cache slot for this request, or None to skip caching.

        Identity-off / no broker keeps a single worker-wide slot (""). With a
        broker, the slot is the authenticated subject's id so User B cannot
        be served User A's brokered graph. A configured broker with no user
        skips the cache entirely — there is no subject to key, and sharing
        another user's payload would be the bug this exists to close.
        """
        broker = getattr(self._client, "_principal_broker", None)
        if broker is None:
            return ""
        current_user = getattr(broker, "current_user", None)
        user = current_user() if callable(current_user) else None
        if user is None:
            return None
        return str(user.id)

    def _evict_stale_export_cache(self, now: float) -> None:
        """Drop expired entries and enforce a small LRU cap."""
        if not self._export_by_subject:
            return
        if self._ttl > 0:
            stale = [key for key, (cached_at, _, _) in self._export_by_subject.items()
                     if now - cached_at >= self._ttl]
            for key in stale:
                del self._export_by_subject[key]
        while len(self._export_by_subject) > self._max_cache_subjects:
            oldest_key = min(self._export_by_subject,
                             key=lambda key: self._export_by_subject[key][1])
            del self._export_by_subject[oldest_key]

    def _export(self) -> Dict[str, Any]:
        key = self._export_cache_key()
        now = self._now()
        if key is not None and self._ttl > 0:
            self._evict_stale_export_cache(now)
            hit = self._export_by_subject.get(key)
            if hit is not None and now - hit[0] < self._ttl:
                self._export_by_subject[key] = (hit[0], now, hit[2])
                return hit[2]
        from knovas_client import _graph_payload_list

        raw = self._client.graph_export() or {}
        node_types = _graph_payload_list(raw.get("node_types") or raw,
                                         "node_types", "nodeTypes", "types")
        nodes = _graph_payload_list(raw.get("nodes") or raw, "nodes")
        edges = _graph_payload_list(raw.get("edges") or raw, "edges")
        # Der Export ist nicht in jeder Ausbaustufe vollstaendig - fehlende
        # Teile einzeln nachholen, statt mit einem leeren Graphen dazustehen.
        if not node_types:
            node_types = self._client.graph_node_types()
        if not nodes:
            nodes = self._client.graph_nodes()
        if not edges:
            edges = self._client.graph_edges()
        data = {"node_types": node_types, "nodes": nodes, "edges": edges}
        if key is not None and self._ttl > 0:
            self._export_by_subject[key] = (now, now, data)
            self._evict_stale_export_cache(now)
        return data

    def summary(self) -> Dict[str, Any]:
        data = self._export()
        nodes = data["nodes"]
        type_by_node = {_node_id(n): _node_type_id(n) for n in nodes}

        counts: Dict[str, int] = {}
        for node in nodes:
            counts[_node_type_id(node)] = counts.get(_node_type_id(node), 0) + 1

        types = []
        for node_type in data["node_types"]:
            tid = _type_id(node_type)
            if not tid:
                continue
            entry = {"id": tid, "label": _type_label(node_type) or tid,
                     "count": counts.get(tid, 0)}
            icon = str(node_type.get("icon") or "").strip().lower()
            if icon:
                entry["icon"] = icon
            types.append(entry)

        # Typ-Ebene: Kanten zwischen Instanzen zu Typ-Paaren verdichten.
        known = {t["id"] for t in types}
        aggregated: Dict[tuple, int] = {}
        for edge in data["edges"]:
            ends = _edge_ends(edge)
            if ends is None:
                continue
            src, dst, predicate = ends
            src_type, dst_type = type_by_node.get(src), type_by_node.get(dst)
            if src_type not in known or dst_type not in known:
                continue
            key = (src_type, predicate, dst_type)
            aggregated[key] = aggregated.get(key, 0) + 1

        relations = [{"src": s, "predicate": p, "dst": d, "count": c}
                     for (s, p, d), c in sorted(aggregated.items())]
        return {"types": types, "relations": relations}

    def entities_for_type(self, type_id: str) -> Dict[str, Any]:
        entities = []
        want = str(type_id or "").strip()
        for node in self._export()["nodes"]:
            if want and _node_type_id(node) != want:
                continue
            entities.append({
                "id": _node_id(node),
                "label": _node_label(node) or _node_id(node),
                "type": _node_type_id(node),
                "doc_count": self._doc_count(node),
            })
        return {"entities": entities}

    @staticmethod
    def _doc_count(node: Dict[str, Any]) -> int:
        for key in ("document_count", "doc_count", "assignment_count"):
            if isinstance(node.get(key), int):
                return int(node[key])
        pointers = _pointers_of(node)
        return len(pointers)

    # -- Detail ---------------------------------------------------------

    def entity_detail(self, entity_id: str) -> Optional[Dict[str, Any]]:
        detail = self._client.graph_node(entity_id)
        if not detail:
            return None
        node = detail.get("node") if isinstance(detail.get("node"), dict) else detail
        entity = {
            "id": _node_id(node) or str(entity_id),
            "label": _node_label(node) or str(entity_id),
            "type": _node_type_id(node),
            "doc_count": self._doc_count(node) or self._doc_count(detail),
        }

        data = self._export()
        label_by_id = {_node_id(n): _node_label(n) for n in data["nodes"]}
        type_by_id = {_node_id(n): _node_type_id(n) for n in data["nodes"]}
        relations = []
        for edge in data["edges"]:
            ends = _edge_ends(edge)
            if ends is None:
                continue
            src, dst, predicate = ends
            if src == entity["id"]:
                other, direction = dst, "out"
            elif dst == entity["id"]:
                other, direction = src, "in"
            else:
                continue
            if other not in label_by_id:
                continue
            relations.append({
                "predicate": predicate,
                "direction": direction,
                "target": {"id": other, "label": label_by_id[other] or other,
                           "type": type_by_id.get(other, "")},
            })

        return {"entity": entity, "relations": relations,
                "evidence": self._evidence(detail, node, entity["label"])}

    def _evidence(self, detail: Dict[str, Any], node: Dict[str, Any],
                  label: str) -> List[Dict[str, Any]]:
        """Belege aus den zugeordneten Dokumenten.

        Die API nennt nur den Pointer. Seite und Wortlaut kommen aus der
        lokalen Aufloesung; findet sie keine woertliche Erwaehnung, bleibt
        das Zitat leer - erfunden wird nichts.
        """
        pointers = _pointers_of(detail) or _pointers_of(node)
        evidence = []
        for pointer in pointers:
            page, quote = 1, ""
            if self._text is not None:
                found = self._text.find_mention(pointer, label)
                if found:
                    page, quote = found
            evidence.append({
                "document": {"path": pointer, "title": _title_for(pointer)},
                "page": page,
                "quote": quote,
            })
        return evidence

    # -- Kuratieren -----------------------------------------------------
    # Der Graph ist client-kuratiert: Knovas leitet Knoten und Relationen
    # nicht selbst ab, wir legen sie an. Darauf setzen die automatischen
    # Teile (Filter, Identifiers) erst auf.

    def _invalidate(self) -> None:
        key = self._export_cache_key()
        if key is None:
            return
        self._export_by_subject.pop(key, None)

    def create_type(self, label: str) -> Optional[Dict[str, Any]]:
        """POST /secured/graph/node-types - Typ-Vokabular erweitern."""
        label = " ".join(str(label or "").split())
        if not label:
            return None
        for existing in self._export()["node_types"]:
            if _type_label(existing).lower() == label.lower():
                return {"id": _type_id(existing), "label": _type_label(existing),
                        "count": 0}
        created = self._client.graph_create_node_type(label)
        if not created:
            return None
        node_type = (created.get("node_type") if isinstance(created.get("node_type"), dict)
                     else created)
        self._invalidate()
        return {"id": _type_id(node_type), "label": _type_label(node_type) or label,
                "count": 0}

    def create_entity(self, label: str, type_id: str) -> Optional[Dict[str, Any]]:
        label = " ".join(str(label or "").split())
        if not label:
            return None
        created = self._client.graph_create_node(label, node_type_id=str(type_id or "") or None)
        if not created:
            return None
        node = created.get("node") if isinstance(created.get("node"), dict) else created
        self._invalidate()
        return {"id": _node_id(node), "label": _node_label(node) or label,
                "type": _node_type_id(node) or str(type_id or ""), "doc_count": 0}

    def create_relation(self, src: str, predicate: str,
                        dst: str) -> Optional[Dict[str, Any]]:
        predicate = " ".join(str(predicate or "").split())
        src, dst = str(src or "").strip(), str(dst or "").strip()
        if not predicate or not src or not dst or src == dst:
            return None
        created = self._client.graph_create_edge(node_lo=src, node_hi=dst,
                                                 relation=predicate)
        if not created:
            return None
        self._invalidate()
        return {"src": src, "predicate": predicate, "dst": dst}

    def create_type_relation(self, src: str, predicate: str,
                             dst: str) -> Optional[Dict[str, Any]]:
        """Vorgabe auf Typebene: im Graph-Modus bewusst noch nicht moeglich.

        Die API kennt keine Kante zwischen Typen. Naheliegend waere ein
        Schema-Attribut vom Typ entity_ref auf dem Quelltyp, doch dabei
        faellt der Zieltyp weg, und der Antwortvertrag des Schema-Endpunkts
        (welche Felder eine Attributliste je Typ zurueckgibt) ist ungeklaert.
        summary() kann die Vorgabe deshalb nicht zurueckliefern: sie baut
        relations ausschliesslich aus verdichteten Kanten, also nie mit
        count 0. Eine Linie, die beim naechsten Laden verschwindet, waere ein
        gebrochenes Versprechen - deshalb None, die Route antwortet mit 400
        und die Oberflaeche zeichnet nichts.

        Sobald der Schema-Endpunkt geklaert ist (Anlegen mit Zieltyp und
        Lesen der Attribute je Typ), gehoert hier das Schreiben hin und in
        summary() das Zurueckgeben der Vorgaben mit count 0.
        """
        return None

    def delete_type_relation(self, src: str, predicate: str, dst: str) -> bool:
        """Attribut anhand seines Namens auf dem Quelltyp suchen und loeschen.

        Der Zieltyp (dst) laesst sich im Schema nicht hinterlegen und damit
        auch nicht pruefen - eine bekannte Grenze der API, siehe
        create_type_relation. Gleichnamige Attribute verschiedener Zieltypen
        sind hier also nicht unterscheidbar.
        """
        predicate = " ".join(str(predicate or "").split())
        for node_type in self._export()["node_types"]:
            if _type_id(node_type) != str(src):
                continue
            for attribut in node_type.get("schema") or node_type.get("attributes") or []:
                if not isinstance(attribut, dict):
                    continue
                if str(_first(attribut, "name", "label")) != predicate:
                    continue
                attribut_id = str(_first(attribut, "id", "attribute_id"))
                if not attribut_id:
                    return False        # keine leere Kennung an die API
                ok = self._client.graph_delete_schema_attribute(
                    src, attribut_id) is not None
                if ok:
                    self._invalidate()
                return ok
        return False

    def delete_relation(self, src: str, predicate: str, dst: str) -> bool:
        """Passende Kante suchen und loeschen.

        Gerichtet wie gezeichnet: nur die Kante von src nach dst wird
        getroffen, nicht die Gegenrichtung. Sonst loeschte ein Klick auf
        eine Linie eine andere als die gezeigte.
        """
        predicate = " ".join(str(predicate or "").split())
        for edge in self._export()["edges"]:
            enden = _edge_ends(edge)
            if enden is None:
                continue
            e_src, e_dst, e_pred = enden
            passt = (e_pred == predicate
                     and e_src == str(src) and e_dst == str(dst))
            if not passt:
                continue
            edge_id = str(_first(edge, "id", "edge_id", "uuid"))
            if not edge_id:
                return False
            ok = self._client.graph_delete_edge(edge_id) is not None
            if ok:
                self._invalidate()
            return ok
        return False

    def delete_entity(self, entity_id: str) -> bool:
        """DELETE auf den Knoten; die API kaskadiert Zuordnungen und Kanten."""
        if not str(entity_id or "").strip():
            return False
        ok = self._client.graph_delete_node(entity_id) is not None
        if ok:
            self._invalidate()
        return ok

    def delete_type(self, type_id: str) -> bool:
        if not str(type_id or "").strip():
            return False
        ok = self._client.graph_delete_node_type(type_id) is not None
        if ok:
            self._invalidate()
        return ok

    def assign_document(self, entity_id: str, pointer: str) -> bool:
        """Dokument einem Knoten zuordnen - Grundlage fuer Filter."""
        if not entity_id or not pointer:
            return False
        ok = self._client.graph_assign_knowledge(entity_id, pointer) is not None
        if ok:
            self._invalidate()
        return ok

    def corpus(self) -> Dict[str, Any]:
        """Anzahl unterschiedlicher Dokumente ueber alle Knoten."""
        pointers = set()
        for node in self._export()["nodes"]:
            pointers.update(_pointers_of(node))
        return {"documents": len(pointers)} if pointers else {}


def _title_for(pointer: str) -> str:
    """Lesbarer Titel aus einem Pointer (Dateiname ohne Endung)."""
    base = str(pointer or "").replace("\\", "/").rstrip("/").split("/")[-1]
    stem = base.rsplit(".", 1)[0] if "." in base else base
    return stem or str(pointer)
