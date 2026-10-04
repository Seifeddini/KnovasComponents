"""Sync window evaluation with overnight support."""
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo


def _parse_hhmm(value: str) -> time:
    parts = value.strip().split(":")
    return time(int(parts[0]), int(parts[1]))


def is_in_window(
    now: datetime,
    start: str,
    end: str,
    tz: ZoneInfo,
) -> bool:
    local = now.astimezone(tz)
    start_t = _parse_hhmm(start)
    end_t = _parse_hhmm(end)
    current = local.time().replace(second=0, microsecond=0)

    if start_t <= end_t:
        return start_t <= current < end_t
    return current >= start_t or current < end_t
