"""The mapping runner as this component's engine (DEC-0040): plan on a dry run, apply exactly the plan a dry run made.

One working folder per source: the configuration package the Platform compiled, the fetched data, the plan and the
runner's state. An apply takes the plan on disk, so what is written is what the dry run showed; without one it refuses.
Only counts leave this module: the runner's plan names keys and values and stays on site.
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,120}$")
PACKAGE_FILE = "brain-config.yaml"


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
    def __init__(self, folder: Path, env: Mapping[str, str], *, run: Optional[Callable[..., Any]] = None):
        self.folder = Path(folder)
        self.env = dict(env)
        self._run = run or subprocess.run

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
        return result
