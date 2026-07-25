#!/usr/bin/env python3
"""Backfill per-document context sidecars without re-uploading to Knovas."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sync.context_sidecar import context_store_dir_from_env, write_context_sidecar  # noqa: E402
from sync.document_text import SYNCABLE_EXTENSIONS, extract_document, is_syncable_extension  # noqa: E402

logger = logging.getLogger(__name__)


def _iter_files(roots: list[Path], *, max_files: int = 0) -> list[Path]:
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
    files = _iter_files(roots, max_files=args.max_files)
    logger.info("Scanning %d file(s) under %s", len(files), ", ".join(str(r) for r in roots))

    ok = 0
    failed = 0
    for file_path in files:
        root = next((r for r in roots if file_path.is_relative_to(r)), roots[0])
        pointer = _pointer_for(file_path, root, prefix)
        try:
            doc = extract_document(file_path)
            if write_context_sidecar(store_dir, pointer, pointer, doc.text, doc.sentences):
                ok += 1
            else:
                failed += 1
        except Exception as exc:
            failed += 1
            logger.warning("Failed %s: %s", file_path, exc)

    logger.info("Done: wrote=%d failed=%d store=%s", ok, failed, store_dir)
    return 0 if failed == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
