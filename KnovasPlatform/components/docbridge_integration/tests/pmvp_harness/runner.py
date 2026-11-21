"""A whole run: every question asked once, in order, and the numbers the plan measures.

``ask(question)`` is the caller's: it returns the assistant's reply for that question (asked as
``question.asked_as`` when that is set) or raises. A question that raises becomes an error row carrying the
exception's class name and nothing else, and the run goes on. So does a question whose reply is not a reply
(None, a dict, a missing or wrong part): its row carries ``NotAReply`` and no score, and it is not ok.

Log lines carry ids, counts and class names only.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional

from pmvp_harness.questions import KINDS, Question
from pmvp_harness.scoring import STATUSES, Score, score

logger = logging.getLogger(__name__)

#: Plan section 11: at least this many of the measured set are ok. Printed beside a result, never enforced.
MVP_TARGET_OK = 24
MVP_SET_SIZE = 30


@dataclass(frozen=True)
class Row:
    question: Question
    reply: Any = None
    score: Optional[Score] = None
    #: The class name of what ``ask`` or the scoring raised; never the message.
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.score is not None and self.score.ok


def status_label(status: str) -> str:
    """A status as a report may print it: one of the contract's, or "other"."""
    return status if status in STATUSES else "other"


class Report:
    def __init__(self, rows: Iterable[Row], *, pointer_prefix: str = ""):
        self.rows: List[Row] = list(rows)
        #: The run's, so the judging sheet marks the same passages the scoring did.
        self.pointer_prefix = pointer_prefix

    def summary(self) -> Dict[str, Any]:
        """Numbers only. ``leaked`` counts documents, ``leaking_questions`` the questions they showed up in.

        ``unknown_pointers`` of ``passages``: passages whose document the truth files do not know. When that is
        every passage, the pointer prefix is probably wrong and no leak could have been seen.
        """
        scored = [row.score for row in self.rows if row.score is not None]
        by_kind = {kind: {"questions": 0, "ok": 0} for kind in KINDS}
        for row in self.rows:
            entry = by_kind.setdefault(row.question.kind, {"questions": 0, "ok": 0})
            entry["questions"] += 1
            entry["ok"] += int(row.ok)
        by_status = {status: 0 for status in STATUSES}
        for item in scored:
            label = status_label(item.status)
            by_status[label] = by_status.get(label, 0) + 1
        error_classes: Dict[str, int] = {}
        for row in self.rows:
            if row.error:
                error_classes[row.error] = error_classes.get(row.error, 0) + 1
        measured = [row for row in self.rows if row.question.in_mvp_set]
        return {
            "questions": len(self.rows),
            "ok": sum(int(row.ok) for row in self.rows),
            "errors": sum(1 for row in self.rows if row.error),
            "error_classes": dict(sorted(error_classes.items())),
            "refs_shown": sum(item.refs_shown for item in scored),
            "refs_matching": sum(item.refs_matching for item in scored),
            "unreferenced_shown": sum(item.unreferenced_shown for item in scored),
            "leaked": sum(len(item.leaked_pointers) for item in scored),
            "leaking_questions": sum(1 for item in scored if item.leaked_pointers),
            "removed": sum(item.removed for item in scored),
            "passages": sum(item.passages for item in scored),
            "unknown_pointers": sum(item.unknown_pointers for item in scored),
            "by_status": by_status,
            "by_kind": by_kind,
            "mvp_set": {"questions": len(measured), "ok": sum(int(row.ok) for row in measured)},
        }


def run(questions: Iterable[Question], ask: Callable[[Question], Any], *, rights=None,
        pointer_prefix: str = "") -> Report:
    questions = list(questions)
    known = frozenset(pointer for question in questions for pointer in question.supporting_pointers)
    rows: List[Row] = []
    for position, question in enumerate(questions, start=1):
        try:
            reply = ask(question)
            marks = score(question, reply, rights=rights, pointer_prefix=pointer_prefix, known=known)
        except Exception as exc:  # noqa: BLE001 - one broken question must not end the run
            # No traceback: its last line is the exception's message.
            logger.warning("pmvp harness: question %d of %d id=%s error=%s", position, len(questions),
                           question.id, type(exc).__name__)
            rows.append(Row(question=question, error=type(exc).__name__))
            continue
        logger.info("pmvp harness: question %d of %d id=%s kind=%s status=%s ok=%s refs=%d/%d leaked=%d",
                    position, len(questions), question.id, question.kind, status_label(marks.status), marks.ok,
                    marks.refs_matching, marks.refs_shown, len(marks.leaked_pointers))
        rows.append(Row(question=question, reply=reply, score=marks))
    return Report(rows, pointer_prefix=pointer_prefix)
