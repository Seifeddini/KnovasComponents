"""Which nodes are definition or bookkeeping nodes (decision D2).

``meta = yes`` is a fact on each node, not a property of its type, and reading
it on every node of every meta-capable type costs more calls a minute than the
tenant allows. So, in this order:

1. a type-level marker in the definition or ontology files wins and costs
   nothing (Phase P is adding one);
2. only a type whose schema declares a ``meta`` attribute can be meta at all;
3. such a type with at most ``meta_exact_max`` nodes is decided per node from
   its fact, read only for the nodes a view would draw;
4. a larger one is hidden as a whole and reported as "nach Typ ausgeblendet".
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set

from graph_views.facts import KeyedTTL, fact_values, read_facts
from graph_views.reader import GraphReader
from graph_views.topology import SubjectState


def _live(attr: Dict[str, Any]) -> bool:
    return not (attr.get("deprecated_at") or attr.get("deprecated"))


class TypeSchemas:
    """Schema attributes per type, read only for the types a view meets.

    Reading all 59 schemas up front cost a board its rate budget; one batch of
    the types actually drawn costs a few calls, cached per subject for the
    definitions TTL.
    """

    def __init__(self, cache: KeyedTTL, key: Optional[str], reader: GraphReader, index: Any):
        self._cache, self._key, self._reader, self._index = cache, key, reader, index

    def attrs(self, type_names: Iterable[str]) -> Dict[str, List[Dict[str, Any]]]:
        names = [t for t in dict.fromkeys(type_names) if t and t in self._index.type_id]
        out: Dict[str, List[Dict[str, Any]]] = {}
        missing = []
        for t in names:
            hit = self._cache.peek((self._key, t)) if self._key is not None else None
            if hit is None:
                missing.append(t)
            else:
                out[t] = hit
        if missing:
            got = self._reader.schemas([self._index.type_id[t] for t in missing])
            for t in missing:
                attrs = [a for a in got.get(self._index.type_id[t]) or [] if _live(a)]
                out[t] = attrs
                if self._key is not None:
                    self._cache.put((self._key, t), attrs)
        return out

    def declares(self, type_name: str, names: Iterable[str]) -> Optional[str]:
        """The first of ``names`` the type's schema declares, or None."""
        wanted = list(names)
        declared = {str(a.get("name") or "") for a in self.attrs([type_name]).get(type_name, [])}
        return next((n for n in wanted if n in declared), None)


class MetaClassifier:
    def __init__(self, state: SubjectState, reader: GraphReader, *,
                 schemas: TypeSchemas, markers: Set[str], exact_max: int):
        self._state = state
        self._reader = reader
        self._schemas = schemas
        self._exact_max = exact_max
        self.markers = set(markers)
        self.capable: Set[str] = set()
        self.by_type: Set[str] = set()
        self.exact: Set[str] = set()
        self._seen: Set[str] = set()
        self._met: Set[str] = set()

    def _learn(self, type_names: Iterable[str]) -> None:
        todo = [t for t in dict.fromkeys(type_names) if t and t not in self._seen and t not in self.markers]
        if not todo:
            return
        for t, attrs in self._schemas.attrs(todo).items():
            if any(str(a.get("name") or "") == "meta" for a in attrs):
                self.capable.add(t)
                if len(self._state.index.by_type.get(t, ())) > self._exact_max:
                    self.by_type.add(t)
                else:
                    self.exact.add(t)
        self._seen.update(todo)

    def hidden_types(self) -> Set[str]:
        """Types hidden as a whole among those this request met: marked or too large."""
        return (self.markers | self.by_type) & self._met

    def classify(self, node_ids: Iterable[str]) -> Dict[str, bool]:
        index = self._state.index
        ids = list(dict.fromkeys(node_ids))
        types = {index.type_of(n) for n in ids}
        self._met |= types
        self._learn(types)
        out: Dict[str, bool] = {}
        exact_ids = []
        for nid in ids:
            t = index.type_of(nid)
            if t in self.markers or t in self.by_type:
                out[nid] = True
            elif t in self.exact:
                with self._state.lock:
                    known = self._state.meta.get(nid)
                if known is None:
                    exact_ids.append(nid)
                else:
                    out[nid] = known
            else:
                out[nid] = False
        if exact_ids:
            facts = read_facts(self._state, self._reader, exact_ids)
            with self._state.lock:
                for nid in exact_ids:
                    decided = fact_values(facts.get(nid)).get("meta", "").strip().lower() == "yes"
                    self._state.meta[nid] = decided
                    out[nid] = decided
        return out

    def reason(self, node_id: str) -> Optional[str]:
        t = self._state.index.type_of(node_id)
        if t in self.markers:
            return "type"
        if t in self.by_type:
            return "by_type"
        if t in self.exact:
            return "fact"
        return None


def type_markers(*documents: Optional[Dict[str, Any]]) -> Set[str]:
    """Type-level meta markers, from whichever file carries them.

    Accepts ``{"meta_types": [...]}`` at the top of a definitions file and
    ``types.<T>.meta: true`` in descriptions.yaml. Neither exists on the
    tenant's files yet (Phase P adds one); until then the result is empty and
    the schema rule below applies.
    """
    found: Set[str] = set()
    for doc in documents:
        if not isinstance(doc, dict):
            continue
        for name in doc.get("meta_types") or []:
            if isinstance(name, str) and name:
                found.add(name)
        types = doc.get("types")
        if isinstance(types, dict):
            for name, entry in types.items():
                if isinstance(entry, dict) and entry.get("meta") is True:
                    found.add(str(name))
    return found
