# Copied for the Platform's sync tests from KnowledgeBase scripts/client_zero/fake_api.py (master d7d0a43f): the
# in-memory graph API brain_runner's own tests run against. A test helper only; the Platform never imports KnowledgeBase.
"""In-memory stand-in for the graph API routes the Client Zero scripts use."""
from __future__ import annotations

import uuid
from collections import deque


class FakeApi:
    def __init__(self):
        self.types: dict[str, dict] = {}
        self.attrs: dict[str, dict] = {}
        self.nodes: dict[str, dict] = {}
        self.facts: dict[str, dict] = {}
        self.edges: dict[str, dict] = {}
        self.calls = 0
        self.failures: deque[tuple[int, dict]] = deque()  # injected (status, body) answers for the next POSTs
        self.writes = 0
        self.enum_only_attributes = False  # mimic the graph-store defect: non-enum attributes answer 409
        self._clock = 0

    def _stamp(self) -> str:
        self._clock += 1
        return f"2026-10-25 00:00:{self._clock:09d}"

    # -- helpers
    def _parts(self, path: str) -> list[str]:
        return path.strip("/").removeprefix("secured/graph/").split("/")

    def get(self, path: str, params=None):
        self.calls += 1
        p = self._parts(path)
        if p == ["node-types"]:
            return {"node_types": list(self.types.values())}
        if p[0] == "node-types" and p[-1] == "schema":
            return {"attributes": [a for a in self.attrs.values() if a["node_type_id"] == p[1]]}
        if p == ["nodes"]:
            return {"nodes": list(self.nodes.values())}
        if p[0] == "nodes" and len(p) == 2:
            n = dict(self.nodes[p[1]]); n["facts"] = [f for f in self.facts.values() if f["node_id"] == p[1]]
            return n
        if p[0] == "nodes" and p[-1] == "facts":
            # like the graph store (F9): only the facts valid now; a closed fact cannot be read back
            return {"facts": [f for f in self.facts.values() if f["node_id"] == p[1] and self._valid_now(f)]}
        if p == ["edges"]:
            return {"edges": list(self.edges.values())}
        raise KeyError(path)

    def post(self, path: str, body: dict):
        self.calls += 1
        if self.failures:
            status, data = self.failures.popleft()
            if status >= 400:
                return status, data
        self.writes += 1
        p = self._parts(path)
        new_id = str(uuid.uuid4())
        if p == ["node-types"]:
            row = {"id": new_id, "name": body["name"], "description": body.get("description"), "parent_type_id": "euo:Entity"}
            self.types[new_id] = row
            return 201, {"node_type": row}
        if p[0] == "node-types" and p[-1] == "schema":
            if self.enum_only_attributes and body.get("datatype") != "enum":
                self.writes -= 1
                return 409, {"error_code": "CONFLICT", "error": "Write violates a structural constraint (tenancy or uniqueness)"}
            row = {"id": new_id, "node_type_id": p[1], **body}
            self.attrs[new_id] = row
            return 201, {"attribute": row}
        if p == ["nodes"]:
            row = {"id": new_id, "name": body["name"], "node_type_id": body.get("node_type_id"), "description": body.get("description")}
            self.nodes[new_id] = row
            return 201, {"node": row}
        if p[0] == "nodes" and p[-1] == "facts":
            row = {"id": new_id, "node_id": p[1], "curation_status": "manual", "created_at": self._stamp(), **body}
            self.facts[new_id] = row
            return 201, {"fact": row}
        if p == ["edges"]:
            # the graph store keys an edge by its unordered node pair and relation (F8): the reverse edge collides
            key = (frozenset((body["node_lo"], body["node_hi"])), body["relation"])
            if any((frozenset((e["node_lo"], e["node_hi"])), e["relation"]) == key for e in self.edges.values()):
                return 409, {"error_code": "conflict"}
            row = {"id": new_id, **body}
            self.edges[new_id] = row
            return 201, {"edge": row}
        raise KeyError(path)

    hides_closed_facts = True        # the graph store's behaviour (F9); a test of logic for a fixed engine turns it off

    def _valid_now(self, f: dict) -> bool:
        import datetime as dt
        to = str(f.get("valid_to") or "")[:10]
        return not self.hides_closed_facts or not to or to > dt.date.today().isoformat()

    def patch(self, path: str, body: dict):
        self.calls += 1
        p = self._parts(path)
        if p[0] == "facts" and p[1] in self.facts:
            self.facts[p[1]].update({k: body[k] for k in ("value", "valid_from", "valid_to", "label") if k in body})
            return 200, {"fact": self.facts[p[1]]}
        if p[0] == "nodes" and len(p) == 2 and p[1] in self.nodes:
            self.nodes[p[1]].update({k: body[k] for k in ("name", "description", "node_type_id") if k in body})
            return 200, {"node": self.nodes[p[1]]}
        if p[0] == "node-types" and len(p) == 2 and p[1] in self.types:
            self.types[p[1]].update({k: body[k] for k in ("name", "description") if k in body})
            return 200, {"node_type": self.types[p[1]]}
        return 404, {}

    def delete(self, path: str):
        self.calls += 1
        p = self._parts(path)
        store = {"nodes": self.nodes, "edges": self.edges, "facts": self.facts, "node-types": self.types}[p[0]]
        row = store.pop(p[1], None)
        if row is None:
            return 404, {}
        if p[0] == "nodes":   # the real API cascades a node's facts and edges
            self.facts = {k: f for k, f in self.facts.items() if f["node_id"] != p[1]}
            self.edges = {k: e for k, e in self.edges.items() if p[1] not in (e["node_lo"], e["node_hi"])}
        return 200, {}

    def snapshot_for_rules(self):
        """What rules_report.build_snapshot would return, built from memory."""
        return {"types": self.types, "attrs": self.attrs, "nodes": self.nodes, "facts": self.facts, "edges": self.edges}
