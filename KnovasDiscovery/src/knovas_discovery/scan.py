"""scan: run the probes the intake points to and write findings.json (shareable) and detail.json (stays on site)."""

from __future__ import annotations

import datetime as _dt
import sys
import time
from pathlib import Path

from . import __version__
from .catalogue import load_catalogue
from .intake import shareable_client
from .probes import SECTIONS, TARGETS, Context, Output, directory, environment, fileshare, m365, mssql
from .secrets import Secrets
from .util import error_code, result, write_json

PROBES = {"fileshare": fileshare, "directory": directory, "mssql": mssql, "m365": m365}
ORDER = ("environment", "fileshare", "directory", "mssql", "m365")


def parse_only(value: str | None) -> list[str]:
    if not value:
        return list(ORDER)
    names = [v.strip() for v in value.split(",") if v.strip()]
    unknown = [n for n in names if n not in ORDER]
    if unknown:
        raise ValueError(f"unknown probe(s): {', '.join(unknown)}; known: {', '.join(ORDER)}")
    return names


def run_scan(intake: dict, out_dir: Path, only: list[str] | None = None, secrets: Secrets | None = None,
             now: _dt.datetime | None = None, ctx_overrides: dict | None = None,
             log=None) -> tuple[dict, dict]:
    """Run the selected probes and write findings.json and detail.json into out_dir. Returns both documents."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    only = only or list(ORDER)
    secrets = secrets or Secrets()
    now = now or _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)
    if log is None:
        def log(message: str) -> None:
            print(secrets.scrub(message), file=sys.stderr, flush=True)
    ctx = Context(now=now, secrets=secrets, catalogue=load_catalogue(), knovas=intake.get("knovas") or {},
                  out_dir=out_dir, log=log)
    for k, v in (ctx_overrides or {}).items():
        setattr(ctx, k, v)

    started = time.monotonic()
    findings: dict = {
        "tool_version": __version__,
        "scanned_at": now.isoformat().replace("+00:00", "Z"),
        "client": shareable_client(intake),
        "software": intake.get("software", []),
        "probes": {"environment": None, "fileshares": [], "directories": [], "databases": [], "m365": []},
    }
    detail: dict = {"note": "Names for the consultant on site. This file stays at the client.",
                    "scanned_at": findings["scanned_at"], "probes": {}}

    def add(probe: str, outputs: list[Output]) -> None:
        section = SECTIONS[probe]
        for o in outputs:
            if probe == "environment":
                findings["probes"]["environment"] = o.result
            else:
                findings["probes"][section].append(o.result)
            if o.detail:
                detail["probes"][f"{probe}/{o.result['key']}"] = o.detail

    # environment
    if "environment" in only:
        try:
            add("environment", environment.run(ctx))
        except Exception as exc:  # a failing probe never stops the scan
            add("environment", [Output(result("environment", "failed", error_code(exc)))])
    else:
        add("environment", [Output(result("environment", "skipped", "not_selected"))])

    for probe in ORDER[1:]:
        targets = intake["targets"].get(TARGETS[probe]) or []
        for target in targets:
            if probe not in only:
                add(probe, [Output(result(target["key"], "skipped", "not_selected"))])
                continue
            log(f"{probe} {target['key']}: started")
            try:
                add(probe, PROBES[probe].run(target, ctx))
            except Exception as exc:
                add(probe, [Output(result(target["key"], "failed", error_code(exc)),
                                   {"message": secrets.scrub(f"{type(exc).__name__}: {exc}")})])

    findings["scan_seconds"] = round(time.monotonic() - started, 1)
    write_json(out_dir / "findings.json", findings)
    write_json(out_dir / "detail.json", detail)
    return findings, detail
