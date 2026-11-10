"""What Link counts for its heartbeat (MVP-19): job outcomes since the last beat, and per source when it was last read
well and how its last run ended. Codes, counts and times only; a source is named by its workspace and key, never by a
file or a document."""
from __future__ import annotations

import datetime as dt
from typing import Iterable, Mapping, Optional


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class Telemetry:
    def __init__(self):
        self.jobs: dict[str, dict[str, int]] = {}
        self.sources: dict[tuple, dict] = {}

    # ------------------------------------------------------------------ jobs

    def job(self, kind: str, status: str) -> None:
        by_kind = self.jobs.setdefault(str(kind), {})
        by_kind[str(status)] = by_kind.get(str(status), 0) + 1

    def jobs_snapshot(self) -> dict:
        return {k: dict(v) for k, v in self.jobs.items()}

    def sent(self, snapshot: Mapping[str, Mapping[str, int]]) -> None:
        """The beat carrying ``snapshot`` was taken: count only what came after it."""
        for kind, by_status in snapshot.items():
            mine = self.jobs.get(kind, {})
            for status, n in by_status.items():
                left = mine.get(status, 0) - n
                if left > 0:
                    mine[status] = left
                else:
                    mine.pop(status, None)
            if not mine:
                self.jobs.pop(kind, None)

    # ------------------------------------------------------------------ sources

    def read_ok(self, workspace: str, key: str, at: Optional[str] = None) -> None:
        self.sources.setdefault((workspace, key), {})["last_read_ok_at"] = at or now_iso()

    def run_status(self, workspace: str, key: str, status: str) -> None:
        self.sources.setdefault((workspace, key), {})["last_run_status"] = status

    def sync(self, entries: Iterable[Mapping]) -> None:
        """A component's sync status per source (protocol 1.2, ``sync_status.check``), kept for the next heartbeat."""
        from knovas_link.sync_status import FIELDS

        for e in entries:
            kept = self.sources.setdefault((e.get("workspace"), e.get("key")), {})
            kept.update({k: e[k] for k in FIELDS if k in e})

    def sources_list(self, configured: Iterable[Mapping]) -> list[dict]:
        """Per configured source: when it was last read well, its last run, and -- once its component said so -- its
        sync status (protocol 1.2; a Platform that does not know these fields drops them)."""
        from knovas_link.sync_status import FIELDS

        out = []
        for s in configured:
            seen = self.sources.get((s.get("workspace"), s.get("key")), {})
            out.append({"workspace": s.get("workspace"), "key": s.get("key"),
                        "last_read_ok_at": seen.get("last_read_ok_at"), "last_run_status": seen.get("last_run_status"),
                        **{k: seen[k] for k in FIELDS if k in seen}})
        return out
