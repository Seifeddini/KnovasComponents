"""Past chats of the assistant ("Knovas Assistent"), stored in the Platform's own database.

A chat holds what a person asked and the answers as they were shown, each answer with the references it cites
(number, pointer, page, title and, if given, the passage text). It belongs to one person: requirement W-07 and
contract L2-PLATFORM-ASSISTANT POST-03 (plan FEAT-2026-11-19-platform-mvp, item PMVP-21).

Three properties are load-bearing:

    Owner only, in SQL. Every statement that reads, writes or deletes a chat filters on ``owner_user_id`` itself; no
    statement trusts an earlier check. Another person's chat, an unknown id and a malformed id all raise the same
    ``ChatNotFound``, whose message holds no id, so an id reveals nothing about whether it exists.

    Checked before the database. A role other than ``user`` or ``assistant``, a text that is not a string, a title
    over 200 characters and a malformed reference raise ``ValueError`` before any statement runs. No error message
    repeats what a person typed.

    No text in logs (POST-04). Log lines carry ids and counts only: never a question, an answer, a title, a pointer
    or a passage.

The text is stored as given: nothing is encrypted, cut or summarised here. A title is one line (runs of white space
become one space, the ends are trimmed) and may be empty. Messages come back in the order they were added; the
references of a message by their number. A message and its references are written in one transaction that holds the
chat's row, so two writers on one chat queue rather than collide.

Rows are tuples, mapped by position, like the rest of identity/.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional
from uuid import UUID

logger = logging.getLogger(__name__)

ROLES = ("user", "assistant")
TITLE_MAX = 200


class ChatNotFound(LookupError):
    """No chat of this person by that id: missing, someone else's, or not an id at all. Says which never."""

    status = 404

    def __init__(self) -> None:
        super().__init__("Chat nicht gefunden.")


