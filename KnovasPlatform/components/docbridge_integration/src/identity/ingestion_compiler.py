"""One profile in, the two RemoteController documents out.

Why this module exists
----------------------
RemoteController splits its configuration in two, and its own documentation
presents the split as a feature ("Two configuration layers",
RemoteController/docs/configuration.md):

    what to sync   -> the POST /sync body     (sync_request.schema.json)
    when/how fast  -> a file on disk          (remote_controller_sync_config.schema.json)

For a service that is a reasonable seam. For a person it is six things to know
before changing one thing, and two of the traps are silent:

    - both documents have a field called ``mode``, with disjoint vocabularies
      (``incremental|full`` against ``one_time|continuous``);
    - ``max_document_age_seconds`` exists in both, with a precedence rule.

So the seam stays and the administrator stops seeing it. One
``IngestionProfile`` — the thing the Ingestion tab edits and the thing
``ingestion_profiles`` versions — compiles here into both documents. This module
is the only place in the product where the two-layer split is still visible, and
no human reads it.

Design decisions worth knowing
------------------------------
    - ``max_document_age_seconds`` is written to the **sync body only**. The
      config-file default is never emitted, so the precedence rule cannot fire
      and does not have to be explained.
    - ``paused`` is a separate flag from ``schedule``, because
      ``sync_scheduler._run_once`` treats ``enabled: false`` as "do nothing"
      even for a hand-started run. Pausing is not a schedule.
    - Compilation validates against the schemas RemoteController ships. The
      checkout copy at ``RemoteController/contracts/`` is preferred; the
      Docker image never contains that tree (build context is
      ``docbridge_integration`` only), so ``rc_contracts/`` beside this
      module is the install fallback. A test keeps the two byte-identical.
    - Errors are ``ProfileError`` with a sentence, not a schema traceback.

Plan: docs/superpowers/plans/2026-08-14-section-b-buildout.md (KC-IN-6, KC-IN-4)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator
from jsonschema import ValidationError as _SchemaValidationError

from doc_fields_view import METADATA_TARGETS
from identity.field_templates import (
    KEY_RE,
    SYSTEM_KEYS,
    TemplateError,
    compile_template,
)
from identity.ingestion_presets import (
    DEFAULT_EXCLUDE_GLOBS,
    FILE_TYPE_PRESETS,
    SCHEDULE_PRESETS,
    THROUGHPUT_PRESETS,
)

_SECONDS_PER_DAY = 86400
_BYTES_PER_MEGABYTE = 1024 * 1024

#: Keys ``sync_config.FORBIDDEN_KEYS`` refuses. Never compiled, never sent.
FORBIDDEN_KEYS = frozenset(
    {
        "rc_instance_token",
        "semantix_client_cert_path",
        "semantix_client_key_path",
        "semantix_ca_cert_path",
    }
)


class ProfileError(ValueError):
    """The profile cannot be compiled. The message is shown to a person."""


# -- document fields per source (spec 4.8) -----------------------------------
#
# A source can give every document it uploads Knovas field values: static
# values, path-template captures and opted-in extractor metadata. They travel
# in the sync body's ``sources[]`` entries (never at the top level of the
# profile: an older Platform builds IngestionProfile(**fields) and would
# crash on a new key) and only when non-empty, so a profile without them
# compiles to exactly today's bytes.

#: Caps of the sync-request schema (sources[].fields / field_templates).
MAX_SOURCE_FIELDS = 64
MAX_FIELD_TEMPLATES = 8
MAX_FIELD_VALUES = 32
MAX_FIELD_VALUE_CHARS = 256

#: The extractor metadata items RemoteController maps (spec 3.5, L1), in the
#: order the form offers them. ``keywords`` and ``document_status`` need a
#: Connector that reports ``metadata_fields_v2``.
METADATA_ITEMS = (
    "language", "email_date", "email_doc_type", "email_author", "document_author",
    "keywords", "document_status",
)

_SCHEMA_FIELD_KEYS = frozenset({"fields", "field_templates", "metadata_fields"})


def _freeze_value(value: Any) -> Any:
    """A field value as the frozen dataclass holds it: lists become tuples,
    so a SourceFolder stays hashable."""
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return value


def _field_pairs(raw: Any) -> tuple[tuple[str, Any], ...]:
    """Static values as sorted ``(key, value)`` pairs, from pairs or a dict."""
    items = raw.items() if isinstance(raw, Mapping) else (raw or ())
    pairs: dict[str, Any] = {}
    for item in items:
        key, value = item
        pairs[str(key)] = _freeze_value(value)
    return tuple(sorted(pairs.items()))


@dataclass(frozen=True)
class SourceFolder:
    """One folder to index, and the wall its documents are born behind.

    ``access_groups`` is the B3-critical field: RemoteController passes it to
    ``/secured/init_document_transmission``, which materialises the ACL at
    ingest. Without it every new document from a walled matter lands
    unrestricted and the wall has to be repaired afterwards, once, per document.

    ``fields`` (sorted pairs; a dict default would be unhashable),
    ``field_templates`` and ``metadata_fields`` are the source's document
    fields. They go to RemoteController's upload layer, so changing any of
    them re-sends every document of the source.
    """

    path: str
    recursive: bool = True
    access_groups: tuple[str, ...] | list[str] = ()
    fields: tuple[tuple[str, Any], ...] = ()
    field_templates: tuple[str, ...] = ()
    metadata_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", _field_pairs(self.fields))
        object.__setattr__(self, "field_templates",
                           tuple(str(t) for t in self.field_templates or ()))
        object.__setattr__(self, "metadata_fields",
                           tuple(dict.fromkeys(str(m) for m in self.metadata_fields or ())))

    @property
    def has_fields(self) -> bool:
        """True when this source configures any document field."""
        return bool(self.fields or self.field_templates or self.metadata_fields)

    def fields_json(self) -> dict[str, Any]:
        """The static values as the wire and the stored JSON carry them."""
        return {key: list(value) if isinstance(value, tuple) else value
                for key, value in self.fields}

    def field_config(self) -> tuple:
        """What decides RemoteController's config digest for this source, in
        a comparable form: a change here re-sends the source."""
        return (self.fields, self.field_templates, frozenset(self.metadata_fields))


@dataclass(frozen=True)
class IngestionProfile:
    """What the Ingestion tab edits and ``ingestion_profiles`` versions."""

    identifier_prefix: str
    sources: list[SourceFolder]
    file_types: list[str] = field(default_factory=lambda: ["documents"])
    schedule: str = "nightly"
    throughput: str = "normal"
    paused: bool = False
    full_rescan: bool = False
    max_document_age_days: int | None = None
    max_file_megabytes: int | None = None
    exclude_globs: list[str] = field(default_factory=list)
    delete_on_remove: bool = True
    description: str = ""


@dataclass(frozen=True)
class CompiledIngestion:
    """The two documents, ready to push, both already schema-valid."""

    sync_config: dict[str, Any]
    sync_request: dict[str, Any]


_REQUIRED_SCHEMA_FILES = (
    "sync_request.schema.json",
    "remote_controller_sync_config.schema.json",
)


def _is_contracts_dir(path: Path) -> bool:
    return path.is_dir() and all(
        (path / name).is_file() for name in _REQUIRED_SCHEMA_FILES
    )


def _checkout_contracts_dir() -> Path | None:
    """``RemoteController/contracts`` walking up from this file, or None."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "RemoteController" / "contracts"
        if _is_contracts_dir(candidate):
            return candidate
    return None


