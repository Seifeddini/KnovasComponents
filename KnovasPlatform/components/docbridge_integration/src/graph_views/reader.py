"""The few reads the views make, over ``KnovasAPIClient.graph_get_many``.

A narrow port so the views can be tested against a fake and so every read goes
through one pacer. Read-only by construction: there is no write method here.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

logger = logging.getLogger(__name__)


def _list(payload: Any, key: str) -> List[Dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get(key), list):
        return [item for item in payload[key] if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


class ReadFailed(RuntimeError):
    """A read the view cannot do without (topology, node types)."""

    def __init__(self, what: str, status: int):
        super().__init__(f"{what}: HTTP {status}")
        self.status = status


class GraphReader:
    def __init__(self, client: Any, acquire: Optional[Callable[[], None]] = None,
                 workers: int = 4):
        self._client = client
        self._acquire = acquire
        self._workers = workers
        self.calls = 0

    def _many(self, calls: List[Tuple[str, Optional[Dict[str, Any]]]]) -> List[Tuple[int, Any]]:
        self.calls += len(calls)
        return self._client.graph_get_many(calls, workers=self._workers,
                                           acquire=self._acquire)

    def topology(self) -> Dict[str, Any]:
        """Node types, node headers and edges in one parallel round.

        Headers and edges carry no facts (D1); the list route is also the one
        that returns descriptions (BUG-03).
        """
        started = time.perf_counter()
        (s_types, types), (s_nodes, nodes), (s_edges, edges) = self._many(
            [("/node-types", None), ("/nodes", None), ("/edges", None)])
        elapsed = time.perf_counter() - started
        for what, status in (("node-types", s_types), ("nodes", s_nodes), ("edges", s_edges)):
            if status >= 400 or status == 599:
                raise ReadFailed(what, status)
        return {"node_types": _list(types, "node_types"), "nodes": _list(nodes, "nodes"),
                "edges": _list(edges, "edges"), "seconds": elapsed}

    def node_types(self) -> List[Dict[str, Any]]:
        ((status, payload),) = self._many([("/node-types", None)])
        if status >= 400 or status == 599:
            raise ReadFailed("node-types", status)
        return _list(payload, "node_types")

    def schemas(self, type_ids: Iterable[str]) -> Dict[str, Optional[List[Dict[str, Any]]]]:
        ids = list(type_ids)
        out: Dict[str, Optional[List[Dict[str, Any]]]] = {}
        for tid, (status, payload) in zip(ids, self._many(
                [(f"/node-types/{quote(t, safe='')}/schema", None) for t in ids])):
            if status >= 400 or status == 599:
                out[tid] = None
                continue
            attrs = []
            if isinstance(payload, dict):
                for key in ("attributes", "schema"):
                    if isinstance(payload.get(key), list):
                        attrs = payload[key]
                        break
            out[tid] = [a for a in attrs if isinstance(a, dict)]
        return out

    def facts_many(self, node_ids: Iterable[str]) -> Dict[str, Optional[List[Dict[str, Any]]]]:
        """Facts per node; None for a node that answered 404 or failed."""
        ids = list(dict.fromkeys(node_ids))
        out: Dict[str, Optional[List[Dict[str, Any]]]] = {}
        for nid, (status, payload) in zip(ids, self._many(
                [(f"/nodes/{quote(n, safe='')}/facts", None) for n in ids])):
            out[nid] = None if status >= 400 or status == 599 else _list(payload, "facts")
        return out

    def neighbors(self, node_id: str) -> Optional[Dict[str, Any]]:
        """One hop with its induced edges: the per-node fallback of D1."""
        ((status, payload),) = self._many(
            [(f"/nodes/{quote(node_id, safe='')}/neighbors",
              {"depth": 1, "include_edges": "true"})])
        if status >= 400 or status == 599 or not isinstance(payload, dict):
            return None
        return {"neighbors": _list(payload.get("neighbors"), "neighbors"),
                "edges": _list(payload.get("edges"), "edges")}

    def dossier(self, node_id: str) -> Optional[Dict[str, Any]]:
        ((status, payload),) = self._many(
            [(f"/nodes/{quote(node_id, safe='')}/dossier", None)])
        if status >= 400 or status == 599 or not isinstance(payload, dict):
            return None
        return payload
