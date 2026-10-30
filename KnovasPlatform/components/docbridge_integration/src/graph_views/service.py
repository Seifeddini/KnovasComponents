"""The views' entry point for the routes: one request's context and the ego graph.

A ``ViewContext`` is what one request works with: the subject's cached index
(D1), a paced reader (D3), the meta classifier (D2) and the tenant's definitions
bundle (``bundle.py``). Process and spine views (process_views, spine_views) take
the same context.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from graph_views import bundle as bundles
from graph_views import descriptions
from graph_views.facts import STATUS_LABELS, KeyedTTL, fact_values, read_facts, status_of
from graph_views.grouping import breakdown, group, group_key, page
from graph_views.meta import MetaClassifier, TypeSchemas, type_markers
from graph_views.pacing import TokenBucket, shared_bucket
from graph_views.reader import GraphReader
from graph_views.settings import ViewSettings
from graph_views.topology import SubjectState, TopologyCache, subject_key


@dataclass
class ViewContext:
    state: SubjectState
    reader: GraphReader
    meta: MetaClassifier
    schemas: TypeSchemas
    key: Optional[str]
    settings: ViewSettings
    started: float = field(default_factory=time.perf_counter)
    bundle: Optional[bundles.Bundle] = None

    def status_label(self, type_name: str) -> Optional[str]:
        """The state attribute a type's schema declares, if any (status, outcome, ...)."""
        return self.schemas.declares(type_name, STATUS_LABELS)

    @property
    def index(self):
        return self.state.index

    def envelope(self, **body: Any) -> Dict[str, Any]:
        """Every view answer carries its as-of time, cost and guard notice."""
        return {"success": True, **body,
                "as_of": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.state.as_of)),
                "fallback": self.state.fallback,
                "timings_ms": {"total": round((time.perf_counter() - self.started) * 1000),
                               "calls": self.reader.calls},
                "topology": self.state.stats}


class ViewService:
    def __init__(self, client: Callable[[], Any], settings: Optional[ViewSettings] = None,
                 *, bucket: Optional[TokenBucket] = None,
                 now: Optional[Callable[[], float]] = None,
                 markers: Optional[Callable[[], Set[str]]] = None,
                 github: Optional[Callable[[str], Any]] = None,
                 settings_provider: Optional[Callable[[], ViewSettings]] = None):
        self._client = client
        # settings_provider: the current settings (platform_settings.Resolver.view_settings), read on
        # every use and cheap (it caches), so a change on the settings page applies without a restart.
        self._provider = settings_provider
        self._settings = settings or (settings_provider() if settings_provider else ViewSettings())
        self._bucket = bucket
        # github: repository name -> GitHubRepo with the change requests' token (or None)
        self.bundles = bundles.BundleStore(self._settings.definitions_ttl_seconds, github=github, now=now)
        self.cache = TopologyCache(self._settings, now=now)
        self.schemas = KeyedTTL(self._settings.definitions_ttl_seconds, now=now)
        self.defs = KeyedTTL(self._settings.definitions_ttl_seconds, now=now)
        self._markers = markers or _file_markers

    @property
    def settings(self) -> ViewSettings:
        if self._provider is not None:
            current = self._provider()
            if current != self._settings:
                self._apply(current)
        return self._settings

    def _apply(self, new: ViewSettings) -> None:
        """New settings: the caches keep their entries and take the new lifetimes and limits."""
        self._settings = new
        self.cache._settings = new
        self.bundles._ttl = new.definitions_ttl_seconds
        self.schemas._ttl = new.definitions_ttl_seconds
        self.defs._ttl = new.definitions_ttl_seconds

    def _acquire(self) -> None:
        bucket = self._bucket or shared_bucket(self.settings.fact_rate_per_minute,
                                               self.settings.fact_burst)
        bucket.acquire()

    def bundle(self, refresh: bool = False, client: Any = None) -> bundles.Bundle:
        """This tenant's definitions bundle, set for the rest of the request; BundleError when refused."""
        client = self._client() if client is None else client
        tenant = str(getattr(client, "customer_id", "") or "")
        found = self.bundles.get(tenant, self.settings.config_source, refresh=refresh)
        bundles.use(found)
        return found

    def context(self, refresh: bool = False) -> ViewContext:
        started = time.perf_counter()
        client = self._client()
        found = self.bundle(refresh, client)
        key = subject_key(client)
        reader = GraphReader(client, acquire=self._acquire, workers=self.settings.workers)
        state = self.cache.get(key, reader, refresh=refresh)
        if refresh and key is not None:
            self.schemas.drop(lambda k: isinstance(k, tuple) and k[0] == key)
        schemas = TypeSchemas(self.schemas, key, reader, state.index)
        decisions = self.schemas.get((key, "__meta__"), dict) if key is not None else None
        meta = MetaClassifier(state, reader, schemas=schemas, markers=self._markers(),
                              exact_max=self.settings.meta_exact_max, decisions=decisions)
        return ViewContext(state, reader, meta, schemas, key, self.settings, started=started, bundle=found)

    def definitions(self, ctx: ViewContext, refresh: bool = False):
        """Process and spine definitions for this subject and bundle, cached for the definitions TTL."""
        from graph_views import definitions as defs

        found = ctx.bundle or bundles.current()
        key = None if ctx.key is None else (ctx.key, found.content_hash)
        return self.defs.get(key, lambda: defs.load(ctx), refresh=refresh)

    def forget_subject(self, subject_id: str) -> None:
        """Logout: drop this subject's index, facts and schema decisions."""
        import json

        self.cache.drop_subject(subject_id)
        self.schemas.drop(lambda k: isinstance(k, tuple) and k[0] is not None
                          and json.loads(k[0])[1] == str(subject_id))
        self.defs.drop(lambda k: k is not None and json.loads(k[0])[1] == str(subject_id))


