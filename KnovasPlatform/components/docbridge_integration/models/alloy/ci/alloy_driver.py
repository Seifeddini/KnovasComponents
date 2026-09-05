#!/usr/bin/env python3
"""Headless Alloy runner with lockfile discipline.

Copied in spirit from KnowledgeBase/knovas-software/models/alloy/ci/alloy_driver.py
(that checkout is not in this repository). Rules:

- a check under mutants/ must find a counterexample
- every non-mutant file that has checks must also carry a run witness
- observed outcomes must match ci/expected_results.json byte-for-byte after
  canonical JSON encoding

Usage (from models/alloy):

    python3 ci/alloy_driver.py
    python3 ci/alloy_driver.py --emit-expected > ci/expected_results.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / "ci"
CACHE = ROOT / ".cache"
OUT = ROOT / ".out"
VERSION = CI / "alloy.version"
EXPECTED = CI / "expected_results.json"
JAR = CACHE / "alloy.jar"

FILE_PREFIX = "models/alloy/"

_LINE = re.compile(
    r"^\s*\d+\.\s+(check|run)\s+(\S+).*?\b(SAT|UNSAT)\s*$",
    re.IGNORECASE,
)


def _clean(text: str) -> str:
    out: list[str] = []
    for ch in text:
        if ch == "\b":
            if out:
                out.pop()
        elif ch not in "\r":
            out.append(ch)
    return "".join(out)


def _ensure_jar() -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    if JAR.is_file():
        return JAR
    url = VERSION.read_text(encoding="utf-8").splitlines()[1].strip()
    subprocess.run(["curl", "-fsSL", "-o", str(JAR), url], check=True)
    return JAR


def _ensure_module_links() -> None:
    """Make `module knovas_platform/node_grants` resolve to node_grants.als."""
    link = ROOT / "knovas_platform"
    if not link.exists() and not link.is_symlink():
        link.symlink_to(".")
    mutants_link = ROOT / "mutants" / "knovas_platform"
    if not mutants_link.exists() and not mutants_link.is_symlink():
        mutants_link.symlink_to("..")


def _iter_als() -> list[Path]:
    files = []
    for path in sorted(ROOT.rglob("*.als")):
        parts = path.relative_to(ROOT).parts
        if any(p in {".cache", ".out", "knovas_platform"} for p in parts):
            continue
        files.append(path)
    return files


def _file_key(path: Path) -> str:
    return FILE_PREFIX + path.relative_to(ROOT).as_posix()


def _exec(als: Path) -> list[dict]:
    out_dir = OUT / als.relative_to(ROOT).with_suffix("")
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "java", "-jar", str(_ensure_jar()),
        "exec", "-c", "*", "-t", "none", "-f",
        "-o", str(out_dir),
        str(als),
    ]
    proc = subprocess.run(
        cmd, cwd=str(ROOT), capture_output=True, text=True, check=False
    )
    text = _clean(proc.stdout + "\n" + proc.stderr)
    rows = []
    for line in text.splitlines():
        match = _LINE.match(line.strip())
        if not match:
            continue
        kind, name, sat = match.group(1).lower(), match.group(2), match.group(3).upper()
        if kind == "check":
            outcome = "counterexample" if sat == "SAT" else "no_counterexample"
        else:
            outcome = "satisfiable" if sat == "SAT" else "unsatisfiable"
        rows.append({
            "file": _file_key(als),
            "command": name,
            "kind": kind,
            "outcome": outcome,
        })
    if not rows:
        sys.stderr.write(f"no commands parsed from {als}:\n{text}\n")
        sys.exit(2)
    if proc.returncode not in (0, None):
        # Alloy still prints SAT/UNSAT on success; a hard failure has no rows.
        pass
    return rows


def _payload(rows: list[dict]) -> dict:
    checks = [
        {"file": r["file"], "command": r["command"], "outcome": r["outcome"]}
        for r in rows if r["kind"] == "check"
    ]
    runs = [
        {"file": r["file"], "command": r["command"], "outcome": r["outcome"]}
        for r in rows if r["kind"] == "run"
    ]
    checks.sort(key=lambda r: (r["file"], r["command"]))
    runs.sort(key=lambda r: (r["file"], r["command"]))
    return {"checks": checks, "runs": runs}


def _dumps(payload: dict) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _validate(rows: list[dict]) -> list[str]:
    problems = []
    by_file: dict[str, list[dict]] = {}
    for row in rows:
        by_file.setdefault(row["file"], []).append(row)
    for file, items in by_file.items():
        mutant = "/mutants/" in file
        kinds = {i["kind"] for i in items}
        if not mutant and "check" in kinds and "run" not in kinds:
            problems.append(f"{file}: file with checks has no run witness")
        for item in items:
            if mutant and item["kind"] == "check" and item["outcome"] != "counterexample":
                problems.append(
                    f"{file}::{item['command']}: mutant check must refute, got {item['outcome']}"
                )
            if not mutant and item["kind"] == "check" and item["outcome"] != "no_counterexample":
                problems.append(
                    f"{file}::{item['command']}: check must hold, got {item['outcome']}"
                )
            if item["kind"] == "run" and item["outcome"] != "satisfiable":
                problems.append(
                    f"{file}::{item['command']}: run must be satisfiable, got {item['outcome']}"
                )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--emit-expected", action="store_true")
    args = parser.parse_args()

    _ensure_module_links()
    rows: list[dict] = []
    for als in _iter_als():
        rows.extend(_exec(als))
    payload = _payload(rows)
    encoded = _dumps(payload)

    if args.emit_expected:
        sys.stdout.write(encoded)
        return 0

    problems = _validate(rows)
    if EXPECTED.is_file():
        expected = EXPECTED.read_text(encoding="utf-8")
        if expected != encoded:
            problems.append("lockfile mismatch: regenerate with --emit-expected")
    else:
        problems.append(f"missing lockfile {EXPECTED}")

    if problems:
        sys.stderr.write("\n".join(problems) + "\n")
        return 1
    print("alloy-checks: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
