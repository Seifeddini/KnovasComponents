"""The protocol's v2 contracts as the kit carries them: a copy of Knovas Link's ``contracts/v2`` (schemas and codes),
checked against its ``contracts.lock`` on first use, and validators for jobs and results.

The copy keeps the kit installable on its own; ``tests/test_vendored_contracts.py`` fails when it differs from Link's.
"""
from __future__ import annotations

import functools
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

FOLDER = Path(__file__).resolve().parent / "contracts" / "v2"


class ContractsStale(RuntimeError):
    """A contract file does not match the lock."""


@functools.lru_cache(maxsize=None)
def schemas() -> dict[str, dict]:
    lock = json.loads((FOLDER / "contracts.lock").read_text(encoding="utf-8"))
    for name, want in lock["files"].items():
        if hashlib.sha256((FOLDER / name).read_bytes()).hexdigest() != want:
            raise ContractsStale(name)
    return {p.name: json.loads(p.read_text(encoding="utf-8")) for p in sorted(FOLDER.glob("*.schema.json"))}


@functools.lru_cache(maxsize=None)
def _registry() -> Registry:
    return Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas().values())


@functools.lru_cache(maxsize=None)
def validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(schemas()[name], registry=_registry())


def errors(name: str, instance: object) -> list[str]:
    """The paths of what is wrong, never the values (a value may be customer data)."""
    return sorted("/".join(str(p) for p in e.absolute_path) or "<root>" for e in validator(name).iter_errors(instance))


def valid(name: str, instance: object) -> bool:
    return not errors(name, instance)


@functools.lru_cache(maxsize=None)
def codes() -> frozenset:
    return frozenset(c["code"] for c in json.loads((FOLDER / "codes.json").read_text(encoding="utf-8"))["codes"])
