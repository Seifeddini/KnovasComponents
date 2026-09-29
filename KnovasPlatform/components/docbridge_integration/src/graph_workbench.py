"""One payload per screen for the directory, card and console pages.

The Secure API allows about one request a second, so a screen that fans out a
request per widget becomes unusable at exactly the moment someone opens it in
front of a client. This module assembles each screen from as few calls as the
API allows:

* names, types and edges come from the topology export the Cortex already
  caches per person (``GraphOntologySource.topology``);
* the values of a whole type come from one paged ``GET /facts?node_type_id=``;
  an API without that route falls back to one entry at a time, and the screen
  says it is loading rather than showing empty columns as unfilled;
* schema reads are cached like the export.

No Flask. Takes a client, the cached source, the platform stores; returns dicts.
Presentation rules live in ``graph_directory``; this module only fetches.

Design: the "Wissenstypen & Verzeichnisse" artifact; data flow as in
docs/superpowers/specs/2026-09-02-typed-node-workbench-design.md §9.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import graph_directory as gd
from identity.directories import slugify

logger = logging.getLogger(__name__)

#: Entries read one by one when the API has no bulk facts route. Enough for a
#: sample the console can label as one; the directory loads the rest in batches.
SAMPLE_CAP = 12
BATCH_CAP = 6
HISTORY_CAP = 25


def with_backoff(call: Callable[..., Any], *args: Any, attempts: int = 3,
                 pause: float = 1.2, **kwargs: Any) -> Any:
    """Call the API, waiting out a 429 a few times before giving up.

    The gateway allows a burst of about four graph calls and then one a second.
    A screen that writes a node and its five fields, or renumbers a field list,
    runs into that limit mid-way; failing there would leave half a write. The
    client itself retries only connection errors, deliberately: search should
    not sit in a sleep. These loops are short and a person is waiting for them.
    """
    from knovas_client import GraphError

    for attempt in range(attempts):
        try:
            return call(*args, **kwargs)
        except GraphError as exc:
            if exc.status != 429 or attempt == attempts - 1:
                raise
            time.sleep(pause * (attempt + 1))
    return None


class NotFound(LookupError):
    """The id is unknown, or not visible to this person. The API answers both
    the same way, and so does every screen: never "exists but not yours"."""


class Workbench:
    def __init__(self, client: Any, source: Any, store: Any, *,
                 people: Optional[Callable[[str], Optional[dict]]] = None) -> None:
        self.client = client
        self.source = source
        self.store = store
        self._people = people or (lambda _uid: None)

    # ── cached reads ──────────────────────────────────────────────────────

    def _cached(self, name: str, loader: Callable[[], Any]) -> Any:
        if self.source is not None and hasattr(self.source, "cached"):
            return self.source.cached(name, lambda: with_backoff(loader))
        return with_backoff(loader)

    def invalidate(self) -> None:
        if self.source is not None and hasattr(self.source, "invalidate"):
            self.source.invalidate()

    def topology(self) -> Dict[str, List[dict]]:
        if self.source is not None and hasattr(self.source, "topology"):
            return self.source.topology()
        return {"node_types": self.client.graph_node_types(),
                "nodes": self.client.graph_nodes(), "edges": []}

    def node_types(self) -> List[dict]:
        raw = self.topology().get("node_types") or []
        tones = gd.type_tones(raw)
        out = [{"id": gd.node_id(t), "name": gd.node_name(t) or gd.node_id(t),
                "description": str(t.get("description") or ""),
                "tone": tones.get(gd.node_id(t), "t4")} for t in raw if gd.node_id(t)]
        return sorted(out, key=lambda t: t["name"].lower())

    def node_type(self, type_id: str) -> Optional[dict]:
        return next((t for t in self.node_types() if t["id"] == str(type_id)), None)

    def nodes(self) -> List[dict]:
        return [n for n in self.topology().get("nodes") or [] if gd.node_id(n)]

    def names(self) -> Dict[str, str]:
        return {gd.node_id(n): gd.node_name(n) or gd.node_id(n) for n in self.nodes()}

    def nodes_of_type(self, type_id: str) -> List[dict]:
        return sorted((n for n in self.nodes() if gd.node_type_id(n) == str(type_id)),
                      key=lambda n: gd.node_name(n).lower())

    def schema(self, type_id: str, *, include_deprecated: bool = False) -> Optional[List[dict]]:
        if not type_id:
            return None
        return self._cached(
            f"schema:{type_id}:{int(include_deprecated)}",
            lambda: self.client.graph_schema(type_id, include_deprecated=include_deprecated))

    def live_schema(self, type_id: str) -> List[dict]:
        return gd.live_attributes(self.schema(type_id) or [])

    # ── values of many entries ────────────────────────────────────────────

    def _bulk_facts(self, type_id: str) -> Optional[dict]:
        reader = getattr(self.client, "graph_type_facts", None)
        if not callable(reader):
            return None
        try:
            return self._cached(f"type-facts:{type_id}", lambda: reader(type_id))
        except Exception as exc:  # noqa: BLE001 - an older API is a fallback, not an error
            logger.warning("Sammelabfrage der Fakten fuer Typ %s nicht moeglich (%s); "
                           "lese einzelne Eintraege.", type_id, exc)
            return None

    def facts_of(self, node_ids: Sequence[str]) -> Dict[str, List[dict]]:
        """Facts of a few entries, one call each. The fallback path."""
        out: Dict[str, List[dict]] = {}
        for nid in node_ids:
            facts = self._cached(f"facts:{nid}", lambda nid=nid: self.client.graph_facts(nid))
            if facts is not None:
                out[str(nid)] = list(facts)
        return out

    def type_facts(self, type_id: str, nodes: Sequence[dict], *,
                   cap: int = SAMPLE_CAP) -> Tuple[Dict[str, List[dict]], bool]:
        """({node_id: facts}, complete) for the entries of a type.

        ``complete`` is False when only a sample could be read; callers must
        not draw conclusions a sample does not support (retire a field, count
        gaps) and must say which it is.
        """
        ids = [gd.node_id(n) for n in nodes]
        bulk = self._bulk_facts(type_id)
        if bulk is not None:
            by_node: Dict[str, List[dict]] = {nid: [] for nid in ids}
            for fact in bulk.get("facts") or []:
                nid = str(fact.get("node_id") or "")
                if nid in by_node:
                    by_node[nid].append(fact)
            return by_node, bool(bulk.get("complete", True))
        embedded = {gd.node_id(n): n["facts"] for n in nodes if isinstance(n.get("facts"), list)}
        if len(embedded) == len(ids):
            return embedded, True
        sample = self.facts_of(ids[:cap])
        return sample, len(sample) == len(ids)

    # ── directories ───────────────────────────────────────────────────────

    def directory(self, view: dict) -> Dict[str, Any]:
        type_id = view["node_type_id"]
        node_type = self.node_type(type_id)
        if node_type is None:
            raise NotFound(type_id)
        live = self.live_schema(type_id)
        columns = gd.columns_for(view, live)
        nodes = self.nodes_of_type(type_id)
        names = self.names()
        counts = gd.connection_counts(self.topology().get("edges") or [])
        ids = [gd.node_id(n) for n in nodes]
        bulk = self._bulk_facts(type_id)
        facts_by_node: Dict[str, List[dict]] = {}
        if bulk is not None:
            facts_by_node = {nid: [] for nid in ids}
            for fact in bulk.get("facts") or []:
                nid = str(fact.get("node_id") or "")
                if nid in facts_by_node:
                    facts_by_node[nid].append(fact)
        rows = [gd.directory_row(n, columns, live, facts_by_node.get(gd.node_id(n)) if bulk
                                 is not None else None, names, counts.get(gd.node_id(n), 0))
                for n in nodes]
        return {
            "view": _public_view(view),
            "type": node_type,
            "columns": [gd.public_attribute(a) for a in columns],
            "attributes": [gd.public_attribute(a) for a in live],
            "rows": rows,
            "pending": [r["id"] for r in rows if not r["loaded"]],
            "complete": bool(bulk is not None and bulk.get("complete", True)),
        }

    def directory_rows(self, view: dict, node_ids: Sequence[str]) -> List[dict]:
        """A batch of rows whose values the directory could not read in bulk."""
        type_id = view["node_type_id"]
        live = self.live_schema(type_id)
        columns = gd.columns_for(view, live)
        wanted = [str(i) for i in node_ids][:BATCH_CAP]
        nodes = {gd.node_id(n): n for n in self.nodes_of_type(type_id)}
        facts = self.facts_of([i for i in wanted if i in nodes])
        names = self.names()
        counts = gd.connection_counts(self.topology().get("edges") or [])
        return [gd.directory_row(nodes[i], columns, live, facts.get(i, []), names, counts.get(i, 0))
                for i in wanted if i in nodes]

    # ── one entry ─────────────────────────────────────────────────────────

    def _detail(self, node_id: str) -> Tuple[dict, dict, List[dict]]:
        detail = self.client.graph_node(node_id)
        if not detail:
            raise NotFound(node_id)
        node = detail.get("node", detail) if isinstance(detail, dict) else {}
        facts = detail.get("facts") if isinstance(detail, dict) else None
        if facts is None:
            facts = self.client.graph_facts(node_id) or []
        return detail, node, list(facts)

    def network(self, node: dict, depth: int = 1) -> Dict[str, Any]:
        topology = self.topology()
        names = {gd.node_id(n): gd.node_name(n) for n in topology.get("nodes") or []}
        types = {gd.node_id(n): gd.node_type_id(n) for n in topology.get("nodes") or []}
        try:
            response = self.client.graph_neighbors(gd.node_id(node), depth=depth,
                                                   include_edges=True)
        except Exception as exc:  # noqa: BLE001 - the card still renders without its network
            logger.warning("Nachbarschaft von %s nicht lesbar: %s", gd.node_id(node), exc)
            response = None
        net = gd.neighbourhood(node, response, names=names, types=types,
                               fallback_edges=topology.get("edges") or [])
        net["available"] = response is not None
        net["depth"] = depth
        return net

    def node_page(self, node_id: str) -> Dict[str, Any]:
        detail, node, facts = self._detail(node_id)
        type_id = gd.node_type_id(node)
        node_type = self.node_type(type_id) if type_id else None
        all_attributes = (self.schema(type_id, include_deprecated=True) or []) if type_id else []
        live = gd.live_attributes(all_attributes)
        names = self.names()
        by_attribute = gd.facts_by_attribute(facts)
        fields = {gd.attribute_id(a): gd.cell(a, by_attribute.get(gd.attribute_id(a)), names)
                  for a in live}

        # Content the schema does not name any more is still content: facts of
        # a retired field stay readable, and free facts keep their label.
        retired = {gd.attribute_id(a): a for a in gd.retired_attributes(all_attributes)}
        extra, seen = [], set(fields)
        for fact in sorted(facts, key=gd._fact_time, reverse=True):
            key = str(fact.get("attribute_id") or "")
            if key and key in seen:
                continue
            if key:
                seen.add(key)
                attribute = retired.get(key) or {"name": fact.get("attribute_name") or "Feld",
                                                  "datatype": fact.get("datatype") or "text"}
                shown = gd.cell(attribute, fact, names)
                if not shown["missing"]:
                    extra.append({"name": str(attribute.get("name")), "display": shown["display"],
                                  "kind": "retired" if key in retired else "unknown"})
            elif fact.get("value") not in (None, ""):
                extra.append({"name": str(fact.get("label") or "Ohne Bezeichnung"),
                              "display": gd.display("text", fact.get("value")),
                              "kind": "free"})

        view = self.store.view_for_type(type_id) if type_id else None
        network = self.network(node, 1)
        return {
            "node": {"id": gd.node_id(node), "name": gd.node_name(node) or gd.node_id(node),
                     "node_type_id": type_id,
                     "created_at": str(node.get("created_at") or ""),
                     "updated_at": str(node.get("updated_at") or ""),
                     "created": gd.format_timestamp(node.get("created_at")),
                     "updated": gd.format_timestamp(node.get("updated_at"))},
            "type": node_type,
            "attributes": [gd.public_attribute(a) for a in live],
            "fields": fields,
            "extra": extra,
            "gaps": gd.entry_gaps(live, by_attribute),
            "layout": gd.card_layout(self.store.card(type_id) if type_id else None, live),
            "view": _public_view(view) if view else None,
            "connections": gd.connections_of(gd.node_id(node), network),
            "documents": gd.documents(detail),
            "visibility": {"access_group_ids": [str(g) for g in (
                node.get("access_group_ids") or node.get("required_groups") or [])]},
        }

    def history(self, node_id: str) -> Dict[str, Any]:
        _detail, node, facts = self._detail(node_id)
        reader = getattr(self.client, "graph_fact_history", None)
        histories: Dict[str, Optional[list]] = {}
        for fact in facts[:HISTORY_CAP]:
            if not callable(reader) or not fact.get("id"):
                continue
            try:
                histories[str(fact["id"])] = reader(str(fact["id"]))
            except Exception as exc:  # noqa: BLE001 - one unreadable history is not a page error
                logger.warning("Verlauf von Fakt %s nicht lesbar: %s", fact.get("id"), exc)
                histories[str(fact["id"])] = None
        type_id = gd.node_type_id(node)
        names = {gd.attribute_id(a): str(a.get("name") or "")
                 for a in (self.schema(type_id, include_deprecated=True) or [])} if type_id else {}
        people: Dict[str, str] = {}
        for rows in histories.values():
            for row in rows or []:
                for ref in (row.get("actor_ref"), row.get("actor")):
                    person = self._people(str(ref)) if ref else None
                    if person:
                        people[str(ref)] = person["display_name"]
        result = gd.history(node, facts[:HISTORY_CAP], histories, names, people)
        result["truncated"] = len(facts) > HISTORY_CAP
        return result

    # ── console ───────────────────────────────────────────────────────────

    def entries_by_type(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for node in self.nodes():
            counts[gd.node_type_id(node)] = counts.get(gd.node_type_id(node), 0) + 1
        return counts

    def admin_types(self) -> List[dict]:
        counts, views = self.entries_by_type(), {v["node_type_id"]: v for v in self.store.views()}
        out = []
        for node_type in self.node_types():
            live = self.live_schema(node_type["id"])
            view = views.get(node_type["id"])
            out.append({**node_type, "fields": len(live),
                        "required": sum(1 for a in live if a.get("required")),
                        "entries": counts.get(node_type["id"], 0),
                        "view": _public_view(view) if view else None})
        return out

    def _suggest(self, items: List[dict]) -> List[dict]:
        dismissed = self.store.dismissed([s["id"] for s in items])
        return [s for s in items if s["id"] not in dismissed]

    def admin_type(self, type_id: str) -> Dict[str, Any]:
        node_type = self.node_type(type_id)
        if node_type is None:
            raise NotFound(type_id)
        attributes = self.schema(type_id, include_deprecated=True) or []
        live = gd.live_attributes(attributes)
        nodes = self.nodes_of_type(type_id)
        facts_by_node, complete = self.type_facts(type_id, nodes)
        stats = gd.fill_stats(live, facts_by_node)
        view = self.store.view_for_type(type_id)
        layout = gd.card_layout(self.store.card(type_id), live)
        return {
            "type": node_type,
            "attributes": [dict(gd.public_attribute(a), fill=stats.get(gd.attribute_id(a)))
                           for a in live],
            "retired": [gd.public_attribute(a) for a in gd.retired_attributes(attributes)],
            "entries": len(nodes),
            "basis": {"loaded": len(facts_by_node), "total": len(nodes), "complete": complete},
            "view": _public_view(view) if view else None,
            "layout": layout,
            "types": self.node_types(),
            "suggestions": self._suggest(gd.field_suggestions(
                node_type, attributes, nodes, facts_by_node, complete=complete, view=view)),
        }

    def admin_card(self, type_id: str) -> Dict[str, Any]:
        node_type = self.node_type(type_id)
        if node_type is None:
            raise NotFound(type_id)
        live = self.live_schema(type_id)
        layout = gd.card_layout(self.store.card(type_id), live)
        view = self.store.view_for_type(type_id)
        return {
            "type": node_type,
            "attributes": [gd.public_attribute(a) for a in live],
            "layout": layout,
            "view": _public_view(view) if view else None,
            "samples": [{"id": gd.node_id(n), "name": gd.node_name(n)}
                        for n in self.nodes_of_type(type_id)][:50],
            "suggestions": self._suggest(gd.card_suggestions(node_type, live, layout)),
        }

    def admin_directories(self) -> Dict[str, Any]:
        node_types, views = self.node_types(), self.store.views()
        counts = self.entries_by_type()
        attributes = {t["id"]: self.live_schema(t["id"]) for t in node_types}
        fill: Dict[str, Dict[str, dict]] = {}
        for view in views:
            if not view.get("columns") or view["node_type_id"] not in attributes:
                continue
            bulk = self._bulk_facts(view["node_type_id"])
            if bulk is None or not bulk.get("complete", True):
                continue       # a column is only called empty on complete data
            by_node: Dict[str, List[dict]] = {
                gd.node_id(n): [] for n in self.nodes_of_type(view["node_type_id"])}
            for fact in bulk.get("facts") or []:
                if str(fact.get("node_id")) in by_node:
                    by_node[str(fact.get("node_id"))].append(fact)
            fill[view["node_type_id"]] = gd.fill_stats(attributes[view["node_type_id"]], by_node)
        known = {t["id"] for t in node_types}
        return {
            "views": [dict(_public_view(v), entries=counts.get(v["node_type_id"], 0),
                           type_known=v["node_type_id"] in known) for v in views],
            "types": [dict(t, entries=counts.get(t["id"], 0),
                           attributes=[gd.public_attribute(a) for a in attributes[t["id"]]])
                      for t in node_types],
            "suggestions": self._suggest(gd.directory_suggestions(
                [{"id": t["id"], "name": t["name"]} for t in node_types], views, counts, fill,
                attributes)),
        }

    # ── suggestions: apply ────────────────────────────────────────────────

    def suggestions_for(self, scope: str, type_id: str) -> List[dict]:
        if scope == "fields":
            return self.admin_type(type_id)["suggestions"]
        if scope == "card":
            return self.admin_card(type_id)["suggestions"]
        if scope == "dirs":
            return self.admin_directories()["suggestions"]
        raise NotFound(scope)

    def apply(self, scope: str, type_id: str, suggestion_id: str, *, by: Any) -> str:
        """Carry out one suggestion, recomputed here rather than trusted from
        the browser: what is applied is what the inventory says today."""
        match = next((s for s in self.suggestions_for(scope, type_id)
                      if s["id"] == suggestion_id), None)
        if match is None or not match.get("action"):
            raise NotFound(suggestion_id)
        action = match["action"]
        kind = action["kind"]
        target = action.get("node_type_id") or type_id
        if kind == "deprecate_attribute":
            if self.client.graph_deprecate_schema_attribute(target, action["attribute_id"]) is None:
                raise NotFound(action["attribute_id"])
            view = self.store.view_for_type(target)
            if view and action["attribute_id"] in view["columns"]:
                self.store.update_view(target, by=by, columns=[
                    c for c in view["columns"] if c != action["attribute_id"]])
            message = "Feld stillgelegt. Bestehende Werte bleiben erhalten."
        elif kind == "trim_enum":
            self.client.graph_update_schema_attribute(target, action["attribute_id"],
                                                      enum_values=action["keep"])
            message = "Auswahlliste gekürzt."
        elif kind == "add_date_attribute":
            live = self.live_schema(target)
            next_order = (max((gd._sort_key(a)[0] for a in live), default=0) // 10 + 1) * 10
            self.client.graph_create_schema_attribute(
                target, action["name"], datatype="date", required=False,
                description=action["description"], sort_order=next_order)
            message = (f"Feld „{action['name']}“ angelegt. Die Werte übernimmt niemand "
                       "automatisch.")
        elif kind in ("auto_place", "trim_head"):
            live = self.live_schema(target)
            layout = gd.card_layout(self.store.card(target), live)
            if kind == "auto_place":
                wanted = set(action["attribute_ids"])
                gd.auto_place(layout, [a for a in live if gd.attribute_id(a) in wanted])
                message = f"{len(wanted)} Feld(er) einsortiert."
            else:
                layout["rail"] = layout["head"][gd.HEAD_COMFORT:] + layout["rail"]
                layout["head"] = layout["head"][:gd.HEAD_COMFORT]
                message = "Kopfzeile gekürzt."
            self.store.save_card(target, gd.clean_layout(layout, live), by=by)
        elif kind == "create_view":
            node_type = self.node_type(target)
            live = self.live_schema(target)
            self.store.create_view(target, title=node_type["name"], slug=slugify(node_type["name"]),
                                   columns=[gd.attribute_id(a) for a in live[:4]], by=by)
            message = f"Verzeichnis „{node_type['name']}“ angelegt."
        elif kind == "drop_column":
            view = self.store.view_for_type(target)
            self.store.update_view(target, by=by, columns=[
                c for c in view["columns"] if c != action["attribute_id"]])
            message = "Spalte entfernt."
        else:
            raise NotFound(kind)
        self.invalidate()
        return message


def _public_view(view: Optional[dict]) -> Optional[dict]:
    if not view:
        return None
    return {"node_type_id": view["node_type_id"], "slug": view["slug"], "title": view["title"],
            "subtitle": view.get("subtitle") or "", "active": bool(view.get("active")),
            "position": view.get("position", 0), "columns": list(view.get("columns") or [])}