def _bundled_contracts_dir() -> Path:
    return Path(__file__).resolve().parent / "rc_contracts"


def _contracts_dir() -> Path:
    """Locate the schemas compile_profile validates against.

    The monorepo checkout wins so a schema change in RemoteController is
    picked up without a second edit. The Platform image has no such
    checkout — only ``src/`` — so the bundled copy beside this module is
    what Docker uses.
    """
    found = _checkout_contracts_dir()
    if found is not None:
        return found
    bundled = _bundled_contracts_dir()
    if _is_contracts_dir(bundled):
        return bundled
    raise ProfileError(
        "RemoteController/contracts was not found in this checkout, so the "
        "compiled configuration cannot be validated before it is sent."
    )


@lru_cache(maxsize=4)
def _validator(schema_filename: str) -> Draft202012Validator:
    schema = json.loads(
        (_contracts_dir() / schema_filename).read_text(encoding="utf-8")
    )
    return Draft202012Validator(schema)


def _choose(table: dict[str, dict], key: str, what: str) -> dict:
    try:
        return table[key]
    except KeyError:
        raise ProfileError(
            f"Unknown {what} {key!r}. Choose one of: {', '.join(sorted(table))}."
        ) from None


def _compile_sync_config(profile: IngestionProfile) -> dict[str, Any]:
    schedule = _choose(SCHEDULE_PRESETS, profile.schedule, "schedule")
    throughput = _choose(THROUGHPUT_PRESETS, profile.throughput, "speed")

    document: dict[str, Any] = {
        "schema_version": 1,
        # Pausing is not a schedule: _run_once short-circuits on `enabled`
        # (sync_scheduler.py:134), so a paused profile keeps its schedule and
        # resumes into it.
        "enabled": not profile.paused,
        "mode": schedule["mode"],
        "window": dict(schedule["window"]),
        "rate_limit": {
            "max_ingestion_requests_per_minute": throughput[
                "max_ingestion_requests_per_minute"
            ],
            "burst": throughput["burst"],
        },
        "max_files_per_cycle": throughput["max_files_per_cycle"],
        "max_scan_entries_per_cycle": throughput["max_scan_entries_per_cycle"],
        "pause_policy": "finish_current_unit_then_pause",
    }
    if schedule["scan_interval_seconds"] is not None:
        document["scan_interval_seconds"] = schedule["scan_interval_seconds"]
    # Deliberately absent: max_document_age_seconds. It belongs to the sync
    # body alone — see the module docstring.
    return document


