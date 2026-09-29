"""Experiment type definitions: their shape, their rules, and the lifecycle
they encode.

A type definition is data a manager writes (in the UI editor, or as YAML in a
pack) and every experiment of that type obeys: which fields it has, which
states it moves through, which transitions are gated by what, which metrics
and evaluations it starts with. Because people write it by hand, validation
has to do two jobs: refuse anything the rest of the module could trip over,
and say precisely where the problem is. Errors are therefore collected per
path (``states[2].key``, ``transitions[0].requires[1]``) in
``ValidationError.fields`` rather than stopping at the first one.

Text is parsed only here and in ``packs.parse_pack_text``: JSON when it looks
like an object, otherwise YAML through ``NoAliasSafeLoader``. That loader is
the SafeLoader with anchors, aliases and merge keys refused (an alias bomb
expands a few kilobytes into gigabytes) and duplicate keys refused (the
second value would silently win). Sizes are capped before parsing.

Plan: docs/superpowers/plans/2026-09-28-experiments-module.md, sections 3-6.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import numbers
import re
import uuid
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

import yaml
from jsonschema import Draft202012Validator

from experiments import kinds
from experiments.errors import Forbidden, ValidationError

# -- vocabulary ---------------------------------------------------------------

FIELD_TYPES: Tuple[str, ...] = (
    "text", "longtext", "number", "integer", "enum", "multi_enum", "date", "url", "boolean",
)

#: Transition requirements. ``variants``, ``field`` and ``n_planned`` take an
#: argument after a colon: ``variants:2``, ``field:channel``,
#: ``n_planned:planned_n``.
REQUIREMENT_KEYS: Tuple[str, ...] = (
    "hypothesis", "primary_metric", "variants", "measurements", "evaluation",
    "decision", "learning", "field", "n_planned",
)

PHASES: Tuple[str, ...] = ("running", "decided", "stopped")
TRANSITION_ROLES: Tuple[str, ...] = ("experimenter", "experiments_manager")
METRIC_ROLES: Tuple[str, ...] = ("primary", "secondary", "guardrail")

#: ``evaluation[].metric`` values with a meaning of their own; a metric may
#: not be called like that, or "all" would be ambiguous.
RESERVED_METRIC_KEYS: FrozenSet[str] = frozenset({"primary", "all"})

MAX_DEFINITION_BYTES = 200 * 1024
MAX_PARAMS_BYTES = 4 * 1024

_ENUM_TYPES = frozenset({"enum", "multi_enum"})
_NUMBER_TYPES = frozenset({"number", "integer"})
_TERMINAL_PHASES = frozenset({"decided", "stopped"})
_MAX_REPORTED_ERRORS = 50
_MAX_DEPTH = 32
_MAX_EXACT_INT = 2 ** 53 - 1

_ROLE_NAMES = {
    "experimenter": "Experimentierende",
    "experiments_manager": "Verantwortliche der Experimente",
}

# -- patterns -----------------------------------------------------------------

FIELD_KEY_PATTERN = r"^[a-z][a-z0-9_]{0,39}$"
STATE_KEY_PATTERN = r"^[a-z][a-z0-9_]{0,31}$"
METRIC_KEY_PATTERN = r"^[a-z][a-z0-9_]{1,47}$"
EVALUATOR_KEY_PATTERN = r"^[a-z][a-z0-9_.-]{1,63}$"
VARIANT_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$"
DIM_KEY_PATTERN = r"^[A-Za-z0-9_.-]{1,40}$"
_SINGLE_LINE_PATTERN = r"^[^\x00-\x1f\x7f]*$"
_FROM_PATTERN = r"^(\*|[a-z][a-z0-9_]{0,31})$"
_REQUIREMENT_PATTERN = (
    r"^(hypothesis|primary_metric|measurements|evaluation|decision|learning"
    r"|variants:[1-9][0-9]?|field:[a-z][a-z0-9_]{0,39}|n_planned:[a-z][a-z0-9_]{0,39})$"
)

_DIM_KEY_RE = re.compile(DIM_KEY_PATTERN)
_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_SWISS_DATE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")
_LEVEL_KEY_RE = re.compile(r"^-?\d{1,15}(\.\d{1,15})?$")

_MSG_SINGLE_LINE = "Keine Zeilenumbr\u00fcche oder Steuerzeichen."
_MSG_FIELD_KEY = (
    "Nur Kleinbuchstaben, Ziffern und _, beginnend mit einem Buchstaben; "
    "h\u00f6chstens 40 Zeichen."
)
_MSG_STATE_KEY = (
    "Nur Kleinbuchstaben, Ziffern und _, beginnend mit einem Buchstaben; "
    "h\u00f6chstens 32 Zeichen."
)
METRIC_KEY_MESSAGE = (
    "Nur Kleinbuchstaben, Ziffern und _, beginnend mit einem Buchstaben; "
    "2 bis 48 Zeichen."
)
EVALUATOR_KEY_MESSAGE = (
    "Nur Kleinbuchstaben, Ziffern, _, . und -, beginnend mit einem Buchstaben; "
    "2 bis 64 Zeichen."
)
_MSG_VARIANT_KEY = (
    "Nur Buchstaben, Ziffern, _, . und -, beginnend mit einem Buchstaben oder "
    "einer Ziffer; h\u00f6chstens 40 Zeichen."
)
_MSG_DIM_KEY = "Nur Buchstaben, Ziffern, _, . und -; h\u00f6chstens 40 Zeichen."
_MSG_FROM = "Ein Status-Schl\u00fcssel oder \u00ab*\u00bb (jeder andere Status)."
_MSG_REQUIREMENT = (
    "Erlaubt sind hypothesis, primary_metric, variants:N, measurements, "
    "evaluation, decision, learning, field:<Feld> und n_planned:<Feld>."
)


def text_schema(max_length: int, *, min_length: int = 1) -> Dict[str, Any]:
    return {
        "type": "string", "minLength": min_length, "maxLength": max_length,
        "pattern": _SINGLE_LINE_PATTERN, "x-message": _MSG_SINGLE_LINE,
    }


def key_schema(pattern: str, message: str) -> Dict[str, Any]:
    return {"type": "string", "pattern": pattern, "x-message": message}


# -- JSON Schemas -------------------------------------------------------------

SCOPE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "runs": {
            "anyOf": [
                {"const": "latest"},
                {
                    "type": "array", "minItems": 1, "maxItems": 200,
                    "items": {"type": "string", "maxLength": 64},
                },
            ],
            "x-message": "Entweder \u00ablatest\u00bb oder eine Liste von Lauf-IDs.",
        },
        "since": {"type": "string", "maxLength": 40},
        "until": {"type": "string", "maxLength": 40},
        "dims": {
            "type": "object",
            "maxProperties": 20,
            "propertyNames": key_schema(DIM_KEY_PATTERN, _MSG_DIM_KEY),
            "additionalProperties": {
                "type": ["string", "number"], "maxLength": 200,
                "x-message": "Werte sind Text oder Zahlen.",
            },
        },
    },
}

METRIC_DEFINITION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "decimals": {"type": "integer", "minimum": 0, "maximum": 6},
        "min": {"type": "number"},
        "max": {"type": "number"},
        "levels": {
            "type": "object",
            "minProperties": 2,
            "maxProperties": 50,
            "additionalProperties": text_schema(80),
        },
    },
}

_FIELD_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["key", "label", "type"],
    "properties": {
        "key": key_schema(FIELD_KEY_PATTERN, _MSG_FIELD_KEY),
        "label": text_schema(80),
        "type": {"enum": list(FIELD_TYPES)},
        "options": {
            "type": "array", "minItems": 1, "maxItems": 50, "uniqueItems": True,
            "items": text_schema(80),
        },
        "required": {"type": "boolean"},
        "help": text_schema(300, min_length=0),
        "min": {"type": "number"},
        "max": {"type": "number"},
    },
    "allOf": [
        {
            "if": {"required": ["type"], "properties": {"type": {"enum": sorted(_ENUM_TYPES)}}},
            "then": {"required": ["options"]},
        },
    ],
}

_STATE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["key", "label"],
    "properties": {
        "key": key_schema(STATE_KEY_PATTERN, _MSG_STATE_KEY),
        "label": text_schema(40),
        "phase": {"enum": list(PHASES)},
    },
}

_TRANSITION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["from", "to", "label"],
    "properties": {
        "from": key_schema(_FROM_PATTERN, _MSG_FROM),
        "to": key_schema(STATE_KEY_PATTERN, _MSG_STATE_KEY),
        "label": text_schema(40),
        "requires": {
            "type": "array", "maxItems": 20, "uniqueItems": True,
            "items": key_schema(_REQUIREMENT_PATTERN, _MSG_REQUIREMENT),
        },
        "roles": {
            "type": "array", "maxItems": len(TRANSITION_ROLES), "uniqueItems": True,
            "items": {"enum": list(TRANSITION_ROLES)},
        },
    },
}

_VARIANTS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "min": {"type": "integer", "minimum": 0, "maximum": 10},
        "max": {"type": "integer", "minimum": 1, "maximum": 20},
        "defaults": {
            "type": "array",
            "maxItems": 10,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["key"],
                "properties": {
                    "key": key_schema(VARIANT_KEY_PATTERN, _MSG_VARIANT_KEY),
                    "name": text_schema(120, min_length=0),
                    "is_control": {"type": "boolean"},
                    "allocation": {"type": "number", "minimum": 0, "maximum": 1},
                },
            },
        },
    },
}

_METRIC_ENTRY_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["metric", "role"],
    "properties": {
        "metric": key_schema(METRIC_KEY_PATTERN, METRIC_KEY_MESSAGE),
        "role": {"enum": list(METRIC_ROLES)},
        "op": {"enum": ["max", "min"]},
        "value": {"type": "number"},
    },
    "allOf": [
        {
            "if": {"required": ["role"], "properties": {"role": {"const": "guardrail"}}},
            "then": {"required": ["op", "value"]},
        },
    ],
}

_EVALUATION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["evaluator", "metric"],
    "properties": {
        "evaluator": key_schema(EVALUATOR_KEY_PATTERN, EVALUATOR_KEY_MESSAGE),
        # "primary", "all" or a metric key (all three fit the metric pattern).
        "metric": key_schema(
            METRIC_KEY_PATTERN,
            "\u00abprimary\u00bb, \u00aball\u00bb oder der Schl\u00fcssel einer Metrik.",
        ),
        "params": {"type": "object", "maxProperties": 50},
        "scope": SCOPE_SCHEMA,
    },
}

#: The full shape of a type version's ``definition``. Rules a JSON Schema
#: cannot express (unique keys, references between states, transitions and
#: fields, one state per phase, ...) are checked in validate_type_definition.
TYPE_DEFINITION_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Experiment type definition",
    "type": "object",
    "additionalProperties": False,
    "required": ["states", "transitions"],
    "properties": {
        "fields": {"type": "array", "maxItems": 40, "items": _FIELD_SCHEMA},
        "states": {"type": "array", "minItems": 2, "maxItems": 12, "items": _STATE_SCHEMA},
        "initial": key_schema(STATE_KEY_PATTERN, _MSG_STATE_KEY),
        "transitions": {
            "type": "array", "minItems": 1, "maxItems": 40, "items": _TRANSITION_SCHEMA,
        },
        "variants": _VARIANTS_SCHEMA,
        "metrics": {"type": "array", "maxItems": 30, "items": _METRIC_ENTRY_SCHEMA},
        "evaluation": {"type": "array", "maxItems": 20, "items": _EVALUATION_SCHEMA},
        "decision": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"require_learning": {"type": "boolean"}},
        },
    },
}

Draft202012Validator.check_schema(TYPE_DEFINITION_SCHEMA)
_TYPE_VALIDATOR = Draft202012Validator(TYPE_DEFINITION_SCHEMA)
_SCOPE_VALIDATOR = Draft202012Validator(SCOPE_SCHEMA)
_METRIC_DEFINITION_VALIDATOR = Draft202012Validator(METRIC_DEFINITION_SCHEMA)


# -- error collection ---------------------------------------------------------


def join_path(prefix: str, *parts: Any) -> str:
    """``join_path("states", 2, "key") -> "states[2].key"``."""
    out = prefix
    for part in parts:
        if isinstance(part, int):
            out = f"{out}[{part}]"
        else:
            out = f"{out}.{part}" if out else str(part)
    return out


def add_error(errors: Dict[str, str], path: str, message: str) -> None:
    errors.setdefault(path or "definition", message)


def raise_invalid(errors: Dict[str, str], base: str) -> None:
    """Raise one ValidationError carrying every collected error.

    ``base`` ends with a full stop; the first error is repeated in the
    message so API clients that only print ``error`` still see where to look.
    """
    if not errors:
        return
    items = list(errors.items())[:_MAX_REPORTED_ERRORS]
    path, first = items[0]
    if len(errors) == 1:
        message = f"{base[:-1]}: {path}: {first}"
    else:
        message = f"{base[:-1]} ({len(errors)} Fehler). {path}: {first}"
    raise ValidationError(message, fields=dict(items))


_TYPE_MESSAGES = {
    "string": "Muss Text sein.",
    "integer": "Muss eine ganze Zahl sein.",
    "number": "Muss eine Zahl sein.",
    "boolean": "Muss true oder false sein.",
    "array": "Muss eine Liste sein.",
    "object": "Muss ein Objekt sein.",
    "null": "Muss leer (null) sein.",
}


def _items(n: Any) -> str:
    return "einen Eintrag" if n == 1 else f"{kinds.format_plain(n)} Eintr\u00e4ge"


def _schema_message(error: Any) -> Tuple[List[Any], str]:
    """German text for one jsonschema error, and the path it belongs to."""
    path = list(error.absolute_path)
    kind = error.validator
    value = error.validator_value
    hint = error.schema.get("x-message") if isinstance(error.schema, dict) else None
    if "propertyNames" in error.schema_path:
        return path + [str(error.instance)], f"Ung\u00fcltiger Schl\u00fcssel. {hint or ''}".strip()
    if kind == "type":
        if isinstance(value, list):
            return path, hint or "Ung\u00fcltiger Wert."
        return path, _TYPE_MESSAGES.get(value, "Ung\u00fcltiger Wert.")
    if kind == "enum":
        shown = ", ".join(str(v) for v in value)
        return path, f"Erlaubt sind: {shown}."
    if kind == "const":
        return path, f"Erlaubt ist nur {value}."
    if kind == "pattern":
        return path, hint or "Ung\u00fcltiges Format."
    if kind == "minLength":
        if value == 1:
            return path, "Darf nicht leer sein."
        return path, f"Mindestens {kinds.format_plain(value)} Zeichen."
    if kind == "maxLength":
        return path, f"H\u00f6chstens {kinds.format_plain(value)} Zeichen."
    if kind in ("minItems", "minProperties"):
        return path, f"Mindestens {_items(value)}."
    if kind in ("maxItems", "maxProperties"):
        return path, f"H\u00f6chstens {_items(value)}."
    if kind == "uniqueItems":
        return path, "Eintr\u00e4ge d\u00fcrfen sich nicht wiederholen."
    if kind == "minimum":
        return path, f"Mindestens {kinds.format_plain(value)}."
    if kind == "maximum":
        return path, f"H\u00f6chstens {kinds.format_plain(value)}."
    if kind in ("anyOf", "oneOf"):
        return path, hint or "Ung\u00fcltiger Wert."
    return path, hint or "Ung\u00fcltiger Wert."


def schema_errors(validator: Draft202012Validator, instance: Any,
                  prefix: str = "") -> Dict[str, str]:
    """Every error ``validator`` finds, as ``{path: German message}``."""
    errors: Dict[str, str] = {}
    for error in validator.iter_errors(instance):
        if error.validator == "required":
            for name in error.validator_value:
                if isinstance(error.instance, dict) and name not in error.instance:
                    add_error(errors, join_path(prefix, *error.absolute_path, name),
                              "Pflichtangabe fehlt.")
            continue
        if error.validator == "additionalProperties" and isinstance(error.instance, dict):
            known = error.schema.get("properties", {})
            for name in error.instance:
                if name not in known:
                    add_error(errors, join_path(prefix, *error.absolute_path, str(name)),
                              "Unbekannter Eintrag.")
            continue
        path, message = _schema_message(error)
        add_error(errors, join_path(prefix, *path), message)
        if len(errors) >= _MAX_REPORTED_ERRORS:
            break
    return errors


# -- JSON-compatible values ---------------------------------------------------


class _Budget:
    """Caps the work spent on one document before any rule is checked.

    A definition arrives from a 32 MB request body at most; walking millions
    of list items only to report "at most 40 fields" would tie up a worker.
    """

    def __init__(self, max_nodes: int, max_chars: int) -> None:
        self.nodes = max_nodes
        self.chars = max_chars

    def spend(self, nodes: int = 1, chars: int = 0) -> bool:
        self.nodes -= nodes
        self.chars -= chars
        return self.nodes >= 0 and self.chars >= 0


class _TooLarge(Exception):
    pass


def _key_text(key: Any) -> Optional[str]:
    """A mapping key as JSON needs it. YAML turns ``1:`` into an int and
    ``2.5:`` into a float (levels are written like that); both become their
    level key. Booleans and null are refused: ``on:`` or ``~:`` as a key is
    a YAML accident, never intended."""
    if isinstance(key, str):
        return key
    if isinstance(key, bool) or key is None:
        return None
    if isinstance(key, int):
        return str(key)
    if isinstance(key, float) and math.isfinite(key):
        return kinds.level_key(key)
    return None


def plain_json(value: Any, path: str, errors: Dict[str, str], *,
               budget: Optional[_Budget] = None, depth: int = 0) -> Any:
    """A JSON-compatible copy of ``value``; problems go into ``errors``.

    Refuses what would break later: NaN and infinities (JSON has none, the
    database refuses them), NUL characters and unpaired surrogates (a JSONB
    or TEXT column cannot store them), non-string keys, excessive nesting.
    YAML dates and timestamps become ISO strings.
    """
    if budget is None:
        budget = _Budget(200_000, 8_000_000)
    if not budget.spend():
        raise _TooLarge()
    if depth > _MAX_DEPTH:
        add_error(errors, path, "Zu tief verschachtelt.")
        return None
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        if not budget.spend(0, len(value)):
            raise _TooLarge()
        if "\x00" in value:
            add_error(errors, path, "Enth\u00e4lt ein Nullzeichen.")
        else:
            try:
                value.encode("utf-8")
            except UnicodeEncodeError:
                add_error(errors, path, "Enth\u00e4lt ung\u00fcltige Zeichen.")
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            add_error(errors, path, "Nur endliche Zahlen sind erlaubt.")
            return None
        return value
    if isinstance(value, _dt.datetime):
        return value.isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            text = _key_text(key)
            if text is None:
                add_error(errors, join_path(path, str(key)), "Schl\u00fcssel m\u00fcssen Text sein.")
                continue
            if not budget.spend(0, len(text)):
                raise _TooLarge()
            if text in out:
                add_error(errors, join_path(path, text), "Der Schl\u00fcssel kommt doppelt vor.")
                continue
            out[text] = plain_json(item, join_path(path, text), errors,
                                   budget=budget, depth=depth + 1)
        return out
    if isinstance(value, list):
        return [
            plain_json(item, join_path(path, i), errors, budget=budget, depth=depth + 1)
            for i, item in enumerate(value)
        ]
    add_error(errors, path, "Nicht unterst\u00fctzter Wert.")
    return None


def checked_json(value: Any, base: str, *, max_nodes: int = 200_000,
                 max_chars: int = 8_000_000) -> Any:
    """plain_json that raises: the size cap first, then every value error."""
    errors: Dict[str, str] = {}
    try:
        result = plain_json(value, "", errors, budget=_Budget(max_nodes, max_chars))
    except _TooLarge:
        raise ValidationError(f"{base[:-1]}: zu gross.") from None
    raise_invalid(errors, base)
    return result


# -- safe text parsing --------------------------------------------------------

_MERGE_TAG = "tag:yaml.org,2002:merge"


class NoAliasSafeLoader(yaml.SafeLoader):
    """yaml.SafeLoader without anchors, aliases, merge keys or duplicate keys.

    SafeLoader already refuses Python object tags. What it still allows is an
    alias bomb ("billion laughs": nested aliases that expand exponentially)
    and silent overwrites by duplicate keys. Refusing anchors at the first
    occurrence stops the bomb before any expansion.
    """

    def compose_node(self, parent, index):  # noqa: D401 - PyYAML hook
        event = self.peek_event()
        if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None) is not None:
            raise ValidationError("Anker und Verweise (&/*) sind nicht erlaubt.")
        return super().compose_node(parent, index)

    def construct_mapping(self, node, deep=False):
        if not isinstance(node, yaml.MappingNode):
            return super().construct_mapping(node, deep=deep)
        mapping: Dict[Any, Any] = {}
        for key_node, value_node in node.value:
            if key_node.tag == _MERGE_TAG:
                raise ValidationError("Zusammenf\u00fchrungen (<<) sind nicht erlaubt.")
            key = self.construct_object(key_node, deep=deep)
            try:
                hash(key)
            except TypeError:
                raise ValidationError(
                    f"Ung\u00fcltiger Schl\u00fcssel (Zeile {key_node.start_mark.line + 1})."
                ) from None
            if key in mapping:
                shown = str(key)[:60]
                raise ValidationError(
                    f"Der Schl\u00fcssel \u00ab{shown}\u00bb kommt doppelt vor "
                    f"(Zeile {key_node.start_mark.line + 1})."
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def _mark(error: Any) -> str:
    mark = getattr(error, "problem_mark", None) or getattr(error, "context_mark", None)
    if mark is None:
        return ""
    return f" (Zeile {mark.line + 1}, Spalte {mark.column + 1})"


def _json_pairs(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValidationError(f"Der Schl\u00fcssel \u00ab{key[:60]}\u00bb kommt doppelt vor.")
        out[key] = value
    return out


def parse_text(text: Any, *, max_bytes: int, what: str) -> Any:
    """Parse YAML or JSON text safely (plan, section 5); returns plain JSON.

    ``what`` names the document in messages ("Die Typdefinition"). Shared by
    parse_definition_text and packs.parse_pack_text so both refuse exactly
    the same things.
    """
    if isinstance(text, (bytes, bytearray)):
        if len(text) > max_bytes:
            raise ValidationError(f"{what} ist zu gross (h\u00f6chstens {max_bytes // 1024} KB).")
        try:
            text = bytes(text).decode("utf-8")
        except UnicodeDecodeError:
            raise ValidationError(f"{what} ist kein g\u00fcltiger UTF-8-Text.") from None
    if not isinstance(text, str):
        raise ValidationError(f"{what} fehlt.")
    if len(text) > max_bytes:
        # Every character is at least one UTF-8 byte: too long either way.
        raise ValidationError(f"{what} ist zu gross (h\u00f6chstens {max_bytes // 1024} KB).")
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError:
        raise ValidationError(f"{what} enth\u00e4lt ung\u00fcltige Zeichen.") from None
    if size > max_bytes:
        raise ValidationError(f"{what} ist zu gross (h\u00f6chstens {max_bytes // 1024} KB).")
    text = text.lstrip("\ufeff")
    if not text.strip():
        raise ValidationError(f"{what} ist leer.")
    try:
        if text.lstrip().startswith("{"):
            try:
                data = json.loads(text, object_pairs_hook=_json_pairs)
            except json.JSONDecodeError as exc:
                raise ValidationError(
                    f"Das JSON ist fehlerhaft (Zeile {exc.lineno}, Spalte {exc.colno})."
                ) from None
        else:
            try:
                data = yaml.load(text, Loader=NoAliasSafeLoader)  # noqa: S506 - safe subclass
            except yaml.constructor.ConstructorError as exc:
                raise ValidationError(
                    f"Das YAML enth\u00e4lt einen nicht erlaubten Typ{_mark(exc)}."
                ) from None
            except yaml.YAMLError as exc:
                raise ValidationError(f"Das YAML ist fehlerhaft{_mark(exc)}.") from None
    except RecursionError:
        raise ValidationError(f"{what} ist zu tief verschachtelt.") from None
    except ValidationError:
        raise
    except (ValueError, TypeError, OverflowError):
        # int() refusing a number with thousands of digits, and friends.
        raise ValidationError(f"{what} enth\u00e4lt einen ung\u00fcltigen Wert.") from None
    return checked_json(data, f"{what} ist ung\u00fcltig.")


def parse_definition_text(text: str) -> Dict[str, Any]:
    """A type definition from YAML or JSON text, validated and normalised.

    Parsing and validation are one step on purpose: text never reaches the
    store without passing validate_type_definition.
    """
    data = parse_text(text, max_bytes=MAX_DEFINITION_BYTES, what="Die Typdefinition")
    return validate_type_definition(data)


# -- type definitions ---------------------------------------------------------


def _clean(text: str) -> str:
    return text.strip()


def _parse_requirement(item: str) -> Tuple[str, Optional[str]]:
    name, _, arg = item.partition(":")
    return name, (arg or None)


def json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def type_definition_errors(
    definition: Any, prefix: str = ""
) -> Tuple[Optional[Dict[str, Any]], Dict[str, str]]:
    """(normalised definition, {}) or (None, {path: message}).

    Paths start with ``prefix`` so a pack can report
    ``types[1].definition.states[0].key``.
    """
    errors: Dict[str, str] = {}
    try:
        definition = plain_json(definition, prefix, errors,
                                budget=_Budget(50_000, 2 * MAX_DEFINITION_BYTES))
    except _TooLarge:
        return None, {prefix or "definition": "Die Typdefinition ist zu gross."}
    if errors:
        return None, errors
    if not isinstance(definition, dict):
        return None, {prefix or "definition": "Muss ein Objekt sein."}
    errors = schema_errors(_TYPE_VALIDATOR, definition, prefix)
    if errors:
        return None, errors

    def p(*parts: Any) -> str:
        return join_path(prefix, *parts)

    # fields
    fields: List[Dict[str, Any]] = []
    field_types: Dict[str, str] = {}
    for i, raw in enumerate(definition.get("fields") or []):
        key = raw["key"]
        if key in field_types:
            add_error(errors, p("fields", i, "key"), f"Das Feld \u00ab{key}\u00bb gibt es schon.")
            continue
        ftype = raw["type"]
        field_types[key] = ftype
        label = _clean(raw["label"])
        if not label:
            add_error(errors, p("fields", i, "label"), "Darf nicht leer sein.")
        field: Dict[str, Any] = {
            "key": key, "label": label, "type": ftype,
            "required": bool(raw.get("required", False)),
            "help": _clean(raw.get("help", "")),
        }
        if "options" in raw:
            if ftype not in _ENUM_TYPES:
                add_error(errors, p("fields", i, "options"),
                          "Optionen gibt es nur bei Auswahlfeldern (enum, multi_enum).")
            else:
                options = [_clean(o) for o in raw["options"]]
                if any(not o for o in options):
                    add_error(errors, p("fields", i, "options"), "Leere Optionen sind nicht erlaubt.")
                elif len(set(options)) != len(options):
                    add_error(errors, p("fields", i, "options"),
                              "Eintr\u00e4ge d\u00fcrfen sich nicht wiederholen.")
                field["options"] = options
        for bound in ("min", "max"):
            if bound not in raw:
                continue
            if ftype not in _NUMBER_TYPES:
                add_error(errors, p("fields", i, bound),
                          "Minimum und Maximum gibt es nur bei Zahlenfeldern (number, integer).")
                continue
            number = raw[bound]
            if ftype == "integer":
                if not float(number).is_integer() or abs(number) > _MAX_EXACT_INT:
                    add_error(errors, p("fields", i, bound), "Muss eine ganze Zahl sein.")
                    continue
                number = int(number)
            field[bound] = number
        if "min" in field and "max" in field and field["min"] > field["max"]:
            add_error(errors, p("fields", i, "min"), "Das Minimum ist gr\u00f6sser als das Maximum.")
        fields.append(field)

    # states
    states: List[Dict[str, Any]] = []
    state_phase: Dict[str, Optional[str]] = {}
    phase_owner: Dict[str, str] = {}
    for i, raw in enumerate(definition["states"]):
        key = raw["key"]
        if key in state_phase:
            add_error(errors, p("states", i, "key"), f"Den Status \u00ab{key}\u00bb gibt es schon.")
            continue
        label = _clean(raw["label"])
        if not label:
            add_error(errors, p("states", i, "label"), "Darf nicht leer sein.")
        state: Dict[str, Any] = {"key": key, "label": label}
        phase = raw.get("phase")
        if phase is not None:
            if phase in phase_owner:
                add_error(errors, p("states", i, "phase"),
                          f"Die Phase \u00ab{phase}\u00bb hat schon der Status "
                          f"\u00ab{phase_owner[phase]}\u00bb.")
            else:
                phase_owner[phase] = key
            state["phase"] = phase
        state_phase[key] = phase
        states.append(state)

    # initial
    initial = definition.get("initial")
    if initial is None:
        initial = next(
            (s["key"] for s in states if s.get("phase") not in _TERMINAL_PHASES), None
        )
        if initial is None:
            add_error(errors, p("initial"), "Es gibt keinen Status, in dem ein Experiment beginnen kann.")
    elif initial not in state_phase:
        add_error(errors, p("initial"), f"Unbekannter Status \u00ab{initial}\u00bb.")
    elif state_phase[initial] in _TERMINAL_PHASES:
        add_error(errors, p("initial"),
                  "Ein Experiment kann nicht in einem entschiedenen oder abgebrochenen Status beginnen.")

    # variants (before transitions: variants:N is checked against max)
    raw_variants = definition.get("variants") or {}
    vmin = int(raw_variants.get("min", 0))
    vmax = int(raw_variants.get("max", 10))
    if vmin > vmax:
        add_error(errors, p("variants", "min"), "Das Minimum ist gr\u00f6sser als das Maximum.")
    defaults: List[Dict[str, Any]] = []
    seen_variants: set = set()
    controls = 0
    allocation_sum = 0.0
    for i, raw in enumerate(raw_variants.get("defaults") or []):
        key = raw["key"]
        if key in seen_variants:
            add_error(errors, p("variants", "defaults", i, "key"),
                      f"Die Variante \u00ab{key}\u00bb gibt es schon.")
            continue
        seen_variants.add(key)
        variant: Dict[str, Any] = {
            "key": key,
            "name": _clean(raw.get("name", "")),
            "is_control": bool(raw.get("is_control", False)),
        }
        if variant["is_control"]:
            controls += 1
            if controls > 1:
                add_error(errors, p("variants", "defaults", i, "is_control"),
                          "Nur eine Variante kann die Kontrolle sein.")
        if "allocation" in raw:
            variant["allocation"] = raw["allocation"]
            allocation_sum += float(raw["allocation"])
        defaults.append(variant)
    if len(defaults) > vmax:
        add_error(errors, p("variants", "defaults"),
                  f"Mehr Vorgaben als Varianten erlaubt sind (h\u00f6chstens {vmax}).")
    elif len(defaults) < vmin <= vmax:
        # A new experiment starts with the defaults (the create form sends no
        # variants), so fewer than the minimum could never be created.
        add_error(errors, p("variants", "defaults"),
                  f"Weniger Vorgaben als Varianten verlangt sind (mindestens {vmin}). "
                  "Mehr Vorgaben angeben oder \u00abmin\u00bb senken.")
    if allocation_sum > 1.0 + 1e-9:
        add_error(errors, p("variants", "defaults"), "Die Anteile ergeben zusammen mehr als 1.")
    variants = {"min": vmin, "max": vmax, "defaults": defaults}

    # transitions
    transitions: List[Dict[str, Any]] = []
    pairs: set = set()
    for i, raw in enumerate(definition["transitions"]):
        src, dst = raw["from"], raw["to"]
        ok = True
        if src != "*" and src not in state_phase:
            add_error(errors, p("transitions", i, "from"), f"Unbekannter Status \u00ab{src}\u00bb.")
            ok = False
        if dst not in state_phase:
            add_error(errors, p("transitions", i, "to"), f"Unbekannter Status \u00ab{dst}\u00bb.")
            ok = False
        if ok and src == dst:
            add_error(errors, p("transitions", i, "to"),
                      "Ein Statuswechsel braucht zwei verschiedene Status.")
            ok = False
        if ok and (src, dst) in pairs:
            add_error(errors, p("transitions", i),
                      f"Den Wechsel \u00ab{src}\u00bb \u2192 \u00ab{dst}\u00bb gibt es schon.")
            ok = False
        pairs.add((src, dst))
        label = _clean(raw["label"])
        if not label:
            add_error(errors, p("transitions", i, "label"), "Darf nicht leer sein.")
        requires = list(raw.get("requires") or [])
        for j, item in enumerate(requires):
            name, arg = _parse_requirement(item)
            where = p("transitions", i, "requires", j)
            if name == "variants" and arg is not None and int(arg) > vmax:
                add_error(errors, where, f"Der Typ erlaubt h\u00f6chstens {vmax} Varianten.")
            elif name == "field" and arg not in field_types:
                add_error(errors, where, f"Unbekanntes Feld \u00ab{arg}\u00bb.")
            elif name == "n_planned":
                if arg not in field_types:
                    add_error(errors, where, f"Unbekanntes Feld \u00ab{arg}\u00bb.")
                elif field_types[arg] not in _NUMBER_TYPES:
                    add_error(errors, where,
                              "Die geplante Stichprobe braucht ein Zahlenfeld; "
                              f"\u00ab{arg}\u00bb ist keines.")
        transitions.append({
            "from": src, "to": dst, "label": label,
            "requires": requires, "roles": list(raw.get("roles") or []),
        })

    # metrics
    metrics: List[Dict[str, Any]] = []
    seen_metrics: set = set()
    primaries = 0
    for i, raw in enumerate(definition.get("metrics") or []):
        key, role = raw["metric"], raw["role"]
        if key in RESERVED_METRIC_KEYS:
            add_error(errors, p("metrics", i, "metric"), f"\u00ab{key}\u00bb ist kein Metrik-Schl\u00fcssel.")
            continue
        if key in seen_metrics:
            add_error(errors, p("metrics", i, "metric"),
                      f"Die Metrik \u00ab{key}\u00bb steht schon in der Liste.")
            continue
        seen_metrics.add(key)
        entry: Dict[str, Any] = {"metric": key, "role": role}
        if role == "primary":
            primaries += 1
            if primaries > 1:
                add_error(errors, p("metrics", i, "role"), "Es kann nur eine prim\u00e4re Metrik geben.")
        if role == "guardrail":
            entry["op"] = raw["op"]
            entry["value"] = raw["value"]
        elif "op" in raw or "value" in raw:
            add_error(errors, p("metrics", i, "op" if "op" in raw else "value"),
                      "Nur Leitplanken haben \u00abop\u00bb und \u00abvalue\u00bb.")
        metrics.append(entry)

    # evaluation
    evaluation: List[Dict[str, Any]] = []
    seen_evaluations: set = set()
    for i, raw in enumerate(definition.get("evaluation") or []):
        params = raw.get("params") or {}
        if json_size(params) > MAX_PARAMS_BYTES:
            add_error(errors, p("evaluation", i, "params"), "Die Parameter sind gr\u00f6sser als 4 KB.")
        scope, scope_errors = _scope_errors(raw.get("scope"), p("evaluation", i, "scope"))
        for path, message in scope_errors.items():
            add_error(errors, path, message)
        entry = {
            "evaluator": raw["evaluator"], "metric": raw["metric"],
            "params": params, "scope": scope or {},
        }
        fingerprint = json.dumps(entry, sort_keys=True)
        if fingerprint in seen_evaluations:
            add_error(errors, p("evaluation", i), "Diese Auswertung steht schon in der Liste.")
        seen_evaluations.add(fingerprint)
        evaluation.append(entry)

    decision = {
        "require_learning": bool((definition.get("decision") or {}).get("require_learning", False)),
    }

    if errors:
        return None, errors
    return {
        "fields": fields,
        "states": states,
        "initial": initial,
        "transitions": transitions,
        "variants": variants,
        "metrics": metrics,
        "evaluation": evaluation,
        "decision": decision,
    }, {}


def validate_type_definition(definition: Dict[str, Any]) -> Dict[str, Any]:
    """The normalised definition (defaults filled in, labels trimmed).

    Normalising is idempotent, so two definitions are the same exactly when
    their normalised forms are equal -- the service relies on that to skip
    a version that changes nothing.
    """
    normalised, errors = type_definition_errors(definition)
    raise_invalid(errors, "Die Typdefinition ist ung\u00fcltig.")
    return normalised  # type: ignore[return-value]


# -- metric definitions -------------------------------------------------------


def validate_metric_definition(kind: str, definition: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """A metric's ``definition`` for its kind (plan, section 3).

    Level keys are normalised to ``kinds.level_key`` form (``"3.0"`` becomes
    ``"3"``) and sorted by value: measured values are mapped to levels with
    that function, so a key in any other spelling could never match.
    """
    if kind not in kinds.KINDS:
        message = f"Unbekannte Messart \u00ab{str(kind)[:40]}\u00bb."
        raise ValidationError(message, fields={"kind": message})
    base = "Die Metrik-Definition ist ung\u00fcltig."
    if definition is None:
        definition = {}
    definition = checked_json(definition, base, max_nodes=2_000, max_chars=200_000)
    if not isinstance(definition, dict):
        raise ValidationError(base, fields={"definition": "Muss ein Objekt sein."})
    errors = schema_errors(_METRIC_DEFINITION_VALIDATOR, definition)
    raise_invalid(errors, base)

    out: Dict[str, Any] = {}
    if "decimals" in definition:
        out["decimals"] = int(definition["decimals"])
    for bound in ("min", "max"):
        if bound in definition:
            if kind not in kinds.BOUNDED_KINDS:
                add_error(errors, bound,
                          "Minimum und Maximum gibt es nur bei Mittelwert, Dauer, Geldbetrag und Skala.")
            else:
                out[bound] = definition[bound]
    if "min" in out and "max" in out and not out["min"] < out["max"]:
        add_error(errors, "min", "Das Minimum muss kleiner als das Maximum sein.")

    levels = definition.get("levels")
    if levels is None:
        if kind == "categorical":
            add_error(errors, "levels", "Eine Kategorie braucht ihre Stufen (mindestens zwei).")
    elif kind not in kinds.LEVEL_KINDS:
        add_error(errors, "levels", "Stufen gibt es nur bei Skala und Kategorie.")
    else:
        parsed: Dict[str, Tuple[float, str]] = {}
        for raw_key, raw_label in levels.items():
            where = join_path("levels", raw_key)
            text = raw_key.strip()
            if not _LEVEL_KEY_RE.match(text):
                add_error(errors, where, "Stufen werden mit Zahlen bezeichnet (z. B. 1 oder 2.5).")
                continue
            number = float(text)
            if kind == "categorical" and not number.is_integer():
                add_error(errors, where, "Kategorien werden mit ganzen Zahlen bezeichnet.")
                continue
            key = kinds.level_key(number)
            if key in parsed:
                add_error(errors, where, f"Die Stufe \u00ab{key}\u00bb kommt doppelt vor.")
                continue
            label = raw_label.strip()
            if not label:
                add_error(errors, where, "Darf nicht leer sein.")
                continue
            lo, hi = out.get("min"), out.get("max")
            if (lo is not None and number < lo) or (hi is not None and number > hi):
                add_error(errors, where, "Die Stufe liegt ausserhalb von Minimum und Maximum.")
                continue
            parsed[key] = (number, label)
        out["levels"] = {k: v[1] for k, v in sorted(parsed.items(), key=lambda kv: kv[1][0])}
    raise_invalid(errors, base)
    return out


# -- scope --------------------------------------------------------------------


def _parse_instant(raw: Any) -> Optional[_dt.datetime]:
    if isinstance(raw, _dt.datetime):
        value = raw
    elif isinstance(raw, _dt.date):
        value = _dt.datetime(raw.year, raw.month, raw.day)
    elif isinstance(raw, str):
        text = raw.strip()
        match = _SWISS_DATE_RE.match(text)
        try:
            if match:
                day, month, year = (int(g) for g in match.groups())
                value = _dt.datetime(year, month, day)
            elif _ISO_DATE_RE.match(text):
                value = _dt.datetime.combine(_dt.date.fromisoformat(text), _dt.time())
            else:
                value = _dt.datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=_dt.timezone.utc)
    try:
        return value.astimezone(_dt.timezone.utc)
    except (OverflowError, ValueError):
        # 0001-01-01T00:00+01:00 has no UTC equivalent in datetime's range.
        return None


def _dim_value(raw: Any) -> Optional[str]:
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, str):
        return raw
    if isinstance(raw, int):
        return str(raw)
    if isinstance(raw, float) and math.isfinite(raw):
        return kinds.level_key(raw)
    return None


def _dims_errors(dims: Any, path: str, errors: Dict[str, str]) -> Dict[str, str]:
    if not isinstance(dims, dict):
        add_error(errors, path, "Muss ein Objekt sein.")
        return {}
    if len(dims) > 20:
        add_error(errors, path, "H\u00f6chstens 20 Eintr\u00e4ge.")
        return {}
    out: Dict[str, str] = {}
    for key, raw in dims.items():
        where = join_path(path, str(key))
        if not isinstance(key, str) or not _DIM_KEY_RE.match(key):
            add_error(errors, where, f"Ung\u00fcltiger Schl\u00fcssel. {_MSG_DIM_KEY}")
            continue
        value = _dim_value(raw)
        if value is None:
            add_error(errors, where, "Werte sind Text oder Zahlen.")
        elif len(value) > 200 or "\x00" in value:
            add_error(errors, where, "H\u00f6chstens 200 Zeichen.")
        else:
            out[key] = value
    return out


def normalize_dims(dims: Any) -> Dict[str, str]:
    """Measurement ``dims`` / scope ``dims``: at most 20 keys matching
    ``^[A-Za-z0-9_.-]{1,40}$``, values text of at most 200 characters.

    Numbers become text the way ``kinds.level_key`` writes them (``17`` and
    ``17.0`` both become ``"17"``), so a filter and a stored row that name
    the same number always match.
    """
    if dims is None:
        return {}
    errors: Dict[str, str] = {}
    out = _dims_errors(dims, "dims", errors)
    raise_invalid(errors, "Die Dimensionen sind ung\u00fcltig.")
    return out


def _scope_errors(scope: Any, prefix: str) -> Tuple[Dict[str, Any], Dict[str, str]]:
    if scope is None:
        return {}, {}
    errors: Dict[str, str] = {}
    scope = plain_json(scope, prefix, errors, budget=_Budget(5_000, 200_000))
    if errors:
        return {}, errors
    if not isinstance(scope, dict):
        return {}, {prefix or "scope": "Muss ein Objekt sein."}
    errors = schema_errors(_SCOPE_VALIDATOR, scope, prefix)
    if errors:
        return {}, errors
    out: Dict[str, Any] = {}
    runs = scope.get("runs")
    if runs == "latest":
        out["runs"] = "latest"
    elif runs is not None:
        ids: List[str] = []
        for i, raw in enumerate(runs):
            try:
                run_id = str(uuid.UUID(raw.strip()))
            except (ValueError, AttributeError):
                add_error(errors, join_path(prefix, "runs", i), "Keine g\u00fcltige Lauf-ID.")
                continue
            if run_id not in ids:
                ids.append(run_id)
        out["runs"] = ids
    bounds: Dict[str, _dt.datetime] = {}
    for name in ("since", "until"):
        if name in scope:
            instant = _parse_instant(scope[name])
            if instant is None:
                add_error(errors, join_path(prefix, name),
                          "Kein g\u00fcltiges Datum (JJJJ-MM-TT, TT.MM.JJJJ oder ISO 8601).")
            else:
                bounds[name] = instant
                out[name] = instant.isoformat()
    if "since" in bounds and "until" in bounds and bounds["since"] > bounds["until"]:
        add_error(errors, join_path(prefix, "until"), "Das Ende liegt vor dem Beginn.")
    if "dims" in scope:
        dims = _dims_errors(scope["dims"], join_path(prefix, "dims"), errors)
        if dims:
            out["dims"] = dims
    if errors:
        return {}, errors
    return out, {}


def validate_scope(scope: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The rows an aggregate or evaluation uses (plan, section 3); ``{}`` = all.

    Normalised: run ids in canonical UUID form, ``since``/``until`` as UTC
    ISO 8601 (a bare date is midnight UTC), empty ``dims`` dropped.
    """
    out, errors = _scope_errors(scope, "")
    raise_invalid(errors, "Der Datenbereich ist ung\u00fcltig.")
    return out


