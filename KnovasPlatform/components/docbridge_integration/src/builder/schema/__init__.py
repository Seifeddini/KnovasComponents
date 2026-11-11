"""The workspace model's JSON Schema and a check against it."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).resolve().parent / "workspace.schema.json"


@lru_cache(maxsize=1)
def _validator():
    from jsonschema import Draft202012Validator

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def problems(model: Any) -> list[str]:
    """Every place ``model`` breaks the schema, as "path: message", in document order."""
    found = []
    for error in sorted(_validator().iter_errors(model), key=lambda e: list(map(str, e.absolute_path))):
        path = "/".join(str(part) for part in error.absolute_path) or "(Modell)"
        found.append(f"{path}: {error.message}")
    return found
