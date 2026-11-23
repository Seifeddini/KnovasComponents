"""Conditions of the catalogue (`detect` blocks and `checks`): a fact by dotted path, an operator, a value."""

from __future__ import annotations

import re
from typing import Any

OPERATORS = (">=", ">", "<=", "<", "==", "!=", "contains_any", "contains_all")
_MISSING = object()


def get_fact(facts: dict, path: str) -> Any:
    """The value at a dotted path, or _MISSING."""
    node: Any = facts
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return _MISSING
    return node


def has_fact(facts: dict, path: str) -> bool:
    return get_fact(facts, path) is not _MISSING


def _norm(v: Any) -> Any:
    return v.upper() if isinstance(v, str) else v


def holds(condition: dict, facts: dict) -> bool:
    """Whether one condition holds. A missing or null fact, or values that do not compare, never hold."""
    value = get_fact(facts, condition["fact"])
    if value is _MISSING or value is None:
        return False
    op, want = condition["op"], condition.get("value")
    try:
        if op == "==":
            return _eq(value, want)
        if op == "!=":
            return not _eq(value, want)
        if op in ("contains_any", "contains_all"):
            have = {_norm(v) for v in (value.keys() if isinstance(value, dict) else value)}
            wanted = [_norm(w) for w in (want if isinstance(want, list) else [want])]
            return any(w in have for w in wanted) if op == "contains_any" else all(w in have for w in wanted)
        if isinstance(value, bool) or isinstance(want, bool):
            return False
        if op == ">=":
            return value >= want
        if op == ">":
            return value > want
        if op == "<=":
            return value <= want
        if op == "<":
            return value < want
    except TypeError:
        return False
    raise ValueError(f"unknown operator {op!r}")


def _eq(value: Any, want: Any) -> bool:
    if isinstance(value, str) and isinstance(want, str):
        return value.strip().lower() == want.strip().lower()
    if isinstance(value, bool) or isinstance(want, bool):
        return isinstance(value, bool) and isinstance(want, bool) and value == want
    return value == want


def all_hold(conditions: list[dict], facts: dict) -> bool:
    return all(holds(c, facts) for c in conditions)


_FIELD = re.compile(r"\{([A-Za-z_][A-Za-z0-9_.]*)(?::([^{}]*))?\}")


def placeholders(text: str) -> list[str]:
    return [m.group(1) for m in _FIELD.finditer(text)]


def render(text: str, facts: dict) -> str:
    """Fill {dotted.fact} and {fact:format} from the facts; a missing fact is shown as '?'."""

    def sub(m: re.Match) -> str:
        value = get_fact(facts, m.group(1))
        if value is _MISSING or value is None:
            return "?"
        spec = m.group(2)
        if spec:
            try:
                return format(value, spec)
            except (ValueError, TypeError):
                return str(value)
        if isinstance(value, int) and not isinstance(value, bool):
            return f"{value:,}"
        return str(value)

    return _FIELD.sub(sub, text)
