"""<<Im Wissensgraphen>>: the small graph under an answer of <<Wissensgraph>> (PMVP-20 brain-graph).

When an answer of ``assistant.mode`` ``brain_graph`` is ``answered``, ``build_graph`` draws up, from what that answer
already read (the ``GraphSession``'s topology and passages: no further call to the Core), the part of the knowledge
graph the answer stands on:

- **cited nodes** (``cited: true``, with the number of the passage the page shows): the node of every cited node
  passage (pointer ``brain:<node id>``);
- **count nodes** (``kind: "count"``, ``cited: true``): a cited count passage ("Anzahl der Treffer: 97") is one
  summary node ("97 \\u00d7 <type>"), never its 97 matches. It is joined to the node it was asked about (a
  ``neighbours`` count) or, for a count over a type (``list_nodes``), to the cited nodes among its matches;
- **the node asked about** of a cited count (``cited: false`` unless it is cited itself), and the matches of a cited
  count of at most ``SMALL_COUNT`` (their names stand in the count's passage, "Treffer: ...");
- **connecting nodes** (``cited: false``): a node of the topology the answer read that is joined by an edge to two
  or more of the nodes above (the single hop between them), most joined first;
- **edges**: the topology's edges among those nodes (``from`` -> ``to`` as the relation reads, the relation's name),
  and the count nodes' own (``ABOUT_RELATION`` or the relation the count followed, ``MATCH_RELATION``).

Caps: ``MAX_NODES`` nodes and ``MAX_EDGES`` edges, the cited and count nodes first; fewer than two nodes is no
graph. A name shown is one the person was already shown: a cited node's (its passage begins with it), else only a
name that stands in the text of a passage the answer cites; any other node keeps its type and an empty name. The
code is generic: types, relations and names come from the graph at run time.

``clean_graph`` reads a stored graph back (``assistant_routes.stored_reply``): the same shape, the same caps, or
None. Nothing here logs. This module imports neither Flask nor the app. It is ASCII-only.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from assistant import graph_reads as gr

#: The caps of one answer's graph.
MAX_NODES = 30
MAX_EDGES = 60
#: Longest stored name, type, relation and query.
MAX_NAME_CHARS = 200
MAX_QUERY_CHARS = 300
#: A cited count of at most this many matches draws them too (a larger one only its summary node).
SMALL_COUNT = 6
#: The relation drawn from the node a count was asked about to the count, when the count followed none.
ABOUT_RELATION = "Abfrage"
#: The relation drawn from a count over a type to a cited node among its matches.
MATCH_RELATION = "Treffer"
#: The id of a count node: ``abfrage:<the passage number the page shows>``.
COUNT_ID_PREFIX = "abfrage:"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def count_label(total: int, type_name: str) -> str:
    """A count node's label: "97 \u00d7 <type>", or "97 Treffer" when the count names no single type."""
    return f"{total} \u00d7 {type_name}" if type_name else f"{total} Treffer"


def _cut(text: Any, limit: int) -> str:
    value = " ".join(str(text if text is not None else "").replace("\x00", "").split())
    return value if len(value) <= limit else value[:limit - 1].rstrip() + "\u2026"


