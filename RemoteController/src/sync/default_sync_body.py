"""Build default sync request body from environment and watch roots."""
from __future__ import annotations

import os
from typing import Any

from config import AppConfig
from sync.document_text import DEFAULT_INCLUDE_GLOBS

# The shared list, not a local copy of its "**/*.ext" half: before Python
# 3.13, "**/*.pdf" needs at least one directory, so files lying directly in
# the watched folder were never picked up by the default profile.
_DEFAULT_INCLUDE_GLOBS = tuple(DEFAULT_INCLUDE_GLOBS)


def build_default_sync_body(cfg: AppConfig) -> dict[str, Any]:
    prefix = (os.environ.get("KNOVAS_IDENTIFIER_PREFIX") or "tenant").strip() or "tenant"
    roots = cfg.rc_watch_roots
    source_path = roots[0] if roots else "/mnt/documents"
    filters: dict[str, Any] = {
        "include_globs": list(_DEFAULT_INCLUDE_GLOBS),
        "exclude_globs": ["**/.git/**"],
    }
    if not (os.environ.get("M365_FOLDER_URL") or "").strip():
        # A share is bounded to the last 30 days until an administrator saves
        # a profile. A OneDrive/SharePoint folder is not: its files keep the
        # date they were last edited, so this limit would leave most of an
        # existing library unindexed without saying so.
        filters["max_document_age_seconds"] = 2592000
    return {
        "mode": "incremental",
        "sources": [{"path": source_path, "recursive": True}],
        "filters": filters,
        "ingestion": {
            "identifier_prefix": prefix,
            "part_max_chars": 500000,
        },
    }
