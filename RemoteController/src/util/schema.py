"""JSON Schema validation for RC contracts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import jsonschema
from jsonschema import Draft202012Validator

_CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts"
_validators: dict[str, Draft202012Validator] = {}

#: Per-source keys whose instances are customer data: Knovas field values,
#: path templates (they name clients) and metadata items. A schema error under
#: them names the JSON path and the failed keyword only -- `e.message` embeds
#: the offending instance, and /sync, /sync/body and /sync/start return it to
#: the Platform, which shows it to the administrator (spec 3.8).
_REDACTED_SOURCE_KEYS = frozenset({"fields", "field_templates", "metadata_fields"})


def _load_validator(name: str) -> Draft202012Validator:
    if name not in _validators:
        path = _CONTRACTS_DIR / name
        schema = json.loads(path.read_text(encoding="utf-8"))
        _validators[name] = Draft202012Validator(schema)
    return _validators[name]


def _redacted_path(error: jsonschema.ValidationError) -> Optional[str]:
    """``$.sources[i].<key>[j]`` for an error under a redacted source key,
    else None. A ``fields`` error stops at ``fields``: its keys are
    configuration, but a mistyped one may be a value (a client's name)."""
    path = list(error.absolute_path)
    if len(path) < 3 or path[0] != "sources" or not isinstance(path[1], int):
        return None
    container = path[2]
    if container not in _REDACTED_SOURCE_KEYS:
        return None
    out = f"$.sources[{path[1]}].{container}"
    if container != "fields" and len(path) > 3 and isinstance(path[3], int):
        out += f"[{path[3]}]"
    return out


def _message(error: jsonschema.ValidationError) -> str:
    redacted = _redacted_path(error)
    if redacted is None:
        return error.message
    return f"{redacted}: {error.validator}"


def validate(data: Any, schema_file: str) -> list[str]:
    validator = _load_validator(schema_file)
    errors = sorted(validator.iter_errors(data), key=lambda e: e.path)
    return [_message(e) for e in errors]
