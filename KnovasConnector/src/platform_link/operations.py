"""What a dry run, an apply and a check are for the Knovas Connector: its own scan and upload.

A dry run scans the folders and keeps the list of documents it would send. An apply sends only those, once, from the
body the dry run used; a document that appeared since waits for the next dry run. Nothing is removed from the Brain:
an apply keeps no room for that, because a dry run does not show it (ingestion.delete_on_remove is false). The list
stays on the connector's state volume, readable by its own user only; only counts leave.

What to read comes from the workspace's sources (the Platform's /config). A connector enrolled with no such source, an
installation that already has a saved folder list, keeps that list, with its access groups and fields.

A folder's access groups are a choice someone has to make: a source without one is refused rather than sent unrestricted
(GI-ACCESSROLES). «alle» says the whole tenant may read it.
"""
from __future__ import annotations

import copy
import json
import logging
import os
from pathlib import Path
from typing import Any, Mapping, Optional

from config import get_config
from platform_link.agent import Outcome
from sync.default_sync_body import build_default_sync_body
from sync.sync_config import load_sync_config
from sync.sync_executor import _needs_upload, scan_document_inventory
from sync.sync_scheduler import SyncRunContext, load_last_sync_body, run_one_time, stop_continuous
from util.schema import validate_sync_request

log = logging.getLogger("platform_link")
SOURCE_KIND = "knovas_connector"
ALL = "alle"
COUNT_LIMIT = 200_000


def _private_json(path: Path, data: Any) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle)


def _groups(source: Mapping[str, Any]) -> Optional[list[str]]:
    """The source's access groups; [] for the whole tenant; None when nobody chose."""
    raw = str((source.get("settings") or {}).get("access_groups") or "").strip()
    if not raw:
        return None
    if raw.lower() == ALL:
        return []
    return list(dict.fromkeys(g.strip() for g in raw.split(",") if g.strip()))


def _folder(source: Mapping[str, Any], root: str) -> Optional[Path]:
    folder = str((source.get("settings") or {}).get("folder") or "").replace("\\", "/").strip("/")
    if ".." in folder.split("/"):
        return None
    return Path(root) / folder if folder else Path(root)


class ConnectorOperations:
    def __init__(self, folder: Path):
        self.plan_path = Path(folder) / "plan.json"

    # -------------------------------------------------------- what to read

    @staticmethod
    def _sources(config: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        return [s for s in config.get("sources") or [] if s.get("kind") == SOURCE_KIND]

    def _body(self, config: Mapping[str, Any]) -> tuple[Optional[dict], Optional[str]]:
        """(the sync body, or None with the code that says why)."""
        cfg = get_config()
        sources = self._sources(config)
        if sources:
            root = cfg.rc_watch_roots[0] if cfg.rc_watch_roots else "/sources"
            entries = []
            for s in sources:
                groups, folder = _groups(s), _folder(s, root)
                if groups is None:
                    return None, "documents.no_access_choice"
                if folder is None:
                    return None, "fetch.bad_folder"
                entry: dict[str, Any] = {"path": str(folder), "recursive": True}
                if groups:
                    entry["access_groups"] = groups
                entries.append(entry)
            body = build_default_sync_body(cfg)
            # The default's 30-day limit is for an installation nobody configured; here a person chose this folder, and
            # the dry run shows what would be sent.
            body["filters"].pop("max_document_age_seconds", None)
            body["sources"] = entries
        else:
            saved = load_last_sync_body()
            if not saved:
                return None, "config.nothing_to_run"
            body = copy.deepcopy(saved)
        body.setdefault("ingestion", {})["delete_on_remove"] = False
        errors = validate_sync_request(body)
        if errors:
            return None, "documents.bad_config"
        return body, None

    # -------------------------------------------------------- the protocol's operations

    def check(self, source: Mapping[str, Any], config: Mapping[str, Any]) -> tuple[str, dict]:
        from m365.source import m365_configured

        if source.get("kind") != SOURCE_KIND:
            return "source.kind_unknown", {}
        if _groups(source) is None:
            return "documents.no_access_choice", {}
        if not m365_configured():
            cfg = get_config()
            folder = _folder(source, cfg.rc_watch_roots[0] if cfg.rc_watch_roots else "/sources")
            if folder is None:
                return "fetch.bad_folder", {}
            if not folder.is_dir():
                return "fetch.folder_missing", {}
            if not os.access(folder, os.R_OK | os.X_OK):
                return "fetch.folder_unreadable", {}
            files, capped = 0, False
            for _, _, names in os.walk(folder):
                files += len(names)
                if files >= COUNT_LIMIT:
                    files, capped = COUNT_LIMIT, True
                    break
            if files == 0:
                return "fetch.folder_empty", {}
            mapped = [m for m in config.get("mappings") or []
                      if m.get("workspace") == source.get("workspace") and m.get("source") == source.get("key")]
            if mapped:
                return "documents.mappings_not_run", {"files": files}
            return "source.ok", {"files": files, **({"capped": 1} if capped else {})}
        return "source.ok", {}

    def dry_run(self, config: Mapping[str, Any]) -> Outcome:
        self.plan_path.unlink(missing_ok=True)
        body, why = self._body(config)
        if body is None:
            return Outcome(status="failed", errors=1, codes=[str(why)])
        for s in self._sources(config):
            code, _ = self.check(s, config)
            if code not in ("source.ok", "documents.mappings_not_run"):
                return Outcome(status="failed", errors=1, codes=[code])
        summary = scan_document_inventory(body, include_documents=True, sync_config=load_sync_config())
        paths = [d.relative_path for d in summary.documents if _needs_upload(d.status, "incremental")]
        _private_json(self.plan_path, {"body": body, "paths": paths})
        return Outcome(added=summary.pending, changed=summary.modified + summary.fields_changed)

    def apply(self, config: Mapping[str, Any]) -> Outcome:
        """Send what the last dry run listed, from the body it used. Without one it refuses; the plan is used once."""
        if not self.plan_path.exists():
            return Outcome(status="failed", errors=1, codes=["apply.no_dry_run"])
        plan = json.loads(self.plan_path.read_text(encoding="utf-8"))
        paths = frozenset(plan.get("paths") or [])
        state, result = run_one_time(SyncRunContext(sync_body=plan["body"], sync_config=load_sync_config(), only_paths=paths))
        if state == "already_running":
            return Outcome(status="failed", errors=1, codes=["run.busy"])
        self.plan_path.unlink(missing_ok=True)
        errors = len(result.errors or [])
        codes = [f"run.{result.paused_reason}"] if result.paused_reason else []
        if result.files_uploaded + errors < len(paths):
            codes.append("apply.partial")
        return Outcome(status="failed" if errors or result.paused_reason else "ok", added=int(result.files_uploaded),
                       errors=errors, codes=codes)

    def stop(self) -> None:
        stop_continuous()
