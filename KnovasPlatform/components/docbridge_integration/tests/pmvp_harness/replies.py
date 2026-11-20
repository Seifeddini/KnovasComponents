"""Replies recorded on an instance, so they can be scored where the assistant is not importable.

A JSON-lines file with one record per question id and the reply's fields::

    {"id": "q-2024-017-frist", "status": "answered",
     "sentences": [{"text": "...", "refs": [1]}],
     "passages": [{"number": 1, "pointer": "...", "page": 3}],
     "removed": 0, "message": null}

``raw_answer`` (the model's answer before the reference check) is optional. ``reply_record`` writes this shape from
a reply object; ``load_replies`` reads it back as objects ``score`` understands.

A recorded file holds customer text. Like the truth files it stays outside the repository, and a line that cannot
be read is refused with its number, never with its content.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict

from pmvp_harness.files import read_text
from pmvp_harness.scoring import passages_of, refs_of, sentences_of


class MissingReply(LookupError):
    """The replies file has no record for a question."""


def reply_record(question_id: str, reply) -> Dict[str, Any]:
    """One line of a replies file for ``reply`` (anything with the assistant's reply fields)."""
    record: Dict[str, Any] = {
        "id": question_id,
        "status": getattr(reply, "status", ""),
        "sentences": [{"text": getattr(s, "text", ""), "refs": list(refs_of(s))} for s in sentences_of(reply)],
        "passages": [],
        "removed": int(getattr(reply, "removed", 0) or 0),
        "message": getattr(reply, "message", None) or None,
    }
    for passage in passages_of(reply):
        entry = {"number": getattr(passage, "number", None), "pointer": getattr(passage, "pointer", "")}
        for name in ("page", "title"):
            value = getattr(passage, name, None)
            if value not in (None, ""):
                entry[name] = value
        record["passages"].append(entry)
    raw = getattr(reply, "raw_answer", None)
    if raw:
        record["raw_answer"] = raw
    return record


def _is_number(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _reply(record: object, line: int):
    if not isinstance(record, dict):
        raise ValueError(f"replies file, line {line}: not a JSON record")
    key = record.get("id")
    if not isinstance(key, str) or not key.strip():
        raise ValueError(f"replies file, line {line}: 'id' is missing or empty")
    status = record.get("status")
    if not isinstance(status, str) or not status:
        raise ValueError(f"replies file, line {line}: 'status' is missing or empty")
    for name in ("sentences", "passages"):
        if record.get(name) is not None and not isinstance(record[name], list):
            raise ValueError(f"replies file, line {line}: '{name}' is not a list")
    sentences = []
    for item in record.get("sentences") or []:
        refs = item.get("refs", []) if isinstance(item, dict) else None
        if refs is None or not isinstance(item.get("text"), str) or not isinstance(refs, list) \
                or not all(_is_number(ref) for ref in refs):
            raise ValueError(f"replies file, line {line}: a sentence needs a text and a list of numbers as 'refs'")
        sentences.append(SimpleNamespace(text=item["text"], refs=tuple(refs)))
    passages = []
    for item in record.get("passages") or []:
        if not isinstance(item, dict) or not _is_number(item.get("number")) or not isinstance(item.get("pointer"), str):
            raise ValueError(f"replies file, line {line}: a passage needs a 'number' and a 'pointer'")
        passages.append(SimpleNamespace(number=item["number"], pointer=item["pointer"], page=item.get("page"),
                                        title=item.get("title") or "", text=item.get("text") or ""))
    removed = record.get("removed") or 0
    if not _is_number(removed):
        raise ValueError(f"replies file, line {line}: 'removed' is not a number")
    reply = SimpleNamespace(status=status, sentences=sentences, passages=passages, removed=removed,
                            message=record.get("message") or None, raw_answer=record.get("raw_answer") or None)
    return key.strip(), reply


def load_replies(path) -> Dict[str, Any]:
    """The recorded replies of ``path`` by question id."""
    replies: Dict[str, Any] = {}
    first_line: Dict[str, int] = {}
    text = read_text(path, "replies file")
    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except ValueError:
            raise ValueError(f"replies file, line {number}: not a JSON record") from None
        key, reply = _reply(record, number)
        if key in first_line:
            raise ValueError(f"replies file, line {number}: repeats the id of line {first_line[key]}")
        first_line[key] = number
        replies[key] = reply
    return replies
