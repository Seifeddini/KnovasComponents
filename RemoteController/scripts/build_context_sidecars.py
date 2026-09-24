#!/usr/bin/env python3
"""Backfill per-document context sidecars without re-uploading to Knovas.

Walks the share, extracts every document RemoteController would ingest, and
writes the sidecar the Platform reads the text under a search result from.
Nothing is sent to Knovas.

A firm's share holds tens of thousands of documents and takes hours, so the run
reports how far it has got, and a sidecar already newer than its file is left
alone: a run that was stopped -- a reboot, a `docker stop` -- picks up where it
was instead of starting over. --force rewrites every sidecar.
"""
from __future__ import annotations

import argparse
import logging
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path
from typing import Iterator

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sync.context_sidecar import (  # noqa: E402
    context_store_dir_from_env,
    sidecar_path_for_pointer,
    write_context_sidecar,
)
from sync.document_text import extract_document, is_syncable_extension  # noqa: E402

logger = logging.getLogger(__name__)

PER_FILE_TIMEOUT_SECONDS = 120
PROGRESS_EVERY_SECONDS = 60


def _process_one(file_path_str: str, store_dir_str: str, pointer: str, conn) -> None:
    """Run in a child process so a hang inside a C extension (e.g. PDF parsing)
    can be force-killed at the OS level — signal.alarm cannot interrupt a C call
    that never yields back to the Python interpreter."""
    try:
        doc = extract_document(Path(file_path_str))
        ok = write_context_sidecar(Path(store_dir_str), pointer, pointer, doc.text, doc.sentences)
        conn.send(("ok", ok))
    except Exception as exc:  # noqa: BLE001 - report any failure back to parent
        conn.send(("error", str(exc)))
    finally:
        conn.close()


def _iter_files(roots: list[Path], *, max_files: int = 0) -> Iterator[Path]:
    # A generator, not a list. Collecting the whole share first meant the first
    # line of output -- and the first sidecar -- came only after every folder had
    # been walked, which on an SMB mount is a long silence that reads as a hang.
    count = 0
    for root in roots:
        if not root.is_dir():
            logger.warning("Skip missing root: %s", root)
            continue
        for path in root.rglob("*"):
            # Extension first: it costs nothing, while is_file() is a round trip
            # to the file server for every entry on the share.
            if not is_syncable_extension(path.suffix) or not path.is_file():
                continue
            yield path
            count += 1
            if max_files > 0 and count >= max_files:
                return


def _pointer_for(path: Path, root: Path, prefix: str) -> str:
    rel = path.relative_to(root).as_posix()
    prefix = prefix.strip().strip("/")
    return f"{prefix}/{rel}" if prefix else rel


def _is_current(sidecar: Path, source: Path) -> bool:
    try:
        return sidecar.stat().st_mtime >= source.stat().st_mtime
    except OSError:
        return False


