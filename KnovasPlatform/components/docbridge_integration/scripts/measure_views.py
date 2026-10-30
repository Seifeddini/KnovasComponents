"""Render the Ansichten against a running Platform and print what they cost.

    py -3 scripts/measure_views.py --base http://127.0.0.1:5077 [--process PROC-feature-lifecycle]

Read-only: GET requests only. Prints wall-clock time per view (cold = after a
refresh that bypasses the per-subject cache, warm = straight after), the
server's own timing and call count, and checks the board's missing items
against KnowledgeBase's process-completeness.json (--report: the file the
tenant's definitions bundle names, e.g. a KnowledgeBase checkout's
docs/ModernDocs/client-zero/ontology/generated/process-completeness.json). Used for the times recorded in each PR of
docs/superpowers/plans/2026-10-27-process-and-spine-views.md.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

def get(base: str, path: str, **params):
    url = f"{base}{path}?{urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, '')})}"
    started = time.perf_counter()
    with urllib.request.urlopen(url, timeout=300) as res:
        body = json.loads(res.read())
    return body, time.perf_counter() - started


def line(label: str, seconds: float, body: dict, budget: float | None = None) -> None:
    verdict = "" if budget is None else (" within budget" if seconds <= budget else f" OVER BUDGET ({budget:g} s)")
    print(f"{label:<44} {seconds * 1000:7.0f} ms  server={body.get('timings_ms')}{verdict}")


def check_board(board: dict, report_path: Path, process: str) -> int:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    rows = {r["instance"]: r for r in report["instances"] if r["process"] == process}
    cards = {c["key"]: c for col in board["columns"] for c in col["cards"]}
    final = {c["id"] for c in board["columns"] if c.get("resting")}
    mismatches = 0
    for key, row in sorted(rows.items()):
        card = cards.get(key)
        if card is None:
            print(f"  {key}: in the report, not on the board"); mismatches += 1; continue
        want = sorted((m["type"], m["path"], m["found"]) for m in row["missing"])
        got = sorted((m["type"], m["path"], m["found"]) for m in card["missing_items"])
        if card["step"] != row["step"] or want != got:
            mismatches += 1
            print(f"  {key}: report step={row['step']} missing={want}\n  {' ' * len(key)}  board  step={card['step']} missing={got}")
    extra = [k for k, c in cards.items() if k not in rows and c["step"] not in final]
    for key in extra:
        print(f"  {key}: open on the board, not in the report"); mismatches += 1
    print(f"board vs report ({report['generated_at']}): {len(rows)} instances, {mismatches} mismatches")
    return mismatches


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="http://127.0.0.1:5077")
    ap.add_argument("--process", default="PROC-feature-lifecycle")
    ap.add_argument("--report", type=Path, required=True)
    args = ap.parse_args(argv)
    b = args.base

    board, s = get(b, "/api/views/board", process=args.process, refresh=1)
    line("BOARD cold (refresh)", s, board, 5)
    board, s = get(b, "/api/views/board", process=args.process)
    line("BOARD warm", s, board, 5)
    cards = sorted((c for col in board["columns"] for c in col["cards"]), key=lambda c: -c["missing"])
    for card in cards[:1] + [c for c in cards if c["step"] == "implementing"][:1]:
        trace, s = get(b, "/api/views/trace", process=args.process, instance=card["id"], refresh=1)
        line(f"TRACE cold {card['key'][:30]}", s, trace, 2)
        trace, s = get(b, "/api/views/trace", process=args.process, instance=card["id"])
        line(f"TRACE warm {card['key'][:30]}", s, trace, 2)
    return 1 if check_board(board, args.report, args.process) else 0


if __name__ == "__main__":
    sys.exit(main())
