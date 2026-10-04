"""Upload ``fields`` for one document: the pure part (spec sections 3.3-3.7).

Per source the Platform may configure static values (``fields``), path
templates (``field_templates``) and extractor metadata items
(``metadata_fields``). This module turns them into the init ``fields`` of
one document, a digest of the configuration that governs it, and the
reading of the server's answer. It does no I/O, imports no HTTP client and
never logs: values, captures, paths and pointers are customer data, and
only keys, codes and counts may leave the data path. The uploader and the
executor (``knovas_uploader``, ``sync_executor``) call it.

Precedence per key: template capture, then static value, then metadata.
The winner is chosen by presence first and validated second, so a capture
that is dropped (too long) never lets a static value for the same key
through: the document would carry a value its path contradicts. An empty
capture is no value and does not shadow. Client-side caps mirror the
server's init bounds (64 keys, 32 values per key, 16384 bytes of compact
UTF-8 JSON) plus the contract's 256 characters per string, so an upload is
never refused for a size the RC could have avoided. Every dropped value is
counted by reason (``DROP_REASONS``).

What the server says about ``fields``:

* 2xx with a ``fields`` echo (top level, next to ``transmission_key_id``):
  ``staged`` (or ``cleared`` when ``{}`` was sent).
* 2xx without it: ``not_accepted`` -- the server is off or old, or the
  tenant is outside the feature. Never reported as stored (H7).
* A refusal ``classify_init_refusal`` recognises: the uploader re-posts the
  init once without ``fields``; only if that succeeds is the outcome
  ``refused:<code>`` (D5).
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Mapping as MappingABC
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Mapping, Optional, Union

from sync import metadata_fields as _metadata
from sync.field_templates import (  # noqa: F401 - TemplateError is re-exported
    KEY_RE,
    SYSTEM_KEYS,
    CompiledTemplate,
    TemplateError,
    captures,
    compile_templates,
)

#: Version of the rules in this module; part of the config digest, so a
#: bump re-sends every document whose source has fields configured.
FIELDS_PAYLOAD_VERSION = 1

MAX_KEYS = 64
MAX_VALUES_PER_KEY = 32
MAX_VALUE_CHARS = 256
MAX_PAYLOAD_BYTES = 16384

LAYER_CAPTURE = "capture"
LAYER_STATIC = "static"
LAYER_METADATA = "metadata"
_PRECEDENCE = (LAYER_CAPTURE, LAYER_STATIC, LAYER_METADATA)
_DROP_ORDER = (LAYER_METADATA, LAYER_STATIC, LAYER_CAPTURE)

#: Closed set of reasons ``Payload.dropped`` counts (a metric label).
DROP_REASONS = frozenset({"system_key", "value_too_long", "cap_exceeded", "too_large", "invalid_value"})

FieldScalar = Union[str, int, float, bool]
FieldValue = Union[FieldScalar, tuple[FieldScalar, ...]]

# --- init refusals (section 3.6) ---------------------------------------------

#: 400/422 codes that name the ``fields`` themselves.
FIELD_SHAPE_CODES = frozenset({"invalid_fields", "fields_too_large", "ambiguous_field", "unknown_field"})
#: 503 codes: the fields could not be staged now; a later re-upload may succeed.
TRANSIENT_REFUSAL_CODES = frozenset({"doc_fields_ingest_unavailable", "doc_fields_unavailable"})
#: 401 of a BROKERED tenant: an entity value or register mode needed the
#: uploader's principal and the RC sends no assertion.
ASSERTION_REJECTED = "assertion_rejected"
#: A 400/422 whose ``path`` lies under ``fields`` but whose code is not known.
REFUSAL_OTHER = "other"
#: Every code ``classify_init_refusal`` returns (closed: status and metrics).
FIELD_REFUSAL_CODES = FIELD_SHAPE_CODES | TRANSIENT_REFUSAL_CODES | {ASSERTION_REJECTED, REFUSAL_OTHER}

# --- outcomes (sections 3.6 and 3.7) -----------------------------------------

OUTCOME_STAGED = "staged"
OUTCOME_CLEARED = "cleared"
OUTCOME_NOT_ACCEPTED = "not_accepted"
OUTCOME_NONE = "none"
REFUSED_PREFIX = "refused:"
REUPLOAD_FAILED_PREFIX = "reupload_failed:"
REUPLOAD_FAILURE_CLASSES = frozenset(
    {"init_401", "init_403", "init_4xx", "init_5xx", "fields_unavailable", "extract", "other"}
)

#: Warning and error codes are server-defined identifiers; anything that
#: does not look like one is counted as ``other`` so no value can slip into
#: the status or a metric label.
_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
OTHER_CODE = "other"
MAX_SUGGESTIONS = 3


# --- the per-source configuration --------------------------------------------


@dataclass(frozen=True)
class SourceSpec:
    """What one sync source contributes to each of its documents.

    ``fields`` holds the static values (lists as tuples). It is left out of
    the hash, so a spec (and a walk target holding one) stays hashable.
    """

    access_groups: tuple[str, ...] = ()
    fields: Mapping[str, FieldValue] = field(default_factory=dict, hash=False)
    templates: tuple[CompiledTemplate, ...] = ()
    metadata_fields: frozenset[str] = frozenset()

    @property
    def has_fields(self) -> bool:
        """True when any field configuration is present."""
        return bool(self.fields or self.templates or self.metadata_fields)


EMPTY_SOURCE_SPEC = SourceSpec()


def spec_from_source(source: Optional[Mapping[str, Any]]) -> SourceSpec:
    """Build the ``SourceSpec`` of one ``sync_body["sources"]`` entry.

    Raises ``TemplateError`` (with its ``code``) when a template does not
    compile; the caller skips that source for the cycle and reports
    ``field_template_invalid``. Unknown metadata items are ignored (the
    sync schema already refuses them).
    """
    source = source or {}
    access_groups = tuple(source.get("access_groups") or ())
    static: dict[str, FieldValue] = {}
    raw_fields = source.get("fields")
    if isinstance(raw_fields, MappingABC):
        for key in sorted(raw_fields, key=str):
            value = raw_fields[key]
            static[str(key)] = tuple(value) if isinstance(value, list) else value
    raw_templates = source.get("field_templates") or ()
    if not isinstance(raw_templates, (list, tuple)):
        raise TemplateError("syntax")
    templates = compile_templates(raw_templates)
    raw_items = source.get("metadata_fields")
    items: frozenset[str] = frozenset()
    if isinstance(raw_items, (list, tuple, set, frozenset)):
        items = frozenset(i for i in raw_items if i in _metadata.METADATA_ITEMS)
    return SourceSpec(
        access_groups=access_groups,
        fields=static,
        templates=templates,
        metadata_fields=items,
    )


# --- assembling the payload ---------------------------------------------------


@dataclass(frozen=True)
class Payload:
    """The init ``fields`` of one document (``{}`` when nothing applies) and
    the count of dropped values per reason."""

    values: dict[str, Any] = field(default_factory=dict, hash=False)
    dropped: Counter = field(default_factory=Counter, hash=False)


def _clean_scalar(value: Any) -> tuple[Any, Optional[str]]:
    if isinstance(value, (bool, int)):
        return value, None
    if isinstance(value, float):
        return (value, None) if math.isfinite(value) else (None, "invalid_value")
    if isinstance(value, str):
        if not value.strip():
            return None, "invalid_value"
        if len(value) > MAX_VALUE_CHARS:
            return None, "value_too_long"
        return value, None
    return None, "invalid_value"


def _clean_value(value: Any, dropped: Counter) -> Any:
    """The value as it goes on the wire, or None when nothing is left.

    A list over the cap is dropped whole (``cap_exceeded``): sending its
    first 32 items would present a partial list as the configured one.
    Single bad items of a list are dropped one by one.
    """
    if isinstance(value, (list, tuple)):
        if not value:
            dropped["invalid_value"] += 1
            return None
        if len(value) > MAX_VALUES_PER_KEY:
            dropped["cap_exceeded"] += 1
            return None
        kept = []
        for item in value:
            clean, reason = _clean_scalar(item)
            if reason is not None:
                dropped[reason] += 1
            else:
                kept.append(clean)
        return kept or None
    clean, reason = _clean_scalar(value)
    if reason is not None:
        dropped[reason] += 1
        return None
    return clean


def _key_reason(key: Any) -> Optional[str]:
    if not isinstance(key, str):
        return "invalid_value"
    if re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_") in SYSTEM_KEYS:
        return "system_key"
    if not KEY_RE.match(key):
        return "invalid_value"
    return None


def encoded_size(values: Mapping[str, Any]) -> int:
    """Bytes of compact UTF-8 JSON, computed exactly as the server does."""
    return len(json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _victim(kept: dict[str, tuple[str, Any]], *, largest: bool) -> str:
    """The key to drop next: from the lowest-precedence layer present; in
    it the largest entry (size cap) or the last key (key cap)."""
    for layer in _DROP_ORDER:
        keys = [k for k, (lay, _) in kept.items() if lay == layer]
        if not keys:
            continue
        if largest:
            return max(keys, key=lambda k: (encoded_size({k: kept[k][1]}), k))
        return max(keys)
    raise AssertionError("no key to drop")


def assemble(
    rel: str,
    spec: Optional[SourceSpec],
    source_metadata: Optional[Mapping[str, Any]],
    ext: str,
) -> Payload:
    """The upload ``fields`` for the document ``rel`` of a source.

    ``source_metadata`` is ``ExtractedDocument.source_metadata``; ``ext``
    the file extension. Returns ``Payload({}, ...)`` when nothing is
    configured or nothing applies (a PDF in a source with e-mail items
    only, a path no template matches).
    """
    dropped: Counter = Counter()
    if spec is None or not spec.has_fields:
        return Payload({}, dropped)

    layers: dict[str, Mapping[str, Any]] = {
        LAYER_CAPTURE: captures(rel, spec.templates) if spec.templates else {},
        LAYER_STATIC: spec.fields,
        LAYER_METADATA: (
            _metadata.map_metadata(source_metadata, ext, spec.metadata_fields)
            if spec.metadata_fields
            else {}
        ),
    }
    chosen: dict[Any, tuple[str, Any]] = {}
    for layer in _PRECEDENCE:
        for key, value in layers[layer].items():
            chosen.setdefault(key, (layer, value))

    kept: dict[str, tuple[str, Any]] = {}
    for key in sorted(chosen, key=str):
        layer, value = chosen[key]
        reason = _key_reason(key)
        if reason is not None:
            dropped[reason] += 1
            continue
        clean = _clean_value(value, dropped)
        if clean is not None:
            kept[key] = (layer, clean)

    while len(kept) > MAX_KEYS:
        del kept[_victim(kept, largest=False)]
        dropped["cap_exceeded"] += 1
    while kept and encoded_size({k: v for k, (_, v) in kept.items()}) > MAX_PAYLOAD_BYTES:
        del kept[_victim(kept, largest=True)]
        dropped["too_large"] += 1

    return Payload({key: kept[key][1] for key in sorted(kept)}, dropped)


def config_digest(rel: str, spec: Optional[SourceSpec]) -> str:
    """sha256 of the configuration that governs ``rel``'s fields.

    Canonical JSON of the static values, this path's captures, the sorted
    metadata items and both rule versions, plus the rule versions of the
    enabled items that apply to this file (``item_rules``, only when there
    are any: every other digest stays as it was). ``""`` when static
    values, captures and metadata items are all empty. Extractor values are
    not part of it: they are unknown before extraction. A template change
    that leaves this path's captures alone does not change it.
    """
    if spec is None:
        return ""
    static = {
        str(k): list(v) if isinstance(v, tuple) else v for k, v in spec.fields.items()
    }
    caps = captures(rel, spec.templates) if spec.templates else {}
    metadata = sorted(spec.metadata_fields)
    if not static and not caps and not metadata:
        return ""
    document: dict[str, Any] = {
        "v": FIELDS_PAYLOAD_VERSION,
        "static": static,
        "captures": caps,
        "metadata": metadata,
        "mapping_version": _metadata.METADATA_MAPPING_VERSION,
    }
    rules = _metadata.item_rule_versions(metadata, PurePosixPath(rel.replace("\\", "/")).suffix)
    if rules:
        document["item_rules"] = rules
    canonical = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def fields_to_send(payload: Payload, previous_fields_sent: bool) -> Optional[dict[str, Any]]:
    """The init ``fields`` value, or None to leave the key out.

    ``{}`` only clears values an earlier upload staged: an unchanged body
    stays byte-identical for every document that never carried fields.
    The ``RC_DOC_FIELDS`` kill switch is the caller's.
    """
    if payload.values:
        return dict(payload.values)
    if previous_fields_sent:
        return {}
    return None


# --- reading the server's answer ----------------------------------------------


def _code(value: Any) -> str:
    if isinstance(value, str) and _CODE_RE.match(value):
        return value
    return OTHER_CODE


def _is_key(value: Any) -> bool:
    return isinstance(value, str) and bool(KEY_RE.match(value))


def _keys(values: Any) -> tuple[str, ...]:
    if not isinstance(values, list):
        return ()
    out: list[str] = []
    for value in values:
        if _is_key(value) and value not in out:
            out.append(value)
    return tuple(out)


def _warning_key(value: Any) -> str:
    """A warning's field key, or ``""`` when it is not key-shaped (spec F4)."""
    return value if _is_key(value) else ""


