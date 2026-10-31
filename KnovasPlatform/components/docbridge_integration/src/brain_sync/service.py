"""Sync from GitHub (WP KC-0.2): the tenant's Brain from its knowledge base, on demand, reading only changed files.

Three steps, each started by a tenant admin:

* **Neuen Stand holen** (``check``): the branch resolved to a commit (SHA, date, message) and how many files the
  mappings read changed since the last successful sync.
* **Probelauf** (``probe``, in the background): the configuration fetched at that commit (unchanged files copied
  from the last fetch, not downloaded), every mapping planned through the package with the GitHub source (its file
  cache and state are the tenant's, in the Platform's storage), the plans kept; counts and conditions per mapping.
* **Anwenden** (``apply``, in the background): exactly the Probelauf's plans, applied with the tenant's certificate,
  then the package's evaluate and bundle. Refused, and recorded as refused, when there is no successful Probelauf,
  when the branch moved to another commit since, or when the tenant (its id or certificate) changed since.

Storage per tenant (``<storage>/<tenant>/``): ``source-cache/`` (the GitHub source's cache), ``state/state.json``
(the runner's state), ``config/<commit>/`` (the fetched configuration), ``plans/<run>.json``,
``outputs/<commit>/bundle.json`` and ``evaluation.json``, and ``current.json`` naming the last successful sync.
History rows (``store.py``) hold counts and hashes only. One sync per tenant at a time.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from brain_sync.runner import PackageRunner, RunnerError
from brain_sync.store import Busy, SyncStore

logger = logging.getLogger(__name__)


class SyncError(Exception):
    """The sync cannot run as configured; the message is for the page."""

    status = 409


class Refused(SyncError):
    """Anwenden refused: run the Probelauf again."""


class SyncService:
    def __init__(self, resolver: Any, *, tenant: Callable[[], str], tenant_ref: Callable[[], str], storage: Path,
                 store: Callable[[], SyncStore], runner: PackageRunner,
                 run_in_background: Optional[Callable[[Callable[[], None]], None]] = None,
                 on_success: Optional[Callable[[str], None]] = None):
        self._resolver = resolver
        self._tenant = tenant
        self._tenant_ref = tenant_ref
        self._storage = Path(storage)
        self._store = store
        self.runner = runner
        self._background = run_in_background or _thread
        self._on_success = on_success          # tenant -> None, e.g. forget the views' cached bundle

    # -- settings and storage ------------------------------------------------------------------------------------------

    def settings(self) -> Dict[str, Any]:
        r = self._resolver
        return {"repository": str(r.value("brain_sync.repository") or ""), "branch": str(r.value("brain_sync.branch") or ""),
                "config_path": str(r.value("brain_sync.config_path") or "")}

    def tenant(self) -> str:
        return str(self._tenant() or "")

    def token_set(self) -> bool:
        """Whether a read-only token is available (never the token itself)."""
        return bool(self._resolver.secret("brain_sync.github_token"))

    def _token(self) -> str:
        token = self._resolver.secret("brain_sync.github_token")
        if not token:
            raise SyncError("Kein Lese-Token für den Sync gesetzt (Einstellungen → Lese-Token für den Sync).")
        return token

    def _ready(self) -> Dict[str, Any]:
        s = self.settings()
        missing = [k for k in ("repository", "branch", "config_path") if not s[k]]
        if missing:
            raise SyncError(f"Sync nicht eingerichtet: {', '.join(missing)} fehlt (Einstellungen).")
        return s

    def _dir(self) -> Path:
        tenant = re.sub(r"[^A-Za-z0-9_.-]", "_", str(self._tenant() or "")) or "default"
        return self._storage / tenant

    def _cache(self) -> Path:
        return self._dir() / "source-cache" / "github.json"

    def _state(self) -> Path:
        return self._dir() / "state" / "state.json"

    def _with_store(self, fn: Callable[[SyncStore], Any]) -> Any:
        store = self._store()
        try:
            return fn(store)
        finally:
            store.close()

    # -- reading ----------------------------------------------------------------------------------------------------------

    def current(self) -> Optional[Dict[str, Any]]:
        """The last successful sync: commit, time and the paths of its bundle and evaluation."""
        path = self._dir() / "current.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        base = self._dir()
        return {**data, "bundle": str(base / data["bundle"]), "evaluation": str(base / data["evaluation"])}

    def bundle_path(self) -> Optional[str]:
        cur = self.current()
        return cur["bundle"] if cur and Path(cur["bundle"]).is_file() else None

    def adopt(self, bundle_text: str, *, source: str, content_hash: str) -> str:
        """Make a bundle published elsewhere -- the Brain Builder's (build plan step B5) -- the views' current one.

        Written like a sync's: ``outputs/<source>/bundle.json`` and ``current.json`` naming it, through a temporary
        file. The evaluation of the last sync stays (a publish evaluates no mapping). Returns the bundle's path.
        """
        folder = self._dir() / "outputs" / source
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "bundle.json").write_text(bundle_text, encoding="utf-8", newline="\n")
        previous = self.current()
        evaluation = f"outputs/{source}/evaluation.json"
        if previous and Path(previous["evaluation"]).is_file():
            evaluation = str(Path(previous["evaluation"]).relative_to(self._dir()).as_posix())
        else:
            (folder / "evaluation.json").write_text('{"mappings": []}\n', encoding="utf-8", newline="\n")
        current = {"commit": None, "source": source, "at": time.time(), "bundle": f"outputs/{source}/bundle.json",
                   "evaluation": evaluation, "content_hash": content_hash, "package": self.runner.version()}
        tmp = self._dir() / "current.json.tmp"
        tmp.write_text(json.dumps(current, indent=1) + "\n", encoding="utf-8")
        tmp.replace(self._dir() / "current.json")
        if self._on_success is not None:
            self._on_success(self._tenant())
        return str(folder / "bundle.json")

    def default_bundle_source(self) -> Optional[str]:
        """``sync`` once a sync has succeeded (views.config_source's default), else None."""
        return "sync" if self.bundle_path() else None

    def run(self, run_id: str) -> Optional[Dict[str, Any]]:
        return self._with_store(lambda s: s.get(run_id))

    def history(self, limit: int = 20) -> List[Dict[str, Any]]:
        return self._with_store(lambda s: s.history(self._tenant(), limit))

    # -- Neuen Stand holen ------------------------------------------------------------------------------------------------

    def check(self, user: Any) -> Dict[str, Any]:
        s, token = self._ready(), self._token()
        try:
            head = self.runner.head(s["repository"], s["branch"], token, self._cache())
            cur = self.current()
            changed = None
            if cur:
                target = self._dir() / "config" / head["commit"]
                tmp = self._dir() / "config" / f".{head['commit']}.check"
                got = self.runner.fetch(s["repository"], head["commit"], token, self._cache(), s["config_path"], tmp,
                                        since=cur["commit"], reuse=self._config_dir(cur["commit"]))
                # what this fetch downloaded counts for the Probelauf that reuses the folder
                (tmp / ".fetched.json").write_text(json.dumps({"files_downloaded": got.get("files_downloaded", 0)}),
                                                   encoding="utf-8")
                if target.exists():
                    shutil.rmtree(target)
                tmp.rename(target)
                changed = got.get("changed_mapped_files")
        except RunnerError as exc:
            raise SyncError(str(exc)) from None
        return {**head, "branch": s["branch"], "repository": s["repository"], "changed_mapped_files": changed,
                "last_commit": cur["commit"] if cur else None}

    def _config_dir(self, commit: Optional[str]) -> Optional[Path]:
        if commit and (self._dir() / "config" / commit).is_dir():
            return self._dir() / "config" / commit
        folders = sorted((self._dir() / "config").glob("*"), key=lambda p: p.stat().st_mtime) if (self._dir() / "config").is_dir() else []
        return folders[-1] if folders else None

    # -- Probelauf ----------------------------------------------------------------------------------------------------------

    def probe(self, user: Any) -> str:
        s, token = self._ready(), self._token()
        tenant, ref = self._tenant(), self._tenant_ref()
        run_id = self._with_store(lambda st: st.start(tenant, "probe", user, branch=s["branch"], tenant_ref=ref))
        self._background(lambda: self._probe(run_id, user, s, token))
        return run_id

    def _probe(self, run_id: str, user: Any, s: Dict[str, Any], token: str) -> None:
        store = self._store()
        step = "Stand holen"
        try:
            head = self.runner.head(s["repository"], s["branch"], token, self._cache())
            commit = head["commit"]
            store.progress(run_id, "Konfiguration holen", commit_sha=commit)
            step = "Konfiguration holen"
            cur = self.current()
            target = self._dir() / "config" / commit
            # the folder "Neuen Stand holen" fetched for this commit, else the last sync's: only changed files download
            reuse = target if target.is_dir() else self._config_dir(cur["commit"] if cur else None)
            earlier = 0
            if reuse == target:
                try:
                    earlier = int(json.loads((target / ".fetched.json").read_text(encoding="utf-8"))["files_downloaded"])
                except (OSError, ValueError, KeyError):
                    earlier = 0
            tmp = self._dir() / "config" / f".{commit}.{run_id[:8]}"
            got = self.runner.fetch(s["repository"], commit, token, self._cache(), s["config_path"], tmp, reuse=reuse)
            got["files_downloaded"] = int(got.get("files_downloaded") or 0) + earlier
            if target.exists():
                shutil.rmtree(target)
            tmp.rename(target)
            config_file = str(target / s["config_path"])
            store.progress(run_id, "Zuordnungen planen", files_fetched=got.get("files_downloaded", 0))
            step = "Zuordnungen planen"
            planned = self.runner.plan(config_file, s["repository"], commit, token, self._cache(), self._state(),
                                       self._dir() / "plans" / f"{run_id}.json")
            fetched = int(got.get("files_downloaded") or 0) + int(planned["files_downloaded"])
            store.progress(run_id, "fertig", counts=planned["counts"], plan_sha256=planned["plan_sha256"],
                           files_fetched=fetched)
            blocked = sorted(m for m, c in planned["counts"].items() if c["blocked"])
            store.finish(run_id, "ok", user=user,
                         result=f"Probelauf: {len(planned['counts'])} Zuordnungen geplant"
                                + (f", blockiert: {', '.join(blocked)}" if blocked else ""))
        except RunnerError as exc:
            logger.warning("brain sync: Probelauf failed at %s (%s)", step, type(exc).__name__)
            store.finish(run_id, "failed", user=user, result=f"{step}: {exc}"[:500])
        except Exception as exc:  # noqa: BLE001 - recorded by type only: a message may quote data
            logger.warning("brain sync: Probelauf failed at %s (%s)", step, type(exc).__name__)
            store.finish(run_id, "failed", user=user, result=f"{step}: unerwarteter Fehler ({type(exc).__name__})")
        finally:
            store.close()

    # -- Anwenden ---------------------------------------------------------------------------------------------------------

    def apply(self, user: Any) -> str:
        s, token = self._ready(), self._token()
        tenant, ref = self._tenant(), self._tenant_ref()
        store = self._store()
        try:
            probe = store.latest(tenant, "probe")
            reason = None
            if probe is None or probe["status"] != "ok":
                reason = "Kein erfolgreicher Probelauf. Zuerst den Probelauf ausführen."
            else:
                applied = store.latest(tenant, "apply", status="ok")
                if applied is not None and applied["probe_id"] == probe["id"]:
                    reason = "Dieser Probelauf ist schon angewendet. Für einen neuen Stand den Probelauf erneut ausführen."
                elif probe["tenant_ref"] != ref:
                    reason = "Mandant oder Zertifikat hat sich seit dem Probelauf geändert. Probelauf erneut ausführen."
                else:
                    try:
                        head = self.runner.head(s["repository"], s["branch"], token, self._cache())
                    except RunnerError as exc:
                        raise SyncError(str(exc)) from None
                    if head["commit"] != probe["commit"]:
                        reason = (f"Der Branch steht inzwischen auf {head['commit'][:12]}, der Probelauf auf "
                                  f"{probe['commit'][:12]}. Probelauf erneut ausführen.")
            if reason:
                rid = store.start(tenant, "apply", user, branch=s["branch"], commit=probe["commit"] if probe else None,
                                  tenant_ref=ref, probe_id=probe["id"] if probe else None)
                store.finish(rid, "refused", user=user, result=reason)
                raise Refused(reason)
            run_id = store.start(tenant, "apply", user, branch=s["branch"], commit=probe["commit"], tenant_ref=ref,
                                 probe_id=probe["id"])
            store.progress(run_id, "anwenden", counts=probe["counts"], plan_sha256=probe["plan_sha256"], files_fetched=0)
        finally:
            store.close()
        self._background(lambda: self._apply(run_id, user, s, probe))
        return run_id

    def _apply(self, run_id: str, user: Any, s: Dict[str, Any], probe: Dict[str, Any]) -> None:
        store = self._store()
        step = "anwenden"
        try:
            commit = probe["commit"]
            report = self.runner.apply(self._dir() / "plans" / f"{probe['id']}.json", self._state())
            if report["errors"]:
                store.finish(run_id, "failed", user=user,
                             result=f"anwenden: {report['errors']} Schreibfehler; Brain teilweise geschrieben, Probelauf erneut")
                return
            config_file = str(self._dir() / "config" / commit / s["config_path"])
            out = self._dir() / "outputs" / commit
            step = "auswerten"
            store.progress(run_id, "auswerten")
            evaluated = self.runner.evaluate(config_file, self._state(), out / "evaluation.json")
            step = "Bündel bauen"
            store.progress(run_id, "Bündel bauen")
            bundled = self.runner.bundle(config_file, out / "bundle.json")
            current = {"commit": commit, "run_id": run_id, "probe_id": probe["id"], "at": time.time(),
                       "bundle": f"outputs/{commit}/bundle.json", "evaluation": f"outputs/{commit}/evaluation.json",
                       "content_hash": bundled["content_hash"], "package": self.runner.version()}
            tmp = self._dir() / "current.json.tmp"
            tmp.write_text(json.dumps(current, indent=1) + "\n", encoding="utf-8")
            tmp.replace(self._dir() / "current.json")
            store.finish(run_id, "ok", user=user,
                         result=f"angewendet: {report['writes'] or 0} Schreibvorgänge; {evaluated['ready']} von "
                                f"{evaluated['mappings']} Zuordnungen bereit; Bündel {bundled['content_hash'][:19]}")
            if self._on_success is not None:
                self._on_success(self._tenant())
        except RunnerError as exc:
            logger.warning("brain sync: Anwenden failed at %s (%s)", step, type(exc).__name__)
            store.finish(run_id, "failed", user=user, result=f"{step}: {exc}"[:500])
        except Exception as exc:  # noqa: BLE001
            logger.warning("brain sync: Anwenden failed at %s (%s)", step, type(exc).__name__)
            store.finish(run_id, "failed", user=user, result=f"{step}: unerwarteter Fehler ({type(exc).__name__})")
        finally:
            store.close()


def _thread(job: Callable[[], None]) -> None:
    import threading

    threading.Thread(target=job, name="brain-sync", daemon=True).start()
