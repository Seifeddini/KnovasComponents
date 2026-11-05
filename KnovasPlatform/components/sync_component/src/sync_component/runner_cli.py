"""brain-runner with the stream state this component keeps (MVP-20, link mode):

    python -m sync_component.runner_cli [--previous FILE] [--stream-source KEY] [--share-root DIR] <runner arguments>

A plan reads the source as a stream (brain-runner's ``streams.py``). ``previous`` is the stream summary of the last
plan whose apply succeeded for this source (the ``source.stream`` the component kept, ``--previous``): a file that
stream listed and this read does not becomes a tombstone. ``--stream-source`` is the workspace source key the
messages carry. brain-runner's command line takes neither, so this passes them to its documented Python entry point
``make_plan(..., stream_source=, previous=)`` for the one run; everything else is the runner's own command line.
``--share-root`` reads a mounted file share, as ``sync_component.share`` does.
"""
from __future__ import annotations

import argparse
import contextlib
import functools
import json
import logging
import sys
from pathlib import Path
from typing import Any, Iterator, Optional

log = logging.getLogger("sync_component.runner_cli")


def read_previous(path: Optional[str]) -> Optional[dict]:
    """The kept stream summary, or None (none kept yet, or unreadable: then there are no tombstones, and a file gone
    is still retired by its absence from the listing)."""
    if not path:
        return None
    try:
        found = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        log.warning("stream.previous_unreadable")
        return None
    return found if isinstance(found, dict) and isinstance(found.get("documents"), dict) else None


@contextlib.contextmanager
def stream_state(previous: Optional[dict], stream_source: Optional[str]) -> Iterator[None]:
    """brain-runner's ``make_plan`` with ``previous`` and ``stream_source`` for the duration."""
    from brain_runner import runner

    original = runner.make_plan

    @functools.wraps(original)
    def make_plan(*args: Any, **kwargs: Any) -> dict:
        kwargs.setdefault("previous", previous)
        kwargs.setdefault("stream_source", stream_source)
        return original(*args, **kwargs)

    runner.make_plan = make_plan
    try:
        yield
    finally:
        runner.make_plan = original


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--previous")
    ap.add_argument("--stream-source")
    ap.add_argument("--share-root")
    args, rest = ap.parse_known_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    from brain_runner.__main__ import main as runner_main

    source = None
    if args.share_root:
        from sync_component.share import ShareSource
        source = ShareSource(Path(args.share_root))
    with stream_state(read_previous(args.previous), args.stream_source or None):
        return runner_main(rest, source=source)


if __name__ == "__main__":
    sys.exit(main())