@dataclass(frozen=True)
class ChatSummary:
    """One line of a person's chat list."""

    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class Message:
    id: UUID
    role: str
    text: str
    created_at: datetime
    #: {"number", "pointer", "page", "title", "text"} per reference, by number
    references: List[Dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class Chat:
    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime
    messages: List[Message] = field(default_factory=list)


def _uuid(value: Any) -> Optional[UUID]:
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _owner(value: Any) -> UUID:
    owner = _uuid(value)
    if owner is None:
        # The owner comes from the session, never from a request: a bad one is a programming error.
        raise ValueError("owner ist die Id einer Person.")
    return owner


def _title(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("title ist Text.")
    title = " ".join(value.split())
    if len(title) > TITLE_MAX:
        raise ValueError(f"title ist länger als {TITLE_MAX} Zeichen.")
    return title


def _int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _references(references: Iterable[Any]) -> List[tuple]:
    """(number, pointer, page, title, text) per reference, checked; keys beyond these are not stored."""
    rows: List[tuple] = []
    for ref in references or ():
        if not isinstance(ref, Mapping):
            raise ValueError("Eine Referenz ist ein Objekt mit number, pointer, page, title und text.")
        number, pointer, page = ref.get("number"), ref.get("pointer"), ref.get("page")
        title, text = ref.get("title"), ref.get("text")
        if not _int(number) or number < 1:
            raise ValueError("Die number einer Referenz ist eine ganze Zahl ab 1.")
        if not isinstance(pointer, str) or not pointer:
            raise ValueError("Eine Referenz hat einen pointer.")
        if page is not None and not _int(page):
            raise ValueError("Die page einer Referenz ist eine ganze Zahl oder fehlt.")
        if title is not None and not isinstance(title, str):
            raise ValueError("Der title einer Referenz ist Text.")
        if text is not None and not isinstance(text, str):
            raise ValueError("Der text einer Referenz ist Text.")
        rows.append((number, pointer, page, title or "", text))
    numbers = [r[0] for r in rows]
    if len(set(numbers)) != len(numbers):
        raise ValueError("Zwei Referenzen einer Nachricht haben dieselbe number.")
    return rows


class ChatStore:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    # -- writing -------------------------------------------------------------------------------------------------

    def create_chat(self, *, owner: Any, title: str) -> UUID:
        clean = _title(title)
        owner_id = _owner(owner)
        chat_id = self._conn.execute(
            "INSERT INTO assistant_chats (owner_user_id, title) VALUES (%s, %s) RETURNING id",
            (owner_id, clean)).fetchone()[0]
        logger.debug("Chat %s created for user %s", chat_id, owner_id)
        return chat_id

    def add_message(self, chat_id: Any, *, owner: Any, role: str, text: str,
                    references: Iterable[Mapping[str, Any]] = ()) -> UUID:
        """Append a message to the owner's chat and move the chat to the top of their list. Returns its id."""
        if role not in ROLES:
            raise ValueError("role ist user oder assistant.")
        if not isinstance(text, str):
            raise ValueError("text ist Text.")
        refs = _references(references)
        owner_id = _owner(owner)
        cid = _uuid(chat_id)
        if cid is None:
            raise ChatNotFound()
        with self._conn.transaction():
            # Holds the chat's row until the transaction ends, so the next ordinal below is read after any other
            # writer on this chat has committed. No row: not this person's chat; nothing has changed.
            if self._conn.execute(
                    "UPDATE assistant_chats SET updated_at = now() WHERE id = %s AND owner_user_id = %s RETURNING id",
                    (cid, owner_id)).fetchone() is None:
                raise ChatNotFound()
            message_id = self._conn.execute(
                "INSERT INTO assistant_messages (chat_id, ordinal, role, text) "
                "SELECT c.id, (SELECT coalesce(max(m.ordinal) + 1, 0) FROM assistant_messages m "
                "              WHERE m.chat_id = c.id), %s, %s "
                "FROM assistant_chats c WHERE c.id = %s AND c.owner_user_id = %s RETURNING id",
                (role, text, cid, owner_id)).fetchone()[0]
            if refs:
                with self._conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO assistant_references (message_id, number, pointer, page, title, text) "
                        "SELECT m.id, %s, %s, %s, %s, %s FROM assistant_messages m "
                        "JOIN assistant_chats c ON c.id = m.chat_id WHERE m.id = %s AND c.owner_user_id = %s",
                        [(*r, message_id, owner_id) for r in refs])
        logger.debug("Chat %s: message %s stored with %d references", cid, message_id, len(refs))
        return message_id

    def delete_chat(self, chat_id: Any, *, owner: Any) -> None:
        """Delete the owner's chat with its messages and their references."""
        owner_id = _owner(owner)
        cid = _uuid(chat_id)
        if cid is None:
            raise ChatNotFound()
        if self._conn.execute("DELETE FROM assistant_chats WHERE id = %s AND owner_user_id = %s RETURNING id",
                              (cid, owner_id)).fetchone() is None:
            raise ChatNotFound()
        logger.info("Chat %s of user %s deleted", cid, owner_id)

    # -- reading -------------------------------------------------------------------------------------------------

    def list_chats(self, *, owner: Any) -> List[ChatSummary]:
        """The owner's chats, the one with the latest message first."""
        rows = self._conn.execute(
            "SELECT id, title, created_at, updated_at FROM assistant_chats WHERE owner_user_id = %s "
            "ORDER BY updated_at DESC, created_at DESC, id", (_owner(owner),)).fetchall()
        return [ChatSummary(id=r[0], title=r[1], created_at=r[2], updated_at=r[3]) for r in rows]

    def get_chat(self, chat_id: Any, *, owner: Any) -> Chat:
        """The owner's chat with its messages in order, each with its references by number."""
        owner_id = _owner(owner)
        cid = _uuid(chat_id)
        if cid is None:
            raise ChatNotFound()
        head = self._conn.execute(
            "SELECT id, title, created_at, updated_at FROM assistant_chats WHERE id = %s AND owner_user_id = %s",
            (cid, owner_id)).fetchone()
        if head is None:
            raise ChatNotFound()
        # Messages before references: a message written in between is then left out whole, never shown without
        # the references it was stored with (a message and its references commit together).
        messages = self._conn.execute(
            "SELECT m.id, m.role, m.text, m.created_at FROM assistant_messages m "
            "JOIN assistant_chats c ON c.id = m.chat_id "
            "WHERE c.id = %s AND c.owner_user_id = %s ORDER BY m.ordinal", (cid, owner_id)).fetchall()
        refs: Dict[UUID, List[Dict[str, Any]]] = {}
        for r in self._conn.execute(
                "SELECT r.message_id, r.number, r.pointer, r.page, r.title, r.text FROM assistant_references r "
                "JOIN assistant_messages m ON m.id = r.message_id JOIN assistant_chats c ON c.id = m.chat_id "
                "WHERE c.id = %s AND c.owner_user_id = %s ORDER BY m.ordinal, r.number", (cid, owner_id)):
            refs.setdefault(r[0], []).append(
                {"number": r[1], "pointer": r[2], "page": r[3], "title": r[4], "text": r[5]})
        return Chat(id=head[0], title=head[1], created_at=head[2], updated_at=head[3],
                    messages=[Message(id=r[0], role=r[1], text=r[2], created_at=r[3], references=refs.get(r[0], []))
                              for r in messages])