def _file_markers() -> Set[str]:
    """Type-level meta markers from the bundle's definitions (none yet; Phase P adds them)."""
    from graph_views import definitions as defs

    def build() -> Set[str]:
        proc, spine = defs.from_bundle()
        return type_markers(descriptions.load(), proc, spine)

    return bundles.current().memo("type_markers", build)


# -- helpers shared by the views ------------------------------------------------


def node_header(ctx: ViewContext, node_id: str) -> Dict[str, Any]:
    node = ctx.index.nodes.get(node_id) or {"id": node_id, "name": "", "type": "", "description": ""}
    return {"id": node_id, "name": node["name"] or node_id, "type": node["type"]}


def legend(type_names: Set[str]) -> List[Dict[str, str]]:
    doc = descriptions.load()
    return [descriptions.type_entry(t, doc) for t in sorted(t for t in type_names if t)]


# -- the ego graph (Step 1) ------------------------------------------------------


def ego_graph(ctx: ViewContext, node_id: str, *, show_meta: bool = False,
              threshold: Optional[int] = None) -> Dict[str, Any]:
    """The node, its neighbours one hop out, same-type fan-outs as chips."""
    index = ctx.index
    index.ensure([node_id])
    if node_id not in index.nodes:
        return {"success": False, "error": "Knoten nicht gefunden oder nicht sichtbar."}
    n = ctx.settings.fanout_threshold if threshold is None else max(1, int(threshold))
    around = index.neighbours(node_id)
    is_meta = ctx.meta.classify([m for _, _, m in around])
    hidden_meta = sum(1 for _, _, m in around if is_meta.get(m)) if not show_meta else 0
    if not show_meta:
        around = [nb for nb in around if not is_meta.get(nb[2])]
    drawn, chips = group(around, index.type_of, n)

    # A breakdown costs one fact read per member: only for a type whose schema
    # declares a state attribute, and only for chips small enough to read.
    ctx.schemas.attrs(c["type"] for c in chips)
    label_of = {c["type"]: ctx.status_label(c["type"]) for c in chips}
    small = [c for c in chips if label_of[c["type"]] and c["count"] <= ctx.settings.chip_status_max]
    facts = read_facts(ctx.state, ctx.reader, [m for c in small for m in c["members"]])
    out_chips = []
    for c in chips:
        entry = {k: v for k, v in c.items() if k != "members"}
        entry["status_attribute"] = label_of[c["type"]]
        if c in small:
            entry["breakdown"] = breakdown(status_of(facts.get(m), label_of[c["type"]])
                                           for m in c["members"])
        else:
            entry["breakdown"] = None      # no state attribute, or read a page at a time
        entry["relation_label"] = descriptions.relation_entry(c["relation"])["label"]
        out_chips.append(entry)

    nodes = [{**node_header(ctx, m), "relation": r, "direction": d,
              "meta": bool(is_meta.get(m))} for r, d, m in drawn]
    root = {**node_header(ctx, node_id), "meta": bool(ctx.meta.classify([node_id]).get(node_id))}
    types = {root["type"]} | {x["type"] for x in nodes} | {c["type"] for c in out_chips}
    return ctx.envelope(
        root=root, nodes=nodes, chips=out_chips, threshold=n, show_meta=show_meta,
        legend=legend(types),
        hidden={"meta": hidden_meta, "by_type": sorted(ctx.meta.hidden_types())},
        truncated=bool(getattr(index, "truncated", False)))


