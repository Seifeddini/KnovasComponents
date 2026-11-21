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

**A question counts as ok only when it was really tested** (coordinator and verifier, 2026-11-20):

- ``ok`` can be true only for the status ``answered`` or ``no_answer``, exactly. ``busy``, ``failed`` and every
  other value show nothing, so nothing was tested. An ``answered`` reply shows at least one sentence and a
  ``no_answer`` reply none; a reply that contradicts its own status is not ok either.
- Anything that is not a reply raises ``NotAReply``: None, a dict, a missing ``status``, ``sentences`` or
  ``passages``, a part of the wrong type, two passages with one number. ``run`` makes an error row of it. The
  message says which part is wrong and never what it held.
- A passage without a pointer cannot be checked against anything. Where rights are checked (a rights question,
  or a rights table and a person asked as) such a reply is not ok.
- With a rights table, a rights question whose reply carries a passage the table does not know is not ok
  (coordinator, D6, 2026-11-21): it cannot be judged, and a rights question must never pass unjudged. The table
  decides, not the question set: a document that only a question names is unknown to the table and fails it too.

Pointers are compared by ``pointers.normalise``, so a leak is counted however the pointer is spelled.
``unknown_pointers`` counts the passages whose document neither the rights table nor any question of the set
names: when that is every passage of a run, the pointer prefix is probably wrong and no leak could be seen. For
the kinds other than rights an unknown passage is counted and does not change ``ok``: the pilot's tenant holds
documents the truth files do not list.

``points_found`` is a hint for the judges and never part of the verdict.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from pmvp_harness.pointers import normalise

#: The statuses the assistant's contract knows (PMVP-20). Any other value is reported as "other".
STATUSES = ("answered", "no_answer", "busy", "failed")
#: The only statuses a question can be ok with: something was shown, or the search was made and found nothing.
TESTED = ("answered", "no_answer")
ANSWERING_KINDS = ("fact", "document", "process")


class NotAReply(TypeError):
    """What was handed in as a reply is not one. The message names the part, never its content."""


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
    #: Passages whose document neither the rights table nor any question of the set names.
    unknown_pointers: int = 0


def _plain(text: object) -> str:
    """Lower case, one space between words: how an expected point is looked up in an answer."""
    return " ".join(unicodedata.normalize("NFKC", str(text or "")).casefold().split())


def _is_number(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def sentences_of(reply) -> list:
    return list(getattr(reply, "sentences", None) or ())


def passages_of(reply) -> list:
    return list(getattr(reply, "passages", None) or ())


def refs_of(sentence) -> tuple:
    return tuple(getattr(sentence, "refs", None) or ())


def checked(reply) -> Tuple[str, list, list, int]:
    """``reply`` as status, sentences, passages and removed; ``NotAReply`` when it is not a reply."""
    status = getattr(reply, "status", None)
    if not isinstance(status, str):
        raise NotAReply("not a reply: 'status' is missing or not a text")
    sentences = getattr(reply, "sentences", None)
    if not isinstance(sentences, (list, tuple)):
        raise NotAReply("not a reply: 'sentences' is missing or not a list")
    passages = getattr(reply, "passages", None)
    if not isinstance(passages, (list, tuple)):
        raise NotAReply("not a reply: 'passages' is missing or not a list")
    for sentence in sentences:
        refs = getattr(sentence, "refs", None)
        if not isinstance(getattr(sentence, "text", None), str) or not isinstance(refs, (list, tuple)) \
                or not all(_is_number(ref) for ref in refs):
            raise NotAReply("not a reply: a sentence needs a text and a list of numbers as 'refs'")
    numbers = []
    for passage in passages:
        number, pointer = getattr(passage, "number", None), getattr(passage, "pointer", None)
        if not _is_number(number):
            raise NotAReply("not a reply: a passage has no number")
        if pointer is not None and not isinstance(pointer, str):
            raise NotAReply("not a reply: a passage's pointer is not a text")
        numbers.append(number)
    if len(set(numbers)) != len(numbers):
        raise NotAReply("not a reply: two passages have one number")
    removed = getattr(reply, "removed", 0)
    removed = 0 if removed is None else removed
    if not _is_number(removed) or removed < 0:
        raise NotAReply("not a reply: 'removed' is not a count")
    return status, list(sentences), list(passages), removed


def score(question, reply, *, rights=None, pointer_prefix: str = "",
          known: Optional[Iterable[str]] = None) -> Score:
    """The marks of ``reply`` to ``question``.

    ``known`` are the supporting pointers of the whole question set (``run`` hands them in); without it only this
    question's own count as known, beside the rights table.
    """
    status, sentences, passages, removed = checked(reply)
    keys = [normalise(getattr(passage, "pointer", None), pointer_prefix) for passage in passages]
    key_of = {passage.number: key for passage, key in zip(passages, keys)}
    supporting: Dict[str, str] = {}
    for pointer in question.supporting_pointers:
        if normalise(pointer):
            supporting.setdefault(normalise(pointer), pointer)
    known_keys = set(supporting) | {normalise(pointer) for pointer in known or ()}

    refs_shown = refs_matching = unreferenced = 0
    cited: List[str] = []
    for sentence in sentences:
        if not sentence.refs:
            unreferenced += 1
        for ref in sentence.refs:
            refs_shown += 1
            if ref in key_of:
                refs_matching += 1
                cited.append(key_of[ref])
    source_hit = any(key in supporting for key in cited) if supporting else None

    answer = _plain(" ".join(sentence.text for sentence in sentences))
    points_found = tuple(bool(_plain(point)) and _plain(point) in answer for point in question.expected_points)

    # Every passage counts, cited or not: the closest passages are on the page beside a no-answer reply too.
    table_applies = rights is not None and bool(question.asked_as)
    leaked: Dict[str, str] = {}
    unknown = 0
    for key in keys:
        if not key or not (key in known_keys or (rights is not None and rights.knows(key))):
            unknown += 1
        if not key:
            continue
        if question.kind == "rights" and key in supporting:
            leaked.setdefault(key, supporting[key])
        elif table_applies and rights.may_read(question.asked_as, key) is False:
            leaked.setdefault(key, rights.spelling(key) or key)
    unchecked = (question.kind == "rights" or table_applies) and not all(keys)
    # A rights question must never pass unjudged: a document the given table does not know may be the forbidden
    # one in a spelling the key does not fold. Other kinds only count it (a tenant holds more than the truth files).
    unjudged = question.kind == "rights" and rights is not None and not all(rights.knows(key) for key in keys if key)

    says_what_it_shows = bool(sentences) == (status == "answered")
    ok = status in TESTED and says_what_it_shows and refs_shown == refs_matching and unreferenced == 0 \
        and not leaked and not unchecked and not unjudged
    if question.kind in ANSWERING_KINDS:
        ok = ok and status == "answered" and source_hit is True
    elif question.kind == "negative":
        ok = ok and status == "no_answer"
    return Score(
        ok=ok, status=status, sentences_shown=len(sentences), refs_shown=refs_shown, refs_matching=refs_matching,
        unreferenced_shown=unreferenced, source_hit=source_hit, points_found=points_found,
        leaked_pointers=tuple(leaked.values()), removed=removed, passages=len(passages), unknown_pointers=unknown,
    )
