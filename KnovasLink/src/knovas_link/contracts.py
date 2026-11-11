"""The contracts Link is built against (``KnovasLink/contracts``), checked against ``contracts.lock`` at start.

Link validates every job it hands to a component and every result a component answers with against the shared
schemas, so that what passes through it is exactly what the contract allows.
"""
from __future__ import annotations

import functools
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from knovas_link.errors import StartupError

#: In the repository and in the image (PYTHONPATH=/app/src, contracts in /app/contracts) alike.
FOLDER = Path(__file__).resolve().parents[2] / "contracts"
USED = ("job.schema.json", "job-result.schema.json", "codes.json")


def verify(folder: Path = FOLDER) -> None:
    """Refuse to start on a contract file that does not match its lock."""
    try:
        lock = json.loads((folder / "contracts.lock").read_text(encoding="utf-8"))
        for name in USED:
            if hashlib.sha256((folder / name).read_bytes()).hexdigest() != lock["files"][name]:
                raise StartupError("contracts.stale")
    except (OSError, ValueError, KeyError):
        raise StartupError("contracts.missing") from None


@functools.lru_cache(maxsize=None)
def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads((FOLDER / name).read_text(encoding="utf-8")))


def job_valid(job: object) -> bool:
    return isinstance(job, dict) and not any(True for _ in _validator("job.schema.json").iter_errors(job))


def result_valid(result: object) -> bool:
    return isinstance(result, dict) and not any(True for _ in _validator("job-result.schema.json").iter_errors(result))


@functools.lru_cache(maxsize=None)
def codes() -> frozenset:
    return frozenset(c["code"] for c in json.loads((FOLDER / "codes.json").read_text(encoding="utf-8"))["codes"])


# ------------------------------------------------------------------ connector protocol 2.0 (draft; LINK_PROTOCOL_V2)

#: The v2 contracts (``contracts/v2``), pinned by their own lock; used only while the switch is on.
FOLDER_V2 = FOLDER / "v2"
USED_V2 = ("declaration.schema.json", "site.schema.json", "permit-keys.schema.json", "permit.schema.json",
           "job.schema.json", "result.schema.json", "codes.json")


def verify_v2(folder: Path = FOLDER_V2) -> None:
    """Refuse to start with protocol v2 switched on when a v2 contract file does not match its lock."""
    try:
        lock = json.loads((folder / "contracts.lock").read_text(encoding="utf-8"))
        for name in USED_V2:
            if hashlib.sha256((folder / name).read_bytes()).hexdigest() != lock["files"][name]:
                raise StartupError("contracts.stale")
    except (OSError, ValueError, KeyError):
        raise StartupError("contracts.missing") from None


@functools.lru_cache(maxsize=None)
def _v2_validator(name: str) -> Draft202012Validator:
    from referencing import Registry, Resource

    schemas = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(FOLDER_V2.glob("*.schema.json"))]
    registry = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas)
    return Draft202012Validator(json.loads((FOLDER_V2 / name).read_text(encoding="utf-8")), registry=registry)


def v2_valid(name: str, instance: object) -> bool:
    """Whether ``instance`` is valid against the v2 schema ``name`` (e.g. ``job.schema.json``)."""
    return isinstance(instance, dict) and not any(True for _ in _v2_validator(name).iter_errors(instance))


@functools.lru_cache(maxsize=None)
def v2_codes() -> frozenset:
    return frozenset(c["code"] for c in json.loads((FOLDER_V2 / "codes.json").read_text(encoding="utf-8"))["codes"])
