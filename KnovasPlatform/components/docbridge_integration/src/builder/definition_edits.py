"""A tenant's definitions into a workspace that exists (BB-12).

``definitions.adopt`` takes the package the Platform rebuilds from the
definitions bundle the tenant's views read (tenant_definitions.model_from_bundle)
and adds to the draft what it lacks: types, relations, processes with their
rules and report rules, hierarchies, pages, profiles and the packs the package
took over. Nothing the workspace has is replaced or removed: an element with
the same key stays as it is, except that a type or relation takes the fields
it has no value for -- a description, a parent, an iri, an attribute it lacks,
a domain or range entry. Every addition is a change row, so the adoption is
one batch to take back, and a second adoption changes nothing.

Owners, health, edit sources and node descriptions are not adopted: they are
documents, not elements, and the builder edits them nowhere.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping, Optional

from builder.editing import (
    OPERATIONS, PACKS, PAGES, PROCESS_REPORT_RULES, PROCESS_RULES, PROCESSES, PROFILES, RELATIONS, SPINES, TYPES,
    EditError, _Edit, _items, ensure_part, key_of,
)

#: The fields of a type the workspace's own entry takes from the package where it has none.
TYPE_FIELDS = ("parent", "iri", "label", "description", "example", "not_to_confuse", "meta", "id_prefixes",
               "aligns_with", "reason", "state_attribute")
RELATION_FIELDS = ("family", "iri", "inverse", "sentence", "description", "label", "reason")
#: The sections whose elements are added when the workspace lacks them, in the order they are written.
ADDED = (PROCESSES, PROCESS_RULES, PROCESS_REPORT_RULES, SPINES, PAGES, PROFILES, PACKS)
#: The package part a section's first element needs in the manifest (editing.ensure_part).
PART_OF = {PROCESSES: "processes", PROCESS_RULES: "processes", PROCESS_REPORT_RULES: "processes",
           SPINES: "spines", PAGES: "pages", PROFILES: "profiles"}

_EMPTY = (None, "", [], {})


def _filled(old: Mapping[str, Any], new: Mapping[str, Any], fields, *, attributes: bool = False) -> Optional[dict]:
    """``old`` with the fields it has no value for taken from ``new``; None when that changes nothing."""
    merged = copy.deepcopy(dict(old))
    for field in fields:
        if merged.get(field) in _EMPTY and new.get(field) not in _EMPTY:
            merged[field] = copy.deepcopy(new[field])
    if attributes:
        have = {a.get("name") for a in merged.get("attributes") or [] if isinstance(a, Mapping)}
        extra = [copy.deepcopy(a) for a in new.get("attributes") or [] if isinstance(a, Mapping) and a.get("name") not in have]
        if extra:
            merged["attributes"] = list(merged.get("attributes") or []) + extra
    return merged if merged != old else None


def _relation_filled(old: Mapping[str, Any], new: Mapping[str, Any]) -> Optional[dict]:
    merged = _filled(old, new, RELATION_FIELDS) or copy.deepcopy(dict(old))
    for side in ("domain", "range"):
        have = list(merged.get(side) or [])
        extra = [v for v in new.get(side) or [] if v not in have]
        if extra:
            merged[side] = have + extra
    return merged if merged != old else None


def _definitions_adopt(edit: _Edit, op: Mapping[str, Any]) -> None:
    source = op.get("package")
    if not isinstance(source, Mapping) or not isinstance(source.get("ontology"), Mapping):
        raise EditError("Keine Definitionen zum Übernehmen.", 409)
    for new in source["ontology"].get("types") or []:
        if not isinstance(new, Mapping) or new.get("key") is None:
            continue
        old = edit.find(TYPES, new["key"])
        if old is None:
            edit.write(TYPES, None, copy.deepcopy(dict(new)))
        else:
            merged = _filled(old, new, TYPE_FIELDS, attributes=True)
            if merged is not None:
                edit.write(TYPES, old, merged)
    for new in source["ontology"].get("relations") or []:
        if not isinstance(new, Mapping) or new.get("key") is None:
            continue
        old = edit.find(RELATIONS, new["key"])
        if old is None:
            edit.write(RELATIONS, None, copy.deepcopy(dict(new)))
        else:
            merged = _relation_filled(old, new)
            if merged is not None:
                edit.write(RELATIONS, old, merged)
    for section in ADDED:
        for new in _items(dict(source), section, create=False):
            if not isinstance(new, Mapping) or key_of(section, new) is None:
                continue
            if edit.find(section, key_of(section, new)) is not None:
                continue
            if section in PART_OF:
                ensure_part(edit.model, PART_OF[section])
            edit.write(section, None, copy.deepcopy(dict(new)))


OPERATIONS["definitions.adopt"] = _definitions_adopt
