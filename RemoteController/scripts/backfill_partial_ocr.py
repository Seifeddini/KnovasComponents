#!/usr/bin/env python3
"""Re-extract and re-upload the documents the sync recorded as PARTIAL.

A document is partial (GI-EXTRACT-02) when the library returned it with OCR
pages skipped — the per-document OCR budget tripped on a long scan — or when
the extraction child was killed on the wall-clock ceiling `RC_EXTRACT_MAX_
RETRIES` times in a row ("extract_retries_exhausted"). Such a file is NOT
re-uploaded by the incremental cycle (its fingerprint is stored) and would
otherwise stay incomplete; this script is the nightly pass that finishes it:

* an OCR budget trip is re-extracted with a large budget
  (`--max-ocr-pages`, default 5000 pages, `--ocr-time-budget` /
  `--timeout`, default 1800 s);
* an exhausted retry counter is re-extracted with OCR DISABLED, so at
  least the text pages land instead of the file looping on the hung page.

A clean upload clears the partial note; a still-partial result updates it;
a failure leaves it for the next run. Nothing is uploaded with `--dry-run`.

Run it inside the RemoteController container (same env, same volumes),
outside the sync window:

    docker compose --env-file knovas.env run --rm remote-controller \\
      python /app/scripts/backfill_partial_ocr.py --dry-run
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

logger = logging.getLogger("backfill_partial_ocr")

DEFAULT_TIMEOUT_SECONDS = 1800
DEFAULT_MAX_OCR_PAGES = 5000
RETRIES_EXHAUSTED = "extract_retries_exhausted"


def _mtime_iso(path: Path) -> tuple[str, int]:
    stat = path.stat()
    return (
        datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        int(stat.st_size),
    )


def _duration(seconds: float) -> str:
    minutes, _ = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m" if hours else f"{minutes}m"


def _sources(body: dict[str, Any]) -> list[tuple[Path, Any]]:
    """(resolved root, SourceSpec) per source of the saved sync body.

    The spec carries the source's access groups and Knovas document-fields
    configuration, built exactly as the sync cycle builds it; a source whose
    field template does not compile is skipped, as the cycle skips it.
    """
    from config import doc_fields_enabled
    from discover.filesystem import resolve_root
    from sync.sync_executor import source_spec_or_none

    fields_on = doc_fields_enabled()
    out: list[tuple[Path, Any]] = []
    for index, source in enumerate(body.get("sources") or []):
        root, err = resolve_root(source.get("path"))
        if err or root is None:
            logger.warning("Source %r skipped: %s", source.get("path"), err)
            continue
        spec = source_spec_or_none(source, index, fields_on=fields_on, template_errors=Counter())
        if spec is None:
            continue
        out.append((root, spec))
    return out


def _locate(rel: str, sources: list[tuple[Path, Any]]) -> Optional[tuple[Path, Any]]:
    """The file and the spec of the FIRST source that has it: the source
    whose fields govern a relative path, as in the sync cycle."""
    for root, spec in sources:
        candidate = root / rel
        if candidate.is_file():
            return candidate, spec
    return None


def _env_for(note: dict[str, Any], args: argparse.Namespace) -> dict[str, str]:
    """The extraction environment of one backfill attempt."""
    env = {
        "RC_EXTRACT_TIMEOUT_SECONDS": str(args.timeout),
        "RC_EXTRACT_TIMEOUT_MAX_SECONDS": str(max(args.timeout, DEFAULT_TIMEOUT_SECONDS)),
        "RC_OCR_MAX_PAGES": str(args.max_ocr_pages),
        "RC_OCR_TIME_BUDGET_SECONDS": str(args.ocr_time_budget),
    }
    if note.get("reason") == RETRIES_EXHAUSTED:
        # The hung page is what exhausted the retries: land the text pages.
        env["RC_PDF_OCR_ENABLED"] = "false"
    return env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="List what would be re-uploaded; upload nothing")
    parser.add_argument("--limit", type=int, default=0, help="Stop after N documents (0 = all)")
    parser.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Wall-clock ceiling per document in seconds (default {DEFAULT_TIMEOUT_SECONDS})",
    )
    parser.add_argument(
        "--max-ocr-pages", type=int, default=DEFAULT_MAX_OCR_PAGES,
        help=f"OCR page budget per document (default {DEFAULT_MAX_OCR_PAGES})",
    )
    parser.add_argument(
        "--ocr-time-budget", type=int, default=DEFAULT_TIMEOUT_SECONDS,
        help=f"OCR time budget per document in seconds (default {DEFAULT_TIMEOUT_SECONDS}; "
        "the child still caps it below the ceiling)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    logger.setLevel(logging.DEBUG if args.verbose else logging.INFO)

    from config import doc_fields_enabled, get_config, load_config

    load_config(validate=False)
    from m365.source import m365_configured
    from sync.default_sync_body import build_default_sync_body
    from sync.doc_fields_payload import config_digest
    from sync.knovas_uploader import SemantixUploader
    from sync.sync_executor import fields_upload_kwargs, record_upload_outcome
    from sync.sync_scheduler import load_last_sync_body
    from sync.sync_state import SyncStateStore

    if m365_configured():
        logger.error(
            "The documents live in Microsoft 365; this script reads the share only. "
            "Partial documents there are retried by the next re-ingest wave."
        )
        return 2

    body = load_last_sync_body() or build_default_sync_body(get_config())
    sources = _sources(body)
    if not sources:
        logger.error("No readable sync source; nothing to backfill")
        return 2

    state = SyncStateStore()
    try:
        partial = state.partial_paths()
        if args.limit > 0:
            partial = partial[: args.limit]
        logger.info("%d partial document(s) recorded%s", len(partial), " (dry run)" if args.dry_run else "")
        counts = {"synced": 0, "partial": 0, "skipped": 0, "retry": 0, "missing": 0}
        uploader = None if args.dry_run else SemantixUploader()
        started = time.monotonic()
        for rel in partial:
            note = state.partial_note(rel) or {}
            located = _locate(rel, sources)
            if located is None:
                counts["missing"] += 1
                logger.warning("Not on the share any more (left for the prune): %s", rel)
                continue
            path, spec = located
            env = _env_for(note, args)
            mode = "OCR disabled" if env.get("RC_PDF_OCR_ENABLED") == "false" else (
                f"OCR up to {args.max_ocr_pages} pages / {args.ocr_time_budget}s"
            )
            logger.info("%s: %s -> %s", "Would re-upload" if args.dry_run else "Re-uploading", rel, mode)
            if args.dry_run or uploader is None:
                continue
            # The re-upload carries the document's current fields, so it
            # records the governing digest like any cycle upload would.
            fields_on = doc_fields_enabled()
            fields_state = state.fields_state(rel) if fields_on else None
            digest = config_digest(rel, spec) if fields_on else None
            upload_kwargs = fields_upload_kwargs(
                spec,
                fields_on=fields_on,
                previous_fields_sent=bool(fields_state is not None and fields_state.sent),
            )
            saved = {k: os.environ.get(k) for k in env}
            os.environ.update(env)
            try:
                upload = uploader.upload_file(path, rel, body, **upload_kwargs)
            finally:
                for key, value in saved.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
            mtime_iso, size_bytes = _mtime_iso(path)
            outcome = record_upload_outcome(
                state, rel, mtime_iso, size_bytes, upload, "incremental", digest=digest
            )
            counts[outcome] = counts.get(outcome, 0) + 1
            if upload.status != "ok":
                logger.warning("Backfill failed (%s): %s: %s", outcome, rel, upload.error)
            elif outcome == "partial":
                logger.warning("Still partial after the backfill: %s %s", rel, upload.partial)
            else:
                logger.info("Complete: %s", rel)
        logger.info(
            "Done in %s: synced=%d partial=%d skipped=%d retry=%d missing=%d",
            _duration(time.monotonic() - started), counts["synced"], counts["partial"],
            counts["skipped"], counts["retry"], counts["missing"],
        )
        return 0 if counts["retry"] == 0 else 3
    finally:
        state.close()


if __name__ == "__main__":
    raise SystemExit(main())
