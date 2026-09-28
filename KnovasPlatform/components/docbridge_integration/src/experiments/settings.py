"""Deployment settings of the experiments module, read once at app start.

All of them come from config/config.yaml (``experiments:`` section), which
takes them from knovas.env. Runtime settings that an experiments manager may
change without a restart live in the platform ``settings`` table instead (see
``RUNTIME_DEFAULTS``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Tuple

#: Keys in the platform ``settings`` table, with their defaults.
RUNTIME_DEFAULTS = {
    # Experimenters see experiment hits in the normal search.
    "experiments.show_in_search": True,
}

_PREFIX_RE = re.compile(r"^[a-z][a-z0-9_-]{1,39}$")


@dataclass(frozen=True)
class ExperimentsSettings:
    enabled: bool = False
    #: First segment of every Knovas pointer the module writes
    #: (``<prefix>/<domain>/<KEY>``). Must not collide with the prefix
    #: RemoteController uses for files (AUTODOC_IDENTIFIER_PREFIX).
    pointer_prefix: str = "experiments"
    index_enabled: bool = True
    #: Document inits per minute the module may use. The tenant allows about
    #: six, shared with RemoteController; two leaves it the rest.
    index_per_minute: int = 2
    #: Edits within this window are written to Knovas once.
    index_debounce_seconds: int = 60
    #: Knovas access groups sent with every upload; empty = folder rules apply.
    index_access_groups: Tuple[str, ...] = field(default_factory=tuple)
    runner_url: str = ""
    runner_timeout_seconds: int = 120
    worker_enabled: bool = True
    worker_poll_seconds: float = 5.0
    #: Rows a custom evaluator receives at most (aggregates are always complete).
    evaluator_max_rows: int = 100_000
    #: Rows one request may add.
    max_rows_per_request: int = 10_000


def _split_groups(raw: Any) -> Tuple[str, ...]:
    if isinstance(raw, (list, tuple)):
        items = [str(x) for x in raw]
    else:
        items = str(raw or "").replace(";", ",").split(",")
    return tuple(dict.fromkeys(x.strip() for x in items if x and x.strip()))


def load_settings(config: Any, *, identity_enabled: bool = True) -> ExperimentsSettings:
    """Read the ``experiments:`` section.

    The module needs per-user identity (roles, the platform database). With
    ``identity.enabled: false`` it stays off whatever EXPERIMENTS_ENABLED says;
    the caller logs why.
    """
    enabled = config.get_bool("experiments.enabled", False) and identity_enabled
    prefix = str(config.get("experiments.pointer_prefix", "experiments") or "").strip().strip("/")
    if not _PREFIX_RE.match(prefix):
        prefix = "experiments"
    return ExperimentsSettings(
        enabled=enabled,
        pointer_prefix=prefix,
        index_enabled=config.get_bool("experiments.index.enabled", True),
        index_per_minute=max(1, min(60, config.get_int("experiments.index.per_minute", 2))),
        index_debounce_seconds=max(0, config.get_int("experiments.index.debounce_seconds", 60)),
        index_access_groups=_split_groups(config.get("experiments.index.access_groups", "")),
        runner_url=str(config.get("experiments.runner.url", "") or "").strip().rstrip("/"),
        runner_timeout_seconds=max(5, min(3600, config.get_int("experiments.runner.timeout_seconds", 120))),
        worker_enabled=config.get_bool("experiments.worker.enabled", True),
        worker_poll_seconds=max(0.5, config.get_float("experiments.worker.poll_seconds", 5.0)),
        evaluator_max_rows=max(1000, config.get_int("experiments.evaluator_max_rows", 100_000)),
        max_rows_per_request=max(100, config.get_int("experiments.max_rows_per_request", 10_000)),
    )
