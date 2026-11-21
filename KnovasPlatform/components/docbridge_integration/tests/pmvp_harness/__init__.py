"""The Platform MVP's acceptance harness (PMVP-03, FEAT-2026-11-19-platform-mvp section 11).

Reads the demo law firm plan's truth files, asks the test questions through a function the caller hands in, and
scores the replies against the plan's measures. Test tooling: it lives in the tests folder and never reaches an
image (the image copies ``src/`` and ``config/`` only).

No customer text leaves through a log line, an exception message or ``report.json``; ``judging.md`` is the one
file that holds it. How to run it: ``README.md`` beside this file.
"""
from pmvp_harness.questions import KINDS, Question, load_questions
from pmvp_harness.replies import MissingReply, load_replies, reply_record
from pmvp_harness.report import write_report
from pmvp_harness.rights import Rights, load_rights
from pmvp_harness.runner import MVP_SET_SIZE, MVP_TARGET_OK, Report, Row, run
from pmvp_harness.scoring import STATUSES, NotAReply, Score, score

__all__ = [
    "KINDS", "MVP_SET_SIZE", "MVP_TARGET_OK", "MissingReply", "NotAReply", "Question", "Report", "Rights", "Row",
    "STATUSES", "Score", "load_questions", "load_replies", "load_rights", "reply_record", "run", "score",
    "write_report",
]
