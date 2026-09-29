"""Packs: a domain with its metrics, experiment types and evaluators as one
YAML (or JSON) document -- configuration as code.

Knovas ships five (``packs/*.yaml``): ``core`` with the global type
\u00abAllgemeine Hypothese\u00bb and two example evaluators, and one pack each for
engineering, marketing, sales and product. Managers install them from the UI,
export a domain as a pack, edit it anywhere and import it again.

A pack is validated as a whole before anything is written: every type
definition through schema.validate_type_definition, every metric definition
through schema.validate_metric_definition, and every reference must resolve
-- a metric within the pack, among the global keys the pack declares in
``requires_metrics``, or among ``known_metrics``; an evaluator within the
pack, among the built-in ones, or among ``known_evaluators``. A pack that
installs half-way and then fails on a dangling reference would leave a
domain nobody can use.

Parsing goes through schema.parse_text only (JSON, or YAML with the
NoAliasSafeLoader), capped at 2 MB.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from experiments import kinds, schema
from experiments.errors import NotFound, ValidationError

logger = logging.getLogger(__name__)

PACKS_DIR: Path = Path(__file__).resolve().parent / "packs"

MAX_PACK_BYTES = 2 * 1024 * 1024
MAX_PARAMS_SCHEMA_BYTES = 16 * 1024
MAX_CODE_CHARS = 200_000

PACK_NAME_PATTERN = r"^[a-z][a-z0-9-]{1,31}$"
DOMAIN_KEY_PATTERN = r"^[a-z][a-z0-9-]{1,31}$"
ID_PREFIX_PATTERN = r"^[A-Z][A-Z0-9]{1,7}$"
COLOR_PATTERN = r"^#[0-9A-Fa-f]{6}$"
TYPE_KEY_PATTERN = r"^[a-z][a-z0-9_-]{1,47}$"
DEFAULT_COLOR = "#5A6B80"

#: Shipped packs in the order the UI offers them; any further file follows
#: alphabetically.
_SHIPPED_ORDER = ("core", "engineering", "marketing", "sales", "product")
_PACK_NAME_RE = re.compile(PACK_NAME_PATTERN)
_NOT_FOUND = "Das Paket gibt es nicht."
_BASE = "Das Paket ist ung\u00fcltig."

_LONG_TEXT = {"type": "string", "maxLength": 2000}


PACK_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Experiments pack",
    "type": "object",
    "additionalProperties": False,
    "required": ["pack", "title", "version"],
    "properties": {
        "pack": schema.key_schema(PACK_NAME_PATTERN,
                                  "Nur Kleinbuchstaben, Ziffern und -, beginnend mit einem Buchstaben; "
                                  "2 bis 32 Zeichen."),
        "title": schema.text_schema(80),
        "description": _LONG_TEXT,
        "version": {"type": "integer", "minimum": 1, "maximum": 1_000_000},
        "domain": {
            "type": "object",
            "additionalProperties": False,
            "required": ["key", "name", "id_prefix"],
            "properties": {
                "key": schema.key_schema(DOMAIN_KEY_PATTERN,
                                         "Nur Kleinbuchstaben, Ziffern und -, beginnend mit einem "
                                         "Buchstaben; 2 bis 32 Zeichen."),
                "name": schema.text_schema(80),
                "id_prefix": schema.key_schema(ID_PREFIX_PATTERN,
                                               "Grossbuchstaben und Ziffern, beginnend mit einem "
                                               "Buchstaben; 2 bis 8 Zeichen (z. B. MKT)."),
                "color": schema.key_schema(COLOR_PATTERN, "Eine Farbe im Format #RRGGBB."),
                "description": _LONG_TEXT,
            },
        },
        "metrics": {
            "type": "array",
            "maxItems": 200,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["key", "name", "kind"],
                "properties": {
                    "key": schema.key_schema(schema.METRIC_KEY_PATTERN, schema.METRIC_KEY_MESSAGE),
                    "name": schema.text_schema(80),
                    "kind": {"enum": list(kinds.KINDS)},
                    "unit": schema.text_schema(20, min_length=0),
                    "direction": {"enum": ["higher", "lower", "none"]},
                    "description": _LONG_TEXT,
                    "definition": {"type": "object"},
                },
            },
        },
        "types": {
            "type": "array",
            "maxItems": 100,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["key", "name", "definition"],
                "properties": {
                    "key": schema.key_schema(TYPE_KEY_PATTERN,
                                             "Nur Kleinbuchstaben, Ziffern, _ und -, beginnend mit einem "
                                             "Buchstaben; 2 bis 48 Zeichen."),
                    "name": schema.text_schema(80),
                    "description": _LONG_TEXT,
                    "definition": {"type": "object"},
                },
            },
        },
        "evaluators": {
            "type": "array",
            "maxItems": 50,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["key", "name", "language", "input_kinds", "code"],
                "properties": {
                    "key": schema.key_schema(schema.EVALUATOR_KEY_PATTERN, schema.EVALUATOR_KEY_MESSAGE),
                    "name": schema.text_schema(80),
                    "language": {"enum": ["python", "julia"]},
                    "description": _LONG_TEXT,
                    "input_kinds": {
                        "type": "array", "minItems": 1, "maxItems": len(kinds.KINDS),
                        "uniqueItems": True, "items": {"enum": list(kinds.KINDS)},
                    },
                    "params_schema": {"type": "object"},
                    "code": {"type": "string", "minLength": 1, "maxLength": MAX_CODE_CHARS},
                },
            },
        },
        "requires_metrics": {
            "type": "array", "maxItems": 200, "uniqueItems": True,
            "items": schema.key_schema(schema.METRIC_KEY_PATTERN, schema.METRIC_KEY_MESSAGE),
        },
    },
}

Draft202012Validator.check_schema(PACK_SCHEMA)
_PACK_VALIDATOR = Draft202012Validator(PACK_SCHEMA)


# -- built-in evaluators ------------------------------------------------------


def _builtin_specs() -> Dict[str, Any]:
    """evaluators.BUILTINS, imported only when a pack is validated.

    Imported lazily so that parsing and listing packs never pulls in the
    statistics code (and so tests can stand in for it).
    """
    from experiments import evaluators

    return dict(evaluators.BUILTINS)


def _input_kinds(spec: Any) -> Tuple[str, ...]:
    if isinstance(spec, dict):
        return tuple(spec.get("input_kinds") or ())
    return tuple(getattr(spec, "input_kinds", ()) or ())


def _params_schema(spec: Any) -> Dict[str, Any]:
    if isinstance(spec, dict):
        found = spec.get("params_schema")
    else:
        found = getattr(spec, "params_schema", None)
    return found if isinstance(found, dict) else {}


# -- params schemas -----------------------------------------------------------

#: Keywords whose value is a map of name -> subschema.
_SCHEMA_MAPS = ("properties", "$defs", "definitions", "dependentSchemas")
#: Keywords whose value is one subschema.
_SCHEMA_ONE = (
    "items", "additionalProperties", "not", "if", "then", "else", "contains",
    "propertyNames", "unevaluatedProperties", "unevaluatedItems", "additionalItems",
)
#: Keywords whose value is a list of subschemas.
_SCHEMA_LIST = ("allOf", "anyOf", "oneOf", "prefixItems")


def _schema_hazards(node: Any, path: str, errors: Dict[str, str]) -> None:
    """Refuse what would let a params schema hurt the web process.

    ``pattern`` / ``patternProperties`` run a manager-supplied regular
    expression inside the Platform on every request (catastrophic
    backtracking stalls a worker); a ``$ref`` to anything but this document
    would make jsonschema try to resolve a URL.
    """
    if not isinstance(node, dict):
        return
    for key, value in node.items():
        where = schema.join_path(path, key)
        if key in ("pattern", "patternProperties"):
            schema.add_error(errors, where,
                             "Muster (pattern) sind in Parameterschemata nicht erlaubt.")
        elif key in ("$ref", "$dynamicRef"):
            if not isinstance(value, str) or not value.startswith("#"):
                schema.add_error(errors, where, "Nur Verweise innerhalb des Schemas (#/...) sind erlaubt.")
        elif key in _SCHEMA_MAPS and isinstance(value, dict):
            for name, sub in value.items():
                _schema_hazards(sub, schema.join_path(where, name), errors)
        elif key in _SCHEMA_ONE:
            _schema_hazards(value, where, errors)
        elif key in _SCHEMA_LIST and isinstance(value, list):
            for i, sub in enumerate(value):
                _schema_hazards(sub, schema.join_path(where, i), errors)


def _params_schema_errors(params_schema: Any, path: str) -> Tuple[Dict[str, Any], Dict[str, str]]:
    errors: Dict[str, str] = {}
    if params_schema is None:
        return {}, errors
    if not isinstance(params_schema, dict):
        return {}, {path: "Muss ein Objekt sein."}
    if schema.json_size(params_schema) > MAX_PARAMS_SCHEMA_BYTES:
        return {}, {path: "Das Parameterschema ist gr\u00f6sser als 16 KB."}
    if "type" in params_schema and params_schema["type"] != "object":
        schema.add_error(errors, schema.join_path(path, "type"),
                         "Parameter sind immer ein Objekt (type: object).")
    _schema_hazards(params_schema, path, errors)
    if errors:
        return {}, errors
    try:
        Draft202012Validator.check_schema(params_schema)
    except SchemaError as exc:
        where = schema.join_path(path, *exc.path) if exc.path else path
        return {}, {where: "Das Parameterschema ist kein g\u00fcltiges JSON Schema."}
    return params_schema, {}


def validate_params_schema(params_schema: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """An evaluator's params schema: a valid JSON Schema for an object, at most
    16 KB, without ``pattern`` and without references outside itself.

    Used for pack evaluators; the service can use it for evaluators created
    in the UI so both paths accept the same schemas.
    """
    normalised, errors = _params_schema_errors(params_schema, "params_schema")
    schema.raise_invalid(errors, "Das Parameterschema ist ung\u00fcltig.")
    return normalised


def _params_errors(params_schema: Dict[str, Any], params: Dict[str, Any], path: str) -> Dict[str, str]:
    if not params_schema:
        return {}
    try:
        validator = Draft202012Validator(params_schema)
        return schema.schema_errors(validator, params, path)
    except Exception:  # noqa: BLE001 - a broken schema must not escape as a 500
        logger.warning("Could not apply a params schema while validating a pack", exc_info=True)
        return {path: "Die Parameter lassen sich nicht pr\u00fcfen."}


# -- validation ---------------------------------------------------------------


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def validate_pack(pack: Dict[str, Any], *, known_metrics: Iterable[str] = (),
                  known_evaluators: Iterable[str] = ()) -> Dict[str, Any]:
    """A normalised copy of ``pack``, or ValidationError with ``fields``
    keyed by path (``types[1].definition.states[0].key``).

    ``known_metrics`` are global metric keys that already exist, and
    ``known_evaluators`` evaluator keys that already exist. The normalised
    ``requires_metrics`` lists every metric the types use but the pack does
    not define, so an exported pack always names what it depends on; the
    installer must resolve those keys among the global metrics.
    """
    pack = schema.checked_json(pack, _BASE, max_nodes=400_000,
                               max_chars=2 * MAX_PACK_BYTES)
    if not isinstance(pack, dict):
        raise ValidationError(_BASE, fields={"pack": "Muss ein Objekt sein."})
    errors = schema.schema_errors(_PACK_VALIDATOR, pack)
    schema.raise_invalid(errors, _BASE)

    known_metric_set = {k for k in known_metrics if isinstance(k, str)}
    known_evaluator_set = {k for k in known_evaluators if isinstance(k, str)}

    out: Dict[str, Any] = {
        "pack": pack["pack"],
        "title": _clean(pack["title"]),
        "description": _clean(pack.get("description", "")),
        "version": int(pack["version"]),
    }
    if not out["title"]:
        schema.add_error(errors, "title", "Darf nicht leer sein.")

    domain = pack.get("domain")
    if domain is not None:
        out["domain"] = {
            "key": domain["key"],
            "name": _clean(domain["name"]),
            "id_prefix": domain["id_prefix"],
            "color": domain.get("color", DEFAULT_COLOR),
            "description": _clean(domain.get("description", "")),
        }
        if not out["domain"]["name"]:
            schema.add_error(errors, "domain.name", "Darf nicht leer sein.")

    # metrics
    metrics: List[Dict[str, Any]] = []
    metric_kinds: Dict[str, str] = {}
    metric_defs: Dict[str, Dict[str, Any]] = {}
    for i, raw in enumerate(pack.get("metrics") or []):
        key = raw["key"]
        where = schema.join_path("metrics", i)
        if key in schema.RESERVED_METRIC_KEYS:
            schema.add_error(errors, f"{where}.key",
                             f"\u00ab{key}\u00bb ist als Metrik-Schl\u00fcssel reserviert.")
            continue
        if key in metric_kinds:
            schema.add_error(errors, f"{where}.key", f"Die Metrik \u00ab{key}\u00bb gibt es schon.")
            continue
        name = _clean(raw["name"])
        if not name:
            schema.add_error(errors, f"{where}.name", "Darf nicht leer sein.")
        try:
            definition = schema.validate_metric_definition(raw["kind"], raw.get("definition"))
        except ValidationError as exc:
            for path, message in (exc.fields or {"": exc.message}).items():
                schema.add_error(errors, schema.join_path(f"{where}.definition", path)
                                 if path else f"{where}.definition", message)
            definition = {}
        metric_kinds[key] = raw["kind"]
        metric_defs[key] = definition
        metrics.append({
            "key": key, "name": name, "kind": raw["kind"],
            "unit": _clean(raw.get("unit", "")),
            "direction": raw.get("direction", "higher"),
            "description": _clean(raw.get("description", "")),
            "definition": definition,
        })
    out["metrics"] = metrics

    declared_external: List[str] = []
    for i, key in enumerate(pack.get("requires_metrics") or []):
        if key in metric_kinds:
            schema.add_error(errors, schema.join_path("requires_metrics", i),
                             f"Die Metrik \u00ab{key}\u00bb ist im Paket selbst definiert.")
        else:
            declared_external.append(key)

    # evaluators
    evaluators: List[Dict[str, Any]] = []
    pack_evaluators: Dict[str, Dict[str, Any]] = {}
    for i, raw in enumerate(pack.get("evaluators") or []):
        key = raw["key"]
        where = schema.join_path("evaluators", i)
        if key.startswith("builtin."):
            schema.add_error(errors, f"{where}.key",
                             "Schl\u00fcssel mit \u00abbuiltin.\u00bb sind den eingebauten "
                             "Auswertern vorbehalten.")
            continue
        if key in pack_evaluators:
            schema.add_error(errors, f"{where}.key", f"Den Auswerter \u00ab{key}\u00bb gibt es schon.")
            continue
        name = _clean(raw["name"])
        if not name:
            schema.add_error(errors, f"{where}.name", "Darf nicht leer sein.")
        params_schema, schema_errs = _params_schema_errors(
            raw.get("params_schema"), f"{where}.params_schema"
        )
        for path, message in schema_errs.items():
            schema.add_error(errors, path, message)
        code = raw["code"]
        if not code.strip():
            schema.add_error(errors, f"{where}.code", "Darf nicht leer sein.")
        entry = {
            "key": key, "name": name, "language": raw["language"],
            "description": _clean(raw.get("description", "")),
            "input_kinds": list(raw["input_kinds"]),
            "params_schema": params_schema,
            "code": code,
        }
        pack_evaluators[key] = entry
        evaluators.append(entry)

    builtins: Optional[Dict[str, Any]] = None

    def evaluator_spec(key: str) -> Tuple[bool, Tuple[str, ...], Dict[str, Any]]:
        """(known, input kinds, params schema) of an evaluator reference."""
        nonlocal builtins
        if key in pack_evaluators:
            entry = pack_evaluators[key]
            return True, tuple(entry["input_kinds"]), entry["params_schema"]
        if key.startswith("builtin."):
            if builtins is None:
                builtins = _builtin_specs()
            spec = builtins.get(key)
            if spec is not None:
                return True, _input_kinds(spec), _params_schema(spec)
        if key in known_evaluator_set:
            return True, (), {}
        return False, (), {}

    external: List[str] = list(declared_external)

    def resolve_metric(metric: str, path: str) -> Optional[str]:
        """The metric's kind when the pack defines it; None for a global one
        (recorded in requires_metrics) or an unknown one (an error)."""
        if metric in metric_kinds:
            return metric_kinds[metric]
        if metric in known_metric_set or metric in declared_external:
            if metric not in external:
                external.append(metric)
            return None
        schema.add_error(errors, path, f"Die Metrik \u00ab{metric}\u00bb gibt es weder im Paket "
                                       "noch unter requires_metrics.")
        return None

    # types
    types: List[Dict[str, Any]] = []
    type_keys: set = set()
    for i, raw in enumerate(pack.get("types") or []):
        key = raw["key"]
        where = schema.join_path("types", i)
        if key in type_keys:
            schema.add_error(errors, f"{where}.key", f"Den Typ \u00ab{key}\u00bb gibt es schon.")
            continue
        type_keys.add(key)
        name = _clean(raw["name"])
        if not name:
            schema.add_error(errors, f"{where}.name", "Darf nicht leer sein.")
        definition, def_errors = schema.type_definition_errors(
            raw["definition"], f"{where}.definition"
        )
        for path, message in def_errors.items():
            schema.add_error(errors, path, message)
        if definition is None:
            continue

        primary: Optional[str] = None
        for j, entry in enumerate(definition["metrics"]):
            mwhere = f"{where}.definition.metrics[{j}]"
            kind = resolve_metric(entry["metric"], f"{mwhere}.metric")
            if entry["role"] == "primary":
                primary = entry["metric"]
            if entry["role"] == "guardrail" and kind is not None:
                value = float(entry["value"])
                bounds = metric_defs.get(entry["metric"], {})
                if kind == "proportion" and not 0.0 <= value <= 1.0:
                    schema.add_error(errors, f"{mwhere}.value",
                                     "Anteile werden als Bruch angegeben (0.7 f\u00fcr 70 %).")
                elif ("min" in bounds and value < bounds["min"]) or (
                        "max" in bounds and value > bounds["max"]):
                    schema.add_error(errors, f"{mwhere}.value",
                                     "Der Wert liegt ausserhalb von Minimum und Maximum der Metrik.")

        for j, entry in enumerate(definition["evaluation"]):
            ewhere = f"{where}.definition.evaluation[{j}]"
            known, accepted, params_schema = evaluator_spec(entry["evaluator"])
            if not known:
                schema.add_error(errors, f"{ewhere}.evaluator",
                                 f"Den Auswerter \u00ab{entry['evaluator']}\u00bb gibt es nicht.")
                continue
            metric = entry["metric"]
            kind = None
            if metric == "primary":
                kind = metric_kinds.get(primary) if primary else None
            elif metric != "all":
                kind = resolve_metric(metric, f"{ewhere}.metric")
            if kind is not None and accepted and kind not in accepted:
                label = kinds.KINDS[kind].label
                schema.add_error(errors, f"{ewhere}.metric",
                                 f"Der Auswerter \u00ab{entry['evaluator']}\u00bb nimmt keine Metriken "
                                 f"der Art \u00ab{label}\u00bb.")
            for path, message in _params_errors(params_schema, entry["params"],
                                                f"{ewhere}.params").items():
                schema.add_error(errors, path, message)

        types.append({
            "key": key, "name": name,
            "description": _clean(raw.get("description", "")),
            "definition": definition,
        })
    out["types"] = types
    out["evaluators"] = evaluators
    out["requires_metrics"] = external

    schema.raise_invalid(errors, _BASE)
    return out


# -- text ---------------------------------------------------------------------


def parse_pack_text(text: str, *, known_metrics: Iterable[str] = (),
                    known_evaluators: Iterable[str] = ()) -> Dict[str, Any]:
    """A pack from YAML or JSON text (at most 2 MB), validated and normalised."""
    data = schema.parse_text(text, max_bytes=MAX_PACK_BYTES, what="Das Paket")
    return validate_pack(data, known_metrics=known_metrics,
                         known_evaluators=known_evaluators)


class _PackDumper(yaml.SafeDumper):
    """SafeDumper that writes multi-line text (evaluator code, descriptions)
    as literal blocks, so an exported pack stays readable and diffable, and
    never writes anchors: parse_pack_text refuses them, so a pack whose dict
    shares a sub-object must still dump into text that imports again."""

    def ignore_aliases(self, data: Any) -> bool:
        return True


def _represent_str(dumper: yaml.SafeDumper, value: str) -> Any:
    if "\n" in value:
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", value)


_PackDumper.add_representer(str, _represent_str)


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def dump_pack(pack: Dict[str, Any]) -> str:
    """YAML text of a pack (keys in the given order, real umlauts)."""
    return yaml.dump(_plain(pack), Dumper=_PackDumper, sort_keys=False,
                     allow_unicode=True, default_flow_style=False, width=100)


# -- shipped packs ------------------------------------------------------------


def _shipped_files() -> Dict[str, Path]:
    found: Dict[str, Path] = {}
    try:
        candidates = sorted(PACKS_DIR.glob("*.yaml"))
    except OSError:
        return found
    for path in candidates:
        if _PACK_NAME_RE.match(path.stem) and path.is_file():
            found[path.stem] = path
    return found


def _ordered(names: Iterable[str]) -> List[str]:
    names = list(names)
    first = [n for n in _SHIPPED_ORDER if n in names]
    return first + sorted(n for n in names if n not in _SHIPPED_ORDER)


def available_packs() -> List[Dict[str, Any]]:
    """``[{"name", "title", "description", "version", "domain_key"}]``, core first.

    Reads each file's header only; a file that cannot be parsed is logged
    and left out, so one broken file does not hide the others.
    """
    files = _shipped_files()
    result: List[Dict[str, Any]] = []
    for name in _ordered(files):
        try:
            data = schema.parse_text(files[name].read_text(encoding="utf-8"),
                                     max_bytes=MAX_PACK_BYTES, what="Das Paket")
        except (OSError, UnicodeDecodeError, ValidationError):
            logger.warning("Experiments pack %s.yaml cannot be read; not offered", name,
                           exc_info=True)
            continue
        if not isinstance(data, dict):
            continue
        domain = data.get("domain") if isinstance(data.get("domain"), dict) else None
        version = data.get("version")
        result.append({
            "name": name,
            "title": str(data.get("title") or name),
            "description": str(data.get("description") or ""),
            "version": version if isinstance(version, int) and not isinstance(version, bool) else 1,
            "domain_key": str(domain["key"]) if domain and domain.get("key") else None,
        })
    return result


def load_pack(name: str) -> Dict[str, Any]:
    """A shipped pack, validated; NotFound("Das Paket gibt es nicht.")."""
    if not isinstance(name, str) or not _PACK_NAME_RE.match(name):
        raise NotFound(_NOT_FOUND)
    path = _shipped_files().get(name)
    if path is None:
        raise NotFound(_NOT_FOUND)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        logger.error("Experiments pack %s.yaml cannot be read", name, exc_info=True)
        raise NotFound(_NOT_FOUND) from None
    pack = parse_pack_text(text)
    if pack["pack"] != name:
        raise ValidationError(
            f"Die Datei {name}.yaml enth\u00e4lt das Paket \u00ab{pack['pack']}\u00bb.",
            fields={"pack": "Passt nicht zum Dateinamen."},
        )
    return pack
