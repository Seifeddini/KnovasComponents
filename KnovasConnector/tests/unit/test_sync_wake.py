"""Saving a folder list must not wait out the idle backoff.

A continuous worker re-reads the body at the top of its next cycle, and
_effective_scan_interval_seconds doubles the gap between cycles every time one
uploads nothing — capped at scan_interval_idle_max_seconds, an hour by default.
On a deployment that had been idle overnight that is an hour between saving a
profile and anything happening, with the console reporting the sync as running
the whole time. That is what "I started a sync but 0 ingestions so far" looked
like.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sync import sync_scheduler as sched  # noqa: E402


def _wait_in_thread(interval):
    done = threading.Event()

    def run():
        sched._wait_between_cycles(interval)
        done.set()

    threading.Thread(target=run, daemon=True).start()
    return done


def test_a_stored_folder_list_ends_the_wait_immediately():
    sched._stop_event.clear()
    sched._wake_event.clear()
    done = _wait_in_thread(3600)
    time.sleep(0.2)
    assert not done.is_set(), "still waiting, as it should be"
    sched.request_cycle_now()
    assert done.wait(timeout=5), "the wait did not end when a body was stored"


def test_waking_clears_the_idle_backoff():
    """The gap grew because there was nothing to do. A new folder list is a
    reason to think there is."""
    sched._stop_event.clear()
    sched._wake_event.clear()
    sched._idle_scan_multiplier = 32
    done = _wait_in_thread(3600)
    time.sleep(0.2)
    sched.request_cycle_now()
    done.wait(timeout=5)
    assert sched._idle_scan_multiplier == 1


def test_a_stop_still_ends_the_wait():
    sched._stop_event.clear()
    sched._wake_event.clear()
    done = _wait_in_thread(3600)
    time.sleep(0.2)
    sched._stop_event.set()
    try:
        assert done.wait(timeout=5), "stop no longer interrupts the wait"
    finally:
        sched._stop_event.clear()


def test_a_stale_wake_does_not_skip_the_next_wait():
    """The flag is cleared at the start of each wait, so a wake that arrived
    while a cycle was running cannot make the following one a busy loop."""
    sched._stop_event.clear()
    sched._wake_event.set()
    started = time.monotonic()
    sched._wait_between_cycles(1)
    assert time.monotonic() - started >= 0.9


def test_the_wait_still_ends_on_its_own():
    sched._stop_event.clear()
    sched._wake_event.clear()
    started = time.monotonic()
    sched._wait_between_cycles(1)
    assert 0.9 <= time.monotonic() - started < 4