def group_members(ctx: ViewContext, node_id: str, relation: str, direction: str,
                  type_name: str, page_number: int = 0, *, show_meta: bool = False) -> Dict[str, Any]:
    """One page of a chip's members, with their state."""
    index = ctx.index
    index.ensure([node_id])
    ids = [m for r, d, m in index.neighbours(node_id, relation, direction)
           if index.type_of(m) == type_name]
    ids = list(dict.fromkeys(ids))
    if not show_meta:
        is_meta = ctx.meta.classify(ids)
        ids = [m for m in ids if not is_meta.get(m)]
    members, pages = page(ids, page_number, ctx.settings.page_size)
    label = ctx.status_label(type_name)
    facts = read_facts(ctx.state, ctx.reader, members) if label else {}
    rows = [{**node_header(ctx, m), "status": status_of(facts.get(m), label) if label else None}
            for m in members]
    return ctx.envelope(key=group_key(relation, direction, type_name), relation=relation,
                        direction=direction, type=type_name, count=len(ids), page=page_number,
                        pages=pages, members=rows)


def dossier(ctx: ViewContext, node_id: str, *, show_meta: bool = False) -> Dict[str, Any]:
    """The side panel: what the node holds, and its neighbours as lists."""
    index = ctx.index
    index.ensure([node_id])
    header = index.nodes.get(node_id)
    if header is None:
        return {"success": False, "error": "Knoten nicht gefunden oder nicht sichtbar."}
    raw = ctx.reader.dossier(node_id) or {}
    facts = [f for f in raw.get("facts") or [] if isinstance(f, dict)]
    if not facts:
        facts = read_facts(ctx.state, ctx.reader, [node_id]).get(node_id) or []
    values = fact_values(facts)
    confirmed = sorted(str(f.get("updated_at") or "") for f in facts
                       if isinstance(f.get("trust"), dict) and f["trust"].get("tier") == "confirmed")
    rows = [{"label": str(f.get("label") or ""), "value": "" if f.get("value") is None else str(f.get("value")),
             "source": f.get("provenance_pointer") or None,
             "valid_from": f.get("valid_from"), "valid_to": f.get("valid_to"),
             "tier": (f.get("trust") or {}).get("tier") if isinstance(f.get("trust"), dict) else None}
            for f in facts]

    around = index.neighbours(node_id)
    is_meta = ctx.meta.classify([m for _, _, m in around])
    groups: Dict[tuple, List[str]] = {}
    for r, d, m in around:
        if is_meta.get(m) and not show_meta:
            continue
        members = groups.setdefault((r, d, index.type_of(m)), [])
        if m not in members:
            members.append(m)
    lists = []
    for (r, d, t), members in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2])):
        shown = members[:ctx.settings.page_size]
        lists.append({"relation": r, "direction": d, "type": t,
                      "relation_label": descriptions.relation_entry(r)["label"],
                      "type_label": descriptions.type_entry(t)["label"],
                      "count": len(members), "items": [node_header(ctx, m) for m in shown],
                      "more": len(members) - len(shown)})
    t = descriptions.type_entry(header["type"])
    return ctx.envelope(
        node={"id": node_id, "name": header["name"] or node_id, "type": header["type"],
              "type_label": t["label"], "description": header["description"],
              "key": values.get("key"), "owner": values.get("owner"),
              "status": status_of(facts), "source": values.get("source"),
              "last_confirmed": confirmed[-1] if confirmed else None,
              "trust_rollup": raw.get("trust_rollup"),
              "meta": bool(ctx.meta.classify([node_id]).get(node_id))},
        facts=rows, neighbours=lists)


def search(ctx: ViewContext, q: str, *, show_meta: bool = False, limit: int = 20) -> Dict[str, Any]:
    """Nodes whose name contains ``q`` (case-insensitive), from the index."""
    needle = " ".join(str(q or "").split()).lower()
    if len(needle) < 2:
        return ctx.envelope(results=[])
    hits = [nid for nid, n in ctx.index.nodes.items() if needle in n["name"].lower()]
    hits.sort(key=lambda nid: (not ctx.index.nodes[nid]["name"].lower().startswith(needle),
                               len(ctx.index.nodes[nid]["name"])))
    if not show_meta:
        is_meta = ctx.meta.classify(hits[: limit * 3])
        hits = [h for h in hits[: limit * 3] if not is_meta.get(h)]
    return ctx.envelope(results=[node_header(ctx, h) for h in hits[:limit]])
