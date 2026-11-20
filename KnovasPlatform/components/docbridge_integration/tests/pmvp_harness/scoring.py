"""The automatic marks of one reply, and the first-pass verdict ``ok``.

A reply is whatever the assistant returns: ``.status``, ``.sentences`` (each ``.text`` and ``.refs``),
``.passages`` (each ``.number`` and ``.pointer``), ``.removed``, ``.message``. Nothing here imports the assistant.

``ok`` is a first pass; people judge whether an answer is right (plan section 11):

- fact, document, process: answered, and at least one referenced passage is one of the question's supporting
  documents;
- negative: the reply is the no-answer reply;
- rights: none of the question's supporting documents is among the passages, cited or not;
- and in every kind: no shown reference without a passage, no shown sentence without a reference, and no passage
  the rights table forbids to the person the question is asked as.

``points_found`` is a hint for the judges and never part of the verdict.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import List, Optional, Tuple

from pmvp_harness.pointers import normalise

#: The statuses the assistant's contract knows (PMVP-20). Any other value is reported as "other".
STATUSES = ("answered", "no_answer", "busy", "failed")
ANSWERING_KINDS = ("fact", "document", "process")


@dataclass(frozen=True)
class Score:
    ok: bool
    status: str
    sentences_shown: int
    refs_shown: int
    refs_matching: int
    unreferenced_shown: int
    #: None when the question names no supporting document (a negative question).
    source_hit: Optional[bool]
    points_found: Tuple[bool, ...]
    #: Documents that must not have reached this person, in the truth files' spelling, each once.
    leaked_pointers: Tuple[str, ...]
    removed: int
    passages: int


def _plain(text: object) -> str:
    """Lower case, one space between words: how an expected point is looked up in an answer."""
    return " ".join(unicodedata.normalize("NFKC", str(text or "")).casefold().split())


def sentences_of(reply) -> list:
    return list(getattr(reply, "sentences", None) or ())


def passages_of(reply) -> list:
    return list(getattr(reply, "passages", None) or ())


def refs_of(sentence) -> tuple:
    return tuple(getattr(sentence, "refs", None) or ())


def score(question, reply, *, rights=None, pointer_prefix: str = "") -> Score:
    status = str(getattr(reply, "status", "") or "")
    sentences = sentences_of(reply)
    passages = passages_of(reply)
    pointers = [normalise(getattr(passage, "pointer", ""), pointer_prefix) for passage in passages]
    pointer_of = {}
    for passage, pointer in zip(passages, pointers):
        pointer_of.setdefault(getattr(passage, "number", None), pointer)
    supporting = {normalise(pointer) for pointer in question.supporting_pointers}

    refs_shown = refs_matching = unreferenced = 0
    cited: List[str] = []
    for sentence in sentences:
        refs = refs_of(sentence)
        if not refs:
            unreferenced += 1
        for ref in refs:
            refs_shown += 1
            if ref in pointer_of:
                refs_matching += 1
                cited.append(pointer_of[ref])
    source_hit = any(pointer in supporting for pointer in cited) if supporting else None

    answer = _plain(" ".join(str(getattr(s, "text", "") or "") for s in sentences))
    points_found = tuple(bool(_plain(point)) and _plain(point) in answer for point in question.expected_points)

    # Every passage counts, cited or not: the closest passages are on the page beside a no-answer reply too.
    leaked: List[str] = []
    for pointer in pointers:
        forbidden = question.kind == "rights" and pointer in supporting
        if not forbidden and rights is not None and question.asked_as:
            forbidden = rights.may_read(question.asked_as, pointer) is False
        if forbidden and pointer not in leaked:
            leaked.append(pointer)

    ok = refs_shown == refs_matching and unreferenced == 0 and not leaked
    if question.kind in ANSWERING_KINDS:
        ok = ok and status == "answered" and source_hit is True
    elif question.kind == "negative":
        ok = ok and status == "no_answer" and not sentences
    return Score(
        ok=ok, status=status, sentences_shown=len(sentences), refs_shown=refs_shown, refs_matching=refs_matching,
        unreferenced_shown=unreferenced, source_hit=source_hit, points_found=points_found,
        leaked_pointers=tuple(leaked), removed=int(getattr(reply, "removed", 0) or 0), passages=len(passages),
    )
