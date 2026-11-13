"""The ``view.json`` manifest of a custom view: parsing and validation."""
from __future__ import annotations

import json
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


def _field(error) -> str:
    """Name the offending field from a jsonschema error without quoting any value."""
    path = [p for p in error.absolute_path if isinstance(p, str)]
    if error.validator == "required":
        missing = sorted(set(error.validator_value) - set(error.instance))
        return ".".join(path + missing[:1])
    if error.validator == "additionalProperties" and isinstance(error.instance, dict):
        allowed = set(error.schema.get("properties", {}))
        extra = sorted(set(error.instance) - allowed)
        if extra and not error.schema.get("properties"):
            return ".".join(path) or "(Wurzel)"
        return ".".join(path + extra[:1])
    if error.validator == "propertyNames":
        return ".".join(path)
    return ".".join(path) or "(Wurzel)"


def _message(error) -> str:
    field = _field(error)
    if error.validator == "required":
        return f"Feld „{field}“ fehlt."
    if error.validator == "additionalProperties":
        return f"Unbekanntes Feld „{field}“."
    return f"Feld „{field}“ ist ungültig."


def parse_manifest(view_id: str, raw: bytes) -> Manifest:
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ManifestError("Die Datei view.json ist grösser als 64 KB.")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ManifestError("Die Datei view.json ist nicht UTF-8-kodiert.") from None
    try:
        data = json.loads(text)
    except ValueError:
        raise ManifestError("Die Datei view.json ist kein gültiges JSON.") from None
    if not isinstance(data, dict):
        raise ManifestError("Die Datei view.json muss ein JSON-Objekt enthalten.")

    errors = sorted(_VALIDATOR.iter_errors(data), key=lambda e: [str(p) for p in e.absolute_path])
    if errors:
        raise ManifestError(_message(errors[0]))

    description = data.get("description")
    order = data.get("order")
    return Manifest(
        id=view_id,
        title=MappingProxyType(dict(data["title"])),
        description=MappingProxyType(dict(description)) if description is not None else None,
        order=float(order) if order is not None else None,
        roles=tuple(data["roles"]),
        entry=data["entry"],
        scripts=tuple(data.get("scripts", ())),
        styles=tuple(data.get("styles", ())),
        reads=MappingProxyType({k: tuple(v) for k, v in data["reads"].items()}),
        link_hosts=tuple(data.get("links", {}).get("hosts", ())),
    )
