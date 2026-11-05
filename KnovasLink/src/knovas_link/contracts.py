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
