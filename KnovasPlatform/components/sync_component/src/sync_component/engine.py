"""The mapping runner as this component's engine (DEC-0040): plan on a dry run, apply exactly the plan a dry run made.

One working folder per source: the configuration package the Platform compiled, the fetched data, the plan and the
runner's state. An apply takes the plan on disk, so what is written is what the dry run showed; without one it refuses.
Only counts leave this module: the runner's plan names keys and values and stays on site.

With ``keep_stream`` (link mode, MVP-20) the engine also keeps, per source, the stream the last successful apply
acknowledged (the plan's ``source.stream``, in ``stream-acknowledged.json``) and gives it to the next plan as
``previous``, with the source's key as the stream's source (``sync_component.runner_cli``). ``compile_write`` runs
brain-runner's ``compile-write`` for a write job (MVP-22) in a folder of its own.

Diff polling and automatic apply (KG-2): per source the engine keeps its sync state (``sync-state.json``): the commit
the last apply without errors synced (written only then), when, the package it ran, what the last apply wrote (counts
only) and the commit the last poll planned. ``affected`` asks brain-runner which mappings the commits since the synced
one concern; ``attention`` says what in a plan needs a person, so that an automatic apply never takes it.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,120}$")
_CODE = re.compile(r"^[a-z][a-z0-9_.]{1,60}$")
PACKAGE_FILE = "brain-config.yaml"
#: The stream the last successful apply acknowledged, per source (link mode).
ACKNOWLEDGED_FILE = "stream-acknowledged.json"
#: Per source: the synced commit, the package it ran, the last apply's counts, the last poll (KG-2).
SYNC_FILE = "sync-state.json"
#: What the plan on disk would sync when it is applied without errors: its commit and its package.
PENDING_FILE = "sync-pending.json"
COMMIT = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")


def package_hash(files: Mapping[str, str]) -> str:
    """The configuration package a plan ran, as one hash: a new one (a new published version) plans every mapping."""
    digest = hashlib.sha256()
    for rel in sorted(files):
        digest.update(rel.encode("utf-8") + b"\0" + str(files[rel]).encode("ascii", "replace") + b"\0")
    return digest.hexdigest()


def attention(plan: Mapping[str, Any]) -> list[str]:
    """What in a plan needs a person, as codes; empty when nothing does. An automatic apply takes only a plan with
    nothing here: retirements or removals the deletion guard holds (of nodes or documents), drift (of facts, computed
    attributes or derived edges), held retire entries, a release, documents stopped, reported or held, a source
    document that could not be read, anything unresolved, derived edges blocked. A plan of a version this component
    does not know needs a person too (fail closed)."""
    out: list[str] = []

    def add(code: str) -> None:
        if code not in out:
            out.append(code)

    if not isinstance(plan, Mapping) or plan.get("version") != 1:
        return ["plan.unknown_version"]
    tripped = lambda guards: any(isinstance(g, Mapping) and g.get("tripped") for g in (guards or {}).values())  # noqa: E731
    docs = plan.get("documents") if isinstance(plan.get("documents"), Mapping) else {}
    computed = plan.get("computed") if isinstance(plan.get("computed"), Mapping) else {}
    derived = plan.get("derived") if isinstance(plan.get("derived"), Mapping) else {}
    retire = plan.get("retire") if isinstance(plan.get("retire"), Mapping) else {}
    if plan.get("blocked") or plan.get("unguarded") or tripped(plan.get("guard")) or tripped(docs.get("guard")):
        add("plan.guard_held")
    if plan.get("unreadable"):
        add("plan.unreadable")
    if plan.get("drift") or computed.get("drift") or derived.get("drift"):
        add("plan.drift")
    if plan.get("unresolved"):
        add("plan.unresolved")
    if derived.get("blocked"):
        add("plan.derived_blocked")
    if (plan.get("released_mappings") or docs.get("released")
            or any(isinstance(i, Mapping) and i.get("op") == "release" for i in plan.get("items") or [])):
        add("plan.release")
    if retire.get("items"):
        add("plan.retire_held")
    for part, code in (("held", "plan.documents_held"), ("stopped", "plan.documents_stopped"),
                       ("reported", "plan.documents_reported")):
        if docs.get(part):
            add(code)
    return out


class EngineError(RuntimeError):
    """The engine could not do it; the message is a code for the run's events."""


