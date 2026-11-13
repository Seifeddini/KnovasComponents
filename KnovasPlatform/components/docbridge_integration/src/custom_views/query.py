"""The read query a custom view sends, and the check of it against the view's manifest."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Mapping, Optional, Tuple

from .manifest import Manifest

MAX_STRING = 200
MAX_IDS = 200
MAX_WHERE_VALUES = 20
MAX_FACTS = 40
MAX_LINKS = 10

FORBIDDEN_MESSAGE = "Diese Ansicht darf das nicht lesen."

_KEYS = {"type", "ids", "linked", "where", "facts", "links", "limit"}
_LINKED_KEYS = {"to", "relation", "direction"}
_LINK_KEYS = {"relation", "direction"}


class QueryError(ValueError):
    """The query is malformed. The message is German, names the field and never echoes a value."""


class Forbidden(Exception):
    """The query reaches outside the manifest. Never says which part."""

    def __init__(self) -> None:
        super().__init__(FORBIDDEN_MESSAGE)


@dataclass(frozen=True)
class LinkSpec:
    relation: str
    direction: Literal["out", "in"]


@dataclass(frozen=True)
class Query:
    type: str
    ids: Optional[Tuple[str, ...]]
    linked_to: Optional[Tuple[str, ...]]
    linked: Optional[LinkSpec]
    where: Mapping[str, Tuple[str, ...]]
    facts: Tuple[str, ...]
    links: Tuple[LinkSpec, ...]
    limit: int


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_STRING:
        raise QueryError(f"Feld „{field}“ muss ein Text mit 1 bis {MAX_STRING} Zeichen sein.")
    return value


def _strings(value: Any, field: str, maximum: int) -> Tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum:
        raise QueryError(f"Feld „{field}“ muss eine Liste mit höchstens {maximum} Einträgen sein.")
    return tuple(_string(item, field) for item in value)


def _only_keys(value: Any, allowed: set, field: str) -> dict:
    if not isinstance(value, dict):
        raise QueryError(f"Feld „{field}“ muss ein Objekt sein.")
    if set(value) - allowed:
        raise QueryError(f"Feld „{field}“ enthält ein unbekanntes Feld.")
    return value


def _direction(value: Any, field: str) -> str:
    if value not in ("out", "in"):
        raise QueryError(f"Feld „{field}“ muss „out“ oder „in“ sein.")
    return value


def _link_spec(value: Any, field: str) -> LinkSpec:
    value = _only_keys(value, _LINK_KEYS, field)
    if "relation" not in value or "direction" not in value:
        raise QueryError(f"Feld „{field}“ braucht „relation“ und „direction“.")
    return LinkSpec(_string(value["relation"], f"{field}.relation"),
                    _direction(value["direction"], f"{field}.direction"))


def parse_query(body: Any, *, max_nodes: int) -> Query:
    body = _only_keys(body, _KEYS, "(Anfrage)")
    if "type" not in body:
        raise QueryError("Feld „type“ fehlt.")
    type_ = _string(body["type"], "type")

    ids = _strings(body["ids"], "ids", MAX_IDS) if "ids" in body else None

    linked_to: Optional[Tuple[str, ...]] = None
    linked: Optional[LinkSpec] = None
    if "linked" in body:
        raw = _only_keys(body["linked"], _LINKED_KEYS, "linked")
        if not {"to", "relation", "direction"} <= set(raw):
            raise QueryError("Feld „linked“ braucht „to“, „relation“ und „direction“.")
        linked_to = _strings(raw["to"], "linked.to", MAX_IDS)
        if not linked_to:
            raise QueryError("Feld „linked.to“ darf nicht leer sein.")
        linked = LinkSpec(_string(raw["relation"], "linked.relation"),
                          _direction(raw["direction"], "linked.direction"))

    where = {}
    if "where" in body:
        if not isinstance(body["where"], dict):
            raise QueryError("Feld „where“ muss ein Objekt sein.")
        for fact, value in body["where"].items():
            name = _string(fact, "where")
            where[name] = (_string(value, "where"),) if isinstance(value, str) \
                else _strings(value, "where", MAX_WHERE_VALUES)

    facts = _strings(body["facts"], "facts", MAX_FACTS) if "facts" in body else ()

    links: Tuple[LinkSpec, ...] = ()
    if "links" in body:
        if not isinstance(body["links"], list) or len(body["links"]) > MAX_LINKS:
            raise QueryError(f"Feld „links“ muss eine Liste mit höchstens {MAX_LINKS} Einträgen sein.")
        links = tuple(_link_spec(item, "links") for item in body["links"])

    limit = max_nodes
    if "limit" in body:
        limit = body["limit"]
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= max_nodes:
            raise QueryError(f"Feld „limit“ muss eine Zahl von 1 bis {max_nodes} sein.")

    return Query(type_, ids, linked_to, linked, MappingProxyType(where), facts, links, limit)


def authorize(query: Query, manifest: Manifest) -> None:
    allowed = manifest.reads.get(query.type)
    if allowed is None:
        raise Forbidden()
    if any(name not in allowed for name in (*query.facts, *query.where)):
        raise Forbidden()
