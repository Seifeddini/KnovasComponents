"""Deployment settings of the experiments module, read once at app start.

All of them come from config/config.yaml (``experiments:`` section), which
takes them from knovas.env. Runtime settings that an experiments manager may
change without a restart live in the platform ``settings`` table instead (see
``RUNTIME_DEFAULTS``); nothing else in that shared table may be written by
this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Tuple

#: Keys in the platform ``settings`` table this module may read and write,
#: with their defaults. The table is shared with other Platform features (the
#: four-eyes bypass lives there), so writes are refused for any other key.
RUNTIME_DEFAULTS = {
    # Experimenters see experiment hits in the normal search (each person can
    # still switch it off for themselves, see USER_PREF_SHOW_IN_SEARCH).
    "experiments.show_in_search": True,
}

#: Per-person preference key in the ``settings`` table: format with user_id.
USER_PREF_SHOW_IN_SEARCH = "experiments.user.{user_id}.show_in_search"

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
    #: Knovas access groups sent with every upload. Empty means "no group":
    #: the indexer then refuses to upload unless index_unrestricted is set
    #: (fail closed -- an unrestricted document is visible to the whole tenant).
    index_access_groups: Tuple[str, ...] = field(default_factory=tuple)
    #: Upload without access groups; only sensible when a folder rule on
    #: ``<prefix>/`` restricts the documents in Knovas.
    index_unrestricted: bool = False
    #: "" (no runner), "unix:///run/experiments-runner/runner.sock" or
    #: "http://host:port".
    runner_url: str = ""
    runner_timeout_seconds: int = 90
    worker_enabled: bool = True
    worker_poll_seconds: float = 5.0
    #: Rows a custom evaluator receives at most (aggregates are always complete).
    evaluator_max_rows: int = 100_000
    #: Rows one JSON request may add.
    max_rows_per_request: int = 10_000
    #: Rows one CSV import may add.
    max_csv_rows: int = 200_000


def _flag(config: Any, key: str, default: bool) -> bool:
    """A boolean that treats a set-but-empty value as "use the default".

    config_loader substitutes ``${VAR:-default}`` with os.getenv(var, default),
    so ``VAR=`` in knovas.env yields '' -- which get_bool reads as False. For
    a default-on flag that silently switches the feature off.
    """
    raw = config.get(key, None)
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return default
    return config.get_bool(key, default)


def _int(config: Any, key: str, default: int, lo: int, hi: int) -> int:
    raw = config.get(key, None)
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return default
    return max(lo, min(hi, config.get_int(key, default)))


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
    enabled = _flag(config, "experiments.enabled", False) and identity_enabled
    prefix = str(config.get("experiments.pointer_prefix", "experiments") or "").strip().strip("/")
    if not _PREFIX_RE.match(prefix):
        prefix = "experiments"
    poll_raw = config.get("experiments.worker.poll_seconds", None)
    poll = 5.0 if poll_raw in (None, "") else max(0.5, config.get_float("experiments.worker.poll_seconds", 5.0))
    return ExperimentsSettings(
        enabled=enabled,
        pointer_prefix=prefix,
        index_enabled=_flag(config, "experiments.index.enabled", True),
        index_per_minute=_int(config, "experiments.index.per_minute", 2, 1, 60),
        index_debounce_seconds=_int(config, "experiments.index.debounce_seconds", 60, 0, 3600),
        index_access_groups=_split_groups(config.get("experiments.index.access_groups", "")),
        index_unrestricted=_flag(config, "experiments.index.unrestricted", False),
        runner_url=str(config.get("experiments.runner.url", "") or "").strip().rstrip("/"),
        runner_timeout_seconds=_int(config, "experiments.runner.timeout_seconds", 90, 5, 3600),
        worker_enabled=_flag(config, "experiments.worker.enabled", True),
        worker_poll_seconds=poll,
        evaluator_max_rows=_int(config, "experiments.evaluator_max_rows", 100_000, 1000, 1_000_000),
        max_rows_per_request=_int(config, "experiments.max_rows_per_request", 10_000, 100, 100_000),
        max_csv_rows=_int(config, "experiments.max_csv_rows", 200_000, 1000, 1_000_000),
    )
