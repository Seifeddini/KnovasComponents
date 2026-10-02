"""Document fields: what the Platform shows of them, as pure functions.

Everything here is a pure function over what Knovas returned and what the
person typed. No request, no Flask, no cache: the honesty rules of the
document-fields integration (spec 2.4, H1-H9) are decided in Python so a
pytest can pin each one, because there is no JS test runner.

Two rules run through the whole module:

- **A filter is honest or absent.** ``filter_state`` says "applied" only when
  Knovas echoed ``where.applied is True``; anything else is "not_applied",
  and the caller withholds the results.
- **Values never leave the data path.** Nothing here logs. Messages name a
  field by its label or key and never repeat a value; chips repeat the
  person's own input back to that person only.

This file is ASCII-only (scripts/check_ascii_py.py): umlauts are ``\\u``
escapes.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from datetime import date
from pathlib import PurePosixPath
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

# The anchor keys Knovas keeps on every document; they are not registry rows
# (contract 6). ``title`` and ``description`` are editable, the others not.
SYSTEM_KEYS = ("title", "description", "path", "ingested_at")

# Server caps that the Platform mirrors so a request it builds is never one
# the server refuses for its size (planner.py:114, listing.py).
RETURN_FIELDS_MAX = 64
TITLE_FROM_VALUES_MAX = 100
WHERE_MAX_KEYS = 8
WHERE_MAX_DEPTH = 3
WHERE_MAX_BYTES = 8 * 1024
_WHERE_KEY_RE = re.compile(r"^[a-z0-9_.\- ]{1,64}$")

# The template key syntax of spec 3.4 (also the registry key syntax).
_TEMPLATE_KEY_RE = re.compile(r"\{([a-z][a-z0-9_]{0,63})\}")

_LABEL_LANGS = ("de", "fr", "it", "en")

# Extractor metadata items -> the registry key they fill (spec 3.5).
METADATA_TARGETS: Dict[str, str] = {
    "language": "language",
    "email_date": "document_date",
    "email_doc_type": "doc_type",
    "email_author": "author",
    "document_author": "author",
}

# Texts the UI shows next to fields (spec 2.4 H9, 4.5). Kept here so the
# wording is pinned by tests rather than scattered over templates.
INCOMPLETE_LISTING = "Liste unvollst\u00e4ndig \u2013 Filter eingrenzen"
TITLE_NOT_SEARCHABLE = "Titel wird angezeigt, nicht durchsucht"
PRIVILEGED_HINT = "Kennzeichnung, keine Zugriffsbeschr\u00e4nkung"
DEADLINE_BANNER = (
    "Nur Dokumente mit erfasstem Fristfeld, die f\u00fcr Sie sichtbar sind "
    "\u2013 keine Fristenkontrolle"
)
HIDDEN_ENTITY = "verborgen"
LINKED_ENTITY = "verkn\u00fcpfter Eintrag"

_MONTHS_DE = (
    "Januar", "Februar", "M\u00e4rz", "April", "Mai", "Juni", "Juli",
    "August", "September", "Oktober", "November", "Dezember",
)

_LAYER_LABELS = {
    "manual": "Manuell",
    "upload": "Upload",
    "rule": "Ordnervorgabe",
    "extracted": "Extrahiert",
}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def _label(labels: Any, fallback: str, lang: str = "de") -> str:
    """The label in ``lang``, then de, fr, it, en, then ``fallback``."""
    if isinstance(labels, Mapping):
        order = [lang] + [code for code in _LABEL_LANGS if code != lang]
        for code in order:
            text = labels.get(code)
            if isinstance(text, str) and text.strip():
                return text.strip()
    return fallback


def sanitize_registry(fields: Any, lang: str = "de") -> List[Dict[str, Any]]:
    """The registry as the Platform and the browser see it.

    ``[{key, label, datatype, cardinality, display, facet, sensitivity,
    enum: [{code, label}], has_target, status, date_role}]``, in the order
    Knovas returned it (sorted by key). The target node type id stays out:
    the browser needs to know whether a field has suggestions, not which
    type backs them. ``date_role`` is carried so a sort on a "due" field can
    show the deadline banner (H9).

    Anything other than an explicit ``normal`` sensitivity reads as
    ``special``: a field the Platform cannot classify is treated as the one
    that must not reach a card.
    """
    out: List[Dict[str, Any]] = []
    for raw in fields or ():
        if not isinstance(raw, Mapping):
            continue
        key = raw.get("key")
        if not isinstance(key, str) or not key:
            continue
        enum: List[Dict[str, str]] = []
        for item in raw.get("enum_values") or ():
            if isinstance(item, str) and item:
                enum.append({"code": item, "label": item})
            elif isinstance(item, Mapping) and isinstance(item.get("code"), str):
                code = item["code"]
                enum.append({"code": code, "label": _label(item.get("labels"), code, lang)})
        sensitivity = raw.get("sensitivity")
        date_role = raw.get("date_role")
        out.append({
            "key": key,
            "label": _label(raw.get("labels"), key, lang),
            "datatype": str(raw.get("datatype") or ""),
            "cardinality": "many" if raw.get("cardinality") == "many" else "one",
            "display": raw.get("display") is True,
            "facet": raw.get("facet") is True,
            "sensitivity": "normal" if sensitivity in (None, "normal") else "special",
            "enum": enum,
            "has_target": bool(raw.get("target_node_type_id")),
            "status": str(raw.get("status") or "active"),
            "date_role": date_role if isinstance(date_role, str) else None,
        })
    return out


def registry_targets(fields: Any) -> Dict[str, str]:
    """``{key: target_node_type_id}`` for entity fields whose target the
    caller can see. Kept apart from ``sanitize_registry`` so the id never
    reaches the browser."""
    out: Dict[str, str] = {}
    for raw in fields or ():
        if not isinstance(raw, Mapping):
            continue
        key, target = raw.get("key"), raw.get("target_node_type_id")
        if isinstance(key, str) and key and target:
            out[key] = str(target)
    return out


def _by_key(registry: Any) -> Dict[str, Mapping[str, Any]]:
    if isinstance(registry, Mapping):
        return {str(k): v for k, v in registry.items() if isinstance(v, Mapping)}
    return {
        str(f["key"]): f for f in registry or ()
        if isinstance(f, Mapping) and isinstance(f.get("key"), str)
    }


def field_label(registry: Any, key: str) -> str:
    """A field's label, or its key when the registry does not know it."""
    spec = _by_key(registry).get(str(key))
    if spec is not None and isinstance(spec.get("label"), str) and spec["label"]:
        return spec["label"]
    return str(key)


