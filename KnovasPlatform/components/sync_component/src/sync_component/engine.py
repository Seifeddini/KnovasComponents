"""The mapping runner as this component's engine (DEC-0040): plan on a dry run, apply exactly the plan a dry run made.

One working folder per source: the configuration package the Platform compiled, the fetched data, the plan and the
runner's state. An apply takes the plan on disk, so what is written is what the dry run showed; without one it refuses.
Only counts leave this module: the runner's plan names keys and values and stays on site.

With ``keep_stream`` (link mode, MVP-20) the engine also keeps, per source, the stream the last successful apply
acknowledged (the plan's ``source.stream``, in ``stream-acknowledged.json``) and gives it to the next plan as
``previous``, with the source's key as the stream's source (``sync_component.runner_cli``). ``compile_write`` runs
brain-runner's ``compile-write`` for a write job (MVP-22) in a folder of its own.
"""
from __future__ import annotations

import base64
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

    def dry_run(self, root: Path, mappings: list[str], reading: Any = None) -> Result:
        """A plan of the mappings over the source, kept for the apply. Writes nothing to the Brain. ``reading`` says
        where the runner reads (fetchers.Reading): by default the fetched data beside the configuration."""
        plan, state = root / "plan.json", root / "state.json"
        plan.unlink(missing_ok=True)
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
        return self.counts(json.loads(plan.read_text(encoding="utf-8")))

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

    def apply(self, root: Path, *, approve_reason: Optional[str] = None) -> Result:
        """Write what the last dry run planned; with a reason, also what the deletion guard held."""
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
