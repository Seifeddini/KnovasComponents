"""A small Client Zero-shaped graph for the views tests, behind a fake client.

The fake answers ``graph_get_many`` the way the Secure API does (paths and
envelopes as measured on tenant af639810, 2026-10-27) and counts every call, so
tests can assert what a view read — and what it did not.
"""
from __future__ import annotations

import itertools
import re
from typing import Any, Dict, List, Optional, Tuple

_ids = itertools.count(1)


class FixtureGraph:
    def __init__(self):
        self.types: Dict[str, str] = {}          # name -> id
        self.schemas: Dict[str, List[Dict[str, Any]]] = {}
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.facts: Dict[str, List[Dict[str, Any]]] = {}
        self.edges: List[Dict[str, Any]] = []
        self.by_key: Dict[str, str] = {}

    def type(self, name: str, *, meta_capable: bool = False) -> str:
        if name not in self.types:
            tid = f"t-{name}"
            self.types[name] = tid
            self.schemas[tid] = ([{"name": "meta", "datatype": "enum", "enum_values": ["yes"],
                                   "deprecated_at": None}] if meta_capable else [])
        return self.types[name]

    def node(self, type_name: str, name: str, key: Optional[str] = None,
             description: str = "", **facts: Any) -> str:
        nid = f"n{next(_ids):04d}"
        self.nodes[nid] = {"id": nid, "name": name, "node_type_id": self.type(type_name),
                           "description": description}
        rows = []
        declared = {a["name"] for a in self.schemas[self.type(type_name)]}
        for label in facts:
            if label not in declared and label != "meta":
                self.schemas[self.type(type_name)].append(
                    {"name": label, "datatype": "text", "deprecated_at": None})
        if key:
            rows.append(self._fact(nid, "key", key))
            self.by_key[key] = nid
        for label, value in facts.items():
            if isinstance(value, tuple):      # (value, valid_from)
                rows.append(self._fact(nid, label, value[0], valid_from=value[1]))
            else:
                rows.append(self._fact(nid, label, value))
        self.facts[nid] = rows
        return nid

    def _fact(self, nid: str, label: str, value: Any, valid_from: Optional[str] = None,
              valid_to: Optional[str] = None) -> Dict[str, Any]:
        return {"id": f"f{next(_ids)}", "node_id": nid, "label": label,
                "value": None if value is None else str(value), "valid_from": valid_from,
                "valid_to": valid_to, "provenance_pointer": None,
                "updated_at": "2026-10-27 10:00:00+00:00", "trust": {"tier": "asserted"}}

    def add_fact(self, nid: str, label: str, value: Any, valid_from: Optional[str] = None,
                 valid_to: Optional[str] = None) -> None:
        self.facts[nid].append(self._fact(nid, label, value, valid_from, valid_to))

    def edge(self, lo: str, relation: str, hi: str) -> None:
        self.edges.append({"id": f"e{next(_ids)}", "node_lo": lo, "node_hi": hi,
                           "relation": relation})


class FakeViewsClient:
    """The slice of KnovasAPIClient the views use: ``graph_get_many``."""

    customer_id = "af639810-afc7-403f-8139-f6026d085c99"
    cert_path = ""

    def __init__(self, graph: FixtureGraph, *, hidden: Optional[set] = None):
        self.g = graph
        self.hidden = set(hidden or ())        # node ids this caller may not see
        self.calls: List[str] = []
        self.batches: List[int] = []

    def _visible_edges(self):
        return [e for e in self.g.edges
                if e["node_lo"] not in self.hidden and e["node_hi"] not in self.hidden]

    def graph_get_many(self, calls, *, workers=4, acquire=None, max_retries=3):
        self.batches.append(len(calls))
        return [self._one(path, params or {}) for path, params in calls]

    def _one(self, path: str, params: Dict[str, Any]) -> Tuple[int, Any]:
        self.calls.append(path)
        g = self.g
        if path == "/node-types":
            return 200, {"node_types": [{"id": tid, "name": name, "description": ""}
                                        for name, tid in g.types.items()]}
        if path == "/nodes":
            return 200, {"nodes": [n for nid, n in g.nodes.items() if nid not in self.hidden]}
        if path == "/edges":
            return 200, {"edges": self._visible_edges()}
        m = re.fullmatch(r"/node-types/([^/]+)/schema", path)
        if m:
            return 200, {"attributes": g.schemas.get(m.group(1), [])}
        m = re.fullmatch(r"/nodes/([^/]+)/(facts|dossier|neighbors)", path)
        if m:
            nid, what = m.group(1), m.group(2)
            if nid not in g.nodes or nid in self.hidden:
                return 404, {"error": "not found"}
            if what == "facts":
                return 200, {"facts": g.facts.get(nid, [])}
            if what == "dossier":
                return 200, {"node": g.nodes[nid], "facts": g.facts.get(nid, []),
                             "edges": [e for e in self._visible_edges()
                                       if nid in (e["node_lo"], e["node_hi"])],
                             "trust_rollup": {"asserted": len(g.facts.get(nid, []))}}
            edges = [e for e in self._visible_edges() if nid in (e["node_lo"], e["node_hi"])]
            others = {e["node_hi"] if e["node_lo"] == nid else e["node_lo"] for e in edges}
            return 200, {"neighbors": [g.nodes[o] for o in others], "edges": edges}
        return 404, {"error": f"no route {path}"}


