"""JSON Schema validation for RC contracts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
from jsonschema import Draft202012Validator

_CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts"
_validators: dict[str, Draft202012Validator] = {}


def _load_validator(name: str) -> Draft202012Validator:
    if name not in _validators:
        path = _CONTRACTS_DIR / name
        schema = json.loads(path.read_text(encoding="utf-8"))
        _validators[name] = Draft202012Validator(schema)
    return _validators[name]


def validate(data: Any, schema_file: str) -> list[str]:
    validator = _load_validator(schema_file)
    errors = sorted(validator.iter_errors(data), key=lambda e: e.path)
    return [e.message for e in errors]
