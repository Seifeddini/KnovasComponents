"""The Connector image runs gunicorn's threaded worker (spec E6).

With the sync worker, a POST /sync longer than --timeout got the only
worker killed -- and the continuous-sync scheduler thread with it -- and
GET /sync/status could not answer while a one-time sync ran.
"""
from __future__ import annotations

from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[2] / "Dockerfile"


def _cmd() -> str:
    lines = [line for line in DOCKERFILE.read_text(encoding="utf-8").splitlines() if line.startswith("CMD")]
    assert len(lines) == 1, lines
    return lines[0]


def test_the_image_runs_one_gthread_worker_with_four_threads():
    cmd = _cmd()
    assert cmd.startswith("CMD exec gunicorn "), "shell form with exec: gunicorn is PID 1 and gets SIGTERM"
    words = cmd.split()
    assert words[words.index("-w") + 1] == "1", "one process: the sync scheduler lives in it"
    assert words[words.index("-k") + 1] == "gthread"
    assert words[words.index("--threads") + 1] == "4"


def test_the_worker_timeout_is_configurable_with_the_old_default():
    assert '--timeout "${RC_GUNICORN_TIMEOUT:-120}"' in _cmd()
