"""The installed brain-runner package as the sync uses it: the one place that knows the package's interface.

The Platform never imports or runs code from the knowledge-base repository: it uses the brain-runner release the
image installs (pinned in the Dockerfile). The package reads the repository through its GitHub source with the
read-only token, keeps its file cache and state where the sync tells it (per tenant), and writes to the Brain with
the tenant's client certificate. Everything is passed in explicitly: no token goes through the environment.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Dict, Optional


class RunnerError(Exception):
    """The package could not do what was asked; the message names the step, never a token or a file's content."""


def _counts(plan: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Per mapping: the source items and candidates, what the plan would add, change, retire, keep, and whether it is
    blocked (the deletion guard, or source documents that could not be read)."""
    out = {}
    for mid in plan.get("mappings") or []:
        o = (plan.get("observed") or {}).get(mid) or {}
        s = (plan.get("summary") or {}).get(mid) or {}
        g = (plan.get("guard") or {}).get(mid) or {}
        out[mid] = {"source_items": o.get("source_items", 0), "candidates": o.get("candidates", 0),
                    "skipped": o.get("skipped", 0), "add": s.get("add", 0), "change": s.get("change", 0),
                    "retire": s.get("retire", 0), "unchanged": s.get("unchanged", 0), "drift": s.get("drift", 0),
                    "guard_tripped": bool(g.get("tripped")), "unreadable": o.get("unreadable", 0),
                    "blocked": bool(g.get("tripped")) or bool(o.get("unreadable"))}
    return out


class PackageRunner:
    """fetch, plan, apply, evaluate and bundle through the installed brain-runner package.

    ``github_session`` and ``brain_api`` are for tests (an in-memory GitHub and graph API); in production the
    package's own HTTP session is used and ``brain_api`` builds the client from the tenant's certificate.
    """

    def __init__(self, *, brain_api: Callable[[], Any], github_session: Any = None,
                 github_api_url: Optional[str] = None):
        self._brain_api = brain_api
        self._session = github_session
        self._api_url = github_api_url

    @staticmethod
    def version() -> str:
        import brain_runner

        return str(brain_runner.__version__)

    def _source(self, repo: str, ref: str, token: str, cache: Path):
        from brain_runner.sources.github import GitHubApi, GitHubSource

        kw: Dict[str, Any] = {"token": token, "base_url": self._api_url}
        if self._session is not None:
            kw.update(session=self._session, sleep=getattr(self._session, "sleep", None),
                      clock=getattr(self._session, "clock", None))
        return GitHubSource(repo, ref, cache, api=GitHubApi(repo, **kw))

    def head(self, repo: str, ref: str, token: str, cache: Path) -> Dict[str, Any]:
        """The commit ``ref`` names now, with its date and first message line."""
        from brain_runner.sources import SourceError

        src = self._source(repo, ref, token, cache)
        try:
            src._ready()                                    # resolves the ref (no listing beyond the cache)
            commit = src.head
            return {"commit": commit, **src.commit_info(commit)}
        except SourceError as exc:
            raise RunnerError(f"GitHub: {exc}") from None

    def fetch(self, repo: str, commit: str, token: str, cache: Path, config_path: str, to: Path,
              since: Optional[str] = None, reuse: Optional[Path] = None) -> Dict[str, Any]:
        from brain_runner.fetch import fetch
        from brain_runner.sources import SourceError

        src = self._source(repo, commit, token, cache)
        try:
            got = fetch(src, config_path, to, since=since, reuse=reuse)
        except SourceError as exc:
            raise RunnerError(f"Konfiguration nicht geholt: {exc}") from None
        src.save()
        return got

    def plan(self, config_file: str, repo: str, commit: str, token: str, cache: Path, state_path: Path,
             out: Path) -> Dict[str, Any]:
        """Every mapping's plan at ``commit``; written to ``out``. Returns the counts per mapping and the plan's hash."""
        from brain_runner import config as C
        from brain_runner.runner import make_plan
        from brain_runner.sources import SourceError
        from brain_runner.state import State

        try:
            cfg = C.load(Path(config_file))
        except C.ConfigError as exc:
            raise RunnerError(f"Konfiguration ungültig ({len(exc.errors)} Fehler)") from None
        src = self._source(repo, commit, token, cache)
        try:
            plan = make_plan(cfg, self._brain_api(), State.load(state_path), None, source=src)
        except SourceError as exc:
            raise RunnerError(f"Quellen nicht gelesen: {exc}") from None
        src.save()
        plan["state_path"] = str(state_path)
        text = json.dumps(plan, indent=1, ensure_ascii=False, default=str) + "\n"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8", newline="\n")
        source = plan.get("source") or {}
        return {"counts": _counts(plan), "plan_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "files_downloaded": int(source.get("files_downloaded") or 0)}

    def apply(self, plan_path: Path, state_path: Path) -> Dict[str, Any]:
        """Apply exactly the plan file (the package refuses a plan its state has moved past)."""
        from brain_runner.apply import NotApprovable, StalePlan, apply
        from brain_runner.state import Locked, State, lock

        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        try:
            with lock(state_path):
                rep = apply(self._brain_api(), plan, State.load(state_path))
        except (StalePlan, Locked, NotApprovable) as exc:
            raise RunnerError(f"Anwenden verweigert ({type(exc).__name__}): Probelauf erneut ausführen.") from None
        return {"writes": rep.get("writes"), "errors": len(rep.get("errors") or []),
                "changed_since_plan": len(rep.get("changed_since_plan") or []), "blocked": rep.get("blocked")}

    def evaluate(self, config_file: str, state_path: Path, out: Path) -> Dict[str, Any]:
        import datetime as dt

        from brain_runner import config as C
        from brain_runner import status as ST
        from brain_runner.state import State

        cfg = C.load(Path(config_file))
        state = State.load(state_path)
        rows = ST.rows(cfg, state)
        report = {"version": 1, "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                  "company": cfg.company, "state_revision": state.revision, "mappings": rows}
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8", newline="\n")
        return {"mappings": len(rows), "ready": sum(1 for r in rows if r.get("Ready") == "True")}

    def bundle(self, config_file: str, out: Path) -> Dict[str, Any]:
        from brain_runner import bundle as B
        from brain_runner import config as C

        cfg = C.load(Path(config_file))
        try:
            fresh = B.build(cfg, out)
        except B.BundleError as exc:
            raise RunnerError(f"Kein Definitionsbündel ({len(exc.errors)} Fehler)") from None
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(B.dumps(fresh), encoding="utf-8", newline="\n")
        return {"content_hash": fresh["header"]["content_hash"]}
