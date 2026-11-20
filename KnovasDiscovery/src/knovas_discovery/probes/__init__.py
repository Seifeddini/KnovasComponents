"""The probes. Each reads one kind of source and returns results `{key, status, error, facts}` plus a detail.

Every probe module has `FACTS`: the facts it produces, as dotted paths with one line of meaning each. The catalogue
check and docs/facts.md are built from them, so a catalogue entry can only name a fact a probe really produces.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Callable

from ..secrets import Secrets

# Probe name in --only and in the catalogue's `on`  ->  section of findings.json
SECTIONS = {
    "environment": "environment",
    "fileshare": "fileshares",
    "directory": "directories",
    "mssql": "databases",
    "m365": "m365",
}
# Section of intake.targets each probe reads
TARGETS = {"fileshare": "fileshares", "directory": "directories", "mssql": "databases", "m365": "m365"}


@dataclass
class Context:
    """What a probe gets besides its target: the scan time, the secrets, and replaceable access layers."""

    now: _dt.datetime
    secrets: Secrets
    catalogue: dict | None = None
    knovas: dict = field(default_factory=dict)
    # Factories for the thin access layers; tests replace them with fakes.
    ldap_factory: Callable[..., Any] | None = None
    sql_factory: Callable[..., Any] | None = None
    http_session: Any = None
    out_dir: Any = None
    log: Callable[[str], None] = lambda message: None


@dataclass
class Output:
    """One probe result and its detail (the detail stays at the client)."""

    result: dict
    detail: dict = field(default_factory=dict)


def all_facts() -> dict[str, dict[str, str]]:
    """Probe name -> {fact path: meaning}, for the catalogue check and docs/facts.md."""
    from . import directory, environment, fileshare, m365, mssql

    return {
        "environment": environment.FACTS,
        "fileshare": fileshare.FACTS,
        "directory": directory.FACTS,
        "mssql": mssql.FACTS,
        "m365": m365.FACTS,
        "intake": {
            "category": "the category the client named (practice, dms, mail, accounting, time, collaboration, web-api, other)",
            "product": "the product the client named: a catalogue key, a product name in lower case, or unknown",
        },
    }