# -- field values -------------------------------------------------------------


def _empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple)):
        return len(value) == 0
    return False


def _parse_number(raw: Any) -> Optional[float]:
    """A finite number from JSON or a form: ``1'250,5`` is read as 1250.5."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, numbers.Real):
        try:
            value = float(raw)
        except (OverflowError, ValueError):
            return None
        return value if math.isfinite(value) else None
    if isinstance(raw, str):
        text = raw.strip().replace("'", "").replace("\u2019", "").replace(" ", "").replace("\u00a0", "")
        if "," in text and "." not in text and text.count(",") == 1:
            text = text.replace(",", ".")
        if not re.fullmatch(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d{1,3})?", text):
            return None
        value = float(text)
        return value if math.isfinite(value) else None
    return None


def _text_value(raw: Any, limit: int) -> str:
    if not isinstance(raw, str):
        raise ValueError("Muss Text sein.")
    value = raw.strip()
    if len(value) > limit:
        raise ValueError(f"H\u00f6chstens {kinds.format_plain(limit)} Zeichen.")
    if "\x00" in value:
        raise ValueError("Enth\u00e4lt ein Nullzeichen.")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("Enth\u00e4lt ung\u00fcltige Zeichen.") from None
    return value


def _option(raw: Any, options: List[str]) -> str:
    if not isinstance(raw, str):
        raise ValueError("Muss Text sein.")
    text = raw.strip()
    if text in options:
        return text
    folded = {o.casefold(): o for o in options}
    if text.casefold() in folded:
        return folded[text.casefold()]
    raise ValueError(f"\u00ab{text[:80]}\u00bb ist keine der vorgesehenen Optionen.")


def _bounds_message(field: Dict[str, Any]) -> str:
    lo, hi = field.get("min"), field.get("max")
    if lo is not None and hi is not None:
        return f"Erlaubt ist {kinds.format_plain(lo)} bis {kinds.format_plain(hi)}."
    if lo is not None:
        return f"Mindestens {kinds.format_plain(lo)}."
    return f"H\u00f6chstens {kinds.format_plain(hi)}."


def _field_value(field: Dict[str, Any], raw: Any) -> Any:
    ftype = field.get("type")
    if ftype == "text":
        return _text_value(raw, 500)
    if ftype == "longtext":
        return _text_value(raw, 20000)
    if ftype in _NUMBER_TYPES:
        value = _parse_number(raw)
        if value is None:
            raise ValueError("Muss eine endliche Zahl sein." if ftype == "number"
                             else "Muss eine ganze Zahl sein.")
        if ftype == "integer" and not value.is_integer():
            raise ValueError("Muss eine ganze Zahl sein.")
        if value.is_integer() and abs(value) > _MAX_EXACT_INT:
            raise ValueError("Die Zahl ist zu gross.")
        lo, hi = field.get("min"), field.get("max")
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            raise ValueError(_bounds_message(field))
        return int(value) if value.is_integer() else value
    if ftype == "enum":
        return _option(raw, list(field.get("options") or []))
    if ftype == "multi_enum":
        items = [raw] if isinstance(raw, str) else raw
        if not isinstance(items, list):
            raise ValueError("Muss eine Liste sein.")
        options = list(field.get("options") or [])
        chosen = {_option(item, options) for item in items}
        return [o for o in options if o in chosen]
    if ftype == "date":
        if isinstance(raw, _dt.datetime):
            raise ValueError("Bitte ein Datum ohne Uhrzeit angeben (JJJJ-MM-TT).")
        if isinstance(raw, _dt.date):
            return raw.isoformat()
        if isinstance(raw, str):
            text = raw.strip()
            try:
                match = _SWISS_DATE_RE.match(text)
                if match:
                    day, month, year = (int(g) for g in match.groups())
                    return _dt.date(year, month, day).isoformat()
                if _ISO_DATE_RE.match(text):
                    return _dt.date.fromisoformat(text).isoformat()
            except ValueError:
                pass
        raise ValueError("Kein g\u00fcltiges Datum (JJJJ-MM-TT oder TT.MM.JJJJ).")
    if ftype == "url":
        value = _text_value(raw, 2000)
        if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value):
            raise ValueError("Eine Adresse enth\u00e4lt keine Leer- oder Steuerzeichen.")
        try:
            parts = urlsplit(value)
        except ValueError:
            parts = None
        if parts is None or parts.scheme.lower() not in ("http", "https") or not parts.netloc:
            raise ValueError("Bitte eine Adresse mit http:// oder https:// angeben.")
        return value
    if ftype == "boolean":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            folded = raw.strip().casefold()
            if folded in ("true", "ja", "1"):
                return True
            if folded in ("false", "nein", "0"):
                return False
        raise ValueError("Muss ja oder nein sein.")
    raise ValueError("Unbekannter Feldtyp.")


def validate_field_values(definition: Dict[str, Any], values: Dict[str, Any], *,
                          partial: bool = False) -> Dict[str, Any]:
    """An experiment's field values, checked against its type's fields.

    ``""``, ``None`` and ``[]`` mean "no value". With ``partial=False`` (a
    complete set, e.g. on create) such keys are left out and required fields
    must be present. With ``partial=True`` (an update) only the sent keys are
    checked and a cleared key comes back as ``None`` so the caller can remove
    it; clearing a required field is refused either way.
    """
    fields = {f["key"]: f for f in (definition or {}).get("fields") or []}
    if values is None:
        values = {}
    if not isinstance(values, dict):
        raise ValidationError("Die Angaben m\u00fcssen ein Objekt sein.")
    errors: Dict[str, str] = {}
    out: Dict[str, Any] = {}
    for key, raw in values.items():
        field = fields.get(key) if isinstance(key, str) else None
        if field is None:
            add_error(errors, str(key)[:60], "Unbekanntes Feld.")
            continue
        label = field.get("label") or key
        if _empty(raw):
            if field.get("required"):
                add_error(errors, key, f"Bitte \u00ab{label}\u00bb ausf\u00fcllen.")
            elif partial:
                out[key] = None
            continue
        try:
            out[key] = _field_value(field, raw)
        except ValueError as exc:
            add_error(errors, key, str(exc))
    if not partial:
        for key, field in fields.items():
            if field.get("required") and key not in out and key not in errors:
                add_error(errors, key, f"Bitte \u00ab{field.get('label') or key}\u00bb ausf\u00fcllen.")
    if len(errors) == 1:
        key, message = next(iter(errors.items()))
        label = fields[key].get("label", key) if key in fields else key
        raise ValidationError(f"\u00ab{label}\u00bb: {message}", fields=errors)
    if errors:
        raise ValidationError("Bitte die markierten Angaben pr\u00fcfen.", fields=errors)
    return out


def display_field_value(field: Dict[str, Any], value: Any) -> str:
    """A field value for people: ``ja``/``nein``, dates as TT.MM.JJJJ,
    numbers with decimal comma and apostrophes; ``\u2013`` when empty."""
    if _empty(value):
        return kinds.DASH
    ftype = (field or {}).get("type")
    if ftype == "boolean" or isinstance(value, bool):
        return "ja" if value else "nein"
    if ftype == "date" and isinstance(value, str):
        match = _ISO_DATE_RE.match(value.strip())
        if match:
            year, month, day = match.groups()
            return f"{day}.{month}.{year}"
        return value
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    if isinstance(value, numbers.Real):
        return kinds.format_plain(value)
    return str(value)


# -- lifecycle ----------------------------------------------------------------


def _states(definition: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list((definition or {}).get("states") or [])


def initial_state(definition: Dict[str, Any]) -> str:
    initial = (definition or {}).get("initial")
    if initial:
        return initial
    for state in _states(definition):
        if state.get("phase") not in _TERMINAL_PHASES:
            return state["key"]
    raise ValidationError("Der Typ hat keinen Anfangsstatus.")


def state_label(definition: Dict[str, Any], state: str) -> str:
    """The state's label; the key itself for a state the definition lacks."""
    for item in _states(definition):
        if item.get("key") == state:
            return item.get("label") or state
    return state