@dataclass(frozen=True)
class FieldsEcho:
    """The init ``fields`` echo, reduced to keys, codes and counts.

    ``warnings`` keeps one ``(code, key)`` pair per echo warning, in the
    server's order (spec F4): the code exactly as ``warning_codes`` counts
    it, and the warning's field key -- ``""`` when what the server named is
    not key-shaped. The warning's ``path`` is not kept.
    """

    staged: int
    unknown_keys: tuple[str, ...] = ()
    warning_codes: Counter = field(default_factory=Counter, hash=False)
    suggest: Mapping[str, tuple[str, ...]] = field(default_factory=dict, hash=False)
    warnings: tuple[tuple[str, str], ...] = ()


def parse_init_echo(init_json: Any) -> Optional[FieldsEcho]:
    """The ``fields`` echo of a 2xx init answer, or None.

    None when the key is absent (server off or old, tenant outside the
    feature) and when it is malformed: an echo that does not say how many
    keys were staged confirms nothing, so it is read as no echo
    (``not_accepted``), never as stored. Keys outside the key pattern and
    codes outside the code pattern are dropped or counted as ``other``; a
    warning's key outside the pattern becomes ``""`` (its code still counts).
    """
    if not isinstance(init_json, MappingABC):
        return None
    echo = init_json.get("fields")
    if not isinstance(echo, MappingABC):
        return None
    staged = echo.get("staged")
    if isinstance(staged, bool) or not isinstance(staged, int) or staged < 0:
        return None
    codes: Counter = Counter()
    pairs: list[tuple[str, str]] = []
    warnings = echo.get("warnings")
    if isinstance(warnings, list):
        for warning in warnings:
            if isinstance(warning, MappingABC):
                code = _code(warning.get("code"))
                codes[code] += 1
                pairs.append((code, _warning_key(warning.get("key"))))
    suggest: dict[str, tuple[str, ...]] = {}
    raw_suggest = echo.get("suggest")
    if isinstance(raw_suggest, MappingABC):
        for key in sorted(k for k in raw_suggest if _is_key(k)):
            candidates = _keys(raw_suggest[key])[:MAX_SUGGESTIONS]
            if candidates:
                suggest[key] = candidates
    return FieldsEcho(
        staged=staged,
        unknown_keys=_keys(echo.get("unknown_keys")),
        warning_codes=codes,
        suggest=suggest,
        warnings=tuple(pairs),
    )


