"""Software documentation pages ("Dokumentation", Step 8), rendered from KnowledgeBase's page definitions.

KnowledgeBase defines one page per node type (``page-definitions.json``, DEC-0028): its sections in
order, each with a render hint (text, list, table, status-list, diagram), whether it is required, and
where its content comes from (the node's field, its facts, or declared paths from the node, each with
an optional ``where`` filter and a label). The Platform only draws what those definitions say; it
never invents a path. Stale sections come from KnowledgeBase's documentation completeness report
(``docs-completeness.json``, DEC-0029); where an empty required section is fixed comes from the edit
map (DEC-0022).

Facts cost a read each under the tenant's rate limit (plan D3), so a page draws every section's items
from the topology and spends ``doc_fact_budget`` reads on columns, states and links: required sections
first, in page order, then the others. A section the budget does not cover says so and loads on
demand (``section``); a section draws ``doc_page_items`` items and counts the rest.

Every type with a page definition has pages (WP KC-3); nothing here names a type or a key prefix.
How pages form the tree comes from the definitions too:

* a page's **children** are the items of one of its sections that have a page of their own: the
  section the page names (``children``), else its first list or table whose paths are each one hop
  in over a relation of the ``Structure`` family (relations ``family``);
* the **roots** are the pages marked ``root``, else the pages with children that are nobody's
  child, else every page;
* a page with children draws them as a **level diagram**: with the edges its diagram section
  declares between them, else with the edges of its children's pages counted between their parts;
* items **link** to the repository file their fact names: the section's ``link.fact``, else a
  ``path`` fact the item's type declares and the section does not show as a column.

``children``, ``root`` and ``link`` are not in page definition format 1 yet (proposed for
KnowledgeBase's page-definition schema); the fallbacks read the same structure from what is.
"""
from __future__ import annotations

import math
import re
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from graph_views import bundle, descriptions, discovery, edit_map
from graph_views.facts import KeyedTTL, fact_values, read_facts
from graph_views.traverse import follow

#: The loader's old broken component description counts as empty: KnowledgeBase's own pattern
#: (scripts/client_zero/load_ontology.py BROKEN_DESC, used by docs_completeness.py), so the page and
#: the completeness report agree.
BROKEN_DESCRIPTION = re.compile(r"^-{3,}(\s+\*AI-extensive spec: \[spec\.ai\.yaml\]\(\./spec\.ai\.yaml\)\*)?\s*$")
#: The relation family whose one-hop "in" sections are a page's children while the page names none.
STRUCTURE_FAMILY = "structure"
#: Render hints a children section may have (an item list, not a text or a diagram).
_LIST_RENDERS = ("list", "table", "status-list")


class NotDocumented(Exception):
    """The node has no page definition, or is not visible."""


def definitions() -> List[Dict[str, Any]]:
    return list(bundle.current().pages.get("pages") or [])


def documented_types() -> List[str]:
    """Every type that has a page definition, in definition order."""
    return list(dict.fromkeys(p["type"] for p in definitions() if p.get("type")))


def _families() -> Dict[str, str]:
    def build() -> Dict[str, str]:
        rels = (bundle.current().relations or {}).get("relations") or []
        return {r["name"]: str(r.get("family") or "").strip().lower() for r in rels if isinstance(r, dict) and r.get("name")}

    return bundle.current().memo("relation_families", build)


