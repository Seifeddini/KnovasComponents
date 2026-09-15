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
# float() accepts "nan", "Infinity" and "1_000". A money fact is a number in a
# document, so the grammar is spelled out rather than delegated.
_MONEY_AMOUNT = re.compile(r"^-?\d+(\.\d+)?$")

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

    Never raises and never renders a day it was not given. This is the read
    side of decode()'s tolerance: anything it cannot render faithfully — an
    unknown precision, a shape-valid date that is not a real one — comes back
    as the stored ISO string, which is both true and visibly unusual.
    """
    if value is None:
        # An absent fact renders as nothing. "None" on the screen is a Python
        # repr leaking into a document.
        return ""
    decoded = decode("date", value)
    if not isinstance(decoded, dict):
        return str(value)
    raw = str(decoded.get("value") or "")
    # Case and padding are tolerated on the way in; the day fallback is not.
    precision = str(decoded.get("precision") or "day").strip().lower()
    if not _ISO_DATE.match(raw):
        return raw
    try:
        parsed = _date.fromisoformat(raw)
    except ValueError:
        # 2026-13-04, 2026-02-31: the digits fit, the calendar does not.
        # _MONTHS[12] would raise and _MONTHS[-1] would invent "Dezember".
        return raw
    if precision == "year":
        return f"{parsed.year:04d}"
    if precision == "month":
        return f"{_MONTHS[parsed.month - 1]} {parsed.year:04d}"
    if precision == "day":
        return f"{parsed.day:02d}.{parsed.month:02d}.{parsed.year:04d}"
    # An unrecognised precision ("quarter", "hour", a typo) is emphatically not
    # a day. encode() refuses those, but decode() exists to accept payloads
    # written elsewhere, so this branch is reachable — and rendering it as an
    # exact day is the one thing this function must never do.
    return raw


def _text(raw: Any) -> str:
    # Runs of spaces within a line are tidied; the line breaks are not. A
    # multi-line note folded into one line at write time cannot be recovered,
    # and the user typed the breaks on purpose.
    lines = [" ".join(line.split()) for line in str(raw if raw is not None else "").splitlines()]
    text = "\n".join(lines).strip("\n")
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
    given = raw.get("amount")
    # Not `or ""`: a Betrag of 0 is a number, and falsiness would refuse it.
    # A bool is not one, though Python would happily format it as 1.
    if given is None or isinstance(given, bool):
        raise FactValueError("Betrag muss eine Zahl sein.")
    amount = str(given).strip().replace("'", "").replace("\u2019", "")
    currency = str(raw.get("currency") or "").strip().upper()
    if not _MONEY_AMOUNT.match(amount):
        raise FactValueError("Betrag muss eine Zahl sein.")
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
    if isinstance(raw, str):
        node_id: Any = raw
    elif isinstance(raw, dict):
        node_id = raw.get("node_id")
    elif raw is None:
        node_id = None
    else:
        # A list or a number here is a malformed body, not a missing value.
        # Without the guard `.get` raises AttributeError, and the routes in D3
        # surface FactValueError's message verbatim — an AttributeError would
        # be a 500 where the user should read one German sentence.
        raise FactValueError("Verknüpfung erwartet eine Knoten-Id.")
    if node_id is not None and not isinstance(node_id, str):
        raise FactValueError("Verknüpfung erwartet eine Knoten-Id.")
    node_id = str(node_id or "").strip()
    if not node_id:
        raise FactValueError("Verknüpfung braucht einen Knoten.")
    return {"node_id": node_id}
