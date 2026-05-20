from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sync.window import is_in_window


def test_inside_window():
    tz = ZoneInfo("UTC")
    now = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
    assert is_in_window(now, "08:00", "20:00", tz)


def test_outside_window():
    tz = ZoneInfo("UTC")
    now = datetime(2026, 5, 18, 3, 0, tzinfo=timezone.utc)
    assert not is_in_window(now, "08:00", "20:00", tz)


def test_overnight_inside_late():
    tz = ZoneInfo("UTC")
    now = datetime(2026, 5, 18, 23, 0, tzinfo=timezone.utc)
    assert is_in_window(now, "22:00", "06:00", tz)


def test_overnight_outside_midday():
    tz = ZoneInfo("UTC")
    now = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
    assert not is_in_window(now, "22:00", "06:00", tz)
