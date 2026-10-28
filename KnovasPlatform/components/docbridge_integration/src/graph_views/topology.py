"""The topology index (decision D1) and its per-subject cache.

The API has no edge query bounded to one node, and the bounded per-node routes
cost ~1.4 s each. So the views read the edge list once per (tenant, subject):
topology only, filtered by the brain for that caller, kept in this process for
60 s, never shared between subjects and never sent to the browser. Above the
scale guard (edge count or read time) the request walks per node instead.

A6 (server-side bounded traversal) removes the full edge read.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import defaultdict
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from graph_views.reader import GraphReader

logger = logging.getLogger(__name__)


def _str(value: Any) -> str:
    return "" if value is None else str(value)


class TopologyIndex:
    """Adjacency by node, relation and direction over node headers.

    ``complete`` is False for the per-node fallback, which only knows the
    neighbourhoods it has expanded; ``ensure`` expands more on demand.
    """

    complete = True

    def __init__(self, node_types: Iterable[Dict[str, Any]],
                 nodes: Iterable[Dict[str, Any]], edges: Iterable[Dict[str, Any]]):
        self.type_name: Dict[str, str] = {}
        self.type_id: Dict[str, str] = {}
        self.type_description: Dict[str, str] = {}
        for t in node_types:
            tid, name = _str(t.get("id")), _str(t.get("name"))
            if tid and name:
                self.type_name[tid] = name
                self.type_id[name] = tid
                self.type_description[name] = _str(t.get("description"))
        self.nodes: Dict[str, Dict[str, str]] = {}
        self.by_type: Dict[str, List[str]] = defaultdict(list)
        self.out: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
        self.inc: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
        self.edge_count = 0
        self.add_nodes(nodes)
        self.add_edges(edges)

    def add_nodes(self, nodes: Iterable[Dict[str, Any]]) -> None:
        for n in nodes:
            nid = _str(n.get("id"))
            if not nid or nid in self.nodes:
                continue
            tname = self.type_name.get(_str(n.get("node_type_id")), "")
            self.nodes[nid] = {"id": nid, "name": _str(n.get("name")), "type": tname,
                               "description": _str(n.get("description"))}
            self.by_type[tname].append(nid)

    def add_edges(self, edges: Iterable[Dict[str, Any]]) -> None:
        seen = getattr(self, "_seen_edges", None)
        if seen is None:
            seen = self._seen_edges = set()
        for e in edges:
            lo, hi, rel = _str(e.get("node_lo")), _str(e.get("node_hi")), _str(e.get("relation"))
            if not lo or not hi or not rel:
                continue
            key = _str(e.get("id")) or f"{lo}|{rel}|{hi}"
            if key in seen:
                continue
            seen.add(key)
            self.out[lo].append((rel, hi))
            self.inc[hi].append((rel, lo))
            self.edge_count += 1

    def type_of(self, node_id: str) -> str:
        node = self.nodes.get(node_id)
        return node["type"] if node else ""

    def ensure(self, node_ids: Iterable[str]) -> int:
        """The complete index knows every neighbourhood already."""
        return 0

    def neighbours(self, node_id: str, relation: Optional[str] = None,
                   direction: Optional[str] = None) -> List[Tuple[str, str, str]]:
        """(relation, direction, other) around a node, optionally filtered."""
        out: List[Tuple[str, str, str]] = []
        if direction in (None, "out"):
            out += [(r, "out", m) for r, m in self.out.get(node_id, ()) if relation in (None, r)]
        if direction in (None, "in"):
            out += [(r, "in", m) for r, m in self.inc.get(node_id, ()) if relation in (None, r)]
        return out


class PartialIndex(TopologyIndex):
    """The per-node fallback: one ``neighbors`` call per node it is asked about."""

    complete = False

    def __init__(self, node_types: Iterable[Dict[str, Any]], reader: GraphReader,
                 max_expansions: int):
        super().__init__(node_types, [], [])
        self._reader = reader
        self._expanded: Set[str] = set()
        self._budget = max(1, int(max_expansions))
        self._lock = threading.Lock()
        self.truncated = False

    def ensure(self, node_ids: Iterable[str]) -> int:
        todo = [n for n in dict.fromkeys(node_ids) if n not in self._expanded]
        done = 0
        for nid in todo:
            with self._lock:
                if len(self._expanded) >= self._budget:
                    self.truncated = True
                    break
                self._expanded.add(nid)
            got = self._reader.neighbors(nid)
            if got is None:
                continue
            self.add_nodes(got["neighbors"])
            self.add_edges(got["edges"])
            done += 1
        return done

    def remember(self, nodes: Iterable[Dict[str, Any]]) -> None:
        self.add_nodes(nodes)


class SubjectState:
    """Everything the views keep for one (tenant, subject): index and caches."""

    def __init__(self, index: TopologyIndex, as_of: float, stats: Dict[str, Any],
                 fallback: Optional[str]):
        self.index = index
        self.as_of = as_of
        self.stats = stats
        self.fallback = fallback
        self.facts: Dict[str, Optional[List[Dict[str, Any]]]] = {}
        self.meta: Dict[str, bool] = {}
        self.lock = threading.Lock()


def subject_key(client: Any) -> Optional[str]:
    """Cache slot for this request, or None to skip caching.

    Same rule as the Cortex export cache (ontology_graph): without a broker
    one worker-wide slot; with one, the signed-in subject; a broker without a
    user has no subject and gets no cache. The tenant and the client
    certificate's mtime are part of the key, so a renewed certificate starts
    a new slot.
    """
    tenant = _str(getattr(client, "customer_id", ""))
    cert_path = _str(getattr(client, "cert_path", ""))
    try:
        cert_gen = str(os.stat(cert_path).st_mtime_ns) if cert_path else ""
    except OSError:
        cert_gen = ""
    broker = getattr(client, "_principal_broker", None)
    if broker is None:
        subject = ""
    else:
        current_user = getattr(broker, "current_user", None)
        user = current_user() if callable(current_user) else None
        if user is None:
            return None
        subject = _str(getattr(user, "id", ""))
    return json.dumps([tenant, subject, cert_gen])


class TopologyCache:
    def __init__(self, settings: Any, now: Optional[Callable[[], float]] = None,
                 max_subjects: int = 64):
        self._settings = settings
        self._now = now or time.time
        self._max = max_subjects
        self._states: Dict[str, SubjectState] = {}
        self._lock = threading.Lock()

    def get(self, key: Optional[str], reader: GraphReader,
            refresh: bool = False) -> SubjectState:
        now = self._now()
        if key is not None and not refresh:
            with self._lock:
                state = self._states.get(key)
                if state is not None and now - state.as_of < self._settings.ttl_seconds:
                    return state
        state = self._load(reader, now)
        if key is not None:
            with self._lock:
                self._states[key] = state
                while len(self._states) > self._max:
                    oldest = min(self._states, key=lambda k: self._states[k].as_of)
                    self._states.pop(oldest, None)
        return state

    def drop_subject(self, subject_id: str) -> int:
        """Forget every slot of one subject (logout)."""
        with self._lock:
            gone = [k for k in self._states if json.loads(k)[1] == str(subject_id)]
            for k in gone:
                self._states.pop(k, None)
        return len(gone)

    def clear(self) -> None:
        with self._lock:
            self._states.clear()

    def _load(self, reader: GraphReader, now: float) -> SubjectState:
        s = self._settings
        topo = reader.topology()
        edges, nodes = topo["edges"], topo["nodes"]
        stats = {"edges": len(edges), "nodes": len(nodes),
                 "seconds": round(topo["seconds"], 3),
                 "payload_kb": round(len(json.dumps(edges)) / 1024, 1)}
        logger.info("views: topology read edges=%d nodes=%d payload_kb=%.1f seconds=%.3f",
                    stats["edges"], stats["nodes"], stats["payload_kb"], stats["seconds"])
        fallback = None
        if len(edges) > s.edge_limit:
            fallback = f"Kantenliste zu gross ({len(edges)} > {s.edge_limit})"
        elif topo["seconds"] > s.edge_budget_seconds:
            fallback = f"Kantenliste zu langsam ({topo['seconds']:.1f} s > {s.edge_budget_seconds:g} s)"
        if fallback:
            logger.warning("views: scale guard fired, walking per node: %s", fallback)
            index: TopologyIndex = PartialIndex(topo["node_types"], reader,
                                                s.fallback_max_expansions)
            index.remember(nodes)
        else:
            index = TopologyIndex(topo["node_types"], nodes, edges)
        return SubjectState(index, now, stats, fallback)
