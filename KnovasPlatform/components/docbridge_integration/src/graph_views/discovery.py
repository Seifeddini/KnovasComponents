"""Finding a starting point on /views (Step 6b): start tiles, search, suggestions, lists.

Everything here reads the per-subject index (names, types, descriptions from
the node list route; adjacency from the edge list) and as few facts as it can:

* keys: most node names start with their key (``L2-…``, ``DEC-…``, ``GI-…``,
  ``RISK-…``). Per type, the keys of up to three nodes are read once; when
  they appear in the names, the names are searched. Otherwise the keys of a
  small type (≤ ``key_index_max`` nodes) are read once and cached for the
  definitions TTL; a larger type without keys in its names is searched by
  name only, and the answer says so (bulk reads, A7, would close this).
* meta: types the classifier hides as a whole, and nodes it decides are meta,
  stay out unless "Meta-Ebene zeigen" is on.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional, Set, Tuple

from graph_views import descriptions
from graph_views.facts import KeyedTTL, fact_values, read_facts, status_of
from graph_views.process_views import current_step

SPINE_ORDER = ("SPINE-product", "SPINE-organisation", "SPINE-process", "SPINE-market")


def spine_types(defs: Any) -> List[Tuple[str, str, List[str]]]:
    """(spine key, spine name, types in level order), branches after their spine."""
    out = []
    for key in SPINE_ORDER + tuple(s["key"] for s in defs.spines if s["key"] not in SPINE_ORDER):
        spine = defs.spine(key)
        if not spine:
            continue
        types: List[str] = []
        for chain in [spine["levels"]] + [b["levels"] for b in spine.get("branches") or []]:
            for level in chain:
                types += [t for t in level["types"] if t not in types]
        out.append((key, spine["name"], types))
    return out


def _visible_ids(ctx: Any, ids: List[str], show_meta: bool) -> List[str]:
    if show_meta:
        return ids
    is_meta = ctx.meta.classify(ids)
    return [i for i in ids if not is_meta.get(i)]


def _type_is_meta(ctx: Any, type_name: str) -> bool:
    """A type is meta when hidden as a whole, or when every node of a small
    meta-capable type is meta (Event is mixed, so it is not)."""
    ids = ctx.index.by_type.get(type_name, [])
    ctx.meta.classify(ids[:1])
    if type_name in ctx.meta.hidden_types():
        return True
    if type_name in ctx.meta.exact:
        decided = ctx.meta.classify(ids)
        return all(decided.get(i) for i in ids)
    return False


def start(ctx: Any, defs: Any, *, show_meta: bool = False) -> Dict[str, Any]:
    """Tiles per type with counts, grouped by spine, then the remaining types."""
    index, doc = ctx.index, descriptions.load()
    placed: Set[str] = set()
    groups = []

    def tile(t: str) -> Optional[Dict[str, Any]]:
        ids = index.by_type.get(t, [])
        if not ids:
            return None
        meta_type = _type_is_meta(ctx, t)
        if meta_type and not show_meta:
            return None
        count = len(_visible_ids(ctx, ids, show_meta)) if t in ctx.meta.exact else len(ids)
        if not count:
            return None
        return {**descriptions.type_entry(t, doc), "count": count, "meta": meta_type}

    for key, name, types in spine_types(defs):
        tiles = [x for x in (tile(t) for t in types if t not in placed) if x]
        placed |= set(types)
        if tiles:
            groups.append({"spine": key, "name": name, "tiles": tiles})
    rest = [x for x in (tile(t) for t in sorted(index.by_type) if t and t not in placed) if x]
    if rest:
        groups.append({"spine": None, "name": "Weitere Typen", "tiles": rest})
    return ctx.envelope(groups=groups, show_meta=show_meta)


# -- keys -------------------------------------------------------------------------------

def key_index(ctx: Any, cache: KeyedTTL, types: List[str], max_per_type: int) -> Dict[str, Any]:
    """{"keys": id -> key, "by_name": types whose names carry keys, "unkeyed": types searched by name only}."""
    store = cache.get((ctx.key, "__keys__"), lambda: {"keys": {}, "by_name": set(), "unkeyed": set(), "seen": set()}) \
        if ctx.key is not None else {"keys": {}, "by_name": set(), "unkeyed": set(), "seen": set()}
    index = ctx.index
    todo = [t for t in types if t not in store["seen"] and index.by_type.get(t)]
    if todo:
        samples = {t: index.by_type[t][:3] for t in todo}
        facts = read_facts(ctx.state, ctx.reader, [n for ids in samples.values() for n in ids])
        need = []
        for t, ids in samples.items():
            keys = {n: fact_values(facts.get(n)).get("key") for n in ids}
            store["keys"].update({n: k for n, k in keys.items() if k})
            if keys and all(k and index.nodes[n]["name"].startswith(k) for n, k in keys.items()):
                store["by_name"].add(t)
            elif len(index.by_type[t]) <= max_per_type:
                need.append(t)
            else:
                store["unkeyed"].add(t)
            store["seen"].add(t)
        if need:
            ids = [n for t in need for n in index.by_type[t] if n not in store["keys"]]
            got = read_facts(ctx.state, ctx.reader, ids)
            store["keys"].update({n: k for n in ids if (k := fact_values(got.get(n)).get("key"))})
    return store


def _key_of(ctx: Any, keys: Dict[str, Any], nid: str) -> Optional[str]:
    if nid in keys["keys"]:
        return keys["keys"][nid]
    t = ctx.index.type_of(nid)
    if t in keys["by_name"]:
        import re

        from graph_views.keys import key_type

        # The name's first token is a key only if it has the type's declared form.
        candidate = re.split(r" — | \(| · |: ", ctx.index.nodes[nid]["name"], maxsplit=1)[0].strip()
        return candidate if candidate and key_type(candidate, t) else None
    return None


def header(ctx: Any, keys: Dict[str, Any], nid: str) -> Dict[str, Any]:
    n = ctx.index.nodes.get(nid) or {"name": nid, "type": "", "description": ""}
    return {"id": nid, "name": n["name"] or nid, "type": n["type"], "key": _key_of(ctx, keys, nid),
            "type_label": descriptions.type_entry(n["type"])["label"]}


def key_types(query: str) -> List[str]:
    """Types whose declared keys (terms.yaml id_prefixes) can start with what was typed."""
    from graph_views.keys import types_for_query

    return types_for_query(query)


def search(ctx: Any, cache: KeyedTTL, q: str, *, show_meta: bool = False, limit: int = 20,
           key_index_max: int = 60) -> Dict[str, Any]:
    """One typeahead over names, keys and short descriptions of the visible nodes.

    Keys are read only for the types whose declared key pattern can match what
    was typed (FEAT- reads FeaturePlan keys, a plain word reads none).
    """
    needle = " ".join(str(q or "").split()).lower()
    if len(needle) < 2:
        return ctx.envelope(results=[], unkeyed=[])
    index = ctx.index
    # Meta is decided for the candidate hits only, never for every type up front:
    # a first search must not pay the schema of all ~60 types.
    types = [t for t in index.by_type if t]
    keys = key_index(ctx, cache, [t for t in key_types(needle) if t in index.by_type], key_index_max)
    scored = []
    for t in types:
        for nid in index.by_type[t]:
            n = index.nodes[nid]
            name, key = n["name"].lower(), (keys["keys"].get(nid) or "").lower()
            if key and key == needle:
                rank = 0
            elif key and key.startswith(needle):
                rank = 1
            elif name.startswith(needle):
                rank = 2
            elif needle in name or (key and needle in key):
                rank = 3
            elif needle in (n["description"] or "")[:300].lower():
                rank = 4
            else:
                continue
            scored.append((rank, len(n["name"]), nid))
    scored.sort()
    hits = _visible_ids(ctx, [nid for _, _, nid in scored[: limit * 6]], show_meta)[:limit]
    # The hits' own types get their keys too, so every hit can show one.
    keys = key_index(ctx, cache, sorted({index.type_of(h) for h in hits}), key_index_max)
    return ctx.envelope(results=[header(ctx, keys, h) for h in hits],
                        unkeyed=sorted(t for t in keys["unkeyed"] if t in key_types(needle)))


# -- suggestions --------------------------------------------------------------------------

def _owner_matches(owner: str, user: Any) -> bool:
    """A plan's owner fact names a person as written in the plan file; match the
    signed-in user by display name, first name or e-mail local part."""
    o = " ".join((owner or "").lower().split())
    if not o or user is None:
        return False
    display = " ".join(str(getattr(user, "display_name", "") or "").lower().split())
    email = str(getattr(user, "email", "") or "").lower()
    candidates = {display, display.split(" ")[0] if display else "", email.split("@")[0]}
    return o in {c for c in candidates if c}


def suggestions(ctx: Any, defs: Any, cache: KeyedTTL, *, user: Any, recent: List[str],
                today: Optional[dt.date] = None, limit: int = 8) -> Dict[str, Any]:
    today = today or dt.date.today()
    index = ctx.index
    keys = key_index(ctx, cache, ["FeaturePlan", "Goal"], 60)
    out = []

    plans = []
    for proc in defs.processes:
        if "FeaturePlan" not in proc["instance_types"]:
            continue
        pnode = next((n for n, k in defs.process_nodes.items() if k == proc["key"]), None)
        ids = [m for _, _, m in index.neighbours(pnode, "instance_of", "in")
               if index.type_of(m) == "FeaturePlan"] if pnode else []
        facts = read_facts(ctx.state, ctx.reader, ids)
        for nid in ids:
            step = current_step(ctx, defs, proc["key"], nid)
            if step in proc["final"]:
                continue
            if _owner_matches(fact_values(facts.get(nid)).get("owner", ""), user):
                plans.append({**header(ctx, keys, nid), "step": step, "process": proc["key"]})
    out.append({"id": "mine", "label": "Meine offenen Pläne", "items": plans[:limit],
                "note": None if user is not None else "Ohne Anmeldung kein „ich“."})

    seen = [r for r in recent if r in index.nodes][:limit]
    keys = key_index(ctx, cache, sorted({index.type_of(r) for r in seen}), 60)
    out.append({"id": "recent", "label": "Zuletzt geöffnet", "items": [header(ctx, keys, r) for r in seen]})

    quarter = f"{today.year}-Q{(today.month - 1) // 3 + 1}"
    goals = [g for g in index.by_type.get("Goal", []) if quarter in (keys["keys"].get(g) or "")]
    out.append({"id": "goals", "label": f"Ziele dieses Quartals ({quarter})", "items": [header(ctx, keys, g) for g in goals[:limit]]})

    degree = sorted(((len(index.out.get(n, ())) + len(index.inc.get(n, ())), n) for n in index.nodes), reverse=True)
    top = _visible_ids(ctx, [n for _, n in degree[: limit * 4]], False)[:limit]
    keys = key_index(ctx, cache, sorted({index.type_of(n) for n in top}), 60)
    out.append({"id": "hubs", "label": "Am stärksten vernetzt",
                "items": [{**header(ctx, keys, n), "degree": len(index.out.get(n, ())) + len(index.inc.get(n, ()))} for n in top]})
    return ctx.envelope(groups=out)


# -- list -----------------------------------------------------------------------------------

def process_of(ctx: Any, defs: Any, nid: str) -> Optional[str]:
    for _, _, m in ctx.index.neighbours(nid, "instance_of", "out"):
        if m in defs.process_nodes:
            return defs.process_nodes[m]
    return None


def tree_path(ctx: Any, defs: Any, nid: str) -> Optional[Dict[str, Any]]:
    """The spine a node sits on and the ids from the root down to it (for "Baum")."""
    from graph_views.spine_views import _keyed, _link  # the spine's own link rules

    index = ctx.index
    t = index.type_of(nid)
    for spine in [defs.spine(k) for k in SPINE_ORDER] + [s for s in defs.spines if s["key"] not in SPINE_ORDER]:
        if not spine:
            continue
        for branch, levels in [("", spine["levels"])] + [(b["key"], b["levels"]) for b in spine.get("branches") or []]:
            for i in range(len(levels) - 1, -1, -1):
                if t not in levels[i]["types"] or nid not in _keyed(ctx, [nid], levels[i], read=True)[0]:
                    continue
                path, cur = [nid], nid
                for j in range(i, 0, -1):
                    link = _link(levels[j], index.type_of(cur))
                    if not link:
                        break
                    parents = [m for _, _, m in index.neighbours(cur, link["relation"], link["direction"])
                               if index.type_of(m) in levels[j - 1]["types"]]
                    if not parents:
                        break
                    cur = parents[0]
                    path.insert(0, cur)
                else:
                    return {"spine": spine["key"], "branch": branch, "expand": path[:-1], "node": nid}
    return None


def list_view(ctx: Any, defs: Any, cache: KeyedTTL, type_name: str, *, q: str = "", sort: str = "name",
              desc: bool = False, page: int = 0, owner: str = "", status: str = "", step: str = "",
              show_meta: bool = False, full_max: int = 60) -> Dict[str, Any]:
    """One table of a type: key, name, status, owner, step, last confirmed; paged.

    Facts (status, owner, last confirmed) are read for the whole type when it
    is small (≤ full_max), so sort and filters cover every row; for a larger
    type only for the page shown, and the answer says the fact columns sort
    and filter within the page.
    """
    index = ctx.index
    ids = _visible_ids(ctx, list(index.by_type.get(type_name, [])), show_meta)
    keys = key_index(ctx, cache, [type_name], full_max)
    needle = " ".join(str(q or "").split()).lower()
    if needle:
        ids = [n for n in ids if needle in index.nodes[n]["name"].lower()
               or needle in (keys["keys"].get(n) or "").lower()]
    full = len(index.by_type.get(type_name, [])) <= full_max
    rows: List[Dict[str, Any]] = []
    size = ctx.settings.page_size
    facts = read_facts(ctx.state, ctx.reader, ids) if full else {}
    label = ctx.status_label(type_name)

    def row(n: str) -> Dict[str, Any]:
        f = facts.get(n)
        v = fact_values(f)
        confirmed = sorted(str(x.get("updated_at") or "") for x in f or []
                           if str(x.get("curation_status") or "") == "confirmed")
        proc = process_of(ctx, defs, n)
        return {**header(ctx, keys, n), "status": status_of(f, label) if label else None,
                "owner": v.get("owner"), "process": proc,
                "step": current_step(ctx, defs, proc, n) if proc else None,
                "last_confirmed": confirmed[-1][:10] if confirmed else None,
                "degree": len(index.out.get(n, ())) + len(index.inc.get(n, ()))}

    if full:
        rows = [row(n) for n in ids]
        rows = [r for r in rows if (not owner or r["owner"] == owner) and (not status or r["status"] == status)
                and (not step or r["step"] == step)]
        rows.sort(key=lambda r: (r.get(sort) is None, str(r.get(sort) or "").lower()), reverse=desc)
        total = len(rows)
        start_at = max(page, 0) * size
        shown = rows[start_at:start_at + size]
    else:
        order = sorted(ids, key=lambda n: (index.nodes[n]["name"].lower() if sort != "degree"
                                           else -(len(index.out.get(n, ())) + len(index.inc.get(n, ())))),
                       reverse=desc and sort != "degree")
        total = len(order)
        start_at = max(page, 0) * size
        page_ids = order[start_at:start_at + size]
        facts.update(read_facts(ctx.state, ctx.reader, page_ids))
        shown = [row(n) for n in page_ids]
        if owner or status or step:
            shown = [r for r in shown if (not owner or r["owner"] == owner) and (not status or r["status"] == status)
                     and (not step or r["step"] == step)]
    values = lambda k: sorted({r[k] for r in (rows if full else shown) if r.get(k)})  # noqa: E731
    return ctx.envelope(type=descriptions.type_entry(type_name), total=total, page=page,
                        pages=max((total + size - 1) // size, 1), rows=shown, complete_facts=full,
                        filters={"owners": values("owner"), "statuses": values("status"), "steps": values("step")},
                        has_process=any(r["process"] for r in shown),
                        in_spine=any(type_name in types for _, _, types in spine_types(defs)))
