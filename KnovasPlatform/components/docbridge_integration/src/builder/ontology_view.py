"""The ontology of a workspace as a tree, for the canvas and the type tree.

Build plan step B2. The model holds the company's types; the reference packs
hold the Brain's core types and the industry packs' classes. This module puts
them into one tree: every model type under its parent, every core or pack
class a model type descends from as a read-only node above it. A model type
whose iri names a core or pack class *is* that class, as the company uses it.

A type's parent comes from the model; a type that has none takes it from its
description header "[level=...; parent=...]" (DEC-0004), which is where the
Brain keeps it until its API can hold parents. The model is not changed.

It also finds the problems a consultant should see while reading or
editing the ontology, with the rules KnowledgeBase checks a package with
(test_terms_yaml, rules_report R0 and R8): parents that resolve to nothing or
loop; relations without a domain or range or naming an unknown type; names
taken twice; unknown datatypes, enums without values, entity references
without a target; descriptions missing a part rule 8 asks for (names de/en,
the text, an example); retired types that processes or mappings still use;
and attributes the graph store cannot hold today (only enum attributes reach
it), which the canvas shows as a badge.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from builder import packs as reference_packs

#: Datatypes the graph store holds as values today. Others are written as
#: labels only until the store takes them (text, date, money, entity_ref).
HELD_DATATYPES = frozenset({"enum"})
#: A relation's domain or range that admits any type.
WILDCARD = "*"
#: The datatypes KnowledgeBase accepts (scripts/client_zero/terms.py DATATYPES).
DATATYPES = ("text", "date", "money", "enum", "entity_ref")
#: Rule 8: what a type's description must have, and an attribute's and a relation's.
RULE8_TYPE = (("label.de", "Name (de)"), ("label.en", "Name (en)"), ("description", "Beschreibung"),
              ("example", "Beispiel"))
RULE8_ATTRIBUTE = (("label.de", "Name (de)"), ("label.en", "Name (en)"), ("description", "Beschreibung"))
RULE8_RELATION = (("label.de", "Name (de)"), ("label.en", "Name (en)"), ("sentence", "Lesart"))

_HEADER = re.compile(r"^\s*\[([^\]]*)\]")


def header_fields(text: Any) -> dict[str, str]:
    """``[level=pack; parent=euo:Software; iri=...]`` at the start of a description."""
    match = _HEADER.match(text) if isinstance(text, str) else None
    if not match:
        return {}
    fields = {}
    for part in match.group(1).split(";"):
        key, sep, value = part.partition("=")
        if sep and value.strip() and value.strip().lower() not in ("none", "null", "-"):
            fields[key.strip()] = value.strip()
    return fields


def description_text(text: Any) -> str:
    """A description without its header: what a person reads and edits."""
    if not isinstance(text, str):
        return ""
    match = _HEADER.match(text)
    return text[match.end():].strip() if match else text.strip()


def _has(item: Mapping[str, Any], part: str) -> bool:
    if part == "description":
        return bool(description_text((item.get("description") or {}).get("en")))
    if part.startswith("label."):
        return bool((item.get("label") or {}).get(part[6:]))
    return bool(item.get(part))


def rule8_missing(item: Mapping[str, Any], parts) -> list[str]:
    """The parts of rule 8 ``item`` lacks, by their German names."""
    return [label for part, label in parts if not _has(item, part)]


def _attribute(attr: Mapping[str, Any], origin: str | None = None) -> dict:
    out = {
        "name": attr.get("name"),
        "datatype": attr.get("datatype") or "",
        "required": bool(attr.get("required")),
        "enum_values": list(attr.get("enum_values") or []),
        "label": dict(attr.get("label") or {}),
        "description": description_text((attr.get("description") or {}).get("en")),
        "target": attr.get("target") or "",
        "level": attr.get("level") or "",
        "held": (attr.get("datatype") or "") in HELD_DATATYPES,
    }
    if origin:
        out["from"] = origin
    return out


class _Problems:
    def __init__(self):
        self.items: list[dict] = []

    def add(self, kind: str, severity: str, message: str, target_kind: str, target: str, **extra) -> str:
        problem_id = f"p{len(self.items) + 1}"
        self.items.append({"id": problem_id, "kind": kind, "severity": severity, "message": message,
                           "target_kind": target_kind, "target": target, **extra})
        return problem_id


def build(model: Mapping[str, Any], packs: Iterable[Mapping[str, Any]] | None = None) -> dict:
    """The tree, the relations and the problems of ``model``'s ontology."""
    packs = list(packs if packs is not None else reference_packs.load())
    catalog: dict[str, dict] = {}
    for pack in packs:
        for item in pack.get("types") or []:
            catalog.setdefault(item["id"], {**item, "pack": pack["pack"]})

    ontology = model.get("ontology") or {}
    types = list(ontology.get("types") or [])
    problems = _Problems()

    # Node ids. A core-level type whose iri names a core class *is* that class
    # as the company uses it, and takes its id (the first such type does).
    # Any other type only maps to a class: pack and company types often name
    # the class they specialise, which is their parent, not themselves.
    key_to_id: dict[str, str] = {}
    claimed: set[str] = set()
    for item in types:
        iri = item.get("iri")
        if item.get("level") == "core" and iri in catalog and iri not in claimed:
            key_to_id[item["key"]] = iri
            claimed.add(iri)
        else:
            key_to_id[item["key"]] = f"type:{item['key']}"

    nodes: dict[str, dict] = {}
    for item in types:
        node_id = key_to_id[item["key"]]
        description = dict(item.get("description") or {})
        nodes[node_id] = {
            "id": node_id,
            "key": item["key"],
            "name": item["key"],
            "label": dict(item.get("label") or {}),
            "level": item.get("level") or "company",
            "source": "model",
            "readonly": item.get("level") == "core",
            "editable": (item.get("level") or "company") == "company",
            # Pack types take the company's own attributes (step B4).
            "extendable": item.get("level") == "pack",
            "retired": bool(item.get("retired")),
            "pack": item.get("pack"),
            "candidate": _candidate_of(item.get("reason")),
            "meta": bool(item.get("meta")),
            "iri": item.get("iri"),
            "id_prefixes": list(item.get("id_prefixes") or []),
            "description": description,
            "description_text": description_text(description.get("en")),
            "example": item.get("example") or "",
            "not_to_confuse": item.get("not_to_confuse") or "",
            "reason": item.get("reason") or "",
            "rule8_missing": rule8_missing(item, RULE8_TYPE),
            "attributes": [_attribute(a) for a in item.get("attributes") or []],
            "parent": None,
            "parent_ref": item.get("parent") or header_fields(description.get("en")).get("parent"),
            "problems": [],
        }

    def add_catalog(class_id: str) -> None:
        """A read-only node for a catalog class and the classes above it."""
        while class_id and class_id not in nodes:
            entry = catalog[class_id]
            parent = (entry.get("parents") or [None])[0]
            nodes[class_id] = {
                "id": class_id, "key": class_id, "name": entry["name"], "label": dict(entry.get("label") or {}),
                "level": "core" if entry["pack"] == "core" else "pack", "source": entry["pack"],
                "readonly": True, "editable": False, "extendable": False, "retired": False, "meta": False,
                "iri": class_id, "candidate": None,
                "id_prefixes": [], "description": {}, "description_text": "", "reason": "", "rule8_missing": [],
                "example": "", "not_to_confuse": "", "attributes": [],
                "parent": parent if parent in catalog else None, "parent_ref": parent, "problems": [],
                "abstract": bool(entry.get("abstract")),
            }
            class_id = parent if parent in catalog else None

    # Resolve parents.
    for node in list(nodes.values()):
        if node["source"] != "model":
            continue
        ref = node["parent_ref"]
        if not ref or ref == node["id"]:
            # No parent given, or the type names its own class: a core class
            # used as is hangs under the class's own parent from the catalog.
            iri = node["id"] if node["id"] in catalog else None
            ref = (catalog[iri].get("parents") or [None])[0] if iri else None
            if not ref:
                continue
        if ref in key_to_id:
            node["parent"] = key_to_id[ref]
        elif ref in nodes:
            node["parent"] = ref
        elif ref in catalog:
            add_catalog(ref)
            node["parent"] = ref
        else:
            node["problems"].append(problems.add(
                "parent_unknown", "critical", f"Der Elterntyp «{ref}» von {node['name']} ist unbekannt.",
                "type", node["id"]))
    # A parent chain must end: one that loops is cut where it closes.
    for node in nodes.values():
        seen = {node["id"]}
        current = node["parent"]
        while current is not None:
            if current in seen:
                node["problems"].append(problems.add(
                    "parent_cycle", "critical", f"Die Elterntypen von {node['name']} bilden einen Kreis.",
                    "type", node["id"]))
                node["parent"] = None
                break
            seen.add(current)
            current = nodes[current]["parent"] if current in nodes else None

    # Pack membership: the pack the term came from, else its own catalog
    # pack, else the first pack class above it.
    for node in nodes.values():
        if node.get("pack"):
            continue
        node["pack"] = None
        current = node["id"]
        while current is not None and current in nodes:
            entry = catalog.get(current)
            if entry and entry["pack"] != "core":
                node["pack"] = entry["pack"]
                break
            current = nodes[current]["parent"]

    # Inherited attributes: the model types above, then those every type has.
    common = [_attribute(a, "Alle Typen") for a in ontology.get("common_attributes") or []]
    for node in nodes.values():
        inherited = []
        current = node["parent"]
        while current is not None and current in nodes:
            above = nodes[current]
            inherited += [{**a, "from": above["name"]} for a in above["attributes"]]
            current = above["parent"]
        node["inherited"] = inherited + (common if node["source"] == "model" else [])
        node["attribute_count"] = len(node["attributes"])

    # Problems on types and their attributes.
    by_lower: dict[str, list[str]] = {}
    for item in types:
        by_lower.setdefault(str(item["key"]).lower(), []).append(item["key"])
    used = _usages(model, [t["key"] for t in types if t.get("retired")])
    for item in types:
        node = nodes[key_to_id[item["key"]]]
        same = by_lower[str(item["key"]).lower()]
        if len(same) > 1:
            node["problems"].append(problems.add(
                "name_collision", "critical",
                f"Den Typnamen {item['key']} gibt es {len(same)}-mal ({', '.join(same)}).", "type", node["id"]))
        places = used.get(item["key"]) if item.get("retired") else None
        if places:
            node["problems"].append(problems.add(
                "retired_in_use", "critical",
                f"{node['name']} ist stillgelegt, wird aber noch verwendet: {', '.join(places)}.",
                "type", node["id"], places=places))
    for item in types:
        node = nodes[key_to_id[item["key"]]]
        parent = nodes.get(node["parent"]) if node["parent"] else None
        if parent is not None and parent.get("retired") and not node["retired"]:
            node["problems"].append(problems.add(
                "retired_parent", "warning", f"Der Elterntyp {parent['name']} von {node['name']} ist stillgelegt.",
                "type", node["id"]))
        if not node["description_text"]:
            node["problems"].append(problems.add(
                "type_no_description", "warning", f"{node['name']} hat keine Beschreibung.", "type", node["id"]))
        rest = [part for part in node["rule8_missing"] if part != "Beschreibung"]
        if rest:
            node["problems"].append(problems.add(
                "description_incomplete", "warning", f"{node['name']}: es fehlt {', '.join(rest)} (Regel 8).",
                "type", node["id"], parts=rest))
        seen_attrs: set[str] = set()
        for raw_attr, attr in zip(item.get("attributes") or [], node["attributes"]):
            name = str(attr["name"])
            where = f"{node['name']}.{name}"
            if name.lower() in seen_attrs:
                node["problems"].append(problems.add(
                    "name_collision", "critical", f"{node['name']} hat das Attribut {name} zweimal.",
                    "type", node["id"], attribute=name))
            seen_attrs.add(name.lower())
            if attr["datatype"] not in DATATYPES:
                node["problems"].append(problems.add(
                    "datatype_unknown", "critical",
                    f"{where}: den Datentyp «{attr['datatype'] or 'leer'}» gibt es nicht.",
                    "type", node["id"], attribute=name))
            if attr["datatype"] == "enum" and not attr["enum_values"]:
                node["problems"].append(problems.add(
                    "enum_no_values", "critical", f"{where} ist eine Auswahl ohne Werte.",
                    "type", node["id"], attribute=name))
            if attr["datatype"] == "entity_ref":
                target = attr["target"]
                if not target:
                    node["problems"].append(problems.add(
                        "entity_ref_no_target", "critical", f"{where} verweist auf keinen Zieltyp.",
                        "type", node["id"], attribute=name))
                elif target not in key_to_id and target not in catalog:
                    node["problems"].append(problems.add(
                        "entity_ref_unknown_target", "critical",
                        f"{where} verweist auf den unbekannten Typ «{target}».",
                        "type", node["id"], attribute=name))
            attr["rule8_missing"] = rule8_missing(raw_attr, RULE8_ATTRIBUTE)
        gaps = [a["name"] for a in node["attributes"] if a["rule8_missing"]]
        if gaps and node["editable"]:
            node["problems"].append(problems.add(
                "attribute_description_incomplete", "warning",
                f"{node['name']}: Attributen fehlen Namen oder Beschreibung (Regel 8): {', '.join(gaps)}.",
                "type", node["id"]))
    for node in nodes.values():
        if node["source"] != "model":
            continue
        for attr in node["attributes"]:
            if not attr["held"]:
                node["problems"].append(problems.add(
                    "attribute_not_held", "info",
                    f"{node['name']}.{attr['name']} ({attr['datatype'] or 'ohne Datentyp'}) kann der Graph-Speicher "
                    "heute nicht als Wert halten; er bekommt nur die Beschriftung.",
                    "type", node["id"], attribute=attr["name"]))
    for attr in common:
        if not attr["held"]:
            problems.add(
                "attribute_not_held", "info",
                f"{attr['name']} ({attr['datatype'] or 'ohne Datentyp'}) gilt für alle Typen und kann heute nicht als "
                "Wert gehalten werden.", "common", "", attribute=attr["name"])

    # Relations.
    relations = []
    relation_keys: dict[str, int] = {}
    for item in ontology.get("relations") or []:
        relation_keys[str(item["key"]).lower()] = relation_keys.get(str(item["key"]).lower(), 0) + 1
    for item in ontology.get("relations") or []:
        relation_id = f"relation:{item['key']}"
        entry = {
            "id": relation_id, "key": item["key"], "label": dict(item.get("label") or {}),
            "sentence": item.get("sentence") or "", "level": item.get("level") or "company",
            "family": item.get("family") or "", "reason": item.get("reason") or "",
            "editable": (item.get("level") or "company") == "company", "retired": bool(item.get("retired")),
            "pack": item.get("pack"),
            "inverse": item.get("inverse"), "domain": [], "range": [],
            "domain_names": list(item.get("domain") or []), "range_names": list(item.get("range") or []),
            "rule8_missing": rule8_missing(item, RULE8_RELATION),
            "problems": [],
        }
        if relation_keys[str(item["key"]).lower()] > 1:
            entry["problems"].append(problems.add(
                "name_collision", "critical", f"Den Beziehungsnamen {item['key']} gibt es mehrmals.",
                "relation", relation_id))
        if entry["editable"] and entry["rule8_missing"]:
            entry["problems"].append(problems.add(
                "description_incomplete", "warning",
                f"{item['key']}: es fehlt {', '.join(entry['rule8_missing'])} (Regel 8).", "relation", relation_id,
                parts=entry["rule8_missing"]))
        for side in ("domain", "range"):
            names = entry[f"{side}_names"]
            if WILDCARD in names:
                # "*": any type. Valid, and drawn as no particular arrow.
                entry[f"{side}_any"] = True
                continue
            if not names:
                entry["problems"].append(problems.add(
                    "relation_no_bounds", "critical",
                    f"Der Beziehung {item['key']} fehlt der {'Definitions' if side == 'domain' else 'Werte'}bereich.",
                    "relation", relation_id))
            for name in names:
                target = key_to_id.get(name) or (name if name in nodes else None)
                if target is None:
                    entry["problems"].append(problems.add(
                        "relation_unknown_type", "critical",
                        f"Die Beziehung {item['key']} nennt den unbekannten Typ «{name}».",
                        "relation", relation_id))
                else:
                    entry[side].append(target)
                    if nodes[target].get("retired") and not entry["retired"]:
                        entry["problems"].append(problems.add(
                            "retired_in_relation", "warning",
                            f"Die Beziehung {item['key']} nennt den stillgelegten Typ {nodes[target]['name']}.",
                            "relation", relation_id))
        relations.append(entry)

    for relation in relations:
        for side in ("domain", "range"):
            for node_id in relation[side]:
                nodes[node_id].setdefault("outgoing" if side == "domain" else "incoming", []).append(relation["id"])
    for node in nodes.values():
        node.setdefault("outgoing", [])
        node.setdefault("incoming", [])

    ordered = _tree_order(nodes)
    present_packs = sorted({n["pack"] for n in nodes.values() if n["pack"]})
    pack_names = {p["pack"]: p.get("name") or {} for p in packs}
    pack_names.update(reference_packs.names())
    return {
        "nodes": ordered,
        "relations": relations,
        "problems": problems.items,
        "packs": [{"id": p, "name": pack_names.get(p, {})} for p in present_packs],
        # For the editors: every class a type can descend from, and the
        # vocabulary a new relation picks from.
        "catalog": [{"id": c["id"], "name": c["name"], "level": "core" if c["pack"] == "core" else "pack",
                     "pack": c["pack"], "label": dict(c.get("label") or {})} for c in catalog.values()],
        "relation_names": sorted({str(r["key"]) for r in relations}),
        # Company types marked as candidates for a pack (step B4).
        "candidates": [{"id": n["id"], "key": n["key"], "name": n["name"], "label": n["label"],
                        "pack": n["candidate"], "reason": n["reason"]}
                       for n in ordered if n["source"] == "model" and n["level"] == "company"
                       and n["candidate"] is not None],
        "term_packs": [{"id": p["id"], "name": dict(p.get("name") or {})} for p in reference_packs.term_packs()],
        "families": sorted({r["family"] for r in relations if r["family"]}),
        "datatypes": list(DATATYPES),
        "counts": {
            "types": sum(1 for n in ordered if n["source"] == "model"),
            "reference": sum(1 for n in ordered if n["source"] != "model"),
            "relations": len(relations),
            "edges": sum(len(r["domain"]) * len(r["range"]) for r in relations),
            "problems": {
                severity: sum(1 for p in problems.items if p["severity"] == severity)
                for severity in ("critical", "warning", "info")
            },
        },
    }


def _tree_order(nodes: dict[str, dict]) -> list[dict]:
    """Nodes depth first, children by name, each with its depth: the type tree's order."""
    children: dict[str | None, list[str]] = {}
    for node in nodes.values():
        parent = node["parent"] if node["parent"] in nodes else None
        children.setdefault(parent, []).append(node["id"])
    for ids in children.values():
        ids.sort(key=lambda i: nodes[i]["name"].lower())

    ordered = []

    def visit(node_id: str, depth: int) -> None:
        node = nodes[node_id]
        node["depth"] = depth
        node["children"] = len(children.get(node_id, []))
        ordered.append(node)
        for child in children.get(node_id, []):
            visit(child, depth + 1)

    for root in children.get(None, []):
        visit(root, 0)
    return ordered


def _usages(model: Mapping[str, Any], keys: list[str]) -> dict[str, list[str]]:
    if not keys:
        return {}
    from builder import editing  # late: editing builds on this module

    return editing.usages(model, keys)


def _candidate_of(reason: Any) -> str | None:
    from builder import editing  # late: editing builds on this module

    return editing.candidate_of(reason)
