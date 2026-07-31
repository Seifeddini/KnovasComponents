"""Build default sync request body from environment and watch roots."""
from __future__ import annotations

import os
from typing import Any

from config import AppConfig

_DEFAULT_INCLUDE_GLOBS = (
    "**/*.md",
    "**/*.txt",
    "**/*.docx",
    "**/*.pdf",
    "**/*.eml",
    "**/*.msg",
)


def build_default_sync_body(cfg: AppConfig) -> dict[str, Any]:
    prefix = (os.environ.get("KNOVAS_IDENTIFIER_PREFIX") or "tenant").strip() or "tenant"
    roots = cfg.rc_watch_roots
    source_path = roots[0] if roots else "/mnt/documents"
    return {
        "mode": "incremental",
        "sources": [{"path": source_path, "recursive": True}],
        "filters": {
            "include_globs": list(_DEFAULT_INCLUDE_GLOBS),
            "exclude_globs": ["**/.git/**"],
            "max_document_age_seconds": 2592000,
        },
        "ingestion": {
            "identifier_prefix": prefix,
            "part_max_chars": 500000,
        },
    }
