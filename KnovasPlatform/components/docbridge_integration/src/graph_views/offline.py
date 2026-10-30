"""A tenant's knowledge graph held in one JSON file, behind the client slice the views read.

For the offline demo (``serve_views_demo.py``): the views run unchanged against a graph
that no Knovas server holds. ``OfflineClient.graph_get_many`` answers the paths the views
ask for with the Secure API's envelopes (as ``tests/views_fixture.py`` does, measured on
tenant af639810 on 2026-10-27); ``OfflineClient.get`` answers the same reads with the
``secured/graph/`` prefix, the way KnowledgeBase's ``brain_runner`` reads a tenant, so its
``evaluate`` can compute the reports over the same file.

File format (``format: knovas-offline-graph``, version 1)::

    {"format": ..., "format_version": 1, "tenant": "<uuid>", "label": "...",
     "types": {name: {"id": ..., "description": ..., "attributes": [...]}},
     "nodes": [{"id", "name", "node_type_id", "description"}],
     "facts": {node id: [{"label", "value", "valid_from"}]},
     "edges": [{"node_lo", "relation", "node_hi"}]}

An edge runs from ``node_lo`` to ``node_hi`` (``direction: out`` from ``node_lo``).
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

FORMAT = "knovas-offline-graph"
FORMAT_VERSION = 1

FactValue = Union[str, Tuple[str, Optional[str]]]       # value, or (value, valid_from)


class OfflineGraphError(Exception):
    """The file is not an offline graph this reader knows."""


class OfflineGraph:
    """Node types, nodes, facts and edges; ids are derived from the keys, so a rebuild is identical."""

    def __init__(self, tenant: str, label: str = ""):
        self.tenant = tenant
        self.label = label
        self.types: Dict[str, Dict[str, Any]] = {}
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.facts: Dict[str, List[Dict[str, Any]]] = {}
        self.edges: List[Dict[str, Any]] = []
        self.by_key: Dict[str, str] = {}

    # -- building ---------------------------------------------------------------------------------

    def _id(self, *parts: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, "/".join(("knovas-offline", self.tenant) + parts)))

    def type(self, name: str, *, description: str = "", meta_capable: bool = False) -> str:
        if name not in self.types:
            attrs = [{"id": self._id("attr", name, "meta"), "name": "meta", "datatype": "enum", "enum_values": ["yes"],
                      "deprecated_at": None}] if meta_capable else []
            self.types[name] = {"id": self._id("type", name), "description": description, "attributes": attrs}
        elif description:
            self.types[name]["description"] = description
        return self.types[name]["id"]

    def node(self, type_name: str, name: str, key: Optional[str] = None, description: str = "",
             facts: Optional[Dict[str, FactValue]] = None) -> str:
        if key and key in self.by_key:
            raise ValueError(f"key {key} twice")
        nid = self._id("node", key or f"{type_name}:{name}")
        self.nodes[nid] = {"id": nid, "name": name, "node_type_id": self.type(type_name),
                           "description": description}
        self.facts[nid] = []
        if key:
            self.by_key[key] = nid
            self.fact(nid, "key", key)
        for label, value in (facts or {}).items():
            if isinstance(value, tuple):
                self.fact(nid, label, value[0], valid_from=value[1])
            elif value is not None:
                self.fact(nid, label, value)
        return nid

    def fact(self, nid: str, label: str, value: Any, valid_from: Optional[str] = None) -> None:
        tname = self._type_name(nid)
        schema = self.types[tname]["attributes"]
        if label not in {a["name"] for a in schema}:
            schema.append({"id": self._id("attr", tname, label), "name": label, "datatype": "text", "deprecated_at": None})
        self.facts[nid].append({"label": label, "value": str(value), "valid_from": valid_from})

    def edge(self, lo: str, relation: str, hi: str) -> None:
        self.edges.append({"node_lo": self.id_of(lo), "relation": relation, "node_hi": self.id_of(hi)})

    def id_of(self, key_or_id: str) -> str:
        """A node id from its key (or the id itself)."""
        if key_or_id in self.nodes:
            return key_or_id
        try:
            return self.by_key[key_or_id]
        except KeyError:
            raise KeyError(f"no node with key {key_or_id}") from None

    def _type_name(self, nid: str) -> str:
        tid = self.nodes[nid]["node_type_id"]
        return next(n for n, t in self.types.items() if t["id"] == tid)

    # -- file -------------------------------------------------------------------------------------

    def to_json(self) -> Dict[str, Any]:
        return {"format": FORMAT, "format_version": FORMAT_VERSION, "tenant": self.tenant, "label": self.label,
                "types": self.types, "nodes": list(self.nodes.values()), "facts": self.facts, "edges": self.edges}

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps(self.to_json(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    @classmethod
    def from_json(cls, doc: Dict[str, Any]) -> "OfflineGraph":
        if not isinstance(doc, dict) or doc.get("format") != FORMAT:
            raise OfflineGraphError(f"not a {FORMAT} file")
        if doc.get("format_version") != FORMAT_VERSION:
            raise OfflineGraphError(f"format version {doc.get('format_version')!r} is not {FORMAT_VERSION}")
        g = cls(str(doc["tenant"]), str(doc.get("label") or ""))
        g.types = doc["types"]
        g.nodes = {n["id"]: n for n in doc["nodes"]}
        g.facts = {nid: list(rows) for nid, rows in doc["facts"].items()}
        g.edges = list(doc["edges"])
        for nid, rows in g.facts.items():
            for f in rows:
                if f["label"] == "key":
                    g.by_key[f["value"]] = nid
        return g

    @classmethod
    def load(cls, path: Path) -> "OfflineGraph":
        return cls.from_json(json.loads(Path(path).read_text(encoding="utf-8")))


class OfflineClient:
    """The slice of KnovasAPIClient the views use (``graph_get_many``), over an OfflineGraph."""

    cert_path = ""

    def __init__(self, graph: OfflineGraph):
        self.g = graph
        self.customer_id = graph.tenant
        self._fact_rows = {nid: [self._row(nid, i, f) for i, f in enumerate(rows)] for nid, rows in graph.facts.items()}
        self._edge_rows = [{"id": f"e{i}", **e} for i, e in enumerate(graph.edges)]

    @staticmethod
    def _row(nid: str, i: int, f: Dict[str, Any]) -> Dict[str, Any]:
        stamp = f.get("valid_from") or "2026-10-01"
        return {"id": f"{nid}/f{i}", "node_id": nid, "label": f["label"], "value": f["value"],
                "valid_from": f.get("valid_from"), "valid_to": None, "provenance_pointer": None,
                "created_at": f"{stamp} 08:00:00+00:00", "updated_at": f"{stamp} 08:00:00+00:00",
                "trust": {"tier": "asserted"}}

    def graph_get_many(self, calls: Iterable[Tuple[str, Optional[Dict[str, Any]]]], *, workers: int = 4,
                       acquire: Any = None, max_retries: int = 3) -> List[Tuple[int, Any]]:
        return [self._one(path) for path, _params in calls]

    def get(self, path: str) -> Any:
        """``brain_runner``'s read: the path with ``secured/graph/``; anything but 200 raises."""
        status, body = self._one("/" + path.removeprefix("secured/graph/").lstrip("/"))
        if status != 200:
            raise RuntimeError(f"offline graph: {status} for {path}")
        return body

    def _one(self, path: str) -> Tuple[int, Any]:
        g = self.g
        if path == "/node-types":
            return 200, {"node_types": [{"id": t["id"], "name": name,
                                                  "description": t.get("description") or ""} for name, t in g.types.items()]}
        if path == "/nodes":
            return 200, {"nodes": list(g.nodes.values())}
        if path == "/edges":
            return 200, {"edges": self._edge_rows}
        m = re.fullmatch(r"/node-types/([^/]+)/schema", path)
        if m:
            t = next((t for t in g.types.values() if t["id"] == m.group(1)), None)
            return (200, {"attributes": t["attributes"]}) if t else (404, {"error": "not found"})
        m = re.fullmatch(r"/nodes/([^/]+)/(facts|dossier|neighbors)", path)
        if m:
            nid, what = m.groups()
            if nid not in g.nodes:
                return 404, {"error": "not found"}
            facts = self._fact_rows.get(nid, [])
            edges = [e for e in self._edge_rows if nid in (e["node_lo"], e["node_hi"])]
            if what == "facts":
                return 200, {"facts": facts}
            if what == "dossier":
                return 200, {"node": g.nodes[nid], "facts": facts, "edges": edges,
                             "trust_rollup": {"asserted": len(facts)}}
            others = {e["node_hi"] if e["node_lo"] == nid else e["node_lo"] for e in edges}
            return 200, {"neighbors": [g.nodes[o] for o in others], "edges": edges}
        return 404, {"error": f"no route {path}"}
