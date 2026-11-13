"""The ``view.json`` manifest of a custom view: parsing and validation."""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Optional, Tuple

from jsonschema import Draft202012Validator

MAX_MANIFEST_BYTES = 64 * 1024

_SCHEMA = json.loads(Path(__file__).with_name("manifest.schema.json").read_text(encoding="utf-8"))
Draft202012Validator.check_schema(_SCHEMA)
_VALIDATOR = Draft202012Validator(_SCHEMA)


class ManifestError(ValueError):
    """The manifest is unusable. The message is German plain text naming the field, never file content."""


@dataclass(frozen=True)
class Manifest:
    id: str
    title: Mapping[str, str]
    description: Optional[Mapping[str, str]]
    order: Optional[float]
    roles: Tuple[str, ...]
    entry: str
    scripts: Tuple[str, ...]
    styles: Tuple[str, ...]
    reads: Mapping[str, Tuple[str, ...]]
    link_hosts: Tuple[str, ...]

    def allows(self, roles: frozenset) -> bool:
        if "*" in self.roles:
            return True
        return bool(set(self.roles) & set(roles))


_SAFE_NAME = re.compile(r"[A-Za-z0-9_-]{1,40}")
_FILE_RE = {
    "entry": re.compile(r"[A-Za-z0-9_-]{1,60}\.html"),
    "scripts": re.compile(r"[A-Za-z0-9_-]{1,60}\.js"),
    "styles": re.compile(r"[A-Za-z0-9_-]{1,60}\.css"),
}
_FACT_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
_HOST_RE = re.compile(r"[a-z0-9.-]{1,253}")
_ROLE_RE = re.compile(r"\S{1,100}")


def _segment(part) -> str:
    """A path segment for a message: only short plain names survive, anything else becomes an ellipsis."""
    return part if isinstance(part, str) and _SAFE_NAME.fullmatch(part) else "…"


def _path(error) -> list:
    return [_segment(p) for p in error.absolute_path if isinstance(p, str)]


def _field(error) -> str:
    """Name the offending field from a jsonschema error without quoting any file-supplied value."""
    path = _path(error)
    if error.validator == "required":
        missing = sorted(set(error.validator_value) - set(error.instance))
        return ".".join(path + missing[:1])
    return ".".join(path) or "(Wurzel)"


def _message(error) -> str:
    if error.validator == "additionalProperties" and isinstance(error.instance, dict):
        extra = sorted(set(error.instance) - set(error.schema.get("properties", {})))
        if extra and _SAFE_NAME.fullmatch(extra[0]):
            return f"Unbekanntes Feld „{'.'.join(_path(error) + [extra[0]])}“."
        return "Unbekanntes Feld (ungültiger Name)."
    field = _field(error)
    if error.validator == "required":
        return f"Feld „{field}“ fehlt."
    return f"Feld „{field}“ ist ungültig."


def _invalid(field: str) -> ManifestError:
    return ManifestError(f"Feld „{field}“ ist ungültig.")


def _refuse_constant(_name):
    raise ValueError("non-finite number")


def _recheck(data: dict) -> None:
    """The schema's ``$`` lets a trailing newline through; match every name in full again."""
    if not _FILE_RE["entry"].fullmatch(data["entry"]):
        raise _invalid("entry")
    for key in ("scripts", "styles"):
        if not all(_FILE_RE[key].fullmatch(v) for v in data.get(key, ())):
            raise _invalid(key)
    if not all(_ROLE_RE.fullmatch(r) for r in data["roles"]):
        raise _invalid("roles")
    for name, facts in data["reads"].items():
        if not _FACT_RE.fullmatch(name) or not all(_FACT_RE.fullmatch(f) for f in facts):
            raise _invalid("reads")
    if not all(_HOST_RE.fullmatch(h) for h in data.get("links", {}).get("hosts", ())):
        raise _invalid("links")


def parse_manifest(view_id: str, raw: bytes) -> Manifest:
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ManifestError("Die Datei view.json ist grösser als 64 KB.")
    try:
        text = raw.decode("utf-8-sig")  # a leading BOM is dropped, as for the view's other files
    except UnicodeDecodeError:
        raise ManifestError("Die Datei view.json ist nicht UTF-8-kodiert.") from None
    try:
        data = json.loads(text, parse_constant=_refuse_constant)
    except (ValueError, RecursionError):
        raise ManifestError("Die Datei view.json ist kein gültiges JSON.") from None
    if not isinstance(data, dict):
        raise ManifestError("Die Datei view.json muss ein JSON-Objekt enthalten.")

    try:
        errors = sorted(_VALIDATOR.iter_errors(data), key=lambda e: [str(p) for p in e.absolute_path])
    except RecursionError:
        raise ManifestError("Die Datei view.json ist zu tief verschachtelt.") from None
    if errors:
        raise ManifestError(_message(errors[0]))
    _recheck(data)

    description = data.get("description")
    order = data.get("order")
    if order is not None:
        try:
            order = float(order)
        except OverflowError:
            raise _invalid("order") from None
        if not math.isfinite(order):
            raise _invalid("order")
    return Manifest(
        id=view_id,
        title=MappingProxyType(dict(data["title"])),
        description=MappingProxyType(dict(description)) if description is not None else None,
        order=order,
        roles=tuple(data["roles"]),
        entry=data["entry"],
        scripts=tuple(data.get("scripts", ())),
        styles=tuple(data.get("styles", ())),
        reads=MappingProxyType({k: tuple(v) for k, v in data["reads"].items()}),
        link_hosts=tuple(data.get("links", {}).get("hosts", ())),
    )