def classify_init_refusal(status: int, body: Any) -> Optional[str]:
    """The refusal code when a non-2xx init that carried ``fields`` may have
    been caused by them, else None (the existing error path applies).

    * 400/422: ``error_code`` in ``FIELD_SHAPE_CODES``, or ``path`` starting
      with ``fields`` (an unknown code there is ``other``);
    * 503: ``doc_fields_ingest_unavailable`` / ``doc_fields_unavailable``;
    * 401: ``assertion_rejected``.

    A code is only a suspicion: the caller re-posts once without ``fields``
    and records ``refused:<code>`` only if that retry succeeds.
    """
    if not isinstance(body, MappingABC):
        return None
    raw_code = body.get("error_code")
    code = raw_code if isinstance(raw_code, str) else ""
    if status in (400, 422):
        if code in FIELD_SHAPE_CODES:
            return code
        path = body.get("path")
        if isinstance(path, str) and path.startswith("fields"):
            return code if code in FIELD_REFUSAL_CODES else REFUSAL_OTHER
        return None
    if status == 503 and code in TRANSIENT_REFUSAL_CODES:
        return code
    if status == 401 and code == ASSERTION_REJECTED:
        return code
    return None


def is_transient_refusal(code: Optional[str]) -> bool:
    return code in TRANSIENT_REFUSAL_CODES


