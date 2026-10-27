"""The views' thresholds, from the ``views:`` section of the configuration.

Every number a reviewer may want to move without a release lives here: the
TTL of the per-subject index, the scale guard of D1, the meta threshold of D2,
the fact pacing of D3 and the fan-out threshold of Step 1.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ViewSettings:
    ttl_seconds: int = 60
    definitions_ttl_seconds: int = 600
    edge_limit: int = 100_000
    edge_budget_seconds: float = 2.0
    meta_exact_max: int = 50
    fact_rate_per_minute: int = 300
    fact_burst: int = 120
    workers: int = 4
    fanout_threshold: int = 5
    chip_status_max: int = 60
    trace_fact_budget: int = 48
    page_size: int = 50
    max_hops: int = 4
    max_nodes_per_hop: int = 2000
    fallback_max_expansions: int = 40

    @classmethod
    def from_config(cls, config) -> "ViewSettings":
        """Read ``views.*``; a missing or unreadable key keeps its default."""
        if config is None:
            return cls()
        defaults = cls()
        values = {}
        for name, default in defaults.__dict__.items():
            key = f"views.{name}"
            try:
                if isinstance(default, float):
                    values[name] = float(config.get_float(key, default))
                else:
                    values[name] = int(config.get_int(key, default))
            except (TypeError, ValueError):
                values[name] = default
        return cls(**values)