def state_phase(definition: Dict[str, Any], state: str) -> Optional[str]:
    for item in _states(definition):
        if item.get("key") == state:
            return item.get("phase")
    return None


def phase_state(definition: Dict[str, Any], phase: str) -> Optional[str]:
    """The state carrying ``phase`` (at most one does)."""
    for item in _states(definition):
        if item.get("phase") == phase:
            return item.get("key")
    return None


def _transition_list(definition: Dict[str, Any], state: str) -> List[Dict[str, Any]]:
    """Transitions leaving ``state``, in definition order.

    ``from: "*"`` stands for every state except the target. A transition
    written for this state explicitly wins over a ``*`` one to the same
    target, so a type can gate "cancel" differently in one state.
    """
    if state not in {s.get("key") for s in _states(definition)}:
        return []
    transitions = list((definition or {}).get("transitions") or [])
    explicit = {t.get("to") for t in transitions if t.get("from") == state}
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for t in transitions:
        target = t.get("to")
        if target in seen or target == state:
            continue
        if t.get("from") == state or (t.get("from") == "*" and target not in explicit):
            seen.add(target)
            out.append(t)
    return out


def transitions_from(definition: Dict[str, Any], state: str) -> List[Dict[str, Any]]:
    """``[{"to", "label", "requires", "roles"}]`` for the transitions leaving ``state``."""
    return [
        {
            "to": t.get("to"),
            "label": t.get("label") or t.get("to"),
            "requires": list(t.get("requires") or []),
            "roles": list(t.get("roles") or []),
        }
        for t in _transition_list(definition, state)
    ]


