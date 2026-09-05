"""Fact value shapes for the five schema datatypes.

The shapes belong to the Knowledge Graph API (graph_api.py validates them
server-side); this module is the single place the Platform knows them, so a
malformed value is caught with the field still on screen instead of arriving
as a 422 the user cannot act on.

Deliberately I/O-free: no Flask, no HTTP, no database. That is what makes the
whole datatype surface testable in one fast file.

Design: docs/superpowers/specs/2026-09-02-typed-node-workbench-design.md (7.0)
"""
from __future__ import annotations

import re
from datetime import date as _date
from typing import Any, Optional, Sequence

DATATYPES = ("text", "date", "money", "enum", "entity_ref")
PRECISIONS = ("day", "month", "year")

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_CURRENCY = re.compile(r"^[A-Z]{3}$")

# German month names: the UI is German and a date is rendered, never localised
# at read time by a library the tests would then have to pin.
_MONTHS = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
           "August", "September", "Oktober", "November", "Dezember")


class FactValueError(ValueError):
    """A value does not match its attribute's datatype. Message is for a user."""


def encode(datatype: str, raw: Any, *, enum_values: Optional[Sequence[str]] = None) -> Any:
    if datatype == "text":
        return _text(raw)
    if datatype == "date":
        return _date_value(raw)
    if datatype == "money":
        return _money(raw)
    if datatype == "enum":
        return _enum(raw, enum_values)
    if datatype == "entity_ref":
        return _entity_ref(raw)
    raise FactValueError(f"Unbekannter Datentyp: {datatype}")


def decode(datatype: str, value: Any) -> Any:
    """Payload to a display-ready value.

    Tolerant by design: facts predate this module and a shape written by an
    older path must still render. A read path that raises turns one odd row
    into a blank screen.
    """
    if datatype == "date" and isinstance(value, str):
        return {"value": value, "precision": "day"}
    if datatype == "entity_ref" and isinstance(value, str):
        return {"node_id": value}
    return value


def format_date(value: Any) -> str:
    """Render honouring precision. A month-precision fact must never appear as
    a specific day — that is a fabricated detail in a document a court may see.
    """
    decoded = decode("date", value)
    if not isinstance(decoded, dict):
        return str(value)
    raw = str(decoded.get("value") or "")
    precision = decoded.get("precision") or "day"
    if not _ISO_DATE.match(raw):
        return raw
    year, month, day = raw.split("-")
    if precision == "year":
        return year
    if precision == "month":
        return f"{_MONTHS[int(month) - 1]} {year}"
    return f"{day}.{month}.{year}"


def _text(raw: Any) -> str:
    text = " ".join(str(raw or "").split())
    if not text:
        raise FactValueError("Text darf nicht leer sein.")
    return text


def _date_value(raw: Any) -> dict:
    if isinstance(raw, str):
        raw = {"value": raw}
    if not isinstance(raw, dict):
        raise FactValueError("Datum erwartet {value, precision}.")
    value = str(raw.get("value") or "").strip()
    precision = str(raw.get("precision") or "day").strip()
    if not _ISO_DATE.match(value):
        raise FactValueError("Datum muss im Format JJJJ-MM-TT vorliegen.")
    try:
        _date.fromisoformat(value)
    except ValueError as exc:
        raise FactValueError("Kein gültiges Datum.") from exc
    if precision not in PRECISIONS:
        raise FactValueError("Genauigkeit muss day, month oder year sein.")
    return {"value": value, "precision": precision}


def _money(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise FactValueError("Betrag erwartet {amount, currency}.")
    amount = str(raw.get("amount") or "").strip().replace("'", "")
    currency = str(raw.get("currency") or "").strip().upper()
    try:
        float(amount)
    except ValueError as exc:
        raise FactValueError("Betrag muss eine Zahl sein.") from exc
    if not _ISO_CURRENCY.match(currency):
        raise FactValueError("Währung muss ein ISO-4217-Code sein, z. B. CHF.")
    return {"amount": amount, "currency": currency}


def _enum(raw: Any, enum_values: Optional[Sequence[str]]) -> str:
    if not enum_values:
        raise FactValueError("Für dieses Attribut sind keine Werte definiert.")
    value = str(raw or "").strip()
    if value not in list(enum_values):
        raise FactValueError("Wert ist für dieses Attribut nicht zugelassen.")
    return value


def _entity_ref(raw: Any) -> dict:
    node_id = raw if isinstance(raw, str) else (raw or {}).get("node_id")
    node_id = str(node_id or "").strip()
    if not node_id:
        raise FactValueError("Verknüpfung braucht einen Knoten.")
    return {"node_id": node_id}