def _duration(seconds: float) -> str:
    minutes, _ = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m" if hours else f"{minutes}m"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--store-dir",
        default="",
        help="Output directory (default: SEARCH_CONTEXT_STORE_PATH env)",
    )
    parser.add_argument(
        "--root",
        action="append",
        default=[],
        help="Corpus root to scan (repeatable). Default: RC_WATCH_ROOTS env",
    )
    parser.add_argument(
        "--identifier-prefix",
        default="",
        help="Pointer prefix (e.g. corpus). Default: KNOVAS_IDENTIFIER_PREFIX env, else corpus",
    )
    parser.add_argument("--max-files", type=int, default=0, help="Limit files processed (0=all)")
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="Documents extracted at the same time (default 1). Each is one CPU "
        "core while it runs; leave some for the stack that is serving searches.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=PER_FILE_TIMEOUT_SECONDS,
        help=f"Seconds one document may take before it is killed (default {PER_FILE_TIMEOUT_SECONDS})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rewrite sidecars that are already newer than their file",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    # The extraction libraries narrate every document -- three INFO lines per
    # e-mail from extract_msg, an OCR report per scanned page from pymupdf4llm --
    # and among them the once-a-minute progress line was lost. They are held to
    # warnings; this script's own INFO still shows.
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    logger.setLevel(logging.DEBUG if args.verbose else logging.INFO)
    try:
        import pymupdf

        pymupdf.set_messages(pylogging=True, pylogging_level=logging.INFO)
    except (ImportError, AttributeError, TypeError):
        pass

    store_dir = Path(args.store_dir).resolve() if args.store_dir else context_store_dir_from_env()
    if store_dir is None:
        logger.error("Set --store-dir or SEARCH_CONTEXT_STORE_PATH")
        return 1

    roots_raw = args.root
    if not roots_raw:
        env_roots = (os.environ.get("RC_WATCH_ROOTS") or "").strip()
        roots_raw = [r.strip() for r in env_roots.split(",") if r.strip()]
    if not roots_raw:
        logger.error("Provide --root or set RC_WATCH_ROOTS")
        return 2

    # The unified stack gives RemoteController KNOVAS_IDENTIFIER_PREFIX; with it as
    # the default, a run inside that container files the text under the prefix
    # the documents were ingested with. The old default alone, "corpus", filed
    # it where the Platform never looks.
    prefix = args.identifier_prefix.strip() or (os.environ.get("KNOVAS_IDENTIFIER_PREFIX") or "").strip()
    if not prefix:
        prefix = "corpus"

    jobs = max(1, args.jobs)
    roots = [Path(r).resolve() for r in roots_raw]
    logger.info(
        "Walking %s: pointers '%s/...', %d document(s) at a time, progress every %ds, store=%s",
        ", ".join(str(r) for r in roots), prefix.strip("/"), jobs, PROGRESS_EVERY_SECONDS, store_dir,
    )

    counts = {"wrote": 0, "current": 0, "failed": 0, "timed_out": 0}
    started = last_report = time.monotonic()

    def maybe_report() -> None:
        nonlocal last_report
        now = time.monotonic()
        if now - last_report < PROGRESS_EVERY_SECONDS:
            return
        last_report = now
        done = sum(counts.values())
        logger.info(
            "Progress: %d document(s) in %s -- wrote=%d current=%d failed=%d timed_out=%d",
            done, _duration(now - started), counts["wrote"], counts["current"],
            counts["failed"], counts["timed_out"],
        )

    ctx = mp.get_context("fork")
    files = _iter_files(roots, max_files=args.max_files)
    running: list[tuple] = []  # (process, receiving end, path, start time)
    exhausted = False
    while True:
        while not exhausted and len(running) < jobs:
            file_path = next(files, None)
            if file_path is None:
                exhausted = True
                break
            root = next((r for r in roots if file_path.is_relative_to(r)), roots[0])
            pointer = _pointer_for(file_path, root, prefix)
            if not args.force and _is_current(sidecar_path_for_pointer(store_dir, pointer), file_path):
                counts["current"] += 1
                maybe_report()
                continue
            receiver, sender = ctx.Pipe(duplex=False)
            proc = ctx.Process(
                target=_process_one,
                args=(str(file_path), str(store_dir), pointer, sender),
            )
            proc.start()
            sender.close()
            running.append((proc, receiver, file_path, time.monotonic()))
        if not running:
            break

        time.sleep(0.05)
        still_running = []
        for proc, receiver, file_path, began in running:
            if proc.is_alive() and time.monotonic() - began <= args.timeout:
                still_running.append((proc, receiver, file_path, began))
                continue
            if proc.is_alive():
                proc.terminate()
                proc.join(5)
                if proc.is_alive():
                    proc.kill()
                    proc.join()
                counts["timed_out"] += 1
                logger.warning("Timed out after %ds (killed): %s", args.timeout, file_path)
            else:
                proc.join()
                result = None
                if receiver.poll():
                    try:
                        result = receiver.recv()
                    except EOFError:
                        result = None
                if result is not None and result[0] == "ok" and result[1]:
                    counts["wrote"] += 1
                else:
                    counts["failed"] += 1
                    if result is not None and result[0] == "error":
                        logger.warning("Failed %s: %s", file_path, result[1])
                    elif result is None:
                        logger.warning(
                            "Failed %s: worker exited with no result (exitcode=%s)",
                            file_path, proc.exitcode,
                        )
            receiver.close()
        running = still_running
        maybe_report()

    logger.info(
        "Done: wrote=%d current=%d failed=%d timed_out=%d in %s store=%s",
        counts["wrote"], counts["current"], counts["failed"], counts["timed_out"],
        _duration(time.monotonic() - started), store_dir,
    )
    return 0 if counts["failed"] == 0 and counts["timed_out"] == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