@dataclass
class Result:
    status: str = "ok"                # ok | held | failed
    added: int = 0
    changed: int = 0
    retired: int = 0
    errors: int = 0
    held: list[dict] = field(default_factory=list)
    codes: list[str] = field(default_factory=list)


def _safe_path(root: Path, rel: str) -> Path:
    parts = rel.replace("\\", "/").split("/")
    if rel.startswith(("/", "\\")) or ".." in parts or ":" in parts[0]:
        raise EngineError("package.bad_path")
    return root.joinpath(*parts)


class RunnerEngine:
    def __init__(self, folder: Path, env: Mapping[str, str], *, run: Optional[Callable[..., Any]] = None,
                 keep_stream: bool = False):
        self.folder = Path(folder)
        self.env = dict(env)
        self._run = run or subprocess.run
        self.keep_stream = keep_stream

    def source_root(self, workspace: str, key: str) -> Path:
        if not (_SAFE.match(str(workspace)) and _SAFE.match(str(key))):
            raise EngineError("source.bad_key")
        root = self.folder / str(workspace) / str(key)
        root.mkdir(parents=True, exist_ok=True)
        return root

    def write_package(self, root: Path, files: Mapping[str, str]) -> None:
        for rel, encoded in files.items():
            target = _safe_path(root, rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(encoded))

    def _cli(self, *args: str, module: tuple[str, ...] = ("brain_runner",), env: Optional[Mapping[str, str]] = None) -> Any:
        return self._run([sys.executable, "-m", *module, *args], capture_output=True, text=True, timeout=3600,
                         env={**os.environ, "PYTHONIOENCODING": "utf-8", **self.env, **(env or {})})

    def dry_run(self, root: Path, mappings: list[str], reading: Any = None, *, commit: Optional[str] = None,
                package: Optional[str] = None) -> Result:
        """A plan of the mappings over the source, kept for the apply. Writes nothing to the Brain. ``reading`` says
        where the runner reads (fetchers.Reading): by default the fetched data beside the configuration. What the plan
        syncs once applied without errors is kept beside it: ``commit`` (for a plan of only the mappings new commits
        concern, the commit those were computed to; else the commit the plan read) and the ``package``."""
        plan, state = root / "plan.json", root / "state.json"
        plan.unlink(missing_ok=True)
        (root / PENDING_FILE).unlink(missing_ok=True)
        args = ["plan", "--config", str(root / PACKAGE_FILE), "--out", str(plan), "--state", str(state)]
        for m in mappings:
            args += ["--mapping", m]
        args += list(getattr(reading, "args", None) or [])
        share = getattr(reading, "share_root", None)
        if self.keep_stream:
            module = ("sync_component.runner_cli", "--stream-source", root.name, "--previous", str(root / ACKNOWLEDGED_FILE))
            module += ("--share-root", str(share)) if share else ()
        else:
            module = ("sync_component.share", "--root", str(share)) if share else ("brain_runner",)
        done = self._cli(*args, module=module, env=getattr(reading, "env", None))
        if done.returncode != 0 or not plan.exists():
            return Result(status="failed", errors=1, codes=[f"runner.plan_exit_{done.returncode}"])
        made = json.loads(plan.read_text(encoding="utf-8"))
        read = ((made.get("source") or {}).get("commit") if isinstance(made.get("source"), dict) else None)
        found = commit or (read if isinstance(read, str) and COMMIT.match(read) else None)
        _write_json(root / PENDING_FILE, {"commit": found, "package": package})
        return self.counts(made)

    @staticmethod
    def counts(plan: Mapping[str, Any]) -> Result:
        result = Result()
        blocked = set(plan.get("blocked") or [])
        for mid, s in (plan.get("summary") or {}).items():
            result.added += int(s.get("add", 0))
            result.changed += int(s.get("change", 0))
            result.retired += int(s.get("retire", 0))
        for mid in sorted(blocked):
            types = ((plan.get("guard") or {}).get(mid) or {}).get("types") or {}
            result.held.append({"mapping": mid, "retire": {t: int(v.get("retire", 0)) for t, v in types.items()},
                                "share": max((float(v.get("share", 0)) for v in types.values()), default=None)})
        if result.held:
            result.status = "held"
        return result

    def apply(self, root: Path, *, approve_reason: Optional[str] = None, auto: bool = False) -> Result:
        """Write what the last dry run planned; with a reason, also what the deletion guard held. ``auto``: the
        component applies on its own (the source's automatic apply, KG-2); the caller checked ``attention`` first.
        The sync state records what the apply wrote, and an apply without errors the commit its plan synced."""
        result = self._apply(root, approve_reason)
        if result.codes != ["apply.no_dry_run"]:
            self._record_apply(root, result, auto)
        return result

    def _apply(self, root: Path, approve_reason: Optional[str]) -> Result:
        plan, state = root / "plan.json", root / "state.json"
        if not plan.exists():
            return Result(status="failed", errors=1, codes=["apply.no_dry_run"])
        args = ["apply", "--plan", str(plan), "--state", str(state)]
        if approve_reason:
            args += ["--approve-guard", approve_reason]
        stream = self._planned_stream(plan) if self.keep_stream else None
        done = self._cli(*args)
        if done.returncode == 3:
            return Result(status="failed", errors=1, codes=["apply.stale_plan"])
        try:
            report = json.loads(done.stdout)
        except ValueError:
            return Result(status="failed", errors=1, codes=[f"runner.apply_exit_{done.returncode}"])
        totals: Counter = Counter()
        for name, n in (report.get("done") or {}).items():
            totals[str(name).rsplit(".", 1)[-1]] += int(n)
        result = Result(added=totals["added"], changed=totals["modified"], retired=totals["retired"],
                        errors=int(report.get("errors") or 0))
        result.status = "failed" if done.returncode != 0 else ("held" if report.get("blocked") else "ok")
        if done.returncode == 0 and not report.get("blocked"):
            plan.unlink(missing_ok=True)              # a plan is applied once: the next apply needs a new dry run
            if stream is not None:
                self._acknowledge(root, stream)
        return result

    # -------------------------------------------------------- the sync state (KG-2)

    @staticmethod
    def sync_state(root: Path) -> dict:
        try:
            found = json.loads((root / SYNC_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return found if isinstance(found, dict) else {}

    def update_sync_state(self, root: Path, **fields: Any) -> dict:
        state = {**self.sync_state(root), **fields}
        _write_json(root / SYNC_FILE, state)
        return state

    @staticmethod
    def pending(root: Path) -> dict:
        """What the plan on disk syncs once applied without errors: ``{commit, package}``; {} without a plan."""
        try:
            found = json.loads((root / PENDING_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return found if isinstance(found, dict) else {}

    def _record_apply(self, root: Path, result: Result, auto: bool) -> None:
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        fields: dict = {"last_apply": {"at": now, "status": result.status, "added": result.added,
                                       "changed": result.changed, "retired": result.retired, "errors": result.errors,
                                       "auto": bool(auto)}}
        if result.status == "ok" and result.errors == 0:
            pending = self.pending(root)
            if isinstance(pending.get("commit"), str) and COMMIT.match(pending["commit"]):
                fields.update(synced_commit=pending["commit"], synced_at=now)
            if pending:
                fields["package"] = pending.get("package")
            (root / PENDING_FILE).unlink(missing_ok=True)
        self.update_sync_state(root, **fields)

    def sync_states(self) -> list[dict]:
        """Every source this component keeps a sync state for: ``{workspace, key, **state}``."""
        out = []
        for found in sorted(self.folder.glob(f"*/*/{SYNC_FILE}")):
            workspace, key = found.parent.parent.name, found.parent.name
            if _SAFE.match(workspace) and _SAFE.match(key):
                out.append({"workspace": workspace, "key": key, **self.sync_state(found.parent)})
        return out

    def attention(self, root: Path) -> list[str]:
        """What in the plan on disk needs a person (``attention``); a missing or unreadable plan needs one too."""
        try:
            return attention(json.loads((root / "plan.json").read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return ["plan.missing"]

    def affected(self, root: Path, since: str, repository: str, ref: str, env: Optional[Mapping[str, str]] = None):
        """brain-runner's ``affected`` over GitHub's API (0.3.2): ``{head, mappings}`` (mappings None: every one), or a
        code when it cannot say (then the caller plans every mapping)."""
        done = self._cli("affected", "--config", str(root / PACKAGE_FILE), "--since", since, "--source", "github",
                         "--repo", repository, "--ref", ref or "HEAD", "--json", env=env)
        try:
            out = json.loads((done.stdout or "").strip().splitlines()[-1]) if done.returncode == 0 else None
        except (ValueError, IndexError):
            out = None
        if not isinstance(out, dict) or not COMMIT.match(str(out.get("head") or "")):
            return f"runner.affected_exit_{done.returncode}"
        ids = out.get("mappings")
        return {"head": out["head"], "mappings": None if ids is None else [str(m) for m in ids]}

    # -------------------------------------------------------- the acknowledged stream (link mode)

    @staticmethod
    def _planned_stream(plan: Path) -> Optional[dict]:
        try:
            stream = (json.loads(plan.read_text(encoding="utf-8")).get("source") or {}).get("stream")
        except (OSError, ValueError, AttributeError):
            return None
        return stream if isinstance(stream, dict) and isinstance(stream.get("documents"), dict) else None

    @staticmethod
    def _acknowledge(root: Path, stream: dict) -> None:
        """Keep the stream an apply succeeded with: the next plan's ``previous``."""
        tmp = root / (ACKNOWLEDGED_FILE + ".tmp")
        tmp.write_text(json.dumps(stream), encoding="utf-8")
        os.replace(tmp, root / ACKNOWLEDGED_FILE)

    @staticmethod
    def acknowledged(root: Path) -> Optional[dict]:
        try:
            return json.loads((root / ACKNOWLEDGED_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    # -------------------------------------------------------- write jobs (MVP-22)

    def compile_write(self, root: Path, files: Mapping[str, str], job: Mapping[str, Any]) -> tuple[str, Any]:
        """brain-runner's compile-write for one write job, over the source's package in a folder of its own:
        ("ok", change), ("refused", code) or ("failed", code)."""
        with tempfile.TemporaryDirectory(dir=root, prefix="write-") as folder:
            work = Path(folder)
            self.write_package(work, files)
            job_file = work / "write-job.json"
            job_file.write_text(json.dumps(job), encoding="utf-8")
            done = self._cli("compile-write", "--config", str(work / PACKAGE_FILE), "--job", str(job_file))
        if done.returncode in (0, 3):
            try:
                out = json.loads(done.stdout)
            except ValueError:
                out = None
            if done.returncode == 0 and isinstance(out, dict) and "edits" in out:
                return "ok", out
            if done.returncode == 3 and isinstance(out, dict) and _CODE.match(str(out.get("refused") or "")):
                return "refused", out["refused"]
        return "failed", f"runner.compile_exit_{done.returncode}"


def _write_json(path: Path, data: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)
