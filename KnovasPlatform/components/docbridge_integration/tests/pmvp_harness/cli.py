"""The command line: score replies recorded on any instance against a questions file.

    py -3 -m pmvp_harness score --questions <file> --replies <file> [--rights <file>]
                                [--pointer-prefix <text>] [--mvp-set] --out <folder>

Run from ``KnovasPlatform/components/docbridge_integration/tests``. Prints the summary (numbers only) and writes
``report.json`` and ``judging.md`` into ``--out``. Exit code 0 when it ran, whatever the score: the plan's target is
printed beside the result and not enforced.

Exit code 2 when nothing could be measured, with a message that names a file's role, a line and a reason, never
content: a file that cannot be used, no question to ask, an ``--out`` inside a git work tree, or a run in which
every passage names a document the truth files do not know (the pointer prefix is then probably wrong, and a leak
would not have been seen).
"""
from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from pmvp_harness.files import inside_a_work_tree
from pmvp_harness.questions import KINDS, load_questions
from pmvp_harness.replies import MissingReply, load_replies
from pmvp_harness.report import write_report
from pmvp_harness.rights import load_rights
from pmvp_harness.runner import MVP_SET_SIZE, MVP_TARGET_OK, run
from pmvp_harness.scoring import STATUSES


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pmvp_harness", description="The Platform MVP's acceptance harness.")
    commands = parser.add_subparsers(dest="command", required=True)
    score = commands.add_parser("score", help="score recorded replies against a questions file")
    score.add_argument("--questions", required=True, help="questions.jsonl of the demo plan's truth files")
    score.add_argument("--replies", required=True, help="JSON lines, one recorded reply per question id")
    score.add_argument("--rights", help="rights.csv of the truth files: also catches a forbidden passage")
    score.add_argument("--pointer-prefix", default="", help="what the instance puts before the truth files' pointers")
    score.add_argument("--mvp-set", action="store_true", help="only the set the plan measures on (in_mvp_set)")
    score.add_argument("--out", required=True, help="folder for report.json and judging.md, outside any git work tree")
    return parser


def _load(what: str, loader, path, **options):
    """``loader(path)``; a file that is not there is reported by its role and the error's class."""
    try:
        return loader(path, **options)
    except OSError as exc:
        raise ValueError(f"{what}: cannot be read ({type(exc).__name__})") from None


def _print_summary(summary: dict, *, unused: int, out) -> None:
    def line(label: str, value: object) -> None:
        print(f"  {label:<34}{value}", file=out)

    classes = ", ".join(f"{name} {count}" for name, count in summary["error_classes"].items())
    measured = summary["mvp_set"]
    line("questions", summary["questions"])
    line("ok (automatic first pass)", summary["ok"])
    line("no reply (errors)", f"{summary['errors']}" + (f"   {classes}" if classes else ""))
    shown = [*STATUSES, "other"] if summary["by_status"].get("other") else list(STATUSES)
    line("statuses", ", ".join(f"{name} {summary['by_status'].get(name, 0)}" for name in shown))
    line("references shown / matching", f"{summary['refs_shown']} / {summary['refs_matching']}")
    line("sentences without a reference", summary["unreferenced_shown"])
    line("leaked documents", f"{summary['leaked']}   in {summary['leaking_questions']} question(s)")
    line("passages not in the truth files", f"{summary['unknown_pointers']} of {summary['passages']}")
    line("sentences removed by the check", summary["removed"])
    line("by kind (ok/questions)", ", ".join(
        f"{kind} {summary['by_kind'][kind]['ok']}/{summary['by_kind'][kind]['questions']}" for kind in KINDS))
    line("measured set", f"{measured['ok']} of {measured['questions']} ok   "
                         f"(plan target: at least {MVP_TARGET_OK} of {MVP_SET_SIZE}; not enforced)")
    line("replies not used", unused)


def _score(args, out) -> int:
    if inside_a_work_tree(args.out):
        raise ValueError("--out is inside a git work tree; judging.md holds customer text and stays outside "
                         "every repository")
    questions = _load("questions file", load_questions, args.questions, only_mvp_set=args.mvp_set)
    if not questions:
        raise ValueError("questions file: no question" + (" with in_mvp_set" if args.mvp_set else "")
                         + "; nothing was measured")
    replies = _load("replies file", load_replies, args.replies)
    rights = _load("rights file", load_rights, args.rights) if args.rights else None

    def recorded(question):
        if question.id not in replies:
            raise MissingReply(question.id)
        return replies[question.id]

    report = run(questions, recorded, rights=rights, pointer_prefix=args.pointer_prefix)
    summary = report.summary()
    if summary["passages"] and summary["unknown_pointers"] == summary["passages"]:
        # Nothing matched: neither a supporting document nor a forbidden one. A score of this run would say
        # "no leak" about passages it could not recognise.
        raise ValueError(f"all {summary['passages']} passages of the replies name documents the truth files do not "
                         "know; the pointer prefix is probably wrong (--pointer-prefix). Nothing was measured")
    asked = {question.id for question in questions}
    try:
        report_path, sheet_path = write_report(report, args.out)
    except OSError as exc:
        raise ValueError(f"--out: cannot be written ({type(exc).__name__})") from None
    print("pmvp_harness score", file=out)
    _print_summary(summary, unused=sum(1 for key in replies if key not in asked), out=out)
    print(f"  written: {report_path.name} (no customer text) and {sheet_path.name} (customer text, for the judges)",
          file=out)
    return 0


def main(argv: Optional[List[str]] = None, *, out=None, err=None) -> int:
    out, err = out or sys.stdout, err or sys.stderr
    args = _parser().parse_args(argv)
    try:
        return _score(args, out)
    except ValueError as exc:
        # The harness's own messages only: a role, a line number and a reason.
        print(f"pmvp_harness: {exc}", file=err)
        return 2