def build_graph(session: Any, passages: Sequence[Any]) -> Optional[Dict[str, Any]]:
    """The graph under an answered reply: ``{"nodes": [...], "edges": [...]}`` or None (fewer than two nodes, no
    topology read). ``session`` is the answer's ``GraphSession``, ``passages`` the reply's (cited, renumbered)."""
    topo = session.topology_if_read()
    if topo is None:
        return None
    book = session.book
    shown_texts = [p.text for p in passages if isinstance(getattr(p, "text", None), str)]

    def shown_name(node_id: str) -> str:
        name = topo.nodes[node_id].name
        return name if any(name in text for text in shown_texts) else ""

    def type_of(node_id: str) -> str:
        return topo.type_name.get(topo.nodes[node_id].type_id, "")

    nodes: Dict[str, Dict[str, Any]] = {}
    edges: List[Dict[str, Any]] = []
    edge_keys: Set[Tuple[str, str, str]] = set()

    def add_edge(source: str, target: str, relation: str) -> None:
        key = (source, relation, target)
        if (len(edges) < MAX_EDGES and key not in edge_keys and source != target and source in nodes
                and target in nodes):
            edge_keys.add(key)
            edges.append({"from": source, "to": target, "relation": _cut(relation, MAX_NAME_CHARS)})

    def add_node(node_id: str, **fields: Any) -> bool:
        if node_id in nodes:
            return True
        if len(nodes) >= MAX_NODES:
            return False
        nodes[node_id] = {"id": node_id, **fields}
        return True

    # 1. the cited passages, in the order the page numbers them: nodes and counts
    counts: List[Tuple[str, gr.CountRead]] = []
    for passage in passages:
        pointer, text, number = getattr(passage, "pointer", None), getattr(passage, "text", None), passage.number
        original = book.number_of(pointer, text) if isinstance(pointer, str) and isinstance(text, str) else None
        asked = session.counts.get(original) if original is not None else None
        if asked is not None:
            type_name = topo.type_name.get(asked.type_id, "") if asked.type_id else ""
            if not type_name:
                kinds = {topo.nodes[n].type_id for n in asked.matched if n in topo.nodes}
                type_name = topo.type_name.get(kinds.pop(), "") if len(kinds) == 1 else ""
            count_id = f"{COUNT_ID_PREFIX}{number}"
            if add_node(count_id, name=count_label(asked.total, type_name), type=_cut(type_name, MAX_NAME_CHARS),
                        cited=True, passage=number, kind="count", query=_cut(asked.description, MAX_QUERY_CHARS)):
                counts.append((count_id, asked))
            continue
        if isinstance(text, str) and text.startswith(gr.COUNT_HEAD):
            continue                       # a count this session did not record: no node of its own
        node_id = gr.node_of_pointer(pointer)
        if node_id is None or node_id not in topo.nodes:
            continue                       # a document's passage
        add_node(node_id, name=_cut(topo.nodes[node_id].name, MAX_NAME_CHARS),
                 type=_cut(type_of(node_id), MAX_NAME_CHARS), cited=True, passage=number)

    def add_read(node_id: str) -> None:
        add_node(node_id, name=_cut(shown_name(node_id), MAX_NAME_CHARS), type=_cut(type_of(node_id), MAX_NAME_CHARS),
                 cited=False)

    # 2. the node each count was asked about, then the matches of a small count
    for _count_id, asked in counts:
        if asked.about in topo.nodes:
            add_read(asked.about)
    for _count_id, asked in counts:
        if asked.total <= SMALL_COUNT:
            for match in asked.matched:
                if match in topo.nodes:
                    add_read(match)
    held = [n for n in nodes if n in topo.nodes]

    # 3. the count nodes' edges, then the edges among the nodes held
    for count_id, asked in counts:
        if asked.about in nodes:
            add_edge(asked.about, count_id, asked.relation or ABOUT_RELATION)
        else:
            for match in asked.matched:
                add_edge(count_id, match, MATCH_RELATION)
    for node_id in held:
        for relation, other in topo.out.get(node_id, ()):
            add_edge(node_id, other, relation)

    # 4. the single hops between the nodes held, the most joined first
    held_set = set(held)
    joins: Dict[str, Set[str]] = {}
    for node_id in held:
        for _relation, other in list(topo.out.get(node_id, ())) + list(topo.inc.get(node_id, ())):
            if other not in held_set:
                joins.setdefault(other, set()).add(node_id)
    read = {gr.node_of_pointer(p.pointer) for p in book.passages}
    ranked = sorted((other for other, joined in joins.items() if len(joined) >= 2),
                    key=lambda other: (-len(joins[other]), other not in read, topo.nodes[other].name.casefold(), other))
    for other in ranked:
        links = [(other, relation, target) for relation, target in topo.out.get(other, ()) if target in held_set]
        links += [(source, relation, other) for relation, source in topo.inc.get(other, ()) if source in held_set]
        if len(nodes) >= MAX_NODES or MAX_EDGES - len(edges) < 2:
            break
        add_read(other)
        for source, relation, target in links:
            add_edge(source, target, relation)

    # 5. the nodes this answer read (node passages, cited or not) joined by an edge to a node held: connectors after
    #    those above, the most joined first; when fewer than two nodes qualified above, they make the graph (the live
    #    demo of 2026-11-28: Claude read seven nodes and cited one, and no graph was drawn). Names as above.
    held_set = {n for n in nodes if n in topo.nodes}
    cited_set = {n for n in held_set if nodes[n].get("cited")}
    near: Dict[str, Set[str]] = {}
    for other in read:
        if other is None or other in nodes or other not in topo.nodes:
            continue
        for _relation, neighbour in list(topo.out.get(other, ())) + list(topo.inc.get(other, ())):
            if neighbour in cited_set:
                near.setdefault(other, set()).add(neighbour)
    for other in sorted(near, key=lambda n: (-len(near[n]), topo.nodes[n].name.casefold(), n)):
        if len(nodes) >= MAX_NODES or len(edges) >= MAX_EDGES:
            break
        add_read(other)
        for relation, target in topo.out.get(other, ()):
            if target in held_set:
                add_edge(other, target, relation)
        for relation, source in topo.inc.get(other, ()):
            if source in held_set:
                add_edge(source, other, relation)

    if len(nodes) < 2:
        return None
    return {"nodes": list(nodes.values()), "edges": edges}


