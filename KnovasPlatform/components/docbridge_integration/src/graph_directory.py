"""What the directory and card screens show, computed from graph data.

Directories (one page per node type), the card of an entry, its knowledge
network, and the suggestions the console makes from the inventory. Every
screen is generated from a type's schema: no type name appears in this module,
and a new type is data entry in the console, not a release.

Deliberately I/O-free -- no Flask, no HTTP, no SQL. The routes fetch, this
module decides what the fetched data means, and the tests pin that meaning
without a server.

Design: the "Wissenstypen & Verzeichnisse" artifact; rules from
docs/superpowers/specs/2026-09-02-typed-node-workbench-design.md §8.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from graph_model import decode, format_date

TONES = ("t1", "t2", "t3", "t4")
MAX_SECTIONS = 20
SECTION_NAME_MAX = 80
HEAD_COMFORT = 3          # chips a reader takes in at a glance
HEAD_TOO_MANY = 4
HIDDEN_REF = "Nicht sichtbarer Eintrag"
DATE_LIKE = re.compile(r"\b(\d{1,2}\.\d{1,2}\.\d{4}|\d{4}-\d{2}-\d{2})\b")


class LayoutError(ValueError):
    """A card layout cannot be saved. The message is for a person."""


# ── tolerant reads of API rows ────────────────────────────────────────────

def _first(mapping: Any, *keys: str, default: Any = "") -> Any:
    if not isinstance(mapping, dict):
        return default
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return default


def node_id(node: Any) -> str:
    return str(_first(node, "id", "node_id", "uuid"))


def node_name(node: Any) -> str:
    return str(_first(node, "name", "label", "title"))


def node_type_id(node: Any) -> str:
    value = _first(node, "node_type_id", "type_id", "node_type")
    if isinstance(value, dict):
        return str(_first(value, "id", "node_type_id"))
    return str(value)


def attribute_id(attribute: Any) -> str:
    return str(_first(attribute, "id", "attribute_id"))


def is_deprecated(attribute: Any) -> bool:
    return bool(_first(attribute, "deprecated_at", default=None)
                or attribute.get("deprecated") is True)


def _sort_key(attribute: Dict[str, Any]) -> Tuple[int, str]:
    try:
        order = int(attribute.get("sort_order") or 0)
    except (TypeError, ValueError):
        order = 0
    return order, str(attribute.get("name") or "")


def live_attributes(attributes: Optional[Iterable[dict]]) -> List[dict]:
    """The fields a type currently has, in form order."""
    return sorted((a for a in attributes or [] if not is_deprecated(a)), key=_sort_key)


def retired_attributes(attributes: Optional[Iterable[dict]]) -> List[dict]:
    return sorted((a for a in attributes or [] if is_deprecated(a)), key=_sort_key)


def public_attribute(attribute: Dict[str, Any]) -> Dict[str, Any]:
    """The part of an attribute definition the browser needs."""
    return {
        "id": attribute_id(attribute),
        "name": str(attribute.get("name") or ""),
        "datatype": str(attribute.get("datatype") or "text"),
        "required": bool(attribute.get("required")),
        "sort_order": _sort_key(attribute)[0],
        "description": str(attribute.get("description") or ""),
        "enum_values": [str(v) for v in (attribute.get("enum_values") or [])],
        "target_node_type_id": attribute.get("target_node_type_id") or None,
        "deprecated": is_deprecated(attribute),
    }


def type_tones(node_types: Iterable[dict]) -> Dict[str, str]:
    """A colour per type, stable while types are only ever added.

    Ordered by creation (then id), so the first four types get four distinct
    tones and a new type never repaints an old one.
    """
    ordered = sorted((t for t in node_types if node_id(t)),
                     key=lambda t: (str(_first(t, "created_at", default="")), node_id(t)))
    return {node_id(t): TONES[i % len(TONES)] for i, t in enumerate(ordered)}


# ── values ────────────────────────────────────────────────────────────────

def format_money(value: Any) -> str:
    """84’500.00 CHF -- the Swiss apostrophe, set here rather than by a locale.

    A locale formatter renders a thin space in one environment and an
    apostrophe in another, and an amount that looks different depending on the
    browser is an error source in a file.
    """
    if not isinstance(value, dict) or value.get("amount") in (None, ""):
        return ""
    try:
        amount = Decimal(str(value["amount"]).replace("’", "").replace("'", ""))
    except (InvalidOperation, ValueError):
        return str(value.get("amount"))
    whole, _, cents = f"{abs(amount):.2f}".partition(".")
    grouped = "{:,}".format(int(whole)).replace(",", "’")
    sign = "-" if amount < 0 else ""
    return f"{sign}{grouped}.{cents} {value.get('currency') or ''}".strip()


def ref_target(value: Any) -> Optional[str]:
    decoded = decode("entity_ref", value)
    if isinstance(decoded, dict) and decoded.get("node_id"):
        return str(decoded["node_id"])
    return None


def display(datatype: str, value: Any, names: Optional[Dict[str, str]] = None) -> str:
    if value is None or value == "":
        return ""
    if datatype == "date":
        return format_date(value)
    if datatype == "money":
        return format_money(value)
    if datatype == "entity_ref":
        target = ref_target(value)
        return (names or {}).get(target, "") if target else ""
    if isinstance(value, (dict, list)):
        return ""
    return str(value)


def _fact_time(fact: Dict[str, Any]) -> str:
    return str(_first(fact, "updated_at", "created_at", default=""))


def facts_by_attribute(facts: Iterable[dict]) -> Dict[str, dict]:
    """The current fact per attribute: the most recently written one."""
    out: Dict[str, dict] = {}
    for fact in facts or []:
        key = fact.get("attribute_id")
        if key in (None, ""):
            continue
        key = str(key)
        if key not in out or _fact_time(fact) > _fact_time(out[key]):
            out[key] = fact
    return out


def cell(attribute: Dict[str, Any], fact: Optional[dict],
         names: Dict[str, str]) -> Dict[str, Any]:
    """One field of one entry, ready to render."""
    datatype = str(attribute.get("datatype") or "text")
    if fact is None or fact.get("value") in (None, ""):
        return {"display": "", "missing": True, "ref": None,
                "fact_id": str(fact["id"]) if fact and fact.get("id") else None}
    value = fact.get("value")
    shown, ref = display(datatype, value, names), None
    if datatype == "entity_ref":
        target = ref_target(value)
        if target and target in names:
            ref = {"id": target, "name": names[target]}
        else:
            # The fact is visible but its target is not -- say so, without
            # printing an id that names something the reader may not see.
            shown = HIDDEN_REF
    return {"display": shown, "missing": False, "ref": ref,
            "fact_id": str(fact["id"]) if fact.get("id") else None,
            "value": decode(datatype, value)}


def entry_gaps(live: Sequence[dict], by_attribute: Dict[str, dict]) -> List[str]:
    """Required fields without a value. A gap, never an error: schemas make
    absence visible, they do not block a save."""
    return [attribute_id(a) for a in live if a.get("required")
            and (by_attribute.get(attribute_id(a)) or {}).get("value") in (None, "")]


# ── directories ───────────────────────────────────────────────────────────

def columns_for(view: Optional[dict], live: Sequence[dict]) -> List[dict]:
    """The chosen columns, or the first four fields: a table of names only
    would be useless the first time anyone opens it."""
    by_id = {attribute_id(a): a for a in live}
    chosen = [by_id[c] for c in (view or {}).get("columns") or [] if c in by_id]
    return chosen or list(live[:4])


def directory_row(node: dict, columns: Sequence[dict], live: Sequence[dict],
                  facts: Optional[List[dict]], names: Dict[str, str],
                  connections: Optional[int]) -> Dict[str, Any]:
    row: Dict[str, Any] = {"id": node_id(node), "name": node_name(node) or node_id(node),
                           "connections": connections, "loaded": facts is not None}
    if facts is None:
        return row
    by_attribute = facts_by_attribute(facts)
    row["cells"] = {attribute_id(a): cell(a, by_attribute.get(attribute_id(a)), names)
                    for a in columns}
    row["gaps"] = entry_gaps(live, by_attribute)
    return row


def connection_counts(edges: Iterable[dict]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for edge in edges or []:
        ends = edge_ends(edge)
        if ends is None:
            continue
        a, b = ends[0], ends[1]
        counts[a] = counts.get(a, 0) + 1
        if b != a:
            counts[b] = counts.get(b, 0) + 1
    return counts


# ── card layouts ──────────────────────────────────────────────────────────

def card_layout(stored: Optional[dict], live: Sequence[dict]) -> Dict[str, Any]:
    """A stored layout, reconciled with the fields the type has today.

    Fields that no longer exist drop out; a field placed twice keeps its first
    place; fields nobody placed are listed as ``loose`` and appear on the card
    under "Nicht zugeordnet", so a new field never silently vanishes.
    """
    known = [attribute_id(a) for a in live]
    placed: Set[str] = set()

    def take(ids: Any) -> List[str]:
        out = []
        for item in ids if isinstance(ids, list) else []:
            key = str(item)
            if key in known and key not in placed:
                placed.add(key)
                out.append(key)
        return out

    stored = stored if isinstance(stored, dict) else {}
    head, rail = take(stored.get("head")), take(stored.get("rail"))
    sections = []
    for section in stored.get("sections") if isinstance(stored.get("sections"), list) else []:
        if not isinstance(section, dict):
            continue
        name = " ".join(str(section.get("name") or "").split())[:SECTION_NAME_MAX]
        sections.append({"name": name or "Abschnitt", "fields": take(section.get("fields"))})
    if not sections:
        sections = [{"name": "Details", "fields": []}]
    loose = [key for key in known if key not in placed]
    return {"head": head, "rail": rail, "sections": sections, "loose": loose}


def clean_layout(payload: Any, live: Sequence[dict]) -> Dict[str, Any]:
    """A layout from the designer, validated for saving."""
    if not isinstance(payload, dict):
        raise LayoutError("Der Kartenaufbau erwartet ein Objekt.")
    sections = payload.get("sections")
    if not isinstance(sections, list) or not sections:
        raise LayoutError("Die Karte braucht mindestens einen Abschnitt.")
    if len(sections) > MAX_SECTIONS:
        raise LayoutError(f"Mehr als {MAX_SECTIONS} Abschnitte sind keine Gliederung mehr.")
    for section in sections:
        if not isinstance(section, dict) or not " ".join(str(section.get("name") or "").split()):
            raise LayoutError("Jeder Abschnitt braucht einen Namen.")
    layout = card_layout(payload, live)
    return {"head": layout["head"], "rail": layout["rail"], "sections": layout["sections"]}


def _section_index(layout: Dict[str, Any], name: str) -> int:
    for index, section in enumerate(layout["sections"]):
        if section["name"] == name:
            return index
    layout["sections"].append({"name": name, "fields": []})
    return len(layout["sections"]) - 1


def auto_place(layout: Dict[str, Any], attributes: Sequence[dict]) -> Dict[str, Any]:
    """Place fields by datatype -- coarse and visible, so the suggestion can
    name its rule and everything stays movable afterwards."""
    for attribute in attributes:
        key, kind = attribute_id(attribute), attribute.get("datatype")
        if kind == "enum" and len(attribute.get("enum_values") or []) <= 4 \
                and len(layout["head"]) < HEAD_COMFORT:
            layout["head"].append(key)
        elif kind == "entity_ref":
            layout["sections"][_section_index(layout, "Beteiligte")]["fields"].append(key)
        elif kind == "date":
            layout["sections"][_section_index(layout, "Termine")]["fields"].append(key)
        elif kind == "money":
            layout["sections"][_section_index(layout, "Wirtschaftliches")]["fields"].append(key)
        else:
            layout["sections"][0]["fields"].append(key)
    return layout


# ── inventory statistics ──────────────────────────────────────────────────

def fill_stats(live: Sequence[dict], facts_by_node: Dict[str, List[dict]]) -> Dict[str, dict]:
    """In how many of the loaded entries each field holds a value."""
    total = len(facts_by_node)
    filled = {attribute_id(a): 0 for a in live}
    for facts in facts_by_node.values():
        present = facts_by_attribute(facts)
        for key in filled:
            if (present.get(key) or {}).get("value") not in (None, ""):
                filled[key] += 1
    return {key: {"filled": count, "total": total} for key, count in filled.items()}


def incomplete_entries(live: Sequence[dict], facts_by_node: Dict[str, List[dict]]) -> List[str]:
    return [nid for nid, facts in facts_by_node.items()
            if entry_gaps(live, facts_by_attribute(facts))]


# ── suggestions ───────────────────────────────────────────────────────────
#
# The console does not ask anyone to guess a schema in advance: which fields
# a matter needs is known after forty matters, not before. So it reads the
# inventory and PROPOSES. The system may propose; a person decides. Nothing
# below changes anything by itself, and a dismissed proposal never returns.

def _quoted(values: Iterable[str]) -> str:
    return ", ".join(f"„{v}“" for v in values)


def field_suggestions(node_type: dict, attributes: Sequence[dict], nodes: Sequence[dict],
                      facts_by_node: Dict[str, List[dict]], *, complete: bool,
                      view: Optional[dict]) -> List[dict]:
    type_id, out = node_id(node_type), []
    live = live_attributes(attributes)
    total = len(nodes)
    newest_entry = max((str(_first(n, "created_at", default="")) for n in nodes), default="")

    if complete:
        stats = fill_stats(live, facts_by_node)
        for attribute in live:
            key, name = attribute_id(attribute), attribute.get("name")
            born = str(_first(attribute, "created_at", default=""))
            # A field created after every entry cannot be filled yet; judging
            # it by its fill rate would propose retiring every new field.
            if born and newest_entry and born > newest_entry:
                continue
            if total >= 3 and stats[key]["filled"] == 0:
                out.append({
                    "id": f"leer:{type_id}:{key}", "tone": "warn",
                    "title": f"„{name}“ stilllegen?",
                    "why": (f"In keinem der {total} Einträge ausgefüllt. Ein Feld, das nie jemand "
                            "füllt, verlängert jedes Formular und lässt jede Karte leerer "
                            "aussehen, als sie ist."),
                    "cta": "Stilllegen",
                    "action": {"kind": "deprecate_attribute", "attribute_id": key}})

        for attribute in (a for a in live if a.get("datatype") == "enum"):
            key, values = attribute_id(attribute), list(attribute.get("enum_values") or [])
            used = {str((facts_by_attribute(f).get(key) or {}).get("value") or "")
                    for f in facts_by_node.values()} - {""}
            unused = [v for v in values if v not in used]
            if total >= 3 and used and unused:
                out.append({
                    "id": f"enum:{type_id}:{key}:{'|'.join(unused)}", "tone": "info",
                    "title": f"Auswahlliste von „{attribute.get('name')}“ kürzen?",
                    "why": (f"{_quoted(unused)} {'wird' if len(unused) == 1 else 'werden'} in "
                            f"keinem der {total} Einträge verwendet. Kürzere Listen werden "
                            "richtiger ausgefüllt."),
                    "cta": "Ungenutzte entfernen",
                    "action": {"kind": "trim_enum", "attribute_id": key,
                               "keep": [v for v in values if v in used]}})

    names = {str(a.get("name") or "") for a in live}
    for attribute in (a for a in live if a.get("datatype") == "text"):
        key, name = attribute_id(attribute), str(attribute.get("name") or "")
        hits = sum(1 for facts in facts_by_node.values()
                   if DATE_LIKE.search(str((facts_by_attribute(facts).get(key) or {})
                                           .get("value") or "")))
        if hits >= 2 and f"{name} (Datum)" not in names:
            # The proposal adds a date field. It converts nothing: the API
            # cannot change a field's datatype, and a value silently
            # reinterpreted is a statement nobody made.
            out.append({
                "id": f"datum:{type_id}:{key}", "tone": "info",
                "title": f"Steckt in „{name}“ ein Datum?",
                "why": (f"In {hits} {'von ' + str(total) + ' ' if complete else ''}Einträgen steht "
                        "dort etwas, das wie ein Datum aussieht. Als eigenes Datumsfeld wäre es "
                        "sortierbar und auf seine Genauigkeit festgelegt. Der Vorschlag legt das "
                        "Feld an — die bestehenden Texte bleiben, wo sie sind."),
                "cta": "Datumsfeld anlegen",
                "action": {"kind": "add_date_attribute", "name": f"{name} (Datum)",
                           "description": f"Aus „{name}“ herausgelöst."}})

    if complete:
        gaps = incomplete_entries(live, facts_by_node)
        if gaps and total >= 3:
            action = ({"kind": "show_gaps", "href": f"/verzeichnis/{view['slug']}?luecken=1"}
                      if view and view.get("active") else None)
            out.append({
                "id": f"luecken:{type_id}:{len(gaps)}", "tone": "info", "dismissable": False,
                "title": f"{len(gaps)} von {total} Einträgen haben eine Lücke in einem Pflichtfeld",
                "why": ("Das ist kein Fehler: Pflichtfelder machen eine fehlende Angabe sichtbar, "
                        "sie sperren das Speichern nicht."),
                "cta": "Betroffene zeigen" if action else "",
                "action": action})
    return out


def card_suggestions(node_type: dict, live: Sequence[dict], layout: Dict[str, Any]) -> List[dict]:
    type_id, out = node_id(node_type), []
    loose = layout.get("loose") or []
    if loose:
        n = len(loose)
        out.append({
            "id": f"karte:{type_id}:{'|'.join(loose)}", "tone": "info",
            "title": (f"{n} Feld{'' if n == 1 else 'er'} {'steht' if n == 1 else 'stehen'} auf "
                      "der Karte unter „Nicht zugeordnet“"),
            "why": ("Ein Vorschlag nach Datentyp: Verbindungen zu „Beteiligte“, Daten zu "
                    "„Termine“, Beträge zu „Wirtschaftliches“, kurze Auswahlen in die "
                    "Kopfzeile. Danach beliebig verschiebbar."),
            "cta": "Automatisch einsortieren",
            "action": {"kind": "auto_place", "attribute_ids": list(loose)}})
    head = len(layout.get("head") or [])
    if head > HEAD_TOO_MANY:
        out.append({
            "id": f"kopf:{type_id}:{head}", "tone": "warn",
            "title": "Die Kopfzeile trägt zu viel",
            "why": (f"{head} Marken neben dem Titel. Auf einen Blick liest man drei, danach wird "
                    "es eine zweite Tabelle. Der Rest steht gut in der Seitenleiste."),
            "cta": "Überzählige in die Seitenleiste",
            "action": {"kind": "trim_head"}})
    return out


def directory_suggestions(node_types: Sequence[dict], views: Sequence[dict],
                          entries_by_type: Dict[str, int],
                          column_fill: Dict[str, Dict[str, dict]],
                          attributes_by_type: Dict[str, Sequence[dict]]) -> List[dict]:
    out = []
    with_view = {v["node_type_id"] for v in views}
    for node_type in node_types:
        type_id, count = node_id(node_type), entries_by_type.get(node_id(node_type), 0)
        if type_id not in with_view and count >= 3:
            out.append({
                "id": f"verz:{type_id}", "tone": "info",
                "title": f"„{node_name(node_type)}“ hat {count} Einträge, aber kein Verzeichnis",
                "why": ("Ohne Verzeichnis sind sie nur über die Suche und den Cortex erreichbar — "
                        "niemand sieht sie als Bestand."),
                "cta": "Verzeichnis anlegen",
                "action": {"kind": "create_view", "node_type_id": type_id}})
    for view in views:
        live = {attribute_id(a): a for a in live_attributes(attributes_by_type.get(view["node_type_id"]))}
        for column in view.get("columns") or []:
            stats = (column_fill.get(view["node_type_id"]) or {}).get(column)
            if column not in live or not stats:
                continue
            if stats["total"] >= 4 and stats["filled"] == 0:
                name = live[column].get("name")
                out.append({
                    "id": f"spalte:{view['node_type_id']}:{column}", "tone": "warn",
                    "title": f"Spalte „{name}“ in „{view['title']}“ ist überall leer",
                    "why": (f"In keinem der {stats['total']} Einträge gefüllt — die Spalte kostet "
                            "Breite und zeigt nichts."),
                    "cta": "Spalte entfernen",
                    "action": {"kind": "drop_column", "node_type_id": view["node_type_id"],
                               "attribute_id": column}})
    return out


# ── the knowledge network ─────────────────────────────────────────────────

def edge_ends(edge: Any) -> Optional[Tuple[str, str, str, bool, str]]:
    """(a, b, label, manual, id) of an edge, or None when unusable."""
    a = str(_first(edge, "node_lo", "source", "src", "from_node_id", "from"))
    b = str(_first(edge, "node_hi", "target", "dst", "to_node_id", "to"))
    if not a or not b:
        return None
    label = str(_first(edge, "relation", "predicate", "label", "type"))
    manual = str(_first(edge, "edge_source", "source_kind", default="")) == "manual"
    return a, b, label, manual, str(_first(edge, "id", default=""))


def _induced(edges: Iterable[dict], visible: Set[str]) -> List[dict]:
    """Edges whose BOTH ends are visible. Never an edge to a node the reader
    cannot see: it would disclose that the node exists (GI-GRAPH-12)."""
    out, seen = [], set()
    for edge in edges or []:
        ends = edge_ends(edge)
        if ends is None or ends[0] not in visible or ends[1] not in visible:
            continue
        a, b, label, manual, eid = ends
        key = (min(a, b), max(a, b), label)
        if key in seen:
            continue
        seen.add(key)
        out.append({"id": eid, "from": a, "to": b, "label": label, "manual": manual})
    return out


def neighbourhood(centre: dict, response: Optional[dict], *, names: Dict[str, str],
                  types: Dict[str, str], fallback_edges: Iterable[dict] = ()) -> Dict[str, Any]:
    """The drawable neighbourhood of an entry.

    Nodes come from the API's walk, which the backend has already filtered to
    what this person may see. Edges come with the walk when the API offers
    ``include_edges``; an API that does not yet answers with an empty list,
    and then the tenant's visible edges are induced on the same node set --
    the same rule, applied to the same filtered set.
    """
    centre_id = node_id(centre)
    nodes: Dict[str, dict] = {centre_id: {
        "id": centre_id, "name": node_name(centre) or names.get(centre_id, centre_id),
        "node_type_id": node_type_id(centre), "hop": 0}}
    for item in (response or {}).get("neighbors") or []:
        nid = node_id(item)
        if not nid or nid in nodes:
            continue
        try:
            hop = max(1, min(3, int(_first(item, "hop", "depth", default=1))))
        except (TypeError, ValueError):
            hop = 1
        nodes[nid] = {"id": nid, "name": node_name(item) or names.get(nid) or nid,
                      "node_type_id": node_type_id(item) or types.get(nid, ""), "hop": hop}
    visible = set(nodes)
    edges = _induced((response or {}).get("edges") or [], visible)
    if not edges and len(nodes) > 1:
        edges = _induced(fallback_edges, visible)
    linked = {e["from"] for e in edges} | {e["to"] for e in edges}
    for nid, node in nodes.items():
        # A first-ring neighbour IS adjacent to the centre; drawn without a
        # label when the API did not say by which relation.
        if node["hop"] == 1 and nid not in linked:
            edges.append({"id": "", "from": centre_id, "to": nid, "label": "", "manual": False})
    ordered = sorted(nodes.values(), key=lambda n: (n["hop"], n["name"].lower()))
    return {"nodes": ordered, "edges": edges}


def connections_of(centre_id: str, network: Dict[str, Any]) -> List[dict]:
    """The centre's direct connections, for the card's rail."""
    names = {n["id"]: n for n in network["nodes"]}
    out = []
    for edge in network["edges"]:
        if centre_id not in (edge["from"], edge["to"]):
            continue
        other = edge["to"] if edge["from"] == centre_id else edge["from"]
        if other in names and other != centre_id:
            out.append({"label": edge["label"], "node": names[other]})
    return out


# ── history and documents ─────────────────────────────────────────────────

_EVENT_LABELS = {
    "created": "gesetzt", "create": "gesetzt", "inserted": "gesetzt", "asserted": "gesetzt",
    "updated": "geändert", "update": "geändert", "changed": "geändert",
    "deleted": "geleert", "delete": "geleert", "removed": "geleert",
    "adopted": "übernommen", "confirmed": "bestätigt", "rejected": "verworfen",
    "proposed": "vorgeschlagen", "superseded": "ersetzt",
}


def format_timestamp(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    return moment.strftime("%d.%m.%Y, %H:%M")


def history(node: dict, facts: Sequence[dict], histories: Dict[str, Optional[list]],
            attribute_names: Dict[str, str], people: Dict[str, str]) -> Dict[str, Any]:
    """Every recorded event of an entry, newest first.

    ``available`` is False when the API answered no history for any fact: the
    page then says the history is not offered, instead of an empty list that
    reads as "nothing ever happened".
    """
    events = []
    for fact in facts:
        rows = histories.get(str(fact.get("id")))
        if not rows:
            continue
        field = attribute_names.get(str(fact.get("attribute_id") or "")) \
            or str(fact.get("label") or "Feld")
        for row in rows:
            kind = str(_first(row, "event_type", "type", "action", default="")).lower()
            actor_ref = str(_first(row, "actor_ref", "actor_id", default=""))
            actor = str(_first(row, "actor", default=""))
            who = people.get(actor_ref) or people.get(actor) or (
                "Knovas" if str(row.get("actor_kind") or "") in ("system", "service") else actor)
            when = _first(row, "occurred_at", "created_at", "at", default="")
            events.append({"at": str(when), "when": format_timestamp(when), "who": who or "—",
                           "field": field, "what": _EVENT_LABELS.get(kind, kind or "geändert")})
    available = any(histories.get(str(f.get("id"))) is not None for f in facts)
    created = _first(node, "created_at", default="")
    if created:
        events.append({"at": str(created), "when": format_timestamp(created), "who": "—",
                       "field": "", "what": "Eintrag angelegt"})
    events.sort(key=lambda e: e["at"], reverse=True)
    return {"available": available or not facts, "events": events}


_DOC_KINDS = {"pdf": "PDF", "doc": "Word", "docx": "Word", "xls": "Excel", "xlsx": "Excel",
              "eml": "E-Mail", "msg": "E-Mail", "txt": "Text", "md": "Text", "html": "HTML"}


def documents(detail: Optional[dict]) -> List[dict]:
    """The documents assigned to an entry, readable rather than raw pointers."""
    out, seen = [], set()
    for key in ("assignments", "knowledge", "documents", "pointers"):
        items = (detail or {}).get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            pointer = item if isinstance(item, str) else str(
                _first(item, "pointer", "identifier", "document_id"))
            if not pointer or pointer in seen:
                continue
            seen.add(pointer)
            base = pointer.replace("\\", "/").rstrip("/").split("/")[-1]
            title = str(_first(item, "title", default="")) if isinstance(item, dict) else ""
            ext = os.path.splitext(base)[1].lstrip(".").lower()
            meta = [_DOC_KINDS.get(ext, ext.upper()) if ext else ""]
            if isinstance(item, dict):
                meta.append(format_date(str(_first(item, "created_at", "assigned_at",
                                                   default=""))[:10] or None))
            out.append({"pointer": pointer, "title": title or base or pointer,
                        "meta": " · ".join(m for m in meta if m)})
        if out:
            break
    return out
