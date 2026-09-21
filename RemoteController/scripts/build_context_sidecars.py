#!/usr/bin/env python3
"""Backfill per-document context sidecars without re-uploading to Knovas."""
from __future__ import annotations

import argparse
import logging
import multiprocessing as mp
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sync.context_sidecar import (  # noqa: E402
    context_store_dir_from_env,
    sidecar_path_for_pointer,
    write_context_sidecar,
)
from sync.document_text import SYNCABLE_EXTENSIONS, extract_document, is_syncable_extension  # noqa: E402

logger = logging.getLogger(__name__)

PER_FILE_TIMEOUT_SECONDS = 120


def _process_one(file_path_str: str, store_dir_str: str, pointer: str, result_queue) -> None:
    """Run in a child process so a hang inside a C extension (e.g. PDF parsing)
    can be force-killed at the OS level — signal.alarm cannot interrupt a C call
    that never yields back to the Python interpreter."""
    try:
        doc = extract_document(Path(file_path_str))
        ok = write_context_sidecar(Path(store_dir_str), pointer, pointer, doc.text, doc.sentences)
        result_queue.put(("ok", ok))
    except Exception as exc:  # noqa: BLE001 - report any failure back to parent
        result_queue.put(("error", str(exc)))


def _iter_files(
    roots: list[Path],
    *,
    max_files: int = 0,
    max_age_seconds: int = 0,
) -> list[Path]:
    """Collect syncable files, newest-first retention applied.

    A retention rule that bounds ingestion has to bound the backfill too. A
    sidecar for a document RemoteController will never upload is written,
    stored and then never read -- and on a corpus this size that is days of
    processing spent on files that are out of scope by policy. The mtime
    basis matches `filters.max_document_age_seconds` in the sync body, so
    the two agree on which documents exist."""
    cutoff = time.time() - max_age_seconds if max_age_seconds > 0 else None
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            logger.warning("Skip missing root: %s", root)
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if not is_syncable_extension(path.suffix):
                continue
            if cutoff is not None:
                try:
                    if path.stat().st_mtime < cutoff:
                        continue
                except OSError as exc:
                    # Unreadable mtime cannot be shown to be in scope, and a
                    # retention limit that fails open is not a limit.
                    logger.debug("Skip (no mtime) %s: %s", path, exc)
                    continue
            found.append(path)
            if max_files > 0 and len(found) >= max_files:
                return found
    return found


def _pointer_for(path: Path, root: Path, prefix: str) -> str:
    rel = path.relative_to(root).as_posix()
    prefix = prefix.strip().strip("/")
    return f"{prefix}/{rel}" if prefix else rel


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
        help="Pointer prefix (e.g. corpus). Default: ingestion.identifier_prefix or corpus",
    )
    parser.add_argument("--max-files", type=int, default=0, help="Limit files processed (0=all)")
    parser.add_argument(
        "--max-age-seconds",
        type=int,
        default=0,
        help=(
            "Skip files whose mtime is older than this (0=no limit). Set it to the "
            "filters.max_document_age_seconds the sync body uses, e.g. 220992000 for 7 years."
        ),
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Leave files that already have a sidecar alone -- resumes a long run.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    store_dir = Path(args.store_dir).resolve() if args.store_dir else context_store_dir_from_env()
    if store_dir is None:
        logger.error("Set --store-dir or SEARCH_CONTEXT_STORE_PATH")
        return 1

    roots_raw = args.root
    if not roots_raw:
        import os

        env_roots = (os.environ.get("RC_WATCH_ROOTS") or "").strip()
        roots_raw = [r.strip() for r in env_roots.split(",") if r.strip()]
    if not roots_raw:
        logger.error("Provide --root or set RC_WATCH_ROOTS")
        return 2

    prefix = args.identifier_prefix.strip()
    if not prefix:
        prefix = "corpus"

    roots = [Path(r).resolve() for r in roots_raw]
    files = _iter_files(
        roots,
        max_files=args.max_files,
        max_age_seconds=args.max_age_seconds,
    )
    logger.info("Scanning %d file(s) under %s", len(files), ", ".join(str(r) for r in roots))

    ok = 0
    failed = 0
    timed_out = 0
    skipped = 0
    ctx = mp.get_context("fork")
    for file_path in files:
        root = next((r for r in roots if file_path.is_relative_to(r)), roots[0])
        pointer = _pointer_for(file_path, root, prefix)

        if args.skip_existing and sidecar_path_for_pointer(store_dir, pointer).is_file():
            skipped += 1
            continue

        result_queue = ctx.Queue()
        proc = ctx.Process(
            target=_process_one,
            args=(str(file_path), str(store_dir), pointer, result_queue),
        )
        proc.start()
        proc.join(PER_FILE_TIMEOUT_SECONDS)

        if proc.is_alive():
            proc.terminate()
            proc.join(5)
            if proc.is_alive():
                proc.kill()
                proc.join()
            timed_out += 1
            logger.warning("Timed out after %ds (killed): %s", PER_FILE_TIMEOUT_SECONDS, file_path)
        elif not result_queue.empty():
            status, payload = result_queue.get()
            if status == "ok" and payload:
                ok += 1
            else:
                failed += 1
                if status == "error":
                    logger.warning("Failed %s: %s", file_path, payload)
        else:
            failed += 1
            logger.warning("Failed %s: worker exited with no result (exitcode=%s)", file_path, proc.exitcode)
        result_queue.close()

    logger.info(
        "Done: wrote=%d failed=%d timed_out=%d skipped=%d store=%s",
        ok,
        failed,
        timed_out,
        skipped,
        store_dir,
    )
    return 0 if failed == 0 and timed_out == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