# ---------------------------------------------------------------------------------------------------------------------
# The assistant's selection (the founder, 2026-11-28: "enable that the agent could show and hide edges and nodes")
# ---------------------------------------------------------------------------------------------------------------------

#: The most nodes and edges of the selection, the longest edge label and caption.
MAX_VIEW_NODES = 15
MAX_VIEW_EDGES = 30
MAX_LABEL_CHARS = 30
MAX_CAPTION_CHARS = 160


def _plain_text(value: Any, limit: int) -> str:
    """A label or caption as plain text: no control or format characters, white space single, at most ``limit``."""
    if not isinstance(value, str):
        return ""
    kept = "".join(" " if char.isspace() else char for char in value if char.isspace()
                   or unicodedata.category(char)[0] != "C")
    return _cut(kept, limit)


def graph_specs(text: str) -> List[Any]:
    """The values of the answer's graph lines (``check.is_graph_spec``), in order."""
    from assistant import check

    return [value["graph"] for value in check.read_answer(text) if check.is_graph_spec(value)]


def curate(session: Any, graph: Optional[Dict[str, Any]], specs: Sequence[Any],
           passages: Sequence[Any]) -> Tuple[Optional[Dict[str, Any]], int]:
    """``(graph, dropped)``: ``graph`` with ``"view"``, the first of ``specs`` that holds, and how many specs were
    not used (malformed, nothing valid in them, or a second one). Nothing is invented: a node is a node or count
    passage of this answer (by the number Claude saw), a node passage not yet in the graph is added to it (as a read
    node, the name rule of ``build_graph``), a count passage only when its count node is in the graph (it was
    cited); an edge is kept only when the graph holds one between those two nodes (the topology, either direction,
    with its real relation and direction; for a count, its own edge). The view: ``focus``, ``nodes`` (ids),
    ``edges`` (``from``, ``to``, ``relation``, ``label``), ``caption``. ``graph`` may be None (the selection may
    then make one, from two nodes up). Never raises for a malformed spec."""
    if not specs:
        return graph, 0
    topo = session.topology_if_read()
    if topo is None:
        return graph, len(specs)
    base = {"nodes": [dict(n) for n in (graph or {}).get("nodes", [])],
            "edges": [dict(e) for e in (graph or {}).get("edges", [])]}
    for spec in specs:
        view = _view(session, topo, base, spec, passages)
        if view is not None:
            base["view"] = view
            return base, len(specs) - 1
    return graph, len(specs)


