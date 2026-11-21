"""The dossier (PMVP-30a, plan B-02 and B-04): what the Brain knows about one thing, read as the signed-in person.

One starting point (PRE-01): exactly one node id. The reads are per thing (POST-03) and go through the Platform's
one Knovas client, whose broker signs every call as the person signed in on this request (app.py,
_RequestScopedBroker), the client the search page uses:

1. ``GET /secured/graph/nodes/<id>`` (``graph_node``): the node; its active document assignments, which the Core
   narrows to the documents this person may read (graph_api.node_detail, ``filter_rows_by_pointer``); and its facts,
   narrowed to the facts this person may read (``_visible_facts``). An unknown node and a node this person may not
   see both answer 404 there; the client gives None for both, and this module raises the same NotFound for both.
2. ``GET /secured/graph/node-types/<type>`` and ``…/<type>/schema`` (``graph_get_many``, one round of two calls,
   each signed as the person): the kind of the thing (the page's eyebrow) and the names of its fields (the labels of
   the facts). Either may fail; the page then has no eyebrow, and a fact falls back to its own label.

Never ``graph_export``, ``graph_edges``, ``graph_node_types`` or ``graph_nodes``.

**Where a fact's source is** (KnowledgeBase, origin/master: knovas-software/DB/init.sql ``kg_node_fact``;
app/src/services/brain_store/graph_repository.py ``_fact``; app/src/api/graph_api.py, which serialises the row
whole): ``provenance_pointer`` names the document, ``provenance_chunk_id`` the passage (a SentenceChunk id) and
``provenance_status`` says whether both still hold (``ok``, ``chunk_missing``, ``pointer_detached``). No per-thing
route gives a passage's text or its page.

**When a fact counts as sourced** (no mark, POST-02): all three say so, *and* its document is one of this node's
documents as the Core returned them for this person, because only those are known to be readable by them. A
visible fact does not prove its provenance document readable: the Core shows a fact when ANY of its evidence
chunks is readable (graph_access.visible_fact_ids), so the pointer of a visible fact can name a document the person
may not open. Such a fact is marked as one without a source, and its pointer is neither shown nor handed on.

Nothing here logs: a node's name, a fact's value and a pointer are the firm's text (GI-DATA-04).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import quote

from knovas_client import KnowledgeGraphDisabled

# A node id as it may go into an address: the Core's are uuids; a few test and core ids are shorter words.
_NODE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
# What a fact may say about its passage and still count as sourced (None: the store records no status).
_SOURCE_OK = (None, "ok")
UNNAMED_FACT = "Angabe"
UNNAMED_NODE = "Ohne Namen"


class NotFound(Exception):
    """No such node, or one this person may not see: the same answer for both (GI-GRAPH-11)."""


class Unavailable(Exception):
    """The Core did not answer the node read; the message is the error's type name only."""


@dataclass(frozen=True)
class Document:
    """A document as the dossier names it: the pointer, its last segment and the folder before it."""

    pointer: str
    name: str
    folder: str


@dataclass(frozen=True)
class Fact:
    id: str
    label: str
    value: str
    # The document the fact's passage is in, when the fact counts as sourced (see the module's docstring).
    source: Optional[Document]
    passage_id: Optional[str]
    # A field of kind entity_ref holds another node's id: that node's dossier.
    ref_node_id: Optional[str]


@dataclass(frozen=True)
class Dossier:
    node_id: str
    name: str
    kind: Optional[str]
    description: Optional[str]
    facts: Tuple[Fact, ...]
    documents: Tuple[Document, ...]


def valid_node_id(node_id: Any) -> bool:
    return isinstance(node_id, str) and bool(_NODE_ID.match(node_id))


def read_dossier(client: Any, node_id: str) -> Dossier:
    """The dossier of one node, as the person the client signs for may see it.

    Raises NotFound for an id that is malformed, unknown, not visible to the person, or when the graph is switched
    off; Unavailable when the node read itself fails. The kind and the field names are optional.
    """
    if not valid_node_id(node_id):
        raise NotFound()
    try:
        payload = client.graph_node(node_id)
    except KnowledgeGraphDisabled:
        raise NotFound() from None
    except Exception as exc:  # noqa: BLE001 - every other failure is one plain message on the page
        raise Unavailable(type(exc).__name__) from None
    if payload is None:
        raise NotFound()
    node = payload.get("node") if isinstance(payload, Mapping) else None
    if not isinstance(node, Mapping):
        raise Unavailable("no node in the answer")

    kind, fields = _kind_and_fields(client, node.get("node_type_id"))
    documents = documents_of(payload.get("assignments"))
    readable = {d.pointer: d for d in documents}
    facts = facts_of(payload.get("facts"), fields, readable)
    return Dossier(
        node_id=node_id,
        name=_clean(node.get("name")) or UNNAMED_NODE,
        kind=kind,
        description=_clean(node.get("description")) or None,
        facts=facts,
        documents=documents,
    )


