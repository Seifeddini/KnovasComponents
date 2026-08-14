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
    - Compilation validates against the schemas RemoteController ships, read
      from ``RemoteController/contracts/`` rather than copied. A copy would
      drift, and the first symptom of drift is a rejected write the
      administrator cannot diagnose.
    - Errors are ``ProfileError`` with a sentence, not a schema traceback.

Plan: docs/superpowers/plans/2026-08-14-section-b-buildout.md (KC-IN-6, KC-IN-4)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema import ValidationError as _SchemaValidationError

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


@dataclass(frozen=True)
class SourceFolder:
    """One folder to index, and the wall its documents are born behind.

    ``access_groups`` is the B3-critical field: RemoteController passes it to
    ``/secured/init_document_transmission``, which materialises the ACL at
    ingest. Without it every new document from a walled matter lands
    unrestricted and the wall has to be repaired afterwards, once, per document.
    """

    path: str
    recursive: bool = True
    access_groups: tuple[str, ...] | list[str] = ()


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


def _contracts_dir() -> Path:
    """Locate RemoteController's shipped contracts.

    Both components live in one checkout; this walks up from
    ``.../docbridge_integration/src/identity/`` to the repository root.
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "RemoteController" / "contracts"
        if candidate.is_dir():
            return candidate
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
        groups = [g for g in (source.access_groups or ()) if str(g).strip()]
        if groups:
            entry["access_groups"] = list(groups)
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
        location = "/".join(str(p) for p in exc.absolute_path) or which
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
    }
    return json.dumps(payload, indent=2, sort_keys=True)