def _include_globs(file_types: list[str]) -> list[str]:
    if not file_types:
        raise ProfileError(
            "Choose at least one kind of file to index: "
            f"{', '.join(sorted(FILE_TYPE_PRESETS))}."
        )
    globs: set[str] = set()
    for name in file_types:
        globs.update(_choose(FILE_TYPE_PRESETS, name, "file type")["globs"])
    return sorted(globs)


def _compile_sync_request(profile: IngestionProfile) -> dict[str, Any]:
    if not profile.sources:
        raise ProfileError("Add at least one folder before saving this profile.")
    if not profile.identifier_prefix.strip():
        raise ProfileError(
            "This profile needs a short name for its documents (the identifier "
            "prefix), so results can be traced back to where they came from."
        )

    sources: list[dict[str, Any]] = []
    for source in profile.sources:
        if not str(source.path).strip():
            raise ProfileError("A folder in this profile has no path.")
        entry: dict[str, Any] = {
            "path": source.path,
            "recursive": bool(source.recursive),
        }
        # Order-preserving de-duplication. Assigning a folder to the same group
        # twice says exactly what assigning it once says, but the sync-request
        # schema declares uniqueItems and refuses the whole profile over it --
        # which stopped an ingest with a message about JSON rather than about
        # anything the administrator did. Deduplicating here covers every
        # producer, including a profile that was saved with duplicates before
        # the console stopped offering them.
        groups = list(dict.fromkeys(
            str(g).strip() for g in (source.access_groups or ()) if str(g).strip()
        ))
        if groups:
            entry["access_groups"] = list(groups)
        # Document fields, each key only when non-empty (D8): a profile
        # without them must reach RemoteController byte-identical to before,
        # and an older RemoteController refuses the keys outright.
        if source.fields:
            entry["fields"] = source.fields_json()
        if source.field_templates:
            for number, template in enumerate(source.field_templates, 1):
                try:
                    compile_template(template)
                except TemplateError as exc:
                    raise ProfileError(
                        f"Ordner {len(sources) + 1}, Pfadvorlage {number}: "
                        f"{TEMPLATE_ERROR_TEXT.get(exc.code, exc.code)}"
                    ) from None
            entry["field_templates"] = list(source.field_templates)
        if source.metadata_fields:
            entry["metadata_fields"] = list(source.metadata_fields)
        sources.append(entry)

    filters: dict[str, Any] = {
        "include_globs": _include_globs(profile.file_types),
        "exclude_globs": sorted(
            set(DEFAULT_EXCLUDE_GLOBS) | set(profile.exclude_globs or ())
        ),
    }
    if profile.max_document_age_days is not None:
        filters["max_document_age_seconds"] = (
            int(profile.max_document_age_days) * _SECONDS_PER_DAY
        )
    if profile.max_file_megabytes is not None:
        filters["max_file_bytes"] = int(profile.max_file_megabytes) * _BYTES_PER_MEGABYTE

    ingestion: dict[str, Any] = {
        "identifier_prefix": profile.identifier_prefix.strip(),
        "delete_on_remove": bool(profile.delete_on_remove),
    }
    if profile.description.strip():
        ingestion["description"] = profile.description.strip()[:2000]

    return {
        # Not the scheduler's `mode`. This one says how much to re-read.
        "mode": "full" if profile.full_rescan else "incremental",
        "sources": sources,
        "filters": filters,
        "ingestion": ingestion,
    }