def card_return_fields(registry: Any) -> List[str]:
    """The ``return_fields`` list a search or listing asks for.

    Fields that are not deprecated, have ``display`` set and are not
    ``special``, plus ``title``; at most 64 keys in all (planner.py:114).
    ``title`` always fits: it is placed first and the field keys fill the
    rest. A special field is never on this list, so its values never reach
    a card.
    """
    keys: List[str] = ["title"]
    for spec in registry or ():
        if not isinstance(spec, Mapping):
            continue
        key = spec.get("key")
        if (not isinstance(key, str) or key in SYSTEM_KEYS
                or spec.get("status") == "deprecated"
                or spec.get("display") is not True
                or spec.get("sensitivity") != "normal"):
            continue
        if key not in keys:
            keys.append(key)
        if len(keys) >= RETURN_FIELDS_MAX:
            break
    return keys


# ---------------------------------------------------------------------------
# Formatting values
# ---------------------------------------------------------------------------

def _iso_date(value: Any) -> Optional[date]:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _day(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def _interval_text(lo: Optional[date], hi: Optional[date]) -> str:
    if lo is None and hi is None:
        return ""
    if lo is None or hi is None or lo == hi:
        return _day(lo or hi)  # type: ignore[arg-type]
    return f"{_day(lo)} \u2013 {_day(hi)}"


def _format_date(value: Mapping[str, Any]) -> str:
    lo, hi = _iso_date(value.get("lo")), _iso_date(value.get("hi"))
    precision = value.get("precision")
    if lo is None:
        return _interval_text(lo, hi)
    if precision == "day":
        return _day(lo)
    if precision == "month":
        return f"{_MONTHS_DE[lo.month - 1]} {lo.year}"
    if precision == "quarter":
        return f"Q{(lo.month - 1) // 3 + 1} {lo.year}"
    if precision == "year":
        return str(lo.year)
    return _interval_text(lo, hi)


def _format_period(value: Mapping[str, Any]) -> str:
    label = value.get("label")
    if isinstance(label, str) and label.strip():
        return label.strip()
    return _interval_text(_iso_date(value.get("lo")), _iso_date(value.get("hi")))


def _group_thousands(raw: Any, min_fraction: int = 0) -> str:
    """``1234.5`` -> ``1'234.50`` (min_fraction 2): the Swiss apostrophe."""
    try:
        number = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        return str(raw)
    if not number.is_finite():
        return str(raw)
    sign = "-" if number < 0 else ""
    text = format(abs(number), "f")
    whole, _, fraction = text.partition(".")
    if len(fraction) < min_fraction:
        fraction += "0" * (min_fraction - len(fraction))
    groups: List[str] = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    return sign + "'".join(groups) + (f".{fraction}" if fraction else "")


def _format_money(value: Mapping[str, Any]) -> str:
    amount = _group_thousands(value.get("amount"), min_fraction=2)
    currency = value.get("currency")
    return f"{currency} {amount}" if isinstance(currency, str) and currency else amount


def _format_entity(value: Any) -> str:
    if isinstance(value, Mapping):
        if value.get("hidden") is True:
            return HIDDEN_ENTITY
        name = value.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
        if value.get("node_id"):
            # A value written as a node id carries no name of its own
            # (values_service.display_value); the id itself is never shown.
            return LINKED_ENTITY
        return ""
    if isinstance(value, str):
        return value.strip()
    return ""


def _format_code(value: Any) -> str:
    if isinstance(value, Mapping):
        text = value.get("value")
        if not isinstance(text, (str, int)):
            return ""
        text = str(text)
        return f"{text} MWST" if value.get("vat") is True else text
    return "" if value is None else str(value)


def _enum_label(spec: Optional[Mapping[str, Any]], code: Any) -> str:
    if not isinstance(code, str):
        return "" if code is None else str(code)
    for item in (spec or {}).get("enum") or ():
        if isinstance(item, Mapping) and item.get("code") == code:
            label = item.get("label")
            return label if isinstance(label, str) and label else code
    # H4: a missing label falls back to the code.
    return code


def _format_bool(value: Any) -> str:
    return "Ja" if value is True else "Nein" if value is False else ""


def _format_guess(value: Any) -> str:
    """A value whose field the registry does not (or no longer) knows."""
    if isinstance(value, bool):
        return _format_bool(value)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        if value.get("hidden") is True or "name" in value or "node_id" in value:
            return _format_entity(value)
        if "amount" in value:
            return _format_money(value)
        if "precision" in value:
            return _format_date(value)
        if "lo" in value or "hi" in value:
            return _format_period(value)
        if "value" in value:
            return _format_code(value)
    return ""


def _format_one(spec: Optional[Mapping[str, Any]], value: Any) -> str:
    datatype = (spec or {}).get("datatype")
    if value is None:
        return ""
    if datatype == "date" and isinstance(value, Mapping):
        return _format_date(value)
    if datatype == "period" and isinstance(value, Mapping):
        return _format_period(value)
    if datatype == "money" and isinstance(value, Mapping):
        return _format_money(value)
    if datatype == "number" and isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return _group_thousands(value)
    if datatype == "enum":
        return _enum_label(spec, value)
    if datatype == "bool":
        return _format_bool(value)
    if datatype == "entity_ref":
        return _format_entity(value)
    if datatype == "code":
        return _format_code(value)
    if datatype == "text":
        return value.strip() if isinstance(value, str) else str(value)
    return _format_guess(value)


def format_value(field: Optional[Mapping[str, Any]], value: Any) -> str:
    """One field value as text: ``15.03.2024``, ``M\\u00e4rz 2024``,
    ``Q1 2024``, ``2024``, a period's label, ``CHF 1'234.50``, an enum label
    (or its code), ``Ja``/``Nein``, an entity name or ``verborgen``. Several
    values are joined with ``; ``. ``field`` is a ``sanitize_registry``
    entry, or None for a key the registry does not know."""
    if isinstance(value, (list, tuple)):
        parts = [_format_one(field, item) for item in value]
        return "; ".join(part for part in parts if part)
    return _format_one(field, value)


def fields_display(registry: Any, fields: Any, *,
                   include_special: bool = False) -> List[Dict[str, str]]:
    """``[{key, label, text}]`` for a card, in registry order, without the
    title (it is the card's heading) and without empty values.

    Only what Knovas returned is shown (H4). ``special`` fields are left out
    unless asked for: they are never requested for cards, and a value that
    arrives anyway still does not belong on one.
    """
    if not isinstance(fields, Mapping) or not fields:
        return []
    specs = _by_key(registry)
    ordered = [key for key in specs if key in fields]
    ordered += sorted(str(key) for key in fields if str(key) not in specs)
    out: List[Dict[str, str]] = []
    for key in ordered:
        if key == "title":
            continue
        spec = specs.get(key)
        if spec is not None and spec.get("sensitivity") != "normal" and not include_special:
            continue
        text = format_value(spec, fields.get(key))
        if text:
            out.append({"key": key, "label": field_label(specs, key), "text": text})
    return out


def layer_label(layer: Any) -> str:
    """``Manuell``, ``Upload``, ``Ordnervorgabe`` or ``Extrahiert``.

    Path-template captures and static per-source values both arrive in the
    upload layer (commit.py:739), so there is deliberately no badge that
    claims to know which of the two set a value.
    """
    return _LAYER_LABELS.get(str(layer or ""), str(layer or ""))


# ---------------------------------------------------------------------------
# Titles
# ---------------------------------------------------------------------------

def title_from_values(pointer: Any, fields_title: Any) -> Optional[str]:
    """The values title when it is a real title, else None.

    Real means: a string of at most 100 characters that is neither the
    pointer's file name nor its stem (case-insensitive). Knovas falls back
    to the path basename when no title was stored (return_fields.py:102-106)
    and the RemoteController sends ``file_path.name`` when nothing was
    extracted, so a basename here says nothing a file name does not.
    """
    if not isinstance(fields_title, str):
        return None
    title = fields_title.strip()
    if not title or len(title) > TITLE_FROM_VALUES_MAX:
        return None
    path = str(pointer or "").strip().replace("\\", "/").rstrip("/")
    base = path.rsplit("/", 1)[-1] if path else ""
    if base:
        folded = title.casefold()
        if folded in (base.casefold(), PurePosixPath(base).stem.casefold()):
            return None
    return title


def display_title(pointer: Any, hit_title: Any, fields_title: Any) -> Tuple[str, bool]:
    """``(title, title_from_values)`` for a result row (spec 4.1).

    The values title wins when ``title_from_values`` accepts it; otherwise
    the long-standing ``_display_title_for_hit`` rule runs on the hit's own
    title, exactly as before.
    """
    title = title_from_values(pointer, fields_title)
    if title is not None:
        return title, True
    # Imported here: knovas_client imports this module at load time.
    from knovas_client import _display_title_for_hit

    return _display_title_for_hit(str(pointer or ""), hit_title), False


def find_row(document: Any, registry: Any) -> Dict[str, Any]:
    """One ``find`` document in the shape of a search result row.

    ``doc_id`` and ``path`` are the pointer, as on search rows. The title
    follows the same rule as search: the document's values title (or the
    anchor title ``find`` returns) when it is a real title, else the file
    name stem -- so a document reads the same in the listing and in search.
    """
    document = document if isinstance(document, Mapping) else {}
    pointer = str(document.get("pointer") or "")
    fields = document.get("fields") if isinstance(document.get("fields"), Mapping) else None
    candidate = (fields or {}).get("title") or document.get("title")
    title, from_values = display_title(pointer, None, candidate)
    row: Dict[str, Any] = {"doc_id": pointer, "path": pointer, "title": title,
                           "title_from_values": from_values, "source": "semantix"}
    if document.get("document_uuid"):
        row["document_uuid"] = str(document["document_uuid"])
    if fields is not None:
        row["fields"] = dict(fields)
        row["fields_display"] = fields_display(registry, fields)
    return row


def match_names(names: Iterable[str], typed: Any, limit: int = 10) -> List[str]:
    """Entity suggestions for what the person typed, filtered here.

    Prefix matches first, then substring matches, casefolded; at most
    ``limit``; nothing below two characters. The typed text never leaves the
    Platform (D10): the names come from a node list fetched without ``q``.
    """
    needle = str(typed or "").strip().casefold()
    if len(needle) < 2:
        return []
    pool = [n for n in names or () if isinstance(n, str)]
    prefix = [n for n in pool if n.casefold().startswith(needle)]
    inner = [n for n in pool if needle in n.casefold() and not n.casefold().startswith(needle)]
    return (prefix + inner)[:max(0, int(limit))]


# ---------------------------------------------------------------------------
# Honesty: filter state, listing notice, resolved chips
# ---------------------------------------------------------------------------

def filter_state(where_sent: Any, meta: Any) -> str:
    """``none``, ``applied``, ``partial`` or ``not_applied`` (H2).

    ``where_sent`` is the filter that went out (or a bool). ``meta`` holds
    the echo under ``where``: the search's ``semantix`` block or a find page.
    Only ``where.applied is True`` counts; a missing echo, ``applied`` in any
    other spelling, or a non-object echo is ``not_applied``, and the caller
    withholds the results.
    """
    if not where_sent:
        return "none"
    echo = meta.get("where") if isinstance(meta, Mapping) else None
    if isinstance(echo, Mapping) and echo.get("applied") is True:
        return "partial" if echo.get("may_be_partial") is True else "applied"
    return "not_applied"


def listing_notice(page: Any) -> Dict[str, Any]:
    """The H5 truth table for one find page.

    ``incomplete`` (and its text) only when the walk is over -- ``next_after
    is None`` -- and Knovas says ``complete is False``: every page that has a
    successor also says ``complete: false`` (listing.py:405-406), and showing
    the notice there would cry wolf on every page. ``total_count`` only when
    it is an int; it is null on overflow and first-page only.
    """
    page = page if isinstance(page, Mapping) else {}
    incomplete = page.get("next_after") is None and page.get("complete") is False
    total = page.get("total_count")
    return {
        "incomplete": incomplete,
        "text": INCOMPLETE_LISTING if incomplete else None,
        "total_count": total if isinstance(total, int) and not isinstance(total, bool) else None,
    }


_OP_PREFIX = {
    "gt": "nach ", "gte": "ab ", "lt": "vor ", "lte": "bis ",
    "overlaps": "\u00fcberschneidet ", "within": "innerhalb ",
    "prefix": "beginnt mit ",
}


def _operand_text(spec: Optional[Mapping[str, Any]], operand: Any) -> str:
    """The person's own operand as text: enum codes become labels, entity
    operands their name. A ``node_id`` operand is never shown as the id."""
    if isinstance(operand, (list, tuple)):
        parts = [_operand_text(spec, item) for item in operand]
        return "; ".join(part for part in parts if part)
    if isinstance(operand, Mapping):
        if "name" in operand or "node_id" in operand:
            return _format_entity(operand)
        parts: List[str] = []
        if "gte" in operand and "lte" in operand:
            parts.append(f"{_operand_text(spec, operand['gte'])} \u2013 "
                         f"{_operand_text(spec, operand['lte'])}")
        for op in ("eq", "in", "gt", "gte", "lt", "lte", "between", "overlaps",
                   "within", "prefix", "exists"):
            if op not in operand or (op in ("gte", "lte") and "gte" in operand and "lte" in operand):
                continue
            value = operand[op]
            if op == "exists":
                parts.append("vorhanden")
            elif op == "between" and isinstance(value, (list, tuple)) and len(value) == 2:
                parts.append(f"{_operand_text(spec, value[0])} \u2013 {_operand_text(spec, value[1])}")
            else:
                parts.append(_OP_PREFIX.get(op, "") + _operand_text(spec, value))
        return ", ".join(part for part in parts if part)
    if isinstance(operand, bool):
        return _format_bool(operand)
    if spec is not None and spec.get("datatype") == "enum":
        return _enum_label(spec, operand)
    return "" if operand is None else str(operand).strip()


def _operand_op(operand: Any) -> str:
    if isinstance(operand, (list, tuple)):
        return "in"
    if isinstance(operand, Mapping):
        ops = [op for op in operand if op not in ("name", "node_id")]
        return ops[0] if len(ops) == 1 else ("eq" if not ops else "range")
    return "eq"


def _linked_text(count: int) -> str:
    return (f"{count} verkn\u00fcpfter Eintrag" if count == 1
            else f"{count} verkn\u00fcpfte Eintr\u00e4ge")


def resolved_chips(where_sent: Any, echo: Any, registry: Any) -> List[Dict[str, Any]]:
    """The "Verstanden als" chips: one per key of the person's own filter.

    The text is built from what the person submitted (enum codes shown as
    labels), not from the server's parsed form, so "GJ 2024" stays "GJ
    2024". From the echo only the count of linked entries is taken
    (``resolved_nodes``, planner.py:238-240): Knovas returns a number there,
    never names or ids.
    """
    if not isinstance(where_sent, Mapping):
        return []
    specs = _by_key(registry)
    resolved = echo.get("resolved") if isinstance(echo, Mapping) else None
    entries = [e for e in resolved or () if isinstance(e, Mapping)]
    chips: List[Dict[str, Any]] = []
    for key, operand in where_sent.items():
        key = str(key)
        spec = specs.get(key)
        mine = [e for e in entries if e.get("field") == key]
        op = mine[0].get("op") if len(mine) == 1 and isinstance(mine[0].get("op"), str) \
            else _operand_op(operand)
        chip: Dict[str, Any] = {
            "field": key,
            "label": field_label(specs, key),
            "op": op,
            "text": _operand_text(spec, operand),
        }
        counts = [e.get("resolved_nodes") for e in mine
                  if isinstance(e.get("resolved_nodes"), int)
                  and not isinstance(e.get("resolved_nodes"), bool)]
        if counts:
            chip["linked_count"] = sum(counts)
            chip["linked_text"] = _linked_text(chip["linked_count"])
        chips.append(chip)
    return chips


# ---------------------------------------------------------------------------
# Input bounds and permissions
# ---------------------------------------------------------------------------

def _depth(value: Any) -> int:
    """Container nesting below an operand: ``"x"`` is 0, ``{"name": "x"}``
    is 1, ``{"in": [{"name": "x"}]}`` is 3."""
    if isinstance(value, Mapping):
        return 1 + max((_depth(v) for v in value.values()), default=0)
    if isinstance(value, (list, tuple)):
        return 1 + max((_depth(v) for v in value), default=0)
    return 0


def validate_where(obj: Any) -> Dict[str, Any]:
    """Bound a ``where`` that came from the browser; return it unchanged.

    A non-empty object of at most 8 keys, each matching
    ``^[a-z0-9_.\\- ]{1,64}$``, operands nested at most 3 deep, at most
    8 KiB as compact JSON. Raises ValueError (with a message that names no
    value). Knovas validates the meaning; this only keeps an oversized or
    oddly shaped body from leaving the Platform.
    """
    if not isinstance(obj, Mapping) or isinstance(obj, (str, bytes)):
        raise ValueError("where must be an object")
    if not obj:
        raise ValueError("where must not be empty")
    if len(obj) > WHERE_MAX_KEYS:
        raise ValueError(f"where has more than {WHERE_MAX_KEYS} keys")
    for key, operand in obj.items():
        if not isinstance(key, str) or not _WHERE_KEY_RE.match(key):
            raise ValueError("where has a key that is not a field key")
        if _depth(operand) > WHERE_MAX_DEPTH:
            raise ValueError("where is nested too deeply")
    try:
        size = len(json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        raise ValueError("where is not plain JSON") from None
    if size > WHERE_MAX_BYTES:
        raise ValueError("where is larger than 8 KiB")
    return dict(obj)


def can_edit(roles: Iterable[str], edit_roles: Iterable[str], sensitivity: Any,
             held: bool, identity_on: bool) -> bool:
    """D12: who may change a document's values from the Platform.

    Identity on, the document not held, one of the person's roles in
    ``edit_roles``, and ``admin`` for a ``special`` field. Knovas still
    decides (``authorize_change``); this keeps the Platform from offering a
    form the backend would refuse, and keeps read-only roles read-only in a
    tenant without walls, where the backend would let them write.
    """
    if not identity_on or held:
        return False
    mine = {str(r).strip().lower() for r in roles or () if str(r).strip()}
    allowed = {str(r).strip().lower() for r in edit_roles or () if str(r).strip()}
    if not mine & allowed:
        return False
    if sensitivity != "normal" and "admin" not in mine:
        return False
    return True


def is_deadline_field(registry: Any, key: Any) -> bool:
    """Whether a sort on ``key`` must carry the deadline banner (H9)."""
    spec = _by_key(registry).get(str(key))
    return bool(spec) and spec.get("date_role") == "due"


# ---------------------------------------------------------------------------
# Audit of a value edit (both edit routes: search panel and admin drawer)
# ---------------------------------------------------------------------------

#: Knovas refusals of a value edit that are audited. ``audit_log.outcome``
#: admits only ok / denied / error (0001_identity.sql CHECK). A refusal --
#: a version conflict included -- is the backend saying no as designed, so it
#: is ``denied`` with Knovas's code in ``detail.code``; ``error`` stays for
#: failures (Knovas unreachable, an answer that makes no sense).
VALUES_EDIT_REFUSALS = frozenset({"version_conflict", "change_not_authorized",
                                  "anchor_quarantined"})
AUDIT_OUTCOME_REFUSED = "denied"
_EDIT_OPS = ("set", "unset", "add", "remove")


def values_edit_audit_detail(ops: Mapping[str, Any], *, version_from: Any, version_to: Any,
                             warning_codes: Iterable[str] = (),
                             code: Optional[str] = None) -> Dict[str, Any]:
    """The ``document.values_edited`` detail: keys, counts, versions,
    warning codes and, for a refused edit, Knovas's error code -- never a
    value (spec 4.6).

    ``version_to`` is the version Knovas reported: the new one after an
    edit, the current one with a ``version_conflict``, None otherwise.
    """
    typed: set = set()
    for op in _EDIT_OPS:
        part = ops.get(op) or ()
        for key in (part if isinstance(part, (list, tuple)) else list(part)):
            if key not in ("title", "description"):
                typed.add(str(key))
    set_part = ops.get("set") or {}

    def _version(value: Any) -> Optional[int]:
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    return {
        "keys": sorted(typed),
        "ops": {op: len(ops.get(op) or ()) for op in _EDIT_OPS},
        "title_changed": "title" in set_part,
        "description_changed": "description" in set_part,
        "version_from": _version(version_from),
        "version_to": _version(version_to),
        "warning_codes": sorted({str(c) for c in warning_codes or () if c}),
        **({"code": str(code)} if code else {}),
    }


# ---------------------------------------------------------------------------
# Ingestion profiles
# ---------------------------------------------------------------------------

def _profile_sources(profile: Any) -> Sequence[Any]:
    if isinstance(profile, (str, bytes)):
        try:
            profile = json.loads(profile)
        except ValueError:
            return ()
    inner = getattr(profile, "profile", None)  # a ProfileVersion
    if inner is not None:
        profile = inner
    if isinstance(profile, Mapping):
        sources = profile.get("sources")
    else:
        sources = getattr(profile, "sources", None)
    return sources if isinstance(sources, (list, tuple)) else ()


def _source_attr(source: Any, name: str) -> Any:
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def profile_field_keys(profile_json: Any) -> set:
    """Every registry key a stored ingestion profile writes.

    Static keys, path-template capture keys (spec 3.4 key syntax) and the
    target keys of its metadata items. Reads the stored JSON shape of spec
    4.8 (``sources[].fields`` an object, ``field_templates`` and
    ``metadata_fields`` lists) and, for convenience, the dataclass form
    (``fields`` as sorted pairs) or a ``ProfileVersion``. Anything it cannot
    read contributes nothing.
    """
    keys: set = set()
    for source in _profile_sources(profile_json):
        static = _source_attr(source, "fields")
        if isinstance(static, Mapping):
            keys.update(str(k) for k in static)
        elif isinstance(static, (list, tuple)):
            for pair in static:
                if isinstance(pair, (list, tuple)) and pair and isinstance(pair[0], str):
                    keys.add(pair[0])
        for template in _source_attr(source, "field_templates") or ():
            if isinstance(template, str):
                keys.update(_TEMPLATE_KEY_RE.findall(template))
        for item in _source_attr(source, "metadata_fields") or ():
            target = METADATA_TARGETS.get(str(item))
            if target:
                keys.add(target)
    return keys


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

# Per-key warnings Knovas returns on a non-strict edit or upload.
_WARNINGS = {
    "unresolved_entity": "nicht verkn\u00fcpft",
    "ambiguous_entity": "mehrere passende Eintr\u00e4ge \u2013 nicht verkn\u00fcpft",
    "ambiguous_date": "Datum mehrdeutig \u2013 bitte pr\u00fcfen",
    "unknown_field": "Feld bei Knovas nicht bekannt",
    "invalid_value": "Wert ung\u00fcltig, nicht \u00fcbernommen",
    "type_mismatch": "Wert passt nicht zum Feldtyp, nicht \u00fcbernommen",
    "checksum_failed": "Pr\u00fcfziffer stimmt nicht, nicht \u00fcbernommen",
    "restricted_identifier": "AHV-Nummern werden nicht gespeichert",
    "cap_exceeded": "zu viele Werte, nicht alle \u00fcbernommen",
    "key_looks_personal": "Schl\u00fcssel sieht nach Personendaten aus",
}


def warning_text(code: Any) -> str:
    """The note shown next to a field for one of Knovas's warning codes."""
    return _WARNINGS.get(str(code or ""), str(code or ""))


def _path_key(path: Any) -> Optional[str]:
    """``where.mandant`` / ``set.doc_type[0]`` / ``fields.x`` -> the key."""
    if not isinstance(path, str) or "." not in path:
        return None
    key = path.split(".", 1)[1]
    key = re.sub(r"\[\d+\]$", "", key)
    return key or None


def _quoted(text: str) -> str:
    return f"\u201e{text}\u201c"


def error_message(code: Any, details: Any = None, registry: Any = None) -> str:
    """German text for a Knovas doc-fields error code, or a Platform one.

    A field is named by its label (or key) from ``details.path``; a value is
    never repeated, because the person's input is not what went wrong in a
    way they cannot see, and the message may end up in a log.
    """
    code = str(code or "")
    details = details if isinstance(details, Mapping) else {}
    path = details.get("path")
    key = _path_key(path)
    field = _quoted(field_label(registry, key)) if key else None

    def _labels(keys: Any) -> str:
        names = [field_label(registry, k) for k in keys or () if isinstance(k, str)]
        return ", ".join(_quoted(n) for n in names)

    if code == "invalid_value" and path == "pointer":
        return ("Knovas-Update n\u00f6tig: der Server liest den Dokumentverweis "
                "noch nicht aus dem Anfragek\u00f6rper.")
    if code in ("where_unsupported", "filters_unavailable"):
        return "Filter sind bei Knovas f\u00fcr diesen Mandanten nicht freigeschaltet."
    if code in ("where_requires_calibration", "filters_need_calibration"):
        # H9: missing calibration is a setup step at Knovas, not an outage.
        return ("Filter in der Suche sind bei Knovas noch nicht eingerichtet "
                "(Kalibrierung fehlt).")
    if code in ("where_unavailable", "filter_temporarily_unavailable"):
        return "Filter sind bei Knovas gerade nicht verf\u00fcgbar. Bitte sp\u00e4ter erneut versuchen."
    if code == "filter_not_applied":
        return ("Knovas hat nicht best\u00e4tigt, dass der Filter angewendet wurde; "
                "deshalb werden keine Ergebnisse angezeigt.")
    if code == "unknown_field":
        text = f"Das Feld {field} ist bei Knovas nicht bekannt." if field \
            else "Ein Feld ist bei Knovas nicht bekannt."
        suggest = details.get("suggest")
        if isinstance(suggest, (list, tuple)) and suggest:
            text += f" Meinten Sie {_labels(suggest)}?"
        return text
    if code == "ambiguous_field":
        text = f"Der Feldname {field} passt auf mehrere Felder." if field \
            else "Ein Feldname passt auf mehrere Felder."
        candidates = details.get("candidates")
        if isinstance(candidates, (list, tuple)) and candidates:
            text += f" In Frage kommen {_labels(candidates)}."
        return text
    if code in ("invalid_value", "type_mismatch", "checksum_failed"):
        what = {
            "invalid_value": "ist ung\u00fcltig",
            "type_mismatch": "passt nicht zum Feldtyp (z. B. Betrag ohne W\u00e4hrung)",
            "checksum_failed": "hat eine falsche Pr\u00fcfziffer",
        }[code]
        return f"Der Wert f\u00fcr {field} {what}." if field else f"Ein Wert {what}."
    if code == "restricted_identifier":
        return "AHV-Nummern d\u00fcrfen weder gespeichert noch gesucht werden."
    if code in ("ambiguous_date", "unresolved_entity", "ambiguous_entity"):
        note = warning_text(code)
        return f"{field}: {note}" if field else note
    if code == "where_too_complex":
        return f"Der Filter ist zu umfangreich (h\u00f6chstens {WHERE_MAX_KEYS} Felder)."
    if code == "invalid_cursor":
        return "Die Liste hat sich ge\u00e4ndert. Bitte neu laden."
    if code == "version_conflict":
        return "Die Werte wurden inzwischen ge\u00e4ndert. Bitte die aktuelle Fassung pr\u00fcfen."
    if code == "change_not_authorized":
        return "Sie d\u00fcrfen die Werte dieses Dokuments nicht \u00e4ndern."
    if code == "anchor_quarantined":
        return ("Werte zur\u00fcckgehalten: die Zugriffseinstellungen dieses Dokuments "
                "stimmen noch nicht \u00fcberein.")
    if code in ("fields_too_large", "invalid_fields", "if_version_required"):
        return "Die \u00c4nderung ist zu umfangreich oder unvollst\u00e4ndig."
    if code == "registry_write_requires_full_clearance":
        return ("Nur Mitglieder der Knovas-Administratorgruppe d\u00fcrfen "
                "Dokumentfelder \u00e4ndern.")
    if code == "key_looks_personal":
        return "Der Schl\u00fcssel sieht nach Personendaten aus und wird nicht angenommen."
    if code == "field_key_exists":
        return "Ein Feld mit diesem Schl\u00fcssel gibt es bereits."
    if code == "field_type_locked":
        return "Der Typ dieses Feldes kann nicht mehr ge\u00e4ndert werden."
    if code == "field_cap_reached":
        return "Die H\u00f6chstzahl an Feldern ist erreicht."
    if code == "invalid_field_definition":
        return (f"Die Felddefinition ist ung\u00fcltig ({_quoted(str(path))})."
                if isinstance(path, str) and path else "Die Felddefinition ist ung\u00fcltig.")
    if code == "pack_not_found":
        return "Dieses Feldpaket gibt es bei Knovas nicht."
    if code in ("NOT_FOUND", "document_not_found"):
        return "Nicht gefunden oder f\u00fcr Sie nicht sichtbar."
    if code in ("HTTP_404", "feature_off", "doc_fields_off"):
        return "Dokumentfelder sind bei Knovas nicht freigeschaltet."
    if code in ("doc_fields_unavailable", "doc_fields_ingest_unavailable", "transport_error"):
        return "Dokumentfelder sind bei Knovas gerade nicht verf\u00fcgbar."
    if code == "too_many_requests":
        return "Zu viele Anfragen. Bitte einen Moment warten."
    if code == "assertion_rejected":
        return "Knovas hat die Anmeldung abgelehnt. Bitte neu anmelden."
    return "Die Anfrage an Knovas ist fehlgeschlagen."


_NO_RESULTS = {
    "no_candidates": "Nichts in den f\u00fcr Sie sichtbaren Dokumenten erw\u00e4hnt das.",
    "below_relevance_floor": "Nichts beantwortet das gut genug.",
    "empty_where": "Kein f\u00fcr Sie sichtbares Dokument erf\u00fcllt diese Filter.",
    "empty_scope": "Kein f\u00fcr Sie sichtbares Dokument erf\u00fcllt diese Filter.",
}
NO_RESULTS_GENERIC = "Keine Treffer in den f\u00fcr Sie sichtbaren Dokumenten."


def no_results_message(reason: Any) -> str:
    """The empty-state text per ``no_results_reason`` (H8): always about the
    documents the person can see, never a claim about the whole corpus."""
    return _NO_RESULTS.get(str(reason or ""), NO_RESULTS_GENERIC)