# -- the kind and the fields -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Field:
    order: int
    name: str
    datatype: str


def _kind_and_fields(client: Any, type_id: Any) -> Tuple[Optional[str], Dict[str, _Field]]:
    if not isinstance(type_id, str) or not type_id.strip():
        return None, {}
    path = f"/node-types/{quote(type_id.strip(), safe='')}"
    try:
        answers = client.graph_get_many([(path, None), (f"{path}/schema", None)], workers=2)
    except Exception:  # noqa: BLE001 - optional: the page goes without
        return None, {}
    if not isinstance(answers, Sequence) or len(answers) != 2:
        return None, {}
    (type_status, type_payload), (schema_status, schema_payload) = answers
    kind = None
    if type_status == 200 and isinstance(type_payload, Mapping):
        node_type = type_payload.get("node_type")
        if isinstance(node_type, Mapping):
            kind = _clean(node_type.get("name")) or None
    fields: Dict[str, _Field] = {}
    if schema_status == 200 and isinstance(schema_payload, Mapping):
        attributes = schema_payload.get("attributes")
        for index, attribute in enumerate(attributes if isinstance(attributes, list) else []):
            if not isinstance(attribute, Mapping) or attribute.get("id") is None:
                continue
            name = _clean(attribute.get("name"))
            if name:
                order = attribute.get("sort_order")
                fields[str(attribute["id"])] = _Field(
                    order=order if isinstance(order, int) and not isinstance(order, bool) else index,
                    name=name,
                    datatype=str(attribute.get("datatype") or ""),
                )
    return kind, fields


# -- documents and facts -----------------------------------------------------------------------------------------


def document_of(pointer: str) -> Document:
    """A pointer's last segment is the document's name, what stands before it its folder."""
    path = pointer.replace("\\", "/").rstrip("/")
    folder, _, name = path.rpartition("/")
    return Document(pointer=pointer, name=name or path or pointer, folder=folder)


def documents_of(assignments: Any) -> Tuple[Document, ...]:
    """The node's documents: each pointer once, active assignments only, by name."""
    found: Dict[str, Document] = {}
    for row in assignments if isinstance(assignments, list) else []:
        if not isinstance(row, Mapping) or row.get("status") not in (None, "active"):
            continue
        pointer = row.get("pointer")
        if isinstance(pointer, str) and pointer.strip() and pointer not in found:
            found[pointer] = document_of(pointer)
    return tuple(sorted(found.values(), key=lambda d: (d.name.casefold(), d.pointer)))


def facts_of(rows: Any, fields: Mapping[str, _Field], readable: Mapping[str, Document]) -> Tuple[Fact, ...]:
    """The facts to show, in the order of the type's fields, then by label. Rejected facts are left out."""
    keyed: List[Tuple[Tuple[int, int, str], Fact]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, Mapping) or row.get("curation_status") == "rejected":
            continue
        attribute_id = row.get("attribute_id")
        field = fields.get(str(attribute_id)) if attribute_id is not None else None
        label = field.name if field else (_clean(row.get("label")) or UNNAMED_FACT)
        value = row.get("value")
        ref = value.strip() if (field and field.datatype == "entity_ref" and isinstance(value, str)
                                and valid_node_id(value.strip())) else None
        pointer, chunk = row.get("provenance_pointer"), row.get("provenance_chunk_id")
        sourced = (isinstance(pointer, str) and pointer in readable and isinstance(chunk, str) and chunk.strip() != ""
                   and row.get("provenance_status") in _SOURCE_OK)
        fact = Fact(
            id=str(row.get("id") or ""),
            label=label,
            value=value_text(value),
            source=readable[pointer] if sourced else None,
            passage_id=chunk.strip() if sourced else None,
            ref_node_id=ref,
        )
        keyed.append(((0 if field else 1, field.order if field else 0, label.casefold()), fact))
    return tuple(fact for _, fact in sorted(keyed, key=lambda pair: pair[0]))


def value_text(value: Any) -> str:
    """A fact's value as one line of text. The value is JSON: a word, a number, a yes or no, or a small structure."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "ja" if value else "nein"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return ""
        return str(int(value)) if value.is_integer() else f"{value:g}"
    if isinstance(value, str):
        return _clean(value)
    if isinstance(value, Mapping):
        amount, currency = value.get("amount"), value.get("currency")
        if amount is not None and isinstance(currency, str) and currency.strip():
            return f"{_clean(currency)} {value_text(amount)}"
        if "value" in value:
            return value_text(value.get("value"))
        return ", ".join(t for t in (value_text(v) for v in value.values()) if t)
    if isinstance(value, (list, tuple)):
        return ", ".join(t for t in (value_text(v) for v in value) if t)
    return _clean(str(value))


def _clean(value: Any) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""
