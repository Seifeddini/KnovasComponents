"""Canonical JSON (RFC 8785) for the values the protocol allows, and the hashes built on it.

Permits are signed over canonical JSON and bind a hash of a step's target and parameters, so both sides must produce
the same bytes. The protocol allows strings, integers in the range of +/- 2^53, booleans, null, lists and objects with
string keys; a floating-point number is refused (a decimal travels as a string), so no number formatting rule is
needed. Keys are sorted by their UTF-16 code units, as RFC 8785 says.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

MAX_SAFE_INTEGER = 2 ** 53 - 1


class CanonicalError(ValueError):
    """A value the protocol's canonical JSON does not allow (a float, an integer out of range, a non-string key)."""


def _encode(value: Any, out: list[str]) -> None:
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise CanonicalError("integer_out_of_range")
        out.append(str(value))
    elif isinstance(value, float):
        raise CanonicalError("float")
    elif isinstance(value, str):
        out.append(json.dumps(value, ensure_ascii=False))
    elif isinstance(value, (list, tuple)):
        out.append("[")
        for i, item in enumerate(value):
            if i:
                out.append(",")
            _encode(item, out)
        out.append("]")
    elif isinstance(value, dict):
        if not all(isinstance(k, str) for k in value):
            raise CanonicalError("key_not_a_string")
        out.append("{")
        for i, key in enumerate(sorted(value, key=lambda k: k.encode("utf-16-be"))):
            if i:
                out.append(",")
            out.append(json.dumps(key, ensure_ascii=False))
            out.append(":")
            _encode(value[key], out)
        out.append("}")
    else:
        raise CanonicalError("type")


def canonical(value: Any) -> bytes:
    """The canonical JSON of ``value`` as UTF-8 bytes."""
    out: list[str] = []
    _encode(value, out)
    return "".join(out).encode("utf-8")


def params_hash(target: str, params: dict) -> str:
    """What a permit binds: ``sha256:`` and the hex SHA-256 of the canonical ``{"target": …, "params": …}``."""
    return "sha256:" + hashlib.sha256(canonical({"target": target, "params": params})).hexdigest()


def digest(value: Any) -> str:
    """The hex SHA-256 of a value's canonical JSON."""
    return hashlib.sha256(canonical(value)).hexdigest()