def _filled(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return len(value) > 0
    return True


def _role_names(roles: Iterable[str]) -> str:
    return " oder ".join(_ROLE_NAMES.get(r, r) for r in roles)


def _field_label(definition: Dict[str, Any], key: str) -> str:
    for field in (definition or {}).get("fields") or []:
        if field.get("key") == key:
            return field.get("label") or key
    return key


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _unmet(definition: Dict[str, Any], requirement: str, facts: Dict[str, Any]) -> Optional[str]:
    name, arg = _parse_requirement(requirement)
    if name == "hypothesis":
        return None if facts.get("hypothesis") else "Die Hypothese fehlt."
    if name == "primary_metric":
        return None if facts.get("primary_metric") else "Es ist keine prim\u00e4re Metrik festgelegt."
    if name == "variants":
        needed = _as_int(arg)
        if _as_int(facts.get("variants")) >= needed:
            return None
        if needed == 1:
            return "Es braucht mindestens eine Variante."
        return f"Es braucht mindestens {needed} Varianten."
    if name == "measurements":
        return None if _as_int(facts.get("measurements")) > 0 else "Es gibt noch keine Messwerte."
    if name == "evaluation":
        if _as_int(facts.get("evaluations")) > 0:
            return None
        return "Es gibt noch keine abgeschlossene Auswertung."
    if name == "decision":
        return None if facts.get("decision") else "Es ist noch keine Entscheidung festgehalten."
    if name == "learning":
        return None if facts.get("learning") else "Die Erkenntnis fehlt."
    fields = facts.get("fields") or {}
    if name == "field" and arg:
        if _filled(fields.get(arg)):
            return None
        return f"Das Feld \u00ab{_field_label(definition, arg)}\u00bb ist leer."
    if name == "n_planned" and arg:
        planned = _parse_number(fields.get(arg))
        if planned is None:
            return (
                "Die geplante Stichprobe ist noch nicht festgelegt "
                f"(Feld \u00ab{_field_label(definition, arg)}\u00bb)."
            )
        counts = [
            float(n or 0) for key, n in (facts.get("variant_n") or {}).items()
            if key is not None
        ]
        # A variant without any row of the primary metric may be missing
        # from variant_n; it still has n = 0.
        if len(counts) < _as_int(facts.get("variants")) or not counts:
            counts.append(0.0)
        reached = min(counts)
        if reached >= planned:
            return None
        return (
            "Die geplante Stichprobe ist noch nicht erreicht "
            f"({kinds.format_plain(reached)} von {kinds.format_plain(planned)} je Variante)."
        )
    return f"Unbekannte Bedingung \u00ab{requirement[:60]}\u00bb."


def check_transition(definition: Dict[str, Any], from_state: str, to_state: str, *,
                     facts: Dict[str, Any], roles: FrozenSet[str]) -> List[str]:
    """Whether ``from_state -> to_state`` may happen now.

    Raises ValidationError when the type has no such transition and
    Forbidden when the actor's roles are not among the transition's
    ``roles`` (admin always may; no roles = every viewer). Otherwise returns
    the German messages of the unmet requirements; ``[]`` means allowed.
    """
    transition = next(
        (t for t in _transition_list(definition, from_state) if t.get("to") == to_state), None
    )
    if transition is None:
        raise ValidationError("Dieser Statuswechsel ist nicht vorgesehen.")
    allowed = list(transition.get("roles") or [])
    actor_roles = frozenset(roles or ())
    if allowed and "admin" not in actor_roles and not (actor_roles & set(allowed)):
        raise Forbidden(f"Diesen Statuswechsel d\u00fcrfen nur {_role_names(allowed)} ausf\u00fchren.")
    facts = facts or {}
    messages: List[str] = []
    for requirement in transition.get("requires") or []:
        message = _unmet(definition, requirement, facts)
        if message:
            messages.append(message)
    return messages


# -- references ---------------------------------------------------------------


def metric_refs(definition: Dict[str, Any]) -> List[str]:
    """Metric keys the definition names (metrics list and evaluation), in order."""
    out: List[str] = []
    for entry in (definition or {}).get("metrics") or []:
        key = entry.get("metric")
        if key and key not in out:
            out.append(key)
    for entry in (definition or {}).get("evaluation") or []:
        key = entry.get("metric")
        if key and key not in RESERVED_METRIC_KEYS and key not in out:
            out.append(key)
    return out


def evaluator_refs(definition: Dict[str, Any]) -> List[str]:
    """Evaluator keys the evaluation list names, in order."""
    out: List[str] = []
    for entry in (definition or {}).get("evaluation") or []:
        key = entry.get("evaluator")
        if key and key not in out:
            out.append(key)
    return out
