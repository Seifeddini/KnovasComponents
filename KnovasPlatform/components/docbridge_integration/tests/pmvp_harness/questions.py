"""The test questions: ``questions.jsonl`` as the demo law firm plan writes it.

One JSON record per line with ``id``, ``question``, ``kind``, ``expected_points``, ``supporting_pointers``,
``asked_as`` and ``in_mvp_set`` (FEAT-2026-11-19-demo-law-firm, ``demo_kanzlei/truth.py``: ``_Questions.add``).
Fields this harness does not know are ignored, so the generator may add some.

A file that cannot be read is refused with the line's number and the reason. The message never quotes the line:
it would put a question into a log or a terminal.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, List, Tuple

from pmvp_harness.files import read_text
from pmvp_harness.pointers import normalise

#: The kinds the demo plan writes, in the order the summary lists them.
KINDS = ("fact", "document", "process", "negative", "rights")


@dataclass(frozen=True)
class Question:
    id: str
    question: str
    kind: str
    expected_points: Tuple[str, ...] = ()
    supporting_pointers: Tuple[str, ...] = ()
    asked_as: str = ""
    in_mvp_set: bool = False


def _texts(record: dict, name: str, line: int) -> Tuple[str, ...]:
    value = record.get(name, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"questions file, line {line}: '{name}' is not a list of texts")
    return tuple(value)


def _question(record: object, line: int) -> Question:
    if not isinstance(record, dict):
        raise ValueError(f"questions file, line {line}: not a JSON record")
    for name in ("id", "question", "kind"):
        value = record.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"questions file, line {line}: '{name}' is missing or empty")
    kind = record["kind"]
    if kind not in KINDS:
        raise ValueError(f"questions file, line {line}: 'kind' is not one of {', '.join(KINDS)}")
    asked_as = record.get("asked_as", "")
    if not isinstance(asked_as, str):
        raise ValueError(f"questions file, line {line}: 'asked_as' is not a text")
    in_mvp_set = record.get("in_mvp_set", False)
    if not isinstance(in_mvp_set, bool):
        raise ValueError(f"questions file, line {line}: 'in_mvp_set' is not true or false")
    pointers = _texts(record, "supporting_pointers", line)
    # "" (or only slashes and spaces) names no document; it would match a passage that has no pointer.
    if not all(normalise(pointer) for pointer in pointers):
        raise ValueError(f"questions file, line {line}: 'supporting_pointers' holds an empty pointer")
    # The generator keeps a question of these kinds only when a document holds its answer. Without one the
    # verdict would be decided before anything is asked: never ok for an answer, always ok for a rights question.
    if kind != "negative" and not pointers:
        raise ValueError(f"questions file, line {line}: a '{kind}' question needs a supporting pointer")
    if kind == "rights" and not asked_as.strip():
        raise ValueError(f"questions file, line {line}: a 'rights' question needs 'asked_as'")
    return Question(
        id=record["id"].strip(), question=record["question"], kind=kind,
        expected_points=_texts(record, "expected_points", line), supporting_pointers=pointers,
        asked_as=asked_as.strip(), in_mvp_set=in_mvp_set,
    )


def load_questions(path, *, only_mvp_set: bool = False) -> List[Question]:
    """The questions of ``path`` in file order; with ``only_mvp_set`` the set the plan measures on."""
    questions: List[Question] = []
    first_line: Dict[str, int] = {}
    text = read_text(path, "questions file")
    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except ValueError:
            # Raised from nothing: the decoder's exception holds the line it could not read.
            raise ValueError(f"questions file, line {number}: not a JSON record") from None
        question = _question(record, number)
        if question.id in first_line:
            raise ValueError(f"questions file, line {number}: repeats the id of line {first_line[question.id]}")
        first_line[question.id] = number
        questions.append(question)
    if only_mvp_set:
        questions = [q for q in questions if q.in_mvp_set]
    return questions