def children_section(page: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The section whose items (those with a page) are the page's children in the tree, or None."""
    if not page:
        return None
    named = page.get("children")
    if isinstance(named, dict):
        named = named.get("section")
    if named:
        return next((x for x in page["sections"] if x["id"] == named), None)
    families, typed = _families(), set(documented_types())
    for x in page["sections"]:
        paths = x.get("paths") or []
        if x.get("render") in _LIST_RENDERS and paths and all(
                len(sp["path"]) == 1 and not sp.get("where") and sp["path"][0]["direction"] == "in"
                and families.get(sp["path"][0]["relation"]) == STRUCTURE_FAMILY and sp["path"][0]["to_type"] in typed
                for sp in paths):
            return x
    return None


def root_types() -> List[str]:
    """The types whose nodes are the tree's roots."""
    pages = definitions()
    marked = [p["type"] for p in pages if p.get("root")]
    if marked:
        return list(dict.fromkeys(marked))
    child_types = {sp["path"][-1]["to_type"] for p in pages for sp in (children_section(p) or {}).get("paths") or []}
    found = [p["type"] for p in pages if children_section(p) and p["type"] not in child_types]
    return list(dict.fromkeys(found)) or documented_types()


def _link_fact(s: Dict[str, Any], item_type: str) -> Optional[str]:
    """The fact naming the repository file an item of this section links to, if any."""
    if isinstance(s.get("link"), dict) and s["link"].get("fact"):
        return s["link"]["fact"]
    declared = bundle.current().memo("declared_attributes", lambda: {
        t["name"]: {a.get("name") for a in t.get("attributes") or [] if isinstance(a, dict)}
        for t in bundle.current().types.get("node_types") or [] if isinstance(t, dict) and t.get("name")})
    if "path" in declared.get(item_type, set()) and "path" not in (s.get("columns") or []):
        return "path"
    return None


def report() -> Dict[str, Any]:
    """KnowledgeBase's documentation completeness report: rows by component key, and its time."""
    data = bundle.current().report("docs_completeness")
    return {"generated_at": data.get("generated_at"), "page": data.get("page"),
            "rows": {r["component"]: r for r in data.get("components") or []}}


def kb_commit() -> Optional[str]:
    """The KnowledgeBase commit the bundle was built at (informative, KB-7), else its content hash."""
    b = bundle.current()
    return b.source_commit or b.content_hash


def page_for(type_name: str, key: Optional[str]) -> Optional[Dict[str, Any]]:
    """The page definition of a node: its type, and the key prefix where a page filters by one."""
    candidates = [p for p in definitions() if p.get("type") == type_name]
    filtered = [p for p in candidates if (p.get("filter") or {}).get("key_prefix")]
    for p in filtered:
        if key and key.startswith(p["filter"]["key_prefix"]):
            return p
    return next((p for p in candidates if not (p.get("filter") or {}).get("key_prefix")), None)


# -- keys -------------------------------------------------------------------------------------

def keys_of(ctx: Any, cache: KeyedTTL, ids: List[str], key_index_max: int) -> Dict[str, Optional[str]]:
    """Keys for nodes: from names that carry them ("<KEY> — <name>"), else read once per type and cached."""
    if not ids:
        return {}
    types = sorted({ctx.index.type_of(n) for n in ids})
    store = discovery.key_index(ctx, cache, types, max(key_index_max, 60))
    return {n: discovery._key_of(ctx, store, n) for n in ids}


def locate(ctx: Any, cache: KeyedTTL, key: str, key_index_max: int) -> Optional[str]:
    """The node a key names, among the types that have a page."""
    types = sorted({p["type"] for p in definitions()})
    for t in types:
        for nid in ctx.index.by_type.get(t, []):
            name = ctx.index.nodes[nid]["name"]
            if name == key or name.startswith(key + " "):
                return nid
    # Keys are read only for the types whose declared key pattern fits (the bundle's id_prefixes),
    # never for every documented type.
    from graph_views.keys import types_for_query

    fits = [t for t in types_for_query(key) if t in types and ctx.index.by_type.get(t)]
    if not fits:
        return None
    store = discovery.key_index(ctx, cache, fits, max(key_index_max, 120))
    return next((n for n, k in store["keys"].items() if k == key and ctx.index.type_of(n) in fits), None)


# -- the tree ---------------------------------------------------------------------------------

def _child_ids(ctx: Any, node: str, section: Optional[Dict[str, Any]]) -> List[str]:
    if not section:
        return []
    return list(dict.fromkeys(m for sp in section["paths"] for m in follow(ctx.index, [node], sp["path"]).nodes
                              if m != node))


def roots(ctx: Any) -> List[str]:
    ids = [n for t in root_types() for n in ctx.index.by_type.get(t, [])]
    return sorted(dict.fromkeys(ids), key=lambda n: ctx.index.nodes[n]["name"])


def children(ctx: Any, cache: KeyedTTL, node: str, key: Optional[str], key_index_max: int) -> List[Dict[str, Any]]:
    """The node's children in the tree: the items of its page's children section that have a page."""
    ids = _child_ids(ctx, node, children_section(page_for(ctx.index.type_of(node), key)))
    if not ids:
        return []
    keys = keys_of(ctx, cache, ids, key_index_max)
    rows = [header(ctx, n, keys.get(n)) for n in ids if page_for(ctx.index.type_of(n), keys.get(n))]
    return sorted(rows, key=lambda r: (r["key"] or r["name"]))


def header(ctx: Any, node: str, key: Optional[str]) -> Dict[str, Any]:
    n = ctx.index.nodes[node]
    t = n["type"]
    page = page_for(t, key)
    marker = topology_marker(ctx, node, key, page)
    return {"id": node, "key": key, "name": n["name"], "type": t, "page": page["key"] if page else None,
            "has_children": bool(_child_ids(ctx, node, children_section(page))), **marker}


def one_line(text: Optional[str], limit: int = 140) -> str:
    text = " ".join(str(text or "").split())
    if not text or BROKEN_DESCRIPTION.match(text):
        return ""
    first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    return first if len(first) <= limit else first[: limit - 1].rstrip() + "…"


def topology_marker(ctx: Any, node: str, key: Optional[str], page: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Incomplete (a required section empty) by the topology alone, and stale by the KB report."""
    if page is None:
        return {"incomplete": [], "stale": []}
    empty = []
    for s in page["sections"]:
        if not s.get("required"):
            continue
        if s.get("field"):
            if not one_line(ctx.index.nodes[node].get(s["field"])):
                empty.append(s["id"])
        elif s.get("paths") and not any(p.get("where") for p in s["paths"]):
            if not any(follow(ctx.index, [node], p["path"]).nodes for p in s["paths"]):
                empty.append(s["id"])
    row = report()["rows"].get(key or "") if page["key"] == report()["page"] else None
    return {"incomplete": empty, "stale": list((row or {}).get("stale") or [])}


def _parent(ctx: Any, node: str) -> Optional[str]:
    """The node's parent in the tree: a node whose page's children section reaches it in one hop back."""
    t = ctx.index.type_of(node)
    for page in definitions():
        section = children_section(page)
        for sp in (section or {}).get("paths") or []:
            hop = sp["path"][0]
            if hop["to_type"] != t:
                continue
            back = "out" if hop["direction"] == "in" else "in"
            found = next((m for _, _, m in ctx.index.neighbours(node, hop["relation"], back)
                          if ctx.index.type_of(m) == page["type"] and m != node), None)
            if found:
                return found
    return None


def path_to(ctx: Any, cache: KeyedTTL, node: str, key_index_max: int) -> List[str]:
    """Ancestors from a root down to the node (for opening the tree at a deep link)."""
    chain = [node]
    for _ in range(4):
        parent = _parent(ctx, chain[0])
        if parent is None or parent in chain:
            break
        chain.insert(0, parent)
    return chain


# -- sections ---------------------------------------------------------------------------------

class Budget:
    def __init__(self, n: int):
        self.left = n

    def take(self, n: int) -> bool:
        if n <= self.left:
            self.left -= n
            return True
        return False


def _members(ctx: Any, node: str, s: Dict[str, Any]) -> List[Tuple[str, Optional[str], Dict[str, Any]]]:
    """(node, path label, the path spec) for every path of a section, first path first, without repeats."""
    seen, out = set(), []
    for p in s.get("paths") or []:
        for m in follow(ctx.index, [node], p["path"]).nodes:
            if m not in seen and m != node:
                seen.add(m)
                out.append((m, p.get("label"), p))
    return out


def _edit_of(node_type: str, s: Dict[str, Any]) -> Dict[str, Any]:
    """Where a section's content is edited: the relation of its first hop, else the node type."""
    if s.get("paths"):
        return edit_map.describe(edit_map.relation_id(node_type, s["paths"][0]["path"][0]))
    if s.get("facts"):
        return edit_map.describe(f"{node_type}.{s['facts'][0]}")
    return edit_map.describe(node_type)


def _where_ok(where: Optional[Dict[str, Any]], values: Dict[str, Any]) -> bool:
    if not where:
        return True
    v = str(values.get(where["fact"], ""))
    if "equals" in where:
        return v == where["equals"]
    if "in" in where:
        return v in where["in"]
    return v not in where.get("not_in", [])


def _needs(ctx: Any, s: Dict[str, Any], shown: List[str], members: List[Tuple[str, Optional[str], Dict[str, Any]]]
           ) -> Tuple[List[str], List[str]]:
    """(nodes whose facts the section needs, status nodes to read) for what it draws."""
    reads: List[str] = []
    if s.get("order_by"):
        reads += [m for m, _, _ in members]                 # ordering needs every member's fact
    elif s.get("columns") or any(p.get("where") for p in s.get("paths") or []) \
            or any(_link_fact(s, ctx.index.type_of(m)) for m in shown):
        reads += shown
    status: List[str] = []
    if s.get("status"):
        for m in shown:
            nodes = follow(ctx.index, [m], s["status"]["path"]).nodes
            status += nodes[:1]                             # one check per item tells whether it passes
    return list(dict.fromkeys(reads)), status


def github_url(repo: Optional[str], path: Optional[str]) -> Optional[str]:
    if not repo or not path or ".." in path.split("/"):
        return None
    return f"https://github.com/{repo}/blob/master/{path.lstrip('/')}"


def render_section(ctx: Any, node: str, node_type: str, values: Dict[str, Any], s: Dict[str, Any], *,
                   budget: Budget, items: int, repo: Optional[str], as_of: str) -> Dict[str, Any]:
    edit = _edit_of(node_type, s)
    item_type = s["paths"][0]["path"][-1]["to_type"] if s.get("paths") else node_type
    out: Dict[str, Any] = {"id": s["id"], "title": (s.get("title") or {}).get("de") or s["id"],
                           "title_en": (s.get("title") or {}).get("en"), "render": s["render"],
                           "required": bool(s.get("required")), "columns": list(s.get("columns") or []),
                           "column_labels": [descriptions.attribute_label(item_type, c) for c in s.get("columns") or []],
                           "source": edit.get("source"), "key_path": edit.get("key_path"), "as_of": as_of,
                           "edit": edit, "deferred": False}
    if s.get("field"):
        text = ctx.index.nodes[node].get(s["field"]) or ""
        broken = bool(BROKEN_DESCRIPTION.match((text or "").strip()))
        out.update(text=None if broken else (text.strip() or None), count=0 if broken or not text.strip() else 1,
                   broken=broken)
    elif s.get("facts"):
        rows = [{"fact": f, "label": descriptions.attribute_label(node_type, f), "value": values.get(f)}
                for f in s["facts"]]
        out.update(facts=rows, count=sum(1 for r in rows if r["value"] not in (None, "")))
    else:
        members = _members(ctx, node, s)
        shown_ids = [m for m, _, _ in members][:items]
        need, status_nodes = _needs(ctx, s, shown_ids, members)
        facts: Dict[str, Any] = {}
        if need or status_nodes:
            # Facts are cached per subject: only what this subject has not read yet costs a read.
            with ctx.state.lock:
                uncached = [m for m in dict.fromkeys(need + status_nodes) if m not in ctx.state.facts]
            if budget.take(len(uncached)):
                facts = read_facts(ctx.state, ctx.reader, need + status_nodes)
            else:
                out["deferred"] = True
                out["deferred_reads"] = len(uncached)
        vals = {m: fact_values(facts.get(m)) for m in need + status_nodes if m in facts}
        if not out["deferred"]:
            if any(p.get("where") for p in s.get("paths") or []):
                members = [x for x in members if _where_ok(x[2].get("where"), vals.get(x[0], {}))]
            if s.get("order_by"):
                ob = s["order_by"]
                members = sorted(members, key=lambda x: str(vals.get(x[0], {}).get(ob["fact"]) or ""),
                                 reverse=ob.get("direction") == "desc")
        out["total"] = len(members)
        if s.get("limit"):
            members = members[: s["limit"]]
        rows = []
        for m, label, _ in members[:items]:
            n = ctx.index.nodes.get(m) or {"name": m, "type": "", "description": ""}
            v = vals.get(m, {})
            row = {"id": m, "name": n["name"], "type": n["type"], "label": label, "key": v.get("key"),
                   "description": "" if one_line(n.get("description"))[:40] in n["name"] else one_line(n.get("description")),
                   "cells": {c: v.get(c) for c in out["columns"]} if v else {}}
            link = _link_fact(s, n["type"]) if v else None
            if link:
                row["path"] = v.get(link) or v.get("source")
                row["url"] = github_url(repo, row["path"])
            if s.get("status") and not out["deferred"]:
                checks = follow(ctx.index, [m], s["status"]["path"]).nodes
                got = [vals.get(c, {}).get(s["status"]["fact"]) for c in checks[:1]]
                row["status"] = ("passing" if s["status"]["passing"] in got else
                                 ("failing" if checks and got and got[0] else "unknown")) if checks else "none"
                row["checks"] = len(checks)
            rows.append(row)
        out.update(items=rows, count=len(members), more=max(len(members) - len(rows), 0))
        if s["render"] == "diagram":
            out["diagram"] = call_diagram(ctx, node, s, members)
    out["empty"] = out["count"] == 0
    if out["empty"] and out["required"]:
        out["missing"] = fix_hint(edit)
    return out


def fix_hint(edit: Dict[str, Any]) -> str:
    """Where a missing section is filled, in the edit map's words."""
    if not edit.get("known"):
        return edit.get("why") or "Kein Eintrag in der Edit-Map."
    if not edit.get("editable"):
        return f"An der Quelle: {edit.get('fix_at')}"
    return f"Eintragen in {edit.get('source')} · {edit.get('key_path')}"


# -- diagrams ---------------------------------------------------------------------------------

BOX_W, BOX_H, GAP_X, GAP_Y = 230, 72, 90, 80


def layout(nodes: List[str], edges: List[Tuple[str, str]], order: Dict[str, str]) -> Dict[str, Tuple[int, int]]:
    """Deterministic layered layout: a node sits one layer below everything that calls it (cycles broken
    in key order), nodes of a layer in key order. No force layout, so the same graph draws the same way."""
    nodes = sorted(nodes, key=lambda n: order.get(n, n))
    succ = {n: sorted({b for a, b in edges if a == n and b != n and b in order}, key=lambda x: order[x]) for n in nodes}
    # Drop the edges that close a cycle, by a depth-first walk in key order.
    state: Dict[str, int] = {}
    dag: Dict[str, List[str]] = {n: [] for n in nodes}

    def visit(n: str) -> None:
        state[n] = 1
        for m in succ.get(n, []):
            if state.get(m) == 1:
                continue                                    # back edge: closes a cycle
            dag[n].append(m)
            if m not in state:
                visit(m)
        state[n] = 2

    for n in nodes:
        if n not in state:
            visit(n)
    layer = {n: 0 for n in nodes}
    for _ in range(len(nodes)):
        changed = False
        for a in nodes:
            for b in dag[a]:
                if layer[b] < layer[a] + 1:
                    layer[b] = layer[a] + 1
                    changed = True
        if not changed:
            break
    rows: Dict[int, List[str]] = {}
    cols = max(2, math.ceil(math.sqrt(len(nodes))))
    if len(nodes) >= 6 and max(layer.values(), default=0) + 1 > cols + 1:
        # Nearly a chain (each area calls the next): one box per layer would be a tall column of
        # crossing arrows. A grid in call order (left to right, top to bottom) reads the same way.
        ordered = sorted(nodes, key=lambda n: (layer[n], order.get(n, n)))
        for i, n in enumerate(ordered):
            rows.setdefault(i // cols, []).append(n)
    else:
        for n in nodes:
            rows.setdefault(layer[n], []).append(n)
    width = max((len(r) for r in rows.values()), default=0)
    pos = {}
    for y, row in sorted(rows.items()):
        offset = (width - len(row)) * (BOX_W + GAP_X) // 2
        for x, n in enumerate(row):
            pos[n] = (offset + x * (BOX_W + GAP_X), y * (BOX_H + GAP_Y))
    return pos


def _box(ctx: Any, n: str, key: Optional[str], pos: Tuple[int, int]) -> Dict[str, Any]:
    node = ctx.index.nodes[n]
    name = node["name"]
    title = name.split(" — ", 1)[1] if " — " in name else name
    if key is None and " — " in name and re.fullmatch(r"[A-Z0-9][A-Z0-9-]+", name.split(" — ", 1)[0]):
        key = name.split(" — ", 1)[0]                  # component names carry their key
    return {"id": n, "key": key, "name": title, "full_name": name, "type": node["type"],
            "description": one_line(node.get("description"), 110), "x": pos[0], "y": pos[1]}


def _canvas(boxes: List[Dict[str, Any]]) -> Dict[str, int]:
    return {"width": max((b["x"] for b in boxes), default=0) + BOX_W, "height": max((b["y"] for b in boxes), default=0) + BOX_H,
            "box_w": BOX_W, "box_h": BOX_H}


def _edge_relations(page: Optional[Dict[str, Any]]) -> List[str]:
    """Relations a page's diagram sections declare between its children: paths that take the children
    section's hop and one more hop, outgoing, to the children's type."""
    section = children_section(page)
    if not section:
        return []
    hops = [sp["path"][0] for sp in section["paths"]]
    out: List[str] = []
    for x in page["sections"]:
        if x.get("render") != "diagram":
            continue
        for sp in x.get("paths") or []:
            path = sp["path"]
            if len(path) == 2 and path[0] in hops and path[1]["direction"] == "out" \
                    and path[1]["to_type"] == path[0]["to_type"] and path[1]["relation"] not in out:
                out.append(path[1]["relation"])
    return out


def _descendants(ctx: Any, node: str, key: Optional[str], depth: int = 2) -> List[str]:
    """The node's parts along the children sections, ``depth`` levels down (keys not read: types only)."""
    found: List[str] = []
    frontier = [(node, key)]
    for _ in range(depth):
        nxt = []
        for n, k in frontier:
            pages = [page_for(ctx.index.type_of(n), k)] if k else [p for p in definitions() if p["type"] == ctx.index.type_of(n)]
            for page in pages:
                for m in _child_ids(ctx, n, children_section(page)):
                    if m not in found and m != node:
                        found.append(m)
                        nxt.append((m, None))
        frontier = nxt
    return found


def level_diagram(ctx: Any, cache: KeyedTTL, node: str, key: Optional[str], key_index_max: int) -> Optional[Dict[str, Any]]:
    """A page with children draws them as boxes and the edges between them (C4-style).

    The edges are the relations the page's diagram section declares between its children
    (``_edge_relations``). A page without such a section aggregates: an arrow between two children
    counts the edges their own pages declare, between the parts of one and the parts of the other.
    No children, or no declared edge on either level: no diagram.
    """
    t = ctx.index.type_of(node)
    page = page_for(t, key)
    section = children_section(page)
    if not section:
        return None
    kids = children(ctx, cache, node, key, key_index_max)
    ids = [k["id"] for k in kids]
    order = {k["id"]: (k["key"] or k["name"]) for k in kids}
    keys = {k["id"]: k["key"] for k in kids}
    rels = _edge_relations(page)
    aggregated = not rels
    counted: Dict[Tuple[str, str, str], int] = {}
    if not aggregated:
        for a in ids:
            for rel in rels:
                for _, _, b in ctx.index.neighbours(a, rel, "out"):
                    if b in order and b != a:
                        counted[(a, b, rel)] = counted.get((a, b, rel), 0) + 1
    else:
        rels = list(dict.fromkeys(r for k in kids for r in _edge_relations(page_for(k["type"], k["key"]))))
        if not rels:
            return None
        owner: Dict[str, str] = {}
        for k in kids:
            for part in _descendants(ctx, k["id"], k["key"]):
                owner.setdefault(part, k["id"])
        for part, child in owner.items():
            for rel in rels:
                for _, _, other in ctx.index.neighbours(part, rel, "out"):
                    target = owner.get(other)
                    if target and target != child:
                        counted[(child, target, rels[0])] = counted.get((child, target, rels[0]), 0) + 1
    pos = layout(ids, [(a, b) for a, b, _ in counted], order)
    boxes = [_box(ctx, n, keys.get(n), pos[n]) for n in ids]
    for b, k in zip(boxes, kids):
        b.update(incomplete=k["incomplete"], stale=k["stale"])
    edges = [{"from": a, "to": b, "relation": rel, "count": c,
              "label": f"{rel} ({c})" if aggregated else rel}
             for (a, b, rel), c in sorted(counted.items(), key=lambda x: (order[x[0][0]], order[x[0][1]], x[0][2]))]
    child_types = list(dict.fromkeys(sp["path"][-1]["to_type"] for sp in section["paths"]))
    label = ", ".join(descriptions.type_entry(c)["label"] for c in child_types)
    return {"kind": "level", "aggregated": aggregated, "boxes": boxes, "edges": edges, **_canvas(boxes),
            "title": str((section.get("title") or {}).get("de") or label),
            "note": (f"Ein Pfeil zählt die Verbindungen ({', '.join(rels)}) zwischen den Teilen zweier Einträge ({label})."
                     if aggregated else f"Verbindungen ({', '.join(rels)}) zwischen den Einträgen ({label}).")}


def call_diagram(ctx: Any, node: str, s: Dict[str, Any], members: List[Tuple[str, Optional[str], Dict[str, Any]]]) -> Dict[str, Any]:
    """A diagram section: callers above, the node, callees below; items of another type as details.

    The boxes are the items of the type the section's first path ends at (a component's peers, an
    area's components); the rest (interactions) are listed under the diagram.
    """
    boxed = s["paths"][0]["path"][-1]["to_type"]
    callers, callees, details = [], [], []
    for m, label, p in members:
        hop = p["path"][-1]
        if hop["to_type"] != boxed:
            details.append(m)
        elif hop["direction"] == "out":
            callees.append((m, hop["relation"]))
        else:
            callers.append((m, hop["relation"]))
    ids = [node] + [m for m, _ in callers + callees]
    edges = [(m, node, rel) for m, rel in callers] + [(node, m, rel) for m, rel in callees]
    order = {n: ctx.index.nodes[n]["name"] for n in ids}
    pos = layout(list(dict.fromkeys(ids)), [(a, b) for a, b, _ in edges], order)
    boxes = [_box(ctx, n, None, pos[n]) for n in dict.fromkeys(ids)]
    for b in boxes:
        b["self"] = b["id"] == node
    return {"kind": "calls", "boxes": boxes, **_canvas(boxes),
            "edges": [{"from": a, "to": b, "relation": rel, "label": rel} for a, b, rel in edges],
            "details": [{"id": d, "name": ctx.index.nodes[d]["name"], "description": one_line(ctx.index.nodes[d].get("description"))}
                        for d in details]}


# -- the page -----------------------------------------------------------------------------------

def _cost(ctx: Any, node: str, s: Dict[str, Any], items: int) -> int:
    """The fact reads a section would cost now (what this subject has not read yet)."""
    if not s.get("paths"):
        return 0
    members = _members(ctx, node, s)
    need, status = _needs(ctx, s, [m for m, _, _ in members][:items], members)
    with ctx.state.lock:
        return len([m for m in dict.fromkeys(need + status) if m not in ctx.state.facts])


#: Rendered pages per (subject, topology load, node): a reload within the topology's lifetime reads
#: nothing, and a page never loads more on its own; "Laden" (``only``) updates the cached page.
_PAGES: "OrderedDict[Tuple[Any, ...], Dict[str, Any]]" = OrderedDict()
_PAGES_MAX = 256


def _page_key(ctx: Any, node: str, repo: Optional[str]) -> Tuple[Any, ...]:
    return (ctx.key, ctx.state.as_of, bundle.current().content_hash, node, repo)


def render(ctx: Any, cache: KeyedTTL, node: str, *, repo: Optional[str] = None, only: Optional[str] = None,
           budget: Optional[int] = None, key: Optional[str] = None) -> Dict[str, Any]:
    """One page: header, the level diagram (a page with children) and the sections from its definition."""
    ctx.index.ensure([node])
    whole = only is None and budget is None
    pkey = _page_key(ctx, node, repo)
    if whole and pkey in _PAGES:
        _PAGES.move_to_end(pkey)
        return ctx.envelope(**_PAGES[pkey])
    body = _render(ctx, cache, node, repo=repo, only=only, budget=budget, key=key)
    if whole:
        _PAGES[pkey] = body
        while len(_PAGES) > _PAGES_MAX:
            _PAGES.popitem(last=False)
    elif only is not None and pkey in _PAGES:
        cached = _PAGES[pkey]
        fresh = {x["id"]: x for x in body["sections"]}
        cached["sections"] = [fresh.get(x["id"], x) for x in cached["sections"]]
    return ctx.envelope(**body)


def _render(ctx: Any, cache: KeyedTTL, node: str, *, repo: Optional[str], only: Optional[str],
            budget: Optional[int], key: Optional[str]) -> Dict[str, Any]:
    s = ctx.settings
    ctx.index.ensure([node])
    if node not in ctx.index.nodes:
        raise NotDocumented("Knoten nicht gefunden oder nicht sichtbar.")
    t = ctx.index.type_of(node)
    page = page_for(t, key) if key else None
    if key and budget == 0 and page and not any(x.get("facts") for x in page["sections"]):
        values = {"key": key}                   # a subtree export: the tree already knows the key
    else:
        values = fact_values(read_facts(ctx.state, ctx.reader, [node]).get(node))
        key = values.get("key")
        page = page_for(t, key)
    if page is None:
        raise NotDocumented(f"Für {t} gibt es keine Dokumentationsseite (page-definitions.json).")
    as_of = ctx.envelope()["as_of"]
    pot = Budget(s.doc_fact_budget if budget is None else budget)
    sections = [x for x in page["sections"] if only in (None, x["id"])]
    # Required sections spend the budget first: the one the completeness report judges for staleness
    # (the explanation and its links) first, then the cheapest, so that as many sections as possible
    # are complete; then the others.
    costs = [_cost(ctx, node, x, s.doc_page_items) for x in sections]
    order = sorted(range(len(sections)), key=lambda i: (not sections[i].get("required"), not sections[i].get("stale"),
                                                        costs[i], i))
    rendered: Dict[int, Dict[str, Any]] = {}
    for i in order:
        rendered[i] = render_section(ctx, node, t, values, sections[i], budget=pot, items=s.doc_page_items,
                                     repo=repo, as_of=as_of)
    out_sections = [rendered[i] for i in range(len(sections))]
    rep = report()
    row = rep["rows"].get(key or "") if page["key"] == rep["page"] else None
    for sec in out_sections:
        if row and sec["id"] in (row.get("stale") or []):
            sec["stale"] = {"source": "KB-Bericht docs-completeness", "as_of": rep["generated_at"]}
    body = {"node": {"id": node, "key": key, "name": ctx.index.nodes[node]["name"], "type": t,
                     "type_label": descriptions.type_entry(t)["label"],
                     "description": one_line(ctx.index.nodes[node].get("description"), 400)},
            "page": {"key": page["key"], "name": (page.get("name") or {}).get("de") or page["key"]},
            "sections": out_sections, "fact_budget_left": pot.left,
            "report": {"generated_at": rep["generated_at"], "row": row},
            "kb_commit": kb_commit(), "path": path_to(ctx, cache, node, s.key_index_max)}
    if only is None:
        body["diagram"] = level_diagram(ctx, cache, node, key, s.key_index_max)
    return body


# -- export -------------------------------------------------------------------------------------

def _md_escape(text: Any) -> str:
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ")


def _mermaid(d: Dict[str, Any]) -> List[str]:
    ids = {b["id"]: f"n{i}" for i, b in enumerate(d["boxes"])}
    lines = ["```mermaid", "flowchart TB"]
    for b in d["boxes"]:
        label = (f"{b['key']} — " if b.get("key") else "") + b["name"]
        lines.append(f'  {ids[b["id"]]}["{label.replace(chr(34), chr(39))}"]')
    for e in d["edges"]:
        lines.append(f'  {ids[e["from"]]} -->|{e["label"]}| {ids[e["to"]]}')
    lines.append("```")
    return lines


def to_markdown(page: Dict[str, Any], *, level: int = 1) -> str:
    h = "#" * level
    n = page["node"]
    lines = [f"{h} {n['name']}" + (f" ({n['key']})" if n["key"] and n["key"] not in n["name"] else ""), "",
             f"_{page['page']['name']} · Stand {page['as_of']}, KnowledgeBase {(page.get('kb_commit') or '?')[:12]}_", ""]
    if page.get("diagram"):
        lines += [f"{h}# Übersicht", ""] + _mermaid(page["diagram"]) + ["", f"_{page['diagram']['note']}_", ""]
    for s in page["sections"]:
        flags = []
        if s.get("stale"):
            flags.append(f"⚠ veraltet (KB-Bericht, Stand {s['stale']['as_of']})")
        lines += [f"{h}# {s['title']}" + (f" — {' · '.join(flags)}" if flags else ""), ""]
        if s.get("missing"):
            lines += [f"_fehlt — {s['missing']}_", ""]
            continue
        if s["render"] == "text":
            if s.get("text"):
                lines += [s["text"], ""]
            for f in s.get("facts") or []:
                lines.append(f"- **{f['fact']}**: {f['value'] if f['value'] not in (None, '') else '—'}")
            if s.get("facts"):
                lines.append("")
            if not s.get("text") and not s.get("facts"):
                lines += ["—", ""]
        elif s["render"] == "diagram" and s.get("diagram") and s["diagram"]["boxes"][1:]:
            lines += _mermaid(s["diagram"]) + [""]
            for d in s["diagram"].get("details") or []:
                lines.append(f"- {d['name']}: {d['description']}")
            lines.append("")
        elif s.get("items") is not None:
            cols = s.get("columns") or []
            if s["render"] == "table" and cols and not s.get("deferred"):
                lines.append("| Name | " + " | ".join(cols) + " |")
                lines.append("|---|" + "---|" * len(cols))
                for r in s["items"]:
                    lines.append(f"| {_md_escape(r['name'])} | " + " | ".join(_md_escape(r['cells'].get(c)) for c in cols) + " |")
            else:
                for r in s["items"]:
                    mark = {"passing": "✓ ", "failing": "✗ ", "unknown": "? ", "none": "· "}.get(r.get("status"), "")
                    name = f"[{r['name']}]({r['url']})" if r.get("url") else r["name"]
                    extra = f" — {r['description']}" if r.get("description") and s["render"] == "list" else ""
                    lines.append(f"- {mark}{name}{extra}")
            if s.get("more"):
                lines.append(f"- … und {s['more']} weitere")
            if s.get("deferred"):
                lines.append(f"_Spalten nicht gelesen (Lesebudget); {s['count']} Einträge._")
            lines.append("")
    return "\n".join(lines)


def export(ctx: Any, cache: KeyedTTL, node: str, *, scope: str = "page", repo: Optional[str] = None,
           max_pages: int = 120) -> Tuple[str, str]:
    """(file name, Markdown) of a page or its subtree. A subtree's pages draw their items from the
    topology only (no fact budget), so a whole area exports without draining the rate limit."""
    first = render(ctx, cache, node, repo=repo)
    key = first["node"]["key"] or node
    if scope != "subtree":
        return f"{key}.md", to_markdown(first)
    parts, queue, seen = [to_markdown(first)], [(node, first["node"]["key"], 2)], {node}
    while queue and len(seen) < max_pages:
        cur, cur_key, level = queue.pop(0)
        for child in children(ctx, cache, cur, cur_key, ctx.settings.key_index_max):
            if child["id"] in seen or child["page"] is None:
                continue
            seen.add(child["id"])
            page = render(ctx, cache, child["id"], repo=repo, budget=0, key=child["key"])
            parts.append(to_markdown(page, level=min(level, 4)))
            queue.append((child["id"], child["key"], level + 1))
    head = (f"_Export von {first['node']['name']} mit {len(seen)} Seiten. Spalten aus Fakten stehen nur auf der "
            f"Einzelseite; hier zählt jede Liste ihre Einträge._\n\n")
    return f"{key}-teilbaum.md", head + "\n\n---\n\n".join(parts)
