"""knovas-discovery: init, scan, report, catalogue. Read-only: it never writes to a source."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from . import intake as intake_mod
from .catalogue import check as check_catalogue
from .catalogue import load_catalogue, load_effort_rules
from .probes import all_facts
from .render import table


def _init(args) -> int:
    try:
        path = intake_mod.write_template(Path(args.folder), force=args.force)
    except intake_mod.IntakeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Wrote {path}. Fill it in from the questionnaire; it holds no password.")
    return 0


def _scan(args) -> int:
    from .scan import parse_only, run_scan

    try:
        only = parse_only(args.only)
        intake = intake_mod.load(Path(args.intake))
    except (ValueError, intake_mod.IntakeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    findings, _ = run_scan(intake, Path(args.out), only)
    bad = [r for section in ("fileshares", "directories", "databases", "m365") for r in findings["probes"][section]
           if r["status"] in ("failed", "partial")]
    env = findings["probes"]["environment"]
    if env and env["status"] == "failed":
        bad.append(env)
    print(f"Wrote {Path(args.out) / 'findings.json'} (may leave the site) and {Path(args.out) / 'detail.json'} "
          f"(stays at the client) in {findings['scan_seconds']} s.")
    for r in bad:
        print(f"  {r['key']}: {r['status']} ({r['error']})")
    return 0


def _report(args) -> int:
    from .report import make_report

    out = Path(args.out)
    if not (out / "findings.json").exists():
        print(f"error: no findings.json in {out}; run knovas-discovery scan first", file=sys.stderr)
        return 2
    try:
        report = make_report(out, Path(args.review) if args.review else None,
                             Path(args.intake) if args.intake else None)
    except (ValueError, intake_mod.IntakeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    counts = ", ".join(f"{c}: {n}" for c, n in report["counts"].items())
    print(f"Wrote report.json, report.md and connection-plan.md in {out} (sources by class: {counts}).")
    return 0


def _catalogue(args) -> int:
    catalogue, rules = load_catalogue(), load_effort_rules()
    if args.check:
        problems = check_catalogue(catalogue, rules, all_facts())
        if problems:
            for p in problems:
                print(p)
            print(f"catalogue: {len(problems)} problem(s)")
            return 1
        print(f"catalogue: {len(catalogue.get('entries') or [])} entries and {len(catalogue.get('checks') or [])} "
              f"checks, no problem")
        return 0
    rows = [[e["key"], e["name"], e["class"], (e.get("knovas") or {}).get("status")] for e in catalogue["entries"]]
    print("\n".join(table(["Key", "Name", "Class", "Knovas status"], rows)))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="knovas-discovery",
                                description="Find a client's data sources and how each can be connected to Knovas. "
                                            "Read-only; reads no content.")
    p.add_argument("--version", action="version", version=f"knovas-discovery {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init", help="write an intake.yaml template into a folder")
    s.add_argument("folder")
    s.add_argument("--force", action="store_true", help="overwrite an existing intake.yaml")
    s.set_defaults(func=_init)

    s = sub.add_parser("scan", help="run the probes; write findings.json and detail.json")
    s.add_argument("--intake", required=True, help="intake.yaml")
    s.add_argument("--out", required=True, help="output folder")
    s.add_argument("--only", help="comma-separated probes: environment,fileshare,directory,mssql,m365")
    s.set_defaults(func=_scan)

    s = sub.add_parser("report", help="classify the findings; write report.json, report.md, connection-plan.md")
    s.add_argument("--out", required=True, help="the folder with findings.json")
    s.add_argument("--review", help="review.yaml with the consultant's corrections")
    s.add_argument("--intake", help="intake.yaml (default: the software list stored in findings.json)")
    s.set_defaults(func=_report)

    s = sub.add_parser("catalogue", help="print the catalogue, or check it")
    s.add_argument("--check", action="store_true", help="validate sources.yaml and effort.yaml")
    s.set_defaults(func=_catalogue)
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    return args.func(args)