def is_doc_fields_unavailable(status: int, body: Any) -> bool:
    """A 503 whose ``error_code`` starts with ``doc_fields_``: the uploader
    returns it at once instead of backing off five times inside one call."""
    if status != 503 or not isinstance(body, MappingABC):
        return False
    code = body.get("error_code")
    return isinstance(code, str) and code.startswith("doc_fields_")


# --- outcomes and what the state DB stores --------------------------------------


@dataclass(frozen=True)
class FieldsOutcome:
    """What happened to one upload's fields (``UploadResult.fields``)."""

    outcome: str
    staged: int = 0
    warning_codes: Counter = field(default_factory=Counter, hash=False)
    unknown_keys: tuple[str, ...] = ()
    suggest: Mapping[str, tuple[str, ...]] = field(default_factory=dict, hash=False)
    digest: str = ""
    transient: bool = False
    #: ``(code, key)`` per echo warning (spec F4); empty without an echo.
    warnings: tuple[tuple[str, str], ...] = ()

    @property
    def refusal_code(self) -> Optional[str]:
        if self.outcome.startswith(REFUSED_PREFIX):
            return self.outcome[len(REFUSED_PREFIX):]
        return None

    @property
    def fields_sent(self) -> bool:
        """True when the init carried a ``fields`` key."""
        return self.outcome != OUTCOME_NONE

    def as_tx_entry(self) -> dict[str, Any]:
        """``tx_entry["fields"]`` of the sync response: codes and counts."""
        return {
            "outcome": self.outcome,
            "staged": self.staged,
            "warning_codes": sorted(self.warning_codes),
        }

    def log_line(self) -> str:
        """``doc_fields outcome=staged staged=3 warnings=2``: codes and counts."""
        return (
            f"doc_fields outcome={self.outcome} staged={self.staged} "
            f"warnings={sum(self.warning_codes.values())}"
        )


