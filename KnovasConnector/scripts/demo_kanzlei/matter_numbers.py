"""How many matters the firm opened per year, and on which day each was opened.

A matter's reference is ``{year}-{running number}``. The written matters of the
world (``2024-017``, ``2024-112`` …) sit inside the firm's full register, so the
register must have at least that many matters in those years, and the opening
dates must rise with the running number. Both the world and the firm's records
use this module, so they cannot disagree.
"""
from __future__ import annotations

from datetime import date, timedelta


def as_of(cfg: dict) -> date:
    """The stand-in's "today". Fixed in the world file, never the wall clock."""
    return date.fromisoformat(str(cfg.get("as_of", "2026-11-18")))


def total_matters(cfg: dict, *, pilot: bool) -> int:
    records = cfg.get("records", {})
    return int(records.get("matters_pilot", 60) if pilot else records.get("matters_total", 1500))


def split(aktenzeichen: str) -> tuple[int, int]:
    year, seq = aktenzeichen.split("-")
    return int(year), int(seq)


def year_counts(cfg: dict, pinned: list[str], *, pilot: bool) -> dict[int, int]:
    """Matters opened per year: a small firm in 1998 that grew.

    ``pinned`` are references that must exist; each year gets at least the
    highest running number pinned in it.
    """
    today = as_of(cfg)
    first = int(cfg.get("records", {}).get("first_year", 1998))
    years = list(range(first, today.year + 1))
    total = total_matters(cfg, pilot=pilot)
    floor = {year: 0 for year in years}
    for ref in pinned:
        year, seq = split(ref)
        if year in floor:
            floor[year] = max(floor[year], seq)
    span = max(1, len(years) - 1)

    def shape(year: int) -> float:
        t = (year - first) / span
        weight = 13 + 116 * t * t
        if year == today.year:
            weight *= today.timetuple().tm_yday / 365
        return weight

    def at(scale: float) -> dict[int, int]:
        return {year: max(floor[year], round(shape(year) * scale)) for year in years}

    low, high = 0.0, 4.0
    for _ in range(60):
        mid = (low + high) / 2
        if sum(at(mid).values()) < total:
            low = mid
        else:
            high = mid
    counts = at(high)
    # Rounding leaves a few too many or too few; settle them in the latest years.
    excess = sum(counts.values()) - total
    for year in reversed(years * 4):
        if excess == 0:
            break
        if excess > 0 and counts[year] > floor[year]:
            counts[year] -= 1
            excess -= 1
        elif excess < 0:
            counts[year] += 1
            excess += 1
    return counts


def _workday(day: date) -> date:
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def opened_on(year: int, seq: int, count: int, today: date) -> date:
    """Opening day of matter ``seq`` of ``count`` in ``year``, rising with ``seq``."""
    start = date(year, 1, 3)
    end = min(date(year, 12, 22), today - timedelta(days=2)) if year == today.year else date(year, 12, 22)
    if end <= start:
        return _workday(start)
    fraction = (seq - 0.5) / max(count, seq)
    return _workday(start + timedelta(days=int((end - start).days * fraction)))


def opened_between(seq: int, before: tuple[int, date], after: tuple[int, date]) -> date:
    """Opening day of ``seq`` between two matters whose days are already fixed."""
    (seq_a, day_a), (seq_b, day_b) = before, after
    if seq_b <= seq_a or day_b <= day_a:
        return day_a
    fraction = (seq - seq_a) / (seq_b - seq_a)
    return _workday(day_a + timedelta(days=int((day_b - day_a).days * fraction)))


def opened_anchored(year: int, seq: int, count: int, today: date, anchors: list[tuple[int, date]]) -> date:
    """Opening day that rises with the running number and meets days already fixed.

    ``anchors`` are (running number, day) of matters in ``year`` whose opening
    day is given, sorted by running number.
    """
    if not anchors:
        return opened_on(year, seq, count, today)
    before = max((a for a in anchors if a[0] <= seq), default=None)
    after = min((a for a in anchors if a[0] >= seq), default=None)
    if before is None:
        return opened_between(seq, (0, date(year, 1, 2)), after)
    if after is None:
        last = date(year, 12, 22)
        if year == today.year:
            last = min(last, today - timedelta(days=2))
        return opened_between(seq, before, (max(count, seq) + 1, max(last, before[1])))
    return opened_between(seq, before, after)