def _reject_secrets(document: dict[str, Any], which: str) -> None:
    leaked = FORBIDDEN_KEYS.intersection(document)
    if leaked:
        raise ProfileError(
            f"The compiled {which} contained {', '.join(sorted(leaked))}, which "
            "RemoteController refuses. This is a bug in the compiler, not in "
            "your configuration."
        )


def _validate(document: dict[str, Any], schema_filename: str, which: str) -> None:
    try:
        _validator(schema_filename).validate(document)
    except _SchemaValidationError as exc:
        path = list(exc.absolute_path)
        location = "/".join(str(p) for p in path) or which
        # A schema message embeds the offending instance. Under the field
        # keys that is a field value or a template, and a ProfileError can
        # reach a log line (a failed approval execution): name the place and
        # the rule, never the value.
        if len(path) >= 3 and path[0] == "sources" and path[2] in _SCHEMA_FIELD_KEYS:
            if path[2] == "fields" and len(path) > 3 and not KEY_RE.match(str(path[3])):
                location = "/".join(str(p) for p in path[:3])
            raise ProfileError(
                f"The compiled {which} is not valid at {location}: {exc.validator}"
            ) from None
        raise ProfileError(
            f"The compiled {which} is not valid at {location}: {exc.message}"
        ) from exc


def compile_profile(profile: IngestionProfile) -> CompiledIngestion:
    """Turn ``profile`` into two schema-valid RemoteController documents.

    Nothing is sent. Validation happens here so a bad configuration is refused
    in the form, where the person is, rather than by a service they cannot see.

    Raises:
        ProfileError: with a sentence a person can act on.
    """
    sync_config = _compile_sync_config(profile)
    sync_request = _compile_sync_request(profile)

    _reject_secrets(sync_config, "schedule")
    _reject_secrets(sync_request, "folder list")
    _validate(sync_config, "remote_controller_sync_config.schema.json", "schedule")
    _validate(sync_request, "sync_request.schema.json", "folder list")

    return CompiledIngestion(sync_config=sync_config, sync_request=sync_request)


