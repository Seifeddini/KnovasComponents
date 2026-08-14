"""The named choices the Ingestion tab offers, in one table.

Every number RemoteController takes has a default somewhere — in
``sync_config.seed_from_env``, in the scheduler, in a doc example. That is fine
for a service and hopeless for a form: an administrator asked to pick
``max_ingestion_requests_per_minute`` is being asked a question they cannot
answer. So the form offers named choices with stated consequences, and this
module is the single place those choices become numbers.

Each preset carries a ``description``. It is not decoration — it is what the
radio button says, and a preset without one is a bare number wearing a name.

Plan: docs/superpowers/plans/2026-08-14-section-b-buildout.md (KC-IN-6)
"""
from __future__ import annotations

#: When RemoteController scans.
#:
#: `manual` deliberately keeps ``enabled: True``. The scheduler's ``_run_once``
#: returns immediately with status "disabled" when ``enabled`` is false
#: (sync_scheduler.py:134), so a manual profile that used it could never be
#: started by hand either. `enabled: False` is Pause — a separate control.
SCHEDULE_PRESETS: dict[str, dict] = {
    "continuous": {
        "label": "Continuously",
        "description": "Picks up new and changed documents within a few minutes, all day.",
        "mode": "continuous",
        "window": {"start_local": "00:00", "end_local": "23:59"},
        "scan_interval_seconds": 120,
    },
    "nightly": {
        "label": "Nightly, outside office hours",
        "description": "Scans between 19:00 and 06:00 only. Nothing runs while people are working.",
        "mode": "continuous",
        "window": {"start_local": "19:00", "end_local": "06:00"},
        "scan_interval_seconds": 300,
    },
    "manual": {
        "label": "Only when I start it",
        "description": "Runs once each time you press Start, then stops.",
        "mode": "one_time",
        "window": {"start_local": "00:00", "end_local": "23:59"},
        "scan_interval_seconds": None,
    },
}

#: How hard RemoteController pushes. Descriptions are approximate on purpose:
#: an administrator needs the order of magnitude and the felt consequence, and
#: a precise figure here would be a promise the file server does not keep.
THROUGHPUT_PRESETS: dict[str, dict] = {
    "gentle": {
        "label": "Gentle",
        "description": "About 300 documents an hour. No noticeable load on the file server.",
        "max_ingestion_requests_per_minute": 5,
        "burst": 2,
        "max_files_per_cycle": 100,
        "max_scan_entries_per_cycle": 5000,
    },
    "normal": {
        "label": "Normal",
        "description": "About 1'800 documents an hour. The right choice for most firms.",
        "max_ingestion_requests_per_minute": 30,
        "burst": 5,
        "max_files_per_cycle": 500,
        "max_scan_entries_per_cycle": 10000,
    },
    "fast": {
        "label": "Fast",
        "description": "About 7'200 documents an hour. Use for the first bulk import, then step down.",
        "max_ingestion_requests_per_minute": 120,
        "burst": 20,
        "max_files_per_cycle": 2000,
        "max_scan_entries_per_cycle": 50000,
    },
}

#: What to index, in the words a lawyer uses, mapped to the glob patterns
#: RemoteController's `filters.include_globs` takes. The extensions match the
#: formats `knovas-extract` can actually convert (RemoteController/docs/
#: configuration.md, "Supported document formats") — offering a type we cannot
#: extract would produce silent misses rather than an error.
FILE_TYPE_PRESETS: dict[str, dict] = {
    "documents": {
        "label": "Documents",
        "description": "Word files, PDFs, plain text and Markdown.",
        "globs": ["**/*.docx", "**/*.pdf", "**/*.txt", "**/*.md"],
    },
    "email": {
        "label": "E-mail",
        "description": "Saved messages exported from Outlook.",
        "globs": ["**/*.eml", "**/*.msg"],
    },
}

#: Folders never worth indexing. Applied on top of whatever the admin excludes,
#: because every firm has them and nobody remembers to name them.
DEFAULT_EXCLUDE_GLOBS: tuple[str, ...] = (
    "**/~$*",
    "**/.DS_Store",
    "**/Thumbs.db",
    "**/*.tmp",
    "**/.git/**",
)