def outcome_after_init(
    sent: Optional[Mapping[str, Any]], echo: Optional[FieldsEcho], digest: str
) -> FieldsOutcome:
    """The outcome of a successful init.

    ``sent`` is the ``fields`` value the init carried (None: no key). A
    clear (``{}``) counts as ``cleared`` only when the server echoed it;
    without an echo nothing was cleared, so it is ``not_accepted`` and the
    clear is repeated once the server accepts fields (H7).
    """
    if sent is None:
        return FieldsOutcome(OUTCOME_NONE, digest=digest)
    if echo is None:
        return FieldsOutcome(OUTCOME_NOT_ACCEPTED, digest=digest)
    return FieldsOutcome(
        OUTCOME_CLEARED if not sent else OUTCOME_STAGED,
        staged=echo.staged,
        warning_codes=Counter(echo.warning_codes),
        unknown_keys=echo.unknown_keys,
        suggest=dict(echo.suggest),
        digest=digest,
        warnings=echo.warnings,
    )


def refused_outcome(code: str, digest: str) -> FieldsOutcome:
    """The outcome when the init without ``fields`` succeeded after a refusal."""
    code = code if code in FIELD_REFUSAL_CODES else REFUSAL_OTHER
    return FieldsOutcome(
        REFUSED_PREFIX + code, digest=digest, transient=is_transient_refusal(code)
    )


@dataclass(frozen=True)
class FieldsRecord:
    """The fields columns of one ``documents`` row.

    ``digest`` None keeps the stored digest (a transient refusal must come
    back); ``sent`` None keeps ``fields_sent``. ``count_attempt`` adds one to
    ``fields_attempts`` instead of resetting it to 0.
    """

    digest: Optional[str]
    outcome: str
    sent: Optional[bool] = None
    warning_codes: tuple[str, ...] = ()
    count_attempt: bool = False

    def warning_codes_json(self) -> str:
        return json.dumps(list(self.warning_codes))


def record_for(outcome: FieldsOutcome) -> FieldsRecord:
    """What the state DB stores for an outcome (the table in section 3.7)."""
    codes = tuple(sorted(outcome.warning_codes))
    name = outcome.outcome
    if name == OUTCOME_STAGED:
        return FieldsRecord(outcome.digest, name, sent=True, warning_codes=codes)
    if name == OUTCOME_CLEARED:
        return FieldsRecord(outcome.digest, name, sent=False, warning_codes=codes)
    if name in (OUTCOME_NOT_ACCEPTED, OUTCOME_NONE):
        return FieldsRecord(outcome.digest, name)
    if name.startswith(REFUSED_PREFIX):
        if outcome.transient:
            return FieldsRecord(None, name, count_attempt=True)
        return FieldsRecord(outcome.digest, name)
    raise ValueError(f"unknown fields outcome: {name[:32]}")


def reupload_failed_record(digest: str, failure_class: str) -> FieldsRecord:
    """The record that takes a document out of the re-upload queue after
    ``RC_FIELDS_REUPLOAD_MAX_ATTEMPTS``; ``failure_class`` is closed."""
    if failure_class not in REUPLOAD_FAILURE_CLASSES:
        failure_class = "other"
    return FieldsRecord(digest, REUPLOAD_FAILED_PREFIX + failure_class)
