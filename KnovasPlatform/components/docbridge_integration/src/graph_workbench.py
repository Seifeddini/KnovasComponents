"""Selection-scoped data composer for the integrated Cortex."""
from __future__ import annotations

import logging
from typing import Any, Optional

import graph_directory as gd
from identity.directories import slugify

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 120
MAX_PAGE_SIZE = 250
HISTORY_CAP = 25


class NotFound(LookupError):
    """The requested graph object is unknown or not visible to this principal."""


class Workbench:
    def __init__(self, client: Any, source: Any, store: Any) -> None:
        self.client = client
        self.source = source
        self.store = store

    def topology(self) -> dict:
        if self.source is not None and hasattr(self.source, "topology"):
            try:
                return self.source.topology()
            except AttributeError:
                # Small test doubles and older graph clients may not expose
                # the export yet; their filtered list endpoints remain valid.
                pass
        export = getattr(self.client, "graph_export", None)
        raw = export() if callable(export) else {}
        raw = raw or {}
        if not isinstance(raw, dict):
            raw = {}
        edge_reader = getattr(self.client, "graph_edges", None)
        return {
            "node_types": raw.get("node_types") or raw.get("nodeTypes")
            or self.client.graph_node_types(),
            "nodes": raw.get("nodes") or self.client.graph_nodes(),
            "edges": raw.get("edges") or (
                edge_reader() if callable(edge_reader) else []
            ),
        }

    def invalidate(self) -> None:
        if self.source is not None and hasattr(self.source, "invalidate"):
            self.source.invalidate()

    def node_types(self) -> list[dict]:
        types = []
        counts: dict[str, int] = {}
        topology = self.topology()
        for node in topology.get("nodes") or []:
            type_id = gd.node_type_id(node)
            counts[type_id] = counts.get(type_id, 0) + 1
        for raw in topology.get("node_types") or []:
            type_id = gd.node_id(raw)
            if type_id:
                types.append({
                    "id": type_id,
                    "name": gd.node_name(raw) or type_id,
                    "description": str(raw.get("description") or ""),
                    "count": counts.get(type_id, 0),
                })
        return sorted(types, key=lambda item: item["name"].casefold())

    def views(self) -> list[dict]:
        """Configured views plus useful defaults for Mandat and Person."""
        configured = {
            str(view["node_type_id"]): self._public_view(view)
            for view in self.store.active_views()
        }
        for node_type in self.node_types():
            folded = node_type["name"].casefold()
            if node_type["id"] in configured:
                continue
            if folded.startswith("mandat") or folded in {"person", "personen"}:
                configured[node_type["id"]] = {
                    "node_type_id": node_type["id"],
                    "slug": slugify(node_type["name"]),
                    "title": (
                        "Mandate" if folded.startswith("mandat")
                        else "Personen"
                    ),
                    "subtitle": "",
                    "active": True,
                    "position": 999,
                    "columns": [],
                    "inferred": True,
                }
        return sorted(
            configured.values(),
            key=lambda view: (int(view.get("position") or 0), view["title"].casefold()),
        )

    def view(self, slug: str) -> Optional[dict]:
        return next((view for view in self.views() if view["slug"] == slug), None)

    @staticmethod
    def _public_view(view: dict) -> dict:
        return {
            "node_type_id": str(view["node_type_id"]),
            "slug": str(view["slug"]),
            "title": str(view["title"]),
            "subtitle": str(view.get("subtitle") or ""),
            "active": bool(view.get("active", True)),
            "position": int(view.get("position") or 0),
            "columns": [str(item) for item in (view.get("columns") or [])],
            "inferred": bool(view.get("inferred")),
        }

    def nodes_for_view(
        self,
        slug: str,
        *,
        offset: int = 0,
        limit: int = DEFAULT_PAGE_SIZE,
        query: str = "",
    ) -> dict:
        view = self.view(slug)
        if view is None:
            raise NotFound(slug)
        limit = max(1, min(MAX_PAGE_SIZE, int(limit)))
        offset = max(0, int(offset))
        query_folded = str(query or "").strip().casefold()
        type_id = view["node_type_id"]
        rows = []
        for raw in self.topology().get("nodes") or []:
            if gd.node_type_id(raw) != type_id:
                continue
            name = gd.node_name(raw) or gd.node_id(raw)
            if query_folded and query_folded not in name.casefold():
                continue
            rows.append({
                "id": gd.node_id(raw),
                "name": name,
                "node_type_id": type_id,
                "document_count": len(gd.documents(raw)),
            })
        rows.sort(key=lambda item: item["name"].casefold())
        page = rows[offset:offset + limit]
        return {
            "view": view,
            "nodes": page,
            "offset": offset,
            "limit": limit,
            "total": len(rows),
            "truncated": offset + len(page) < len(rows),
        }

    def _detail(self, node_id: str) -> tuple[dict, dict, list[dict]]:
        detail = self.client.graph_node(node_id)
        if not detail:
            raise NotFound(node_id)
        node = detail.get("node", detail) if isinstance(detail, dict) else {}
        if not isinstance(node, dict) or not gd.node_id(node):
            raise NotFound(node_id)
        facts = detail.get("facts") if isinstance(detail, dict) else None
        if facts is None:
            facts = self.client.graph_facts(node_id)
        if facts is None:
            raise NotFound(node_id)
        return detail, node, list(facts)

    def _names_and_types(self) -> tuple[dict[str, str], dict[str, str]]:
        names: dict[str, str] = {}
        types: dict[str, str] = {}
        for node in self.topology().get("nodes") or []:
            node_id = gd.node_id(node)
            if node_id:
                names[node_id] = gd.node_name(node) or node_id
                types[node_id] = gd.node_type_id(node)
        return names, types

    def node_page(self, node_id: str) -> dict:
        detail, node, facts = self._detail(node_id)
        type_id = gd.node_type_id(node)
        attributes = gd.live_attributes(
            self.client.graph_schema(type_id, include_deprecated=False) or []
        ) if type_id else []
        names, _types = self._names_and_types()
        current = gd.facts_by_attribute(facts)
        fields = [
            gd.field_cell(attribute, current.get(gd.attribute_id(attribute)), names)
            for attribute in attributes
        ]
        gaps = [
            gd.attribute_id(attribute)
            for attribute in attributes
            if attribute.get("required")
            and current.get(gd.attribute_id(attribute), {}).get("value") in (None, "")
        ]
        return {
            "node": {
                "id": gd.node_id(node),
                "name": gd.node_name(node) or gd.node_id(node),
                "node_type_id": type_id,
                "created": gd.format_timestamp(node.get("created_at")),
                "updated": gd.format_timestamp(node.get("updated_at")),
            },
            "fields": fields,
            "gaps": gaps,
            "layout": self.store.card(type_id) or {},
            "documents": gd.documents(detail),
            "view": next(
                (view for view in self.views() if view["node_type_id"] == type_id),
                None,
            ),
        }

    def network(self, node_id: str, depth: int = 1) -> dict:
        _detail, node, _facts = self._detail(node_id)
        depth = max(1, min(3, int(depth)))
        names, types = self._names_and_types()
        try:
            response = self.client.graph_neighbors(
                node_id, depth=depth, include_edges=True
            )
        except Exception as exc:  # keep the selected node usable
            logger.warning("Nachbarschaft von %s nicht lesbar: %s", node_id, exc)
            response = None
        result = gd.neighbourhood(
            node,
            response,
            names=names,
            types=types,
            fallback_edges=self.topology().get("edges") or [],
        )
        result.update({"depth": depth, "available": response is not None})
        return result

    def history(self, node_id: str) -> dict:
        _detail, node, facts = self._detail(node_id)
        reader = getattr(self.client, "graph_fact_history", None)
        histories: dict[str, Optional[list]] = {}
        for fact in facts[:HISTORY_CAP]:
            fact_id = str(fact.get("id") or "")
            if not fact_id or not callable(reader):
                continue
            try:
                histories[fact_id] = reader(fact_id)
            except Exception as exc:
                logger.warning("Verlauf von Fakt %s nicht lesbar: %s", fact_id, exc)
                histories[fact_id] = None
        type_id = gd.node_type_id(node)
        attributes = self.client.graph_schema(
            type_id, include_deprecated=True
        ) or [] if type_id else []
        names = {
            gd.attribute_id(attribute): str(attribute.get("name") or "")
            for attribute in attributes
        }
        return {
            "events": gd.history_events(facts[:HISTORY_CAP], histories, names),
            "truncated": len(facts) > HISTORY_CAP,
            "available": callable(reader),
        }