def _view(session: Any, topo: Any, base: Dict[str, Any], spec: Any,
          passages: Sequence[Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(spec, Mapping) or not isinstance(spec.get("nodes"), list):
        return None
    book = session.book
    by_number = {p.number: p for p in book.passages}
    shown_texts = [p.text for p in passages if isinstance(getattr(p, "text", None), str)]
    reply_number = {}
    for passage in passages:
        reply_number.setdefault((getattr(passage, "pointer", None), getattr(passage, "text", None)), passage.number)
    nodes = {n["id"]: n for n in base["nodes"]}

    def node_for(number: Any) -> Optional[str]:
        if not isinstance(number, int) or isinstance(number, bool) or number not in by_number:
            return None
        passage = by_number[number]
        if number in session.counts:
            shown = reply_number.get((passage.pointer, passage.text))
            count_id = f"{COUNT_ID_PREFIX}{shown}" if shown is not None else None
            return count_id if count_id in nodes else None
        if isinstance(passage.text, str) and passage.text.startswith(gr.COUNT_HEAD):
            return None
        node_id = gr.node_of_pointer(passage.pointer)
        if node_id is None or node_id not in topo.nodes:
            return None
        if node_id not in nodes:
            if len(nodes) >= MAX_NODES:
                return None
            name = topo.nodes[node_id].name
            entry = {"id": node_id, "name": _cut(name if any(name in t for t in shown_texts) else "", MAX_NAME_CHARS),
                     "type": _cut(topo.type_name.get(topo.nodes[node_id].type_id, ""), MAX_NAME_CHARS),
                     "cited": False}
            nodes[node_id] = entry
            base["nodes"].append(entry)
        return node_id

    chosen: List[str] = []
    for number in spec["nodes"][:MAX_VIEW_NODES * 2]:
        node_id = node_for(number)
        if node_id is not None and node_id not in chosen and len(chosen) < MAX_VIEW_NODES:
            chosen.append(node_id)
    if len(chosen) < 2:
        return None
    focus = node_for(spec.get("focus"))
    if focus not in chosen:
        focus = chosen[0]

    def relation_between(a: str, b: str) -> Optional[Tuple[str, str, str]]:
        for relation, other in topo.out.get(a, ()):
            if other == b:
                return a, b, relation
        for relation, other in topo.out.get(b, ()):
            if other == a:
                return b, a, relation
        for edge in base["edges"]:
            if {edge.get("from"), edge.get("to")} == {a, b}:
                return edge["from"], edge["to"], edge.get("relation", "")
        return None

    edges: List[Dict[str, str]] = []
    seen: Set[Tuple[str, str, str]] = set()
    raw_edges = spec.get("edges", [])
    for item in raw_edges[:MAX_VIEW_EDGES * 2] if isinstance(raw_edges, list) else []:
        if len(edges) >= MAX_VIEW_EDGES or not isinstance(item, Mapping):
            continue
        a, b = node_for(item.get("from")), node_for(item.get("to"))
        if a is None or b is None or a == b or a not in chosen or b not in chosen:
            continue
        found = relation_between(a, b)
        if found is None or found in seen:
            continue                       # an edge the graph does not hold: dropped, never drawn
        seen.add(found)
        source, target, relation = found
        edges.append({"from": source, "to": target, "relation": _cut(relation, MAX_NAME_CHARS),
                      "label": _plain_text(item.get("label"), MAX_LABEL_CHARS)})
    return {"focus": focus, "nodes": chosen, "edges": edges,
            "caption": _plain_text(spec.get("caption"), MAX_CAPTION_CHARS)}


def clean_view(data: Any, nodes: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """A stored selection read back against its graph's ``nodes`` (by id): the shape ``curate`` writes, within its
    caps, or None. Never raises."""
    if not isinstance(data, Mapping) or not isinstance(data.get("nodes"), list):
        return None
    chosen: List[str] = []
    for node_id in data["nodes"]:
        if isinstance(node_id, str) and node_id in nodes and node_id not in chosen and len(chosen) < MAX_VIEW_NODES:
            chosen.append(node_id)
    if len(chosen) < 2:
        return None
    focus = data.get("focus") if data.get("focus") in chosen else chosen[0]
    edges: List[Dict[str, str]] = []
    raw_edges = data.get("edges", [])
    for item in raw_edges if isinstance(raw_edges, list) else []:
        if len(edges) >= MAX_VIEW_EDGES or not isinstance(item, Mapping):
            continue
        source, target, relation = item.get("from"), item.get("to"), item.get("relation", "")
        if source in chosen and target in chosen and source != target and isinstance(relation, str):
            edges.append({"from": source, "to": target, "relation": _cut(relation, MAX_NAME_CHARS),
                          "label": _plain_text(item.get("label"), MAX_LABEL_CHARS)})
    return {"focus": focus, "nodes": chosen, "edges": edges,
            "caption": _plain_text(data.get("caption"), MAX_CAPTION_CHARS)}


def _text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        raise TypeError("not a text")
    return _cut(value, limit)


def clean_graph(data: Any) -> Optional[Dict[str, Any]]:
    """A stored graph read back: the shape ``build_graph`` writes, within its caps, or None (missing, malformed,
    fewer than two nodes). Never raises."""
    if not isinstance(data, Mapping):
        return None
    try:
        raw_nodes, raw_edges = data.get("nodes"), data.get("edges", [])
        if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
            return None
        nodes: Dict[str, Dict[str, Any]] = {}
        for item in raw_nodes:
            if not isinstance(item, Mapping) or len(nodes) >= MAX_NODES:
                continue
            node_id = item.get("id")
            if not isinstance(node_id, str) or not _ID.match(node_id) or node_id in nodes:
                continue
            node: Dict[str, Any] = {"id": node_id, "name": _text(item.get("name", ""), MAX_NAME_CHARS),
                                    "type": _text(item.get("type", ""), MAX_NAME_CHARS),
                                    "cited": item.get("cited") is True}
            number = item.get("passage")
            if node["cited"] and isinstance(number, int) and not isinstance(number, bool) and 0 < number < 2 ** 31:
                node["passage"] = number
            if item.get("kind") == "count" and node_id.startswith(COUNT_ID_PREFIX):
                node["kind"] = "count"
                node["query"] = _text(item.get("query", ""), MAX_QUERY_CHARS)
            nodes[node_id] = node
        edges: List[Dict[str, Any]] = []
        for item in raw_edges:
            if not isinstance(item, Mapping) or len(edges) >= MAX_EDGES:
                continue
            source, target = item.get("from"), item.get("to")
            if source in nodes and target in nodes and source != target:
                edges.append({"from": source, "to": target, "relation": _text(item.get("relation", ""),
                                                                              MAX_NAME_CHARS)})
    except TypeError:
        return None
    if len(nodes) < 2:
        return None
    cleaned: Dict[str, Any] = {"nodes": list(nodes.values()), "edges": edges}
    view = clean_view(data.get("view"), nodes)          # an older graph has none; a broken one is left out
    if view is not None:
        cleaned["view"] = view
    return cleaned


__all__: Sequence[str] = ("ABOUT_RELATION", "COUNT_ID_PREFIX", "MATCH_RELATION", "MAX_CAPTION_CHARS", "MAX_EDGES",
                          "MAX_LABEL_CHARS", "MAX_NODES", "MAX_VIEW_EDGES", "MAX_VIEW_NODES", "SMALL_COUNT",
                          "build_graph", "clean_graph", "clean_view", "count_label", "curate", "graph_specs")