def redact_for_support(profile: IngestionProfile) -> str:
    """The profile as JSON, with the firm's paths and group names removed.

    What a support ticket needs is the shape — which presets, how many folders,
    whether walls are in use. Where the firm keeps its mandates is not Knovas's
    business, and a pasted configuration is the easiest way for it to become so.
    """
    payload = {
        "schedule": profile.schedule,
        "throughput": profile.throughput,
        "paused": profile.paused,
        "full_rescan": profile.full_rescan,
        "file_types": sorted(set(profile.file_types)),
        "folder_count": len(profile.sources),
        "folders_with_access_groups": sum(
            1 for s in profile.sources if s.access_groups
        ),
        "recursive_folders": sum(1 for s in profile.sources if s.recursive),
        "max_document_age_days": profile.max_document_age_days,
        "max_file_megabytes": profile.max_file_megabytes,
        "custom_exclude_count": len(profile.exclude_globs or []),
        "delete_on_remove": profile.delete_on_remove,
        # Document fields as counts only: a value, a template or a key list
        # names the firm's clients as surely as a path does.
        **field_config_counts(profile.sources),
    }
    return json.dumps(payload, indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# Document fields: counts, change detection, validation at save (spec 4.8)
# ---------------------------------------------------------------------------
#
# German texts below are ``\u`` escapes: .py files stay ASCII-only
# (scripts/check_ascii_py.py). No message repeats a field value or a
# template -- keys, positions and counts only -- because a ProfileError can
# end up in a log line.

#: German text per template error code (field_templates.TEMPLATE_ERROR_CODES).
TEMPLATE_ERROR_TEXT = {
    "syntax": "ung\u00fcltige Schreibweise (erlaubt: {schl\u00fcssel}, *, feste Ordnernamen, "
              "am Ende /**)",
    "duplicate_key": "ein Feld kommt zweimal vor",
    "system_key": "Systemfelder (title, description, path, ingested_at, pointer) "
                  "sind nicht erlaubt",
    "too_long": "zu lang",
}

#: RemoteController refuses a body with field keys it does not know; the
#: Platform says so before it tries (spec 2.5, 4.8).
RC_TOO_OLD = ("RemoteController zu alt \u2013 bitte aktualisieren: er meldet keine "
              "Unterst\u00fctzung f\u00fcr Dokumentfelder.")
#: ...and when it cannot be asked at all, it is not called too old.
RC_UNREACHABLE = ("RemoteController nicht erreichbar \u2013 ob er Dokumentfelder "
                  "unterst\u00fctzt, l\u00e4sst sich jetzt nicht pr\u00fcfen. Bitte "
                  "sp\u00e4ter erneut speichern.")


def _source_value(source: Any, name: str) -> Any:
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def field_config_counts(sources: Sequence[Any]) -> dict[str, int]:
    """How many sources use document fields, and how, as counts only.

    Takes SourceFolder objects or the stored JSON form of ``sources[]``, so
    the support JSON, the audit row and the approvals summary agree.
    """
    folders = templates = metadata = static = 0
    for source in sources or ():
        static_values = _source_value(source, "fields") or ()
        source_templates = _source_value(source, "field_templates") or ()
        items = _source_value(source, "metadata_fields") or ()
        if static_values or source_templates or items:
            folders += 1
        static += 1 if static_values else 0
        templates += len(source_templates) if isinstance(source_templates, (list, tuple)) else 0
        metadata += 1 if items else 0
    return {
        "folders_with_fields": folders,
        "folders_with_static_fields": static,
        "field_templates": templates,
        "folders_with_metadata_fields": metadata,
    }


def profile_uses_fields(profile: IngestionProfile | None) -> bool:
    """True when any source of ``profile`` configures document fields."""
    return bool(profile) and any(s.has_fields for s in profile.sources)


def field_config_changes(old: IngestionProfile | None,
                         new: IngestionProfile) -> list[str]:
    """Paths of the sources whose document-field configuration changes.

    Only sources ``old`` already had count: their documents were uploaded
    before and RemoteController re-sends every one of them (its config
    digest changed). A new source's documents are uploaded for the first
    time anyway, fields and all. Removing a source's fields is a change too:
    the next upload of each document clears them with ``{}``.
    """
    if old is None:
        return []
    before = {s.path: s.field_config() for s in old.sources}
    changed: list[str] = []
    for source in new.sources:
        previous = before.get(source.path)
        if previous is None:
            continue
        if previous != source.field_config():
            changed.append(source.path)
    return changed


def _enum_code(spec: Mapping[str, Any], value: Any) -> str | None:
    """The enum code ``value`` names, by code or by label (casefolded)."""
    wanted = str(value).strip().casefold()
    for item in spec.get("enum") or ():
        if not isinstance(item, Mapping):
            continue
        code = str(item.get("code") or "")
        if wanted in (code.casefold(), str(item.get("label") or "").strip().casefold()):
            return code
    return None


def _key_problem(by_key: Mapping[str, Mapping[str, Any]], key: str) -> str | None:
    """Why ``key`` cannot be written, or None. Keys are config, not values,
    so the message may name them."""
    if key in SYSTEM_KEYS:
        return f"\u201e{key}\u201c ist ein Systemfeld und wird nicht \u00fcber Ordner gesetzt"
    spec = by_key.get(key)
    if spec is None:
        return f"Feld \u201e{key}\u201c ist bei Knovas nicht angelegt"
    if spec.get("status") == "deprecated":
        return f"Feld \u201e{key}\u201c ist stillgelegt"
    return None


def validate_profile_fields(profile: IngestionProfile,
                            registry: Sequence[Mapping[str, Any]]) -> IngestionProfile:
    """Check every source's document fields against the tenant registry.

    ``registry`` is ``doc_fields_capability.registry_for(...)`` (the
    sanitized shape). Keys must exist and be active; enum values may be
    given as code or label and are returned as the code; values are at most
    256 characters, at most 32 per key, and only one unless the field takes
    several; at most 64 keys and 8 templates per source; templates compile
    (``identity.field_templates``) and every key they capture, like every
    metadata target, must be registered and active.

    Returns the profile with enum values replaced by their codes. Raises
    ProfileError listing what is wrong, by folder number and key, never by
    value.
    """
    by_key = {str(f.get("key")): f for f in registry or () if isinstance(f, Mapping)}
    errors: list[str] = []
    sources: list[SourceFolder] = []
    for number, source in enumerate(profile.sources, 1):
        where = f"Ordner {number}"
        if len(source.fields) > MAX_SOURCE_FIELDS:
            errors.append(f"{where}: h\u00f6chstens {MAX_SOURCE_FIELDS} feste Felder")
        if len(source.field_templates) > MAX_FIELD_TEMPLATES:
            errors.append(f"{where}: h\u00f6chstens {MAX_FIELD_TEMPLATES} Pfadvorlagen")
        pairs: list[tuple[str, Any]] = []
        for key, value in source.fields:
            problem = _key_problem(by_key, key)
            if problem:
                errors.append(f"{where}: {problem}")
                pairs.append((key, value))
                continue
            spec = by_key[key]
            values = list(value) if isinstance(value, tuple) else [value]
            if not values:
                errors.append(f"{where}: Feld \u201e{key}\u201c hat keinen Wert")
            elif len(values) > MAX_FIELD_VALUES:
                errors.append(f"{where}: Feld \u201e{key}\u201c hat mehr als "
                              f"{MAX_FIELD_VALUES} Werte")
            elif len(values) > 1 and spec.get("cardinality") != "many":
                errors.append(f"{where}: Feld \u201e{key}\u201c nimmt nur einen Wert")
            cleaned: list[Any] = []
            for item in values:
                if isinstance(item, str):
                    if not item.strip():
                        errors.append(f"{where}: Feld \u201e{key}\u201c hat einen leeren Wert")
                    elif len(item) > MAX_FIELD_VALUE_CHARS:
                        errors.append(f"{where}: ein Wert von \u201e{key}\u201c ist l\u00e4nger "
                                      f"als {MAX_FIELD_VALUE_CHARS} Zeichen")
                if spec.get("enum"):
                    code = _enum_code(spec, item)
                    if code is None:
                        errors.append(f"{where}: ein Wert von \u201e{key}\u201c steht nicht "
                                      "in dessen Auswahl")
                        cleaned.append(item)
                    else:
                        cleaned.append(code)
                else:
                    cleaned.append(item)
            pairs.append((key, tuple(cleaned) if isinstance(value, tuple) else
                          (cleaned[0] if cleaned else value)))
        for index, template in enumerate(source.field_templates, 1):
            try:
                compiled = compile_template(template)
            except TemplateError as exc:
                errors.append(f"{where}, Pfadvorlage {index}: "
                              f"{TEMPLATE_ERROR_TEXT.get(exc.code, exc.code)}")
                continue
            for key in compiled.keys:
                problem = _key_problem(by_key, key)
                if problem:
                    errors.append(f"{where}, Pfadvorlage {index}: {problem}")
        for item in source.metadata_fields:
            target = METADATA_TARGETS.get(item)
            if target is None:
                errors.append(f"{where}: unbekannte Dateieigenschaft")
                continue
            problem = _key_problem(by_key, target)
            if problem:
                errors.append(f"{where}, Dateieigenschaft: {problem}")
        sources.append(replace(source, fields=tuple(pairs)))
    if errors:
        unique = list(dict.fromkeys(errors))
        more = len(unique) - 8
        text = "; ".join(unique[:8]) + (f"; und {more} weitere" if more > 0 else "")
        raise ProfileError(f"Dokumentfelder nicht \u00fcbernommen: {text}.")
    return replace(profile, sources=sources)


def upload_field_keys(profile: IngestionProfile) -> set[str]:
    """The keys the profile sets per source as static values or template
    captures: a folder rule on one of them is worth a warning (D3).
    Metadata targets are left out: they fill in what the extractor found,
    not a value the administrator chose."""
    keys: set[str] = set()
    for source in profile.sources:
        keys.update(key for key, _ in source.fields)
        for template in source.field_templates:
            try:
                keys.update(compile_template(template).keys)
            except TemplateError:
                continue
    return keys


def profile_pointer_prefix(profile: IngestionProfile) -> str:
    """The pointer prefix RemoteController gives the profile's documents:
    ``<identifier_prefix>/`` (knovas_uploader.upload_file)."""
    return f"{profile.identifier_prefix.strip()}/"


def folder_rule_conflicts(profile: IngestionProfile,
                          rules: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """``{key: rule count}`` for keys set both per source and by a folder rule
    that reaches this profile's documents.

    A rule reaches them when its prefix lies inside the profile's pointer
    prefix or covers all of it (Knovas matches with a raw ``startswith``).
    Both layers are legal: the per-source value travels in the upload layer
    and wins, so the rule's value never shows on those documents -- which is
    what the warning says. Counts only; a rule's prefix names a client.
    """
    keys = upload_field_keys(profile)
    if not keys:
        return {}
    prefix = profile_pointer_prefix(profile)
    out: dict[str, int] = {}
    for rule in rules or ():
        if not isinstance(rule, Mapping):
            continue
        rule_prefix = str(rule.get("pointer_prefix") or "")
        if not rule_prefix:
            continue
        if not (rule_prefix.startswith(prefix) or prefix.startswith(rule_prefix)):
            continue
        for key in (rule.get("set") or {}):
            if key in keys:
                out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def is_field_key(text: Any) -> bool:
    """A registry key as the sync contract allows it."""
    return isinstance(text, str) and bool(KEY_RE.match(text))