def load_definitions_into(g: FixtureGraph, processes: Optional[List[Dict[str, Any]]] = None,
                          spines: Optional[List[Dict[str, Any]]] = None) -> Dict[str, str]:
    """Write definition nodes the way KnowledgeBase's loader does (--only definitions).

    Facts as on tenant af639810 on 2026-10-27: JSON for context, requires,
    produces, levels and branches; comma lists for instance_types and
    exit_criteria; transitions as transitions_to edges; the value stream as a
    part_of edge. Returns node ids by key.
    """
    import json as _json
    from graph_views import definitions as defs

    proc_doc, spine_doc = defs.vendored()
    ids: Dict[str, str] = {}
    streams: Dict[str, str] = {}
    for p in processes if processes is not None else proc_doc["processes"]:
        facts = {"meta": "yes", "owner_role": p["owner_role"], "instance_types": ", ".join(p["instance_types"]),
                 "context": _json.dumps(p.get("context", []), separators=(",", ":")),
                 "source": "docs/ModernDocs/client-zero/ontology/processes.yaml"}
        for opt in ("exists_today", "pack"):
            if p.get(opt):
                facts[opt] = p[opt]
        pid = g.node("Process", p["name"], key=p["key"], description=p.get("description", ""), **facts)
        ids[p["key"]] = pid
        if p.get("value_stream"):
            vs = streams.get(p["value_stream"]) or g.node("ValueStream", p["value_stream"], key=p["value_stream"])
            streams[p["value_stream"]] = vs
            ids.setdefault(p["value_stream"], vs)
            g.edge(pid, "part_of", vs)
        by_step = {}
        for s in p["steps"]:
            sf = {"meta": "yes", "order": s["order"], "resting": "yes" if s.get("resting") else "no"}
            for fld in ("requires", "produces"):
                if s.get(fld):
                    sf[fld] = _json.dumps(s[fld], separators=(",", ":"))
            if s.get("exit_criteria"):
                sf["exit_criteria"] = ", ".join(s["exit_criteria"])
            sid = g.node("ProcessStep", f"{p['name']}: {s.get('name', s['id'])}", key=s["key"],
                         description=f"Step {s['order']} of {len(p['steps'])} in the {p['name']} process.", **sf)
            ids[s["key"]] = sid
            by_step[s["id"]] = sid
            g.edge(pid, "has_step", sid)
        for t in p.get("transitions", []):
            g.edge(by_step[t["from"]], "transitions_to", by_step[t["to"]])
    for sp in spines if spines is not None else spine_doc["spines"]:
        sf = {"meta": "yes", "levels": _json.dumps(sp["levels"], separators=(",", ":"))}
        if sp.get("branches"):
            sf["branches"] = _json.dumps(sp["branches"], separators=(",", ":"))
        ids[sp["key"]] = g.node("Spine", sp["name"], key=sp["key"], description=sp.get("description", ""), **sf)
    return ids


def client_zero_graph() -> FixtureGraph:
    """Enough of tenant af639810 to exercise every view (grown per step)."""
    g = FixtureGraph()
    for t in ("Process", "ProcessStep", "Spine", "SpineLevel", "GlossaryTerm", "Event", "Phase", "Gate"):
        g.type(t, meta_capable=True)

    defs_ids = load_definitions_into(g)
    proc = defs_ids["PROC-feature-lifecycle"]
    steps = {k[len("STEP-feature-lifecycle-"):]: v for k, v in defs_ids.items()
             if k.startswith("STEP-feature-lifecycle-")}

    comp = g.node("Component", "L2-KNOWLEDGE-GRAPH — Knowledge Graph", key="L2-KNOWLEDGE-GRAPH",
                  description="The knowledge graph the brain is built on.")
    plan = g.node("FeaturePlan", "Adaptive Rate Limits", key="FEAT-2026-09-08-adaptive-rate-limits",
                  status="implementing", owner="ROLE-ENGINE-OWNER", source="docs/plans/rate.md",
                  current_step=("implementing", "2026-10-20"))
    g.edge(plan, "instance_of", proc)
    g.edge(plan, "in_state", steps["implementing"])
    g.edge(plan, "affects", comp)
    for i in range(22):
        check = g.node("AlloyCheck", f"ALLOY-check-{i:02d}", key=f"ALLOY-{i:02d}",
                       outcome="failed" if i in (3, 7) else "passed")
        g.edge(check, "verifies", comp)
    for i in range(3):
        inv = g.node("Invariant", f"GI-GRAPH-{i:02d}", key=f"GI-GRAPH-{i:02d}")
        g.edge(comp, "keeps", inv)
    for i in range(60):                       # above meta_exact_max: hidden by type
        term = g.node("GlossaryTerm", f"Term {i}", key=f"TERM-{i}", meta="yes")
        g.edge(term, "describes", comp)
    run = g.node("Event", "Loader run 2026-10-27", key="EVT-run-1", meta="yes")
    review = g.node("Event", "Plan review", key="EVT-review-1", occurred_at="2026-10-21")
    g.edge(run, "touches", comp)
    g.edge(review, "touches", comp)
    g.edge(review, "touches", plan)
    return g
